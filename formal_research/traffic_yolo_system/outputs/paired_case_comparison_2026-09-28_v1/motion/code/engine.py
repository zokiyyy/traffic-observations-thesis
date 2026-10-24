"""Offline, source-bound image-motion/count proxies from existing observations.

No detector, tracker, media decoder or network is used. The large frame log is
read once, line by line, while hashing its original bytes. Existing artefacts
remain immutable; this module writes only a separate demonstration dataset.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import resource
import subprocess
import sys
import time

SYSTEM_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "Bellevue_150th_Newport__2017-09-11_08-08-31.mp4"
SOURCE_SHA = "969af533145e1a1afaa8fb4afa754205c338171760ff02b97e2aef4f04094c93"
FINGERPRINT = "f979f0ea2ee5e16055e8b16108f71f776745083cace40096860d5f33684e8827"
BASE = SYSTEM_ROOT / "outputs/newport_full_v5_2026-09-24"
ATTEMPT = BASE / "downloaded_server_results/full_runs" / SOURCE_ID / FINGERPRINT / "attempt-000001"
OUTPUT = SYSTEM_ROOT / "outputs/traffic_state_demo_newport_2026-09-27"
UNITY_JSON = SYSTEM_ROOT.parent / "traffic_unity_demo/Assets/StreamingAssets/traffic_demo.json"
TICKS_PER_SECOND = 90000
WINDOW_TICKS = 10 * TICKS_PER_SECOND
LABELS = {"road_left": "Left road", "road_top": "Upper road", "road_right": "Right road", "junction_center": "Junction center"}
PARAMETERS = {
    "window_seconds": 10, "motion_history_min_seconds": 1.0,
    "motion_history_max_seconds": 1.2, "motion_min_coverage": .5,
    "motion_min_vehicle_seconds": 5., "density_quantiles": [.33, .67],
    "motion_low_quantile": .25, "motion_min_calibration_windows": 30,
    "persistent_seconds": 30., "quantile_method": "linear interpolation at (n-1)*q",
    "weighted_median_method": "smallest ordered value whose cumulative duration reaches half the total",
    "calibration_scope": "same-source offline development; not held-out evaluation or road capacity",
    "motion_history_reset": "missing observation, different assigned zone, invalid frame or nonconsecutive frame index",
    "motion_unit": "source-image pixels per second; not km/h",
}
NOTES = [
    "Offline 10-second summaries use all observations within each window; they are not instantaneous or causal real-time decisions.",
    "Count levels compare the same road region within this hour only; actual road density and capacity are not calibrated.",
    "Image motion uses bottom-center displacement over about one second of continuously observed tracks, measured in pixels/second, not km/h.",
    "Relatively low image motion means at or below the 25th percentile of valid motion windows in this region and hour; it does not mean stopped or nearly stationary. Pixels/second cannot be compared directly across regions.",
    "Persistent high count and relatively low image motion may include normal signal waiting; this is not a congestion conclusion validated against ground truth.",
    "Track fragmentation, box jitter, occlusion and perspective affect these proxies. Insufficient evidence remains unknown and is not replaced by zero.",
    "Thresholds are calibrated for development on this same Validation hour; no new manual annotations or Main Test data are used.",
    "The final window shorter than 10 seconds retains count data but is not classified, used for calibration, or included in persistence durations.",
    "No persistent signal does not imply free flow; no observed vehicles does not prove that the road is empty.",
]


def finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def validate_source(row):
    if row.get("split") != "Validation":
        raise ValueError("Only the approved Validation source may be read")
    if row.get("source_id", row.get("source")) != SOURCE_ID:
        raise ValueError("Unexpected source; only the configured completed Validation source is allowed")


def split_ticks(start, duration):
    """Split half-open presentation intervals without dropping fractional frames."""
    end = start + duration
    while start < end:
        index = start // WINDOW_TICKS
        stop = min(end, (index + 1) * WINDOW_TICKS)
        yield index, stop - start
        start = stop


class MotionHistory:
    def __init__(self, zones):
        self.zones = set(zones)
        self.histories = {}
        self.peak_points = 0

    def update(self, frame_index, pts, tracks, valid=True):
        if not valid:
            self.histories.clear()
            return {}
        current, motions, seen = {}, {}, set()
        for track in tracks:
            identity = integer(track.get("track_id"), "track_id", 1)
            if identity in seen:
                raise ValueError("Duplicate track ID in one frame")
            seen.add(identity)
            zone = track.get("assigned_zone")
            if zone not in self.zones:
                if zone != "outside_roi":
                    raise ValueError("Unknown assigned_zone")
                continue
            box = track.get("box_xyxy")
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError("box_xyxy must contain four coordinates")
            x1, y1, x2, y2 = [finite(v, "box coordinate") for v in box]
            if not x1 < x2 or not y1 < y2:
                raise ValueError("Track box must have positive size")
            point = ((x1 + x2) / 2., y2)
            old = self.histories.get(identity)
            if old is None or old["zone"] != zone or old["frame"] != frame_index - 1:
                old = {"zone": zone, "points": deque()}
            points = old["points"]
            while points and points[0][0] < pts - 108000:
                points.popleft()
            reference = next((p for p in reversed(points) if p[0] <= pts - TICKS_PER_SECOND), None)
            speed = None
            if reference is not None:
                elapsed = (pts - reference[0]) / TICKS_PER_SECOND
                speed = math.hypot(point[0] - reference[1], point[1] - reference[2]) / elapsed
            points.append((pts, *point))
            old["frame"] = frame_index
            current[identity] = old
            motions[identity] = (zone, speed)
        self.histories = current  # missing observations disappear immediately
        self.peak_points = max(self.peak_points, sum(len(x["points"]) for x in current.values()))
        return motions


def weighted_median(values):
    values = sorted((value, weight) for value, weight in values if weight > 0)
    if not values:
        return -1.
    half = sum(weight for _, weight in values) / 2.
    total = 0
    for value, weight in values:
        total += weight
        if total >= half:
            return value
    raise AssertionError("Unreachable weighted median")


def aggregate_frames(rows, zones, progress=False):
    """Bounded histories plus small window/track accumulators, never frame arrays."""
    counts = defaultdict(lambda: {zone: 0 for zone in zones})
    valid_ticks = defaultdict(int)
    motions = defaultdict(lambda: defaultdict(lambda: [0., 0]))
    history = MotionHistory(zones)
    cursor, frame_count, last_valid_pts = 0, 0, None
    held_ticks = defaultdict(int)
    held_intervals = []
    for row in rows:
        index = integer(row.get("frame_index"), "frame_index")
        pts = integer(row.get("pts"), "pts")
        duration = integer(row.get("duration_ticks"), "duration_ticks", 1)
        if index != frame_count or pts != cursor:
            raise ValueError("Frames and integer PTS must be contiguous from zero")
        valid = row.get("valid")
        tracks = row.get("tracks_primary")
        if type(valid) is not bool or not isinstance(tracks, list):
            raise ValueError("Require explicit validity and current track observations")
        if not valid and tracks:
            raise ValueError("Invalid frame cannot contain observed tracks")
        held = duration > 45000
        if held:
            history = MotionHistory(zones)
        observed = history.update(index, pts, tracks, valid=valid)
        if held:
            observed = {identity: (zone, None) for identity, (zone, rate) in observed.items()}
            history = MotionHistory(zones)
            held_intervals.append({"frame_index": index, "start_ticks": pts, "end_ticks": pts + duration, "duration_s": duration / TICKS_PER_SECOND})
        if valid:
            last_valid_pts = pts
            declared = row.get("zone_counts")
            if not isinstance(declared, dict) or set(declared) != set(zones):
                raise ValueError("All approved zone counts are required")
            actual = Counter(zone for zone, _ in observed.values())
            if any(integer(declared[z], "zone count") != actual[z] for z in zones):
                raise ValueError("Stored counts differ from current observed ROI tracks")
        for window, ticks in split_ticks(pts, duration):
            if held:
                held_ticks[window] += ticks
            if not valid:
                continue
            valid_ticks[window] += ticks
            for zone in zones:
                counts[window][zone] += declared[zone] * ticks
            for identity, (zone, rate) in observed.items():
                if rate is not None:
                    accumulator = motions[(window, zone)][identity]
                    accumulator[0] += rate * ticks
                    accumulator[1] += ticks
        cursor += duration
        frame_count += 1
        if progress and frame_count % 10000 == 0:
            print(json.dumps({"stage": "stream_derivation", "frames": frame_count,
                              "source_seconds": cursor / TICKS_PER_SECOND}), flush=True)
    return {"held_ticks": dict(held_ticks), "held_intervals": held_intervals, "counts": dict(counts), "valid_ticks": dict(valid_ticks), "motions": dict(motions),
            "duration_ticks": cursor, "frame_count": frame_count, "last_valid_pts": last_valid_pts,
            "peak_history_points": history.peak_points}


def quantile(values, q):
    values = sorted(values)
    if not values:
        return -1.
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def density_level(count, low, high):
    if count == 0:
        return "empty"
    if count < 0 or low < 0 or high <= low:
        return "unknown"
    return "low" if count <= low else "medium" if count <= high else "high"


def build_windows(authoritative, aggregate, zone_ids):
    grouped = defaultdict(dict)
    for row in authoritative:
        validate_source(row)
        index, zone = row["window_index"], row["zone_name"]
        if zone not in zone_ids or zone in grouped[index]:
            raise ValueError("Unexpected or duplicated authoritative zone/window")
        grouped[index][zone] = row
    total_ticks = aggregate["duration_ticks"]
    count = (total_ticks + WINDOW_TICKS - 1) // WINDOW_TICKS
    if set(grouped) != set(range(count)):
        raise ValueError("Authoritative windows are missing, extra or out of sequence")
    result = []
    for index in range(count):
        if set(grouped[index]) != set(zone_ids):
            raise ValueError("Missing authoritative zone")
        begin, end = index * WINDOW_TICKS, min((index + 1) * WINDOW_TICKS, total_ticks)
        actual_valid = aggregate["valid_ticks"].get(index, 0)
        full_valid = end - begin == WINDOW_TICKS and actual_valid == WINDOW_TICKS
        hold_s = aggregate["held_ticks"].get(index, 0) / TICKS_PER_SECOND
        complete = full_valid and hold_s == 0
        output = {"index": index, "start_s": begin / TICKS_PER_SECOND,
                  "end_s": end / TICKS_PER_SECOND, "complete": complete, "full_valid_window": full_valid, "held_duration_s": hold_s, "zones": []}
        for zone in zone_ids:
            row = grouped[index][zone]
            for field, expected in (("source_start_seconds", begin / TICKS_PER_SECOND),
                                    ("source_end_seconds_exclusive", end / TICKS_PER_SECOND),
                                    ("valid_duration_seconds", actual_valid / TICKS_PER_SECOND)):
                if abs(finite(row[field], field) - expected) > 1e-7:
                    raise ValueError(f"Authoritative {field} differs from streamed intervals")
            integral = aggregate["counts"].get(index, {}).get(zone, 0)
            authoritative_mean = row["mean_current_vehicles"]
            if actual_valid:
                if abs(finite(authoritative_mean, "window mean") - integral / actual_valid) > 1e-7:
                    raise ValueError("Recomputed duration-weighted mean differs from authoritative windows")
            elif authoritative_mean is not None:
                raise ValueError("Missing interval cannot have an authoritative mean")
            tracks = aggregate["motions"].get((index, zone), {})
            motion_ticks = sum(value[1] for value in tracks.values())
            median = weighted_median([(value[0] / value[1], value[1]) for value in tracks.values()])
            coverage = motion_ticks / integral if integral else 0.
            if coverage > 1. + 1e-12:
                raise ValueError("Motion evidence cannot exceed current-observation exposure")
            available = (complete and coverage >= PARAMETERS["motion_min_coverage"] and
                         motion_ticks / TICKS_PER_SECOND >= PARAMETERS["motion_min_vehicle_seconds"])
            output["zones"].append({
                "zone_id": zone, "count_mean": authoritative_mean if actual_valid else -1.,
                "motion_median_px_s": median if available else -1.,
                "motion_available": available, "motion_coverage": coverage,
                "motion_vehicle_seconds": motion_ticks / TICKS_PER_SECOND,
                "observed_vehicle_seconds": integral / TICKS_PER_SECOND,
                "motion_track_count": len(tracks), "valid_duration_s": actual_valid / TICKS_PER_SECOND,
                "missing_duration_s": (end - begin - actual_valid) / TICKS_PER_SECOND,
            })
        result.append(output)
    return result


def calibrate(windows, zone_ids):
    thresholds = {}
    for zone in zone_ids:
        rows = [r for w in windows if w["complete"] for r in w["zones"] if r["zone_id"] == zone]
        count_values = [r["count_mean"] for r in rows]
        motion_values = [r["motion_median_px_s"] for r in rows if r["motion_available"]]
        thresholds[zone] = {
            "density_low_upper": quantile(count_values, .33),
            "density_high_lower": quantile(count_values, .67),
            "motion_low_upper": quantile(motion_values, .25) if len(motion_values) >= 30 else -1.,
            "density_calibration_windows": len(count_values),
            "motion_calibration_windows": len(motion_values),
        }
    return thresholds


def classify_windows(windows, thresholds):
    streaks = {zone: [0., 0.] for zone in thresholds}
    previous_end = None
    density_labels = {"empty": "No vehicles observed", "low": "Relatively low count", "medium": "Medium relative count", "high": "Relatively high count", "unknown": "Count level undetermined"}
    state_labels = {"no_observed_vehicles": "No vehicles observed", "no_persistent_signal": "No persistent signal",
                    "high_count_persistent": "Persistent high count", "high_count_low_motion_persistent": "Persistent high count and relatively low image motion",
                    "motion_insufficient": "Insufficient motion evidence", "insufficient_window": "Incomplete window; unclassified",
                    "calibration_insufficient": "Insufficient calibration; unknown"}
    for window in windows:
        contiguous = previous_end is not None and abs(window["start_s"] - previous_end) <= 1e-8
        duration = window["end_s"] - window["start_s"]
        for row in window["zones"]:
            zone = row["zone_id"]
            limits = thresholds[zone]
            high, joint = streaks[zone] if contiguous else (0., 0.)
            level = density_level(row["count_mean"], limits["density_low_upper"], limits["density_high_lower"]) if window["complete"] else "unknown"
            motion_ready = bool(row["motion_available"] and limits["motion_low_upper"] >= 0)
            is_high = window["complete"] and level == "high"
            is_joint = is_high and motion_ready and row["motion_median_px_s"] <= limits["motion_low_upper"]
            high = high + duration if is_high else 0.
            joint = joint + duration if is_joint else 0.
            streaks[zone] = [high, joint]
            if not window["complete"]:
                state, reason = "insufficient_window", "Less than a complete valid 10-second window; data are retained without classification or continued persistence."
            elif level == "empty":
                state, reason = "no_observed_vehicles", "No vehicles were observed in the selected region during this window; the real road is not necessarily empty."
            elif joint >= 30:
                state, reason = "high_count_low_motion_persistent", "High count and relatively low image motion persist for at least 30 seconds. Motion is low only relative to this region and hour, does not imply stopped vehicles, may include signal waiting, and is not congestion ground truth."
            elif high >= 30:
                state, reason = "high_count_persistent", "Relatively high count persists for at least 30 seconds; this signal does not infer vehicle speed or congestion."
            elif level == "unknown":
                state, reason = "calibration_insufficient", "Count quantile thresholds are not distinguishable; the count level remains unknown rather than forcing three levels."
            elif not motion_ready:
                state, reason = "motion_insufficient", "Continuous-track coverage or vehicle-seconds are insufficient, or the region has fewer than 30 motion-calibration windows; unknown motion is not replaced by zero."
            else:
                state, reason = "no_persistent_signal", "The continuous 30-second conditions are not met; this does not imply free flow."
            row.update(density_level=level, density_label=density_labels[level],
                       traffic_state=state, traffic_state_label=state_labels[state],
                       high_count_duration_s=high, joint_duration_s=joint, reason=reason)
        previous_end = window["end_s"]

