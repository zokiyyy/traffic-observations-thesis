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
        raise ValueError("Unexpected source; only the existing Newport hour is allowed")


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
        observed = history.update(index, pts, tracks, valid=valid)
        if valid:
            last_valid_pts = pts
            declared = row.get("zone_counts")
            if not isinstance(declared, dict) or set(declared) != set(zones):
                raise ValueError("All approved zone counts are required")
            actual = Counter(zone for zone, _ in observed.values())
            if any(integer(declared[z], "zone count") != actual[z] for z in zones):
                raise ValueError("Stored counts differ from current observed ROI tracks")
        for window, ticks in split_ticks(pts, duration):
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
    return {"counts": dict(counts), "valid_ticks": dict(valid_ticks), "motions": dict(motions),
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
        complete = end - begin == WINDOW_TICKS and actual_valid == WINDOW_TICKS
        output = {"index": index, "start_s": begin / TICKS_PER_SECOND,
                  "end_s": end / TICKS_PER_SECOND, "complete": complete, "zones": []}
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


def stat(path):
    info = Path(path).stat()
    return {"size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns}


def small_json(path):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def write_text(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refuse to replace a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(content)
    temporary.replace(path)


def write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    write_text(path, text)
    return hashlib.sha256(text.encode()).hexdigest()


def derive(overwrite=False, publish_unity=True):
    started = time.perf_counter()
    if OUTPUT.resolve() != (SYSTEM_ROOT / "outputs/traffic_state_demo_newport_2026-09-27").absolute():
        raise ValueError("Derived output directory must not redirect into existing analysis")
    if OUTPUT.exists() and any(OUTPUT.iterdir()) and not overwrite:
        raise FileExistsError("Derived output exists; use explicit --overwrite to replace only derived files")
    files = {
        "frames": ATTEMPT / "frames.jsonl", "windows": ATTEMPT / "windows.jsonl",
        "summary": ATTEMPT / "run_summary.json", "completion": ATTEMPT / "completion.json",
        "provenance": ATTEMPT / "provenance.json",
        "roi": BASE / "downloaded_server_results/config/roi.json",
        "render_receipt": BASE / "downloaded_server_results/presentation/render_receipt.json",
        "local_delivery": BASE / "local_delivery_completion.json",
        "video": BASE / "presentation/complete_validation_v5.mp4",
        "implementation": Path(__file__).resolve(),
        "software_tests": SYSTEM_ROOT / "tests/test_traffic_state_demo.py",
    }
    before = {key: stat(path) for key, path in files.items()}
    summary, summary_sha = small_json(files["summary"])
    validate_source(summary)  # reject before opening detailed observations
    if summary.get("status") != "completed" or summary.get("synthetic") is not False or summary.get("time_mode") != "audited_pts":
        raise ValueError("Require a completed real audited-PTS run")
    receipt, receipt_sha = small_json(files["render_receipt"])
    validate_source(receipt)
    completion, completion_sha = small_json(files["completion"])
    provenance, provenance_sha = small_json(files["provenance"])
    validate_source(provenance["task"])
    if completion.get("source_id") != SOURCE_ID or completion.get("fingerprint") != FINGERPRINT:
        raise ValueError("Completion receipt belongs to another source/configuration")
    if receipt.get("state") != "complete" or receipt["input_hashes"]["source"] != SOURCE_SHA:
        raise ValueError("Require the completed video bound to the approved source")
    if completion["files"]["run_summary.json"]["sha256"] != summary_sha or completion["files"]["provenance.json"]["sha256"] != provenance_sha:
        raise ValueError("Summary/provenance changed since completed run")
    roi, roi_sha = small_json(files["roi"])
    if roi.get("source_split") != "Validation" or roi.get("source_video") != SOURCE_ID:
        raise ValueError("ROI must belong to the approved Validation source")
    if (roi_sha != receipt["input_hashes"]["zones"] or roi_sha != summary["roi_sha256"] or
            (roi.get("frame_width"), roi.get("frame_height"), roi.get("assignment_point")) != (1280, 720, "bottom_center")):
        raise ValueError("Original ROI hash or geometry coordinate contract differs")
    zone_ids = [row["zone_name"] for row in roi["zones"]]
    if zone_ids != list(LABELS):
        raise ValueError("Unexpected ROI identities/order")
    delivery, delivery_sha = small_json(files["local_delivery"])
    artifact = delivery["artifacts"]["rendered_video"]
    if not all(delivery.get(key) is True for key in ("completed", "video_verified", "full_data_verified")):
        raise ValueError("Local delivery is incomplete")
    if (artifact["sha256"] != receipt["output_sha256"] or artifact["sha256"] != artifact["expected_sha256"] or
            Path(artifact["path"]).resolve() != files["video"].resolve() or before["video"]["size_bytes"] != artifact["bytes"]):
        raise ValueError("Local video no longer matches its verified delivery receipt")

    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(SYSTEM_ROOT / "tests"),
                            "-p", "test_traffic_state_demo.py", "-v"], capture_output=True, text=True,
                           cwd=SYSTEM_ROOT, check=False)
    if tests.returncode:
        raise RuntimeError("State derivation software tests failed:\n" + tests.stderr)
    windows_bytes = files["windows"].read_bytes()
    windows_sha = hashlib.sha256(windows_bytes).hexdigest()
    if windows_sha != completion["files"]["windows.jsonl"]["sha256"]:
        raise ValueError("Authoritative windows changed since completed run")
    authoritative = [json.loads(line) for line in windows_bytes.splitlines()]
    frame_hash = hashlib.sha256()
    frame_bytes = 0

    def frame_rows():
        nonlocal frame_bytes
        with files["frames"].open("rb") as handle:
            for line in handle:
                frame_hash.update(line)
                frame_bytes += len(line)
                row = json.loads(line)
                validate_source(row)
                if row.get("record_type") != "frame" or row.get("configuration_sha256") != FINGERPRINT:
                    raise ValueError("Unexpected frame record/configuration")
                if (row.get("time_base_num"), row.get("time_base_den")) != (1, TICKS_PER_SECOND):
                    raise ValueError("Unexpected original time base")
                for field, expected in (("source_timestamp_seconds", row["pts"] / TICKS_PER_SECOND),
                                        ("timestamp_seconds", row["pts"] / TICKS_PER_SECOND),
                                        ("duration_seconds", row["duration_ticks"] / TICKS_PER_SECOND)):
                    if abs(finite(row[field], field) - expected) > 1e-8:
                        raise ValueError("Float presentation interval differs from original integer ticks")
                yield row

    aggregate = aggregate_frames(frame_rows(), zone_ids, progress=True)
    frames_sha = frame_hash.hexdigest()
    if frames_sha != receipt["input_hashes"]["records"] or frames_sha != completion["files"]["frames.jsonl"]["sha256"]:
        raise ValueError("Original frame-record SHA256 does not match render/completion receipts")
    if aggregate["frame_count"] != summary["frame_count"] or frame_bytes != before["frames"]["size_bytes"]:
        raise ValueError("Incomplete input frame stream")
    duration_s = aggregate["duration_ticks"] / TICKS_PER_SECOND
    if abs(duration_s - summary["duration_seconds"]) > 1e-8:
        raise ValueError("Streamed source duration differs from completed run")
    windows = build_windows(authoritative, aggregate, zone_ids)
    thresholds = calibrate(windows, zone_ids)
    classify_windows(windows, thresholds)
    after = {key: stat(path) for key, path in files.items()}
    if after != before:
        raise ValueError("An original input or implementation changed during derivation")
    zones = []
    for row in roi["zones"]:
        zone = row["zone_name"]
        blue, green, red = row["color_bgr"]
        zones.append({"id": zone, "label": LABELS[zone], "color_hex": f"#{red:02X}{green:02X}{blue:02X}",
                      "polygon": [{"x": x / 1280, "y": y / 720} for x, y in row["polygon_points"]], **thresholds[zone]})
    milestones = [{"time_s": 0., "label": "Start: offline 10-second summary demo"}]
    for zone in zone_ids:
        for state, label in (("high_count_persistent", "First persistent high count"),
                             ("high_count_low_motion_persistent", "First persistent high count and relatively low image motion")):
            match = next((w for w in windows if any(r["zone_id"] == zone and r["traffic_state"] == state for r in w["zones"])), None)
            if match:
                milestones.append({"time_s": match["start_s"], "label": f"{LABELS[zone]}: {label}"})
    milestones.sort(key=lambda row: (row["time_s"], row["label"]))
    dataset = {"schema_version": "newport_state_demo_v1", "source": {
        "id": SOURCE_ID, "split": "validation", "duration_s": duration_s,
        "last_frame_time_s": aggregate["last_valid_pts"] / TICKS_PER_SECOND,
        "frame_count": aggregate["frame_count"], "width": 1280, "height": 720,
        "video_width": 1280, "video_height": 870, "content_x": 0, "content_y": 150,
        "content_width": 1280, "content_height": 720, "video_path": str(files["video"].resolve()),
        "video_sha256": receipt["output_sha256"]}, "notes": NOTES, "zones": zones,
        "windows": windows, "milestones": milestones,
        "offline_window_summary": True, "calibration_same_source": True}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    dataset_sha = write_json(OUTPUT / "traffic_demo.json", dataset)
    write_json(OUTPUT / "rules_and_thresholds.json", {"parameters": PARAMETERS, "zones": thresholds, "notes": NOTES})
    table = io.StringIO()
    columns = ["window_index", "start_s", "end_s", "complete", "zone_id", "count_mean", "density_level", "density_label", "traffic_state", "traffic_state_label", "motion_median_px_s", "motion_available", "motion_coverage", "motion_vehicle_seconds", "observed_vehicle_seconds", "motion_track_count", "high_count_duration_s", "joint_duration_s", "reason"]
    writer = csv.DictWriter(table, fieldnames=columns)
    writer.writeheader()
    for w in windows:
        for row in w["zones"]:
            writer.writerow({"window_index": w["index"], "start_s": w["start_s"], "end_s": w["end_s"], "complete": w["complete"],
                             **{key: row[key] for key in columns[4:]}})
    write_text(OUTPUT / "traffic_states_10s.csv", table.getvalue())
    counts = Counter(row["traffic_state"] for w in windows for row in w["zones"])
    elapsed = time.perf_counter() - started
    report = ("# Newport one-hour offline traffic-state demonstration\n\n"
              f"Derived from {aggregate['frame_count']:,} existing frames into {len(windows)} windows and {len(windows)*len(zone_ids):,} region-window records. No model inference or new manual annotation was performed.\n\n"
              "## Interpretation and scope\n\n"
              + "\n".join(f"- {note}" for note in NOTES) + "\n\n"
              "## Computation\n\n"
              "Count means retain the original `windows.jsonl` values and are independently checked by integrating count times frame duration using integer PTS ticks. Frame holding intervals are split exactly at window boundaries.\n\n"
              "Image motion compares the current bottom-center point with the most recent continuously observed point 1.0-1.2 seconds earlier. Missing observations, invalid frames, nonconsecutive frame indices and region changes reset the history. Rates are averaged within each window, region and track using valid motion duration, then combined as a median weighted by each track's valid motion duration. Missing evidence is not interpreted as stationary traffic.\n\n"
              "Each region's count thresholds are the 33rd and 67th percentiles of complete valid windows, using linear interpolation. Zero counts are separate; indistinguishable quantiles leave nonempty windows unknown. The relatively low image-motion threshold is the 25th percentile of at least 30 valid motion windows in the same region and hour. It does not mean stopped or nearly stationary: the junction-center threshold is approximately 85.27 pixels/second. Motion is available only for complete 10-second windows with at least 50% coverage and 5 vehicle-seconds of motion evidence.\n\n"
              "Persistent high count requires 30 continuous seconds of relatively high count. The combined signal requires both high count and relatively low image motion for 30 continuous seconds and takes precedence. Broken continuity or missing required evidence resets the corresponding duration. These are explicit development rules; they do not establish traffic-state accuracy.\n\n"
              "## Generation and verification\n\n"
              f"Derivation took {elapsed:.3f} seconds. Region-window counts by state (not numbers of separate events): `{dict(counts)}`.\n\n"
              "The original detailed records are streamed and SHA256-hashed during reading, then matched against completion and render receipts. ROI and original window hashes are also checked. The video is checked against its existing verified delivery receipt and current size; this derivation does not decode the video or recompute its hash. All original input sizes and modification times are unchanged before and after the run.\n\n"
              "This report verifies derived data and software boundary tests only. Copying the Unity JSON does not verify the Unity application itself; player and visual acceptance are recorded separately. The final short window is retained, but it need not correspond to a separately seekable frame.\n\n"
              "## Deliverables\n\n"
              "- `traffic_demo.json`: English application data, also copied to Unity StreamingAssets.\n"
              "- `traffic_states_10s.csv`: readable region-window table.\n"
              "- `rules_and_thresholds.json`: development parameters and per-region thresholds.\n"
              "- `generation_receipt.json` and `software_validation.json`: source hashes, input checks and test evidence.\n"
              "- `traffic_state_overview.png`: English overview, rendered and visually reviewed separately.\n\n"
              "Previously generated Chinese notes and figures are retained for informal understanding; this README and the English outputs are the formal deliverables.\n")
    write_text(OUTPUT / "README_state_generation.md", report)
    test_receipt = {"command": tests.args, "exit_code": tests.returncode,
                    "stdout": tests.stdout, "stderr": tests.stderr,
                    "implementation_sha256": hashlib.sha256(files["implementation"].read_bytes()).hexdigest(),
                    "test_sha256": hashlib.sha256(files["software_tests"].read_bytes()).hexdigest()}
    write_json(OUTPUT / "software_validation.json", test_receipt)
    if publish_unity:
        if UNITY_JSON.parent.resolve() != UNITY_JSON.parent.absolute():
            raise ValueError("Unity JSON destination must not be a redirected directory")
        write_json(UNITY_JSON, dataset)
    provenance = {
        "status": "completed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_id": SOURCE_ID, "split": "Validation", "source_video_sha256": SOURCE_SHA,
        "input_paths": {key: str(path.resolve()) for key, path in files.items()},
        "input_stats_before": before, "input_stats_after": after, "all_input_stats_unchanged": True,
        "verified_hashes": {"frames_stream_sha256": frames_sha, "render_records_sha256": receipt["input_hashes"]["records"],
                            "roi_sha256": roi_sha, "windows_sha256": windows_sha, "run_summary_sha256": summary_sha,
                            "completion_sha256": completion_sha, "provenance_sha256": provenance_sha,
                            "render_receipt_sha256": receipt_sha, "delivery_receipt_sha256": delivery_sha,
                            "video_sha256_from_verified_delivery": receipt["output_sha256"]},
        "output_sha256": dataset_sha, "unity_json_path": str(UNITY_JSON) if publish_unity else None,
        "unity_json_sha256": dataset_sha if publish_unity else None,
        "frame_count": aggregate["frame_count"], "window_count": len(windows),
        "zone_window_count": len(windows)*len(zone_ids), "state_counts": dict(counts),
        "elapsed_seconds": elapsed, "peak_history_points": aggregate["peak_history_points"],
        "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "main_test_read": False, "new_inference": False, "original_outputs_modified": False,
    }
    write_json(OUTPUT / "generation_receipt.json", provenance)
    print(json.dumps({"status": "completed", "output": str(OUTPUT), "elapsed_seconds": elapsed,
                      "windows": len(windows), "state_counts": counts}, ensure_ascii=False), flush=True)
    return dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="Replace only this separate derived output, never source analysis")
    parser.add_argument("--no-unity-copy", action="store_true")
    args = parser.parse_args()
    derive(overwrite=args.overwrite, publish_unity=not args.no_unity_copy)


if __name__ == "__main__":
    main()
