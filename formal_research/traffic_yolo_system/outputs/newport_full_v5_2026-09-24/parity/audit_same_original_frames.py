"""Audit real full-backend GPU output against the frozen pilot on identical raw frames.

This is a software integration parity check, not a ground-truth accuracy test.
Reference postprocessing imports the archived v5 pilot implementation, never
the new full-video adapter. Actual original pixels are independently decoded
sequentially and checked against each saved live inference pixel hash.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def jsonl(path):
    with Path(path).open() as f:
        for line in f:
            yield json.loads(line)


class Audit:
    def __init__(self, output):
        self.output = output
        self.checks = Counter()
        self.errors = []

    def check(self, condition, kind, detail):
        self.checks[kind] += 1
        if not condition:
            self.errors.append({"kind": kind, "detail": detail})

    def same(self, a, b, kind, detail):
        self.check(a == b, kind, detail)

    def near(self, a, b, kind, detail, tolerance=1e-8):
        self.check(type(a) in (int, float) and math.isfinite(a) and abs(a - b) <= tolerance,
                   kind, detail)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--attempt", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True, help="Local byte-identical original Validation source")
    p.add_argument("--timing", type=Path, required=True, help="Original full-source integer PTS sidecar")
    p.add_argument("--reference-root", type=Path, required=True, help="Archived completed v5 pilot output root")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    audit = Audit(args.output)
    provenance = load(args.attempt / "provenance.json")
    config, camera, task, preflight = (provenance[k] for k in ("config", "camera", "task", "preflight"))
    # Guard before any source media access.
    if task["split"] != "Validation" or task["camera_id"] != "Bellevue_150th_Newport":
        raise ValueError("This real parity audit is restricted to Newport Validation")
    if args.source.name != task["source_id"]:
        raise ValueError("Local original source filename differs from the completed run")
    frozen_src = args.reference_root / "job" / "src"
    sys.path.insert(0, str(frozen_src.resolve()))
    from confirm_background_candidates import candidate_confirmation
    from replay_vehicle_tracking_pilot import suppress_cross_class_duplicates
    from vehicle_containment import suppress_contained_vehicles
    from run_video_tracking_pilot import TRACKER_SETTINGS, EXPECTED_MODEL_SHA, PREDICT_SETTINGS, decode_tracks
    from roi_geometry import validate_layout, assign_box, boundary_policy
    os.environ["YOLO_AUTOINSTALL"] = "false"
    import cv2
    import numpy as np
    import ultralytics
    from ultralytics.engine.results import Boxes
    from ultralytics.trackers.byte_tracker import BYTETracker
    first_index, end_index = preflight["first_frame_index"], preflight["end_frame_index_exclusive"]
    start, end = preflight["source_start_seconds"], preflight["source_end_seconds"]
    if first_index == 0 and end_index == preflight["input_frame_count"]:
        raise ValueError("This bounded gate is for the predeclared same-original-frame segment, before the full run")
    if preflight["time_mode"] != "audited_pts":
        raise ValueError("Original PTS timing is required; no CFR short-clip comparison accepted")
    audit.same([first_index, end_index], [47975, 51573], "predeclared_segment", "Known development-event native frame range")
    audit.near(start, 1600.0067444444444, "predeclared_segment", "Exact original start PTS")
    audit.near(end, 1720.0070444444445, "predeclared_segment", "Exact original end PTS")
    source_sha, timing_sha = digest(args.source), digest(args.timing)
    audit.same(source_sha, preflight["input_sha256"], "source_identity", "Original source bytes")
    audit.same(timing_sha, preflight["frame_timing_sha256"], "source_identity", "Full timing sidecar bytes")
    audit.same(config["model"]["sha256"], EXPECTED_MODEL_SHA, "frozen_model", "Trained YOLO11s unchanged")
    audit.same(provenance["metadata"].get("synthetic"), False, "actual_live_input", "Run is not a synthetic fixture")
    audit.same(config["input"]["kind"], "video", "actual_live_input", "Run consumes actual original video")
    audit.check(config["device"] not in {"cpu", "mps"}, "actual_live_input", "Explicit CUDA device was required for parity inference")
    status = load(args.attempt / "status.json")
    receipt = load(args.attempt / "completion.json")
    audit.same(status["status"], "completed", "completion_receipt", "Attempt completed before parity gate")
    audit.same(receipt["fingerprint"], provenance["fingerprint"], "completion_receipt", "Receipt configuration identity")
    audit.same(receipt["source_id"], task["source_id"], "completion_receipt", "Receipt source identity")
    for name, info in receipt["files"].items():
        path = (args.attempt / name).resolve()
        if args.attempt.resolve() not in path.parents:
            raise ValueError("Receipt path escapes attempt")
        audit.same(path.stat().st_size, info["bytes"], "completion_receipt", [name, "bytes"])
        audit.same(digest(path), info["sha256"], "completion_receipt", [name, "sha256"])
    audit.same(config["tracker"], TRACKER_SETTINGS, "frozen_tracker", "All ByteTrack fields")
    for key, value in PREDICT_SETTINGS.items():
        if key in {"device", "save", "verbose"}:
            continue
        audit.same(config["detector"].get(key), value, "frozen_detector", key)
    audit.same(camera["background_filter"]["confirmation_imgsz"], 1280, "frozen_detector", "Confirmation resolution")
    audit.same(config["dedup"]["iou_threshold"], .7, "frozen_rules", "Cross-class threshold")
    reference_bg = load(args.reference_root / "input" / "background_policy.json")
    reference_containment = load(args.reference_root / "input" / "containment_policy.json")
    reference_layout = load(args.reference_root / "input" / "zones.json")
    bg = camera["background_filter"]["policy"]
    for key in ("schema_version", "camera_id", "frame_width", "frame_height", "reviewed_background_polygon_xy",
                "confirmation_iou", "confirm_min_confidence", "development_only"):
        audit.same(bg[key], reference_bg[key], "frozen_background", key)
    audit.same(config["dedup"]["containment_policy"], reference_containment, "frozen_containment", "All containment parameters and metadata")
    # Outer source binding may be new; actual geometry and assignment are fixed.
    for key in ("zones", "assignment_point", "boundary_tie_break", "frame_width", "frame_height"):
        audit.same(camera["layout"].get(key), reference_layout.get(key), "frozen_roi", key)
    audit.same(ultralytics.__version__, provenance["runtime"]["ultralytics"], "runtime", "Reference ByteTrack package version")
    audit.same(cv2.__version__, preflight["audited_decoder"]["opencv_version"], "runtime", "Independent decoder version")
    tracker_module = importlib.import_module(BYTETracker.__module__)
    frozen_reference_config = load(args.reference_root / "results" / "config.json")
    audit.same(digest(tracker_module.__file__), frozen_reference_config["tracker_implementation"]["sha256"],
               "runtime", "Reference tracker implementation matches completed v5")
    if audit.errors:
        (args.output / "prerequisite_errors.json").write_text(json.dumps(audit.errors, indent=2))
        raise ValueError("Parity prerequisites differ; see prerequisite_errors.json")
    kwargs = {"frame_rate": 30} if "frame_rate" in inspect.signature(BYTETracker).parameters else {}
    tracker = BYTETracker(SimpleNamespace(**TRACKER_SETTINGS), **kwargs)
    polygons, boundary = validate_layout(reference_layout), boundary_policy(reference_layout)
    zone_names = [name for name, _ in polygons]
    frame_path = args.attempt / "frames.jsonl"
    frames = iter(jsonl(frame_path))
    timing = iter(jsonl(args.timing))
    cap = cv2.VideoCapture(str(args.source), cv2.CAP_FFMPEG,
                           [cv2.CAP_PROP_N_THREADS, preflight["audited_decoder"]["requested_decoder_threads"]])
    if not cap.isOpened():
        raise RuntimeError("Independent original-source decoder failed")
    tick = Fraction(preflight["time_base_num"], preflight["time_base_den"])
    first_pts = preflight["input_first_pts_ticks"]
    mapping, reverse_mapping = {}, {}
    totals, windows = Counter(), {}
    window_seconds = config["aggregation"]["window_seconds"]
    frame_checks = (args.output / "same_frame_evidence.jsonl").open("x")
    reference_records = (args.output / "reference_chain.jsonl").open("x")
    try:
        for index in range(end_index):
            clock = next(timing)
            audit.same(clock["frame_index"], index, "time_sidecar_index", index)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"Independent decoder EOF before original frame {index}")
            audit.same(frame.shape[:2], (preflight["height"], preflight["width"]), "decoded_dimensions", index)
            if index < first_index:
                continue
            row = next(frames)
            frame_sha = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
            source_time = float((clock["pts"] - first_pts) * tick)
            duration_ticks = clock.get("duration", clock.get("pkt_duration"))
            if type(duration_ticks) is not int or duration_ticks <= 0:
                raise ValueError("Original sidecar requires an explicit positive integer frame duration")
            if "duration" in clock and "pkt_duration" in clock and clock["duration"] != clock["pkt_duration"]:
                raise ValueError("Original sidecar duration fields disagree")
            duration = float(duration_ticks * tick)
            audit.same(row["frame_index"], index, "frame_sequence", index)
            audit.same(row["segment_frame_index"], index - first_index, "frame_sequence", index)
            audit.same(row["decoded_frame_sha256"], frame_sha, "same_original_pixels", index)
            audit.same(row["pts"], clock["pts"], "same_original_pts", index)
            audit.same(row["duration_ticks"], duration_ticks, "same_original_pts", index)
            audit.near(row["source_timestamp_seconds"], source_time, "time_mapping", index)
            audit.near(row["timestamp_seconds"], source_time - start, "time_mapping", index)
            audit.near(row["duration_seconds"], duration, "time_mapping", index)
            audit.near(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 - preflight["input_first_pts_seconds"], source_time,
                       "independent_decoder_time", index, tolerance=float(tick) + 1e-7)
            audit.same(row.get("valid"), True, "complete_valid_inference", index)
            raw, confirmation = row["raw_detections"], row["confirmation_raw_detections"]
            eligible, candidate_records = candidate_confirmation(raw, confirmation, reference_bg)
            local, cross_removed = suppress_cross_class_duplicates([raw[i] for i in eligible], .7)
            cross = [eligible[i] for i in local]
            for removal in cross_removed:
                removal["removed_index"] = eligible[removal["removed_index"]]
                removal["kept_index"] = eligible[removal["kept_index"]]
            final, contained_removed, unresolved = suppress_contained_vehicles(raw, cross, reference_containment)
            audit.same(row["background_confirmation"]["kept_detection_indices"], eligible, "background_parity", index)
            audit.same(row["background_confirmation"]["candidate_records"], candidate_records, "background_parity", index)
            audit.same(row["duplicate_suppression"]["kept_detection_indices"], cross, "cross_class_parity", index)
            audit.same(row["duplicate_suppression"]["removed"], cross_removed, "cross_class_parity", index)
            audit.same(row["contained_vehicle_suppression"]["kept_detection_indices"], final, "containment_parity", index)
            audit.same(row["contained_vehicle_suppression"]["removed"], contained_removed, "containment_parity", index)
            audit.same(row["contained_vehicle_suppression"]["unresolved_pairs"], unresolved, "containment_parity", index)
            audit.same(row["tracker_input_detection_indices"], final, "tracker_input_parity", index)
            data = np.array([[*d["box_xyxy"], d["confidence"], d["class_id"]] for d in raw], dtype=np.float32).reshape(-1, 6)
            boxes = Boxes(data, (preflight["height"], preflight["width"]))
            indices = np.array(final, dtype=np.int64)
            tracks = decode_tracks(tracker.update(boxes[indices]), indices, raw)
            audit.same(tracker.frame_id, index - first_index + 1, "tracker_frame_advance", index)
            observed = {item["detection_index"]: item for item in row["tracks_primary"]}
            reference = {item["detection_index"]: item for item in tracks}
            audit.same(set(observed), set(reference), "current_track_membership", index)
            counts, outside = dict.fromkeys(zone_names, 0), 0
            for raw_index, track in reference.items():
                other = observed.get(raw_index)
                if other is None:
                    continue
                for field in ("class_id", "class_name", "detection_index"):
                    audit.same(other[field], track[field], "track_semantics", [index, raw_index, field])
                audit.near(other["confidence"], track["confidence"], "track_semantics", [index, raw_index, "confidence"], 1e-6)
                for coordinate, (a, b) in enumerate(zip(other["box_xyxy"], track["box_xyxy"])):
                    audit.near(a, b, "tracked_box_parity", [index, raw_index, coordinate], 1e-4)
                rid, live_id = track["track_id"], other["track_id"]
                mapping.setdefault(rid, live_id)
                reverse_mapping.setdefault(live_id, rid)
                audit.same(mapping[rid], live_id, "identity_continuity_bijection", [index, rid])
                audit.same(reverse_mapping[live_id], rid, "identity_continuity_bijection", [index, live_id])
                zone = assign_box(track["box_xyxy"], polygons, boundary)
                audit.same(other["assigned_zone"], zone, "roi_assignment_parity", [index, raw_index])
                if zone == "outside_roi":
                    outside += 1
                else:
                    counts[zone] += 1
            audit.same(row["zone_counts"], counts, "count_parity", index)
            audit.same(row["outside_zone_count"], outside, "count_parity", index)
            audit.same(row["total_in_zones"], sum(counts.values()), "count_parity", index)
            # Independently integrate count * original presentation duration.
            position = source_time
            finish = source_time + duration
            while position < finish - 1e-10:
                wi = int(math.floor((position - start + 1e-9) / window_seconds))
                limit = min(finish, start + (wi + 1) * window_seconds, end)
                dt = limit - position
                if dt <= 0:
                    raise RuntimeError("Non-progressing independent time integration")
                for zone, count in counts.items():
                    key = (wi, zone)
                    acc = windows.setdefault(key, {"duration": 0., "weighted": 0., "min": count, "max": count, "zero": 0.})
                    acc["duration"] += dt
                    acc["weighted"] += dt * count
                    acc["zero"] += dt if count == 0 else 0
                    acc["min"], acc["max"] = min(acc["min"], count), max(acc["max"], count)
                position = limit
            totals.update({"frames": 1, "raw_640_observations": len(raw), "raw_1280_observations": len(confirmation),
                           "background_removed": len(raw) - len(eligible), "cross_class_removed": len(cross_removed),
                           "containment_removed": len(contained_removed), "unresolved_pair_observations": len(unresolved),
                           "containment_stage_A_removed": sum(item["stage"] == "A" for item in contained_removed),
                           "containment_stage_B_removed": sum(item["stage"] == "B" for item in contained_removed),
                           "reference_current_track_observations": len(tracks), "final_tracker_input_observations": len(final)})
            frame_checks.write(json.dumps({"frame_index": index, "source_pts": clock["pts"], "duration_ticks": duration_ticks,
                "source_seconds": source_time, "decoded_frame_sha256": frame_sha, "live_decoded_frame_sha256": row["decoded_frame_sha256"]}) + "\n")
            reference_records.write(json.dumps({"frame_index": index, "background_kept": eligible, "cross_class_kept": cross,
                "containment_kept": final, "tracks_primary": tracks, "zone_counts": counts, "outside": outside}) + "\n")
            if (index - first_index + 1) % 600 == 0:
                print(f"Audited {index-first_index+1}/{end_index-first_index} identical original frames", flush=True)
        audit.same(next(frames, None), None, "frame_sequence", "No extra output records")
        audit.same(totals["frames"], preflight["frame_count"], "frame_sequence", "Expected selected frame count")
    finally:
        cap.release()
        frame_checks.close()
        reference_records.close()
    observed_windows = list(jsonl(args.attempt / "windows.jsonl"))
    audit.same(len(observed_windows), len(windows), "window_parity", "Window row count")
    for row in observed_windows:
        key = (row["window_index"], row["zone_name"])
        expected = windows[key]
        audit.near(row["valid_duration_seconds"], expected["duration"], "window_parity", [*key, "duration"], 1e-7)
        audit.near(row["mean_current_vehicles"], expected["weighted"] / expected["duration"], "window_parity", [*key, "mean"], 1e-7)
        audit.near(row["valid_zero_duration_seconds"], expected["zero"], "window_parity", [*key, "zero duration"], 1e-7)
        audit.same(row["min_current_vehicles"], expected["min"], "window_parity", [*key, "min"])
        audit.same(row["max_current_vehicles"], expected["max"], "window_parity", [*key, "max"])
        audit.near(row["missing_duration_seconds"], 0, "window_parity", [*key, "missing"])
    chunk_rows = []
    for item in jsonl(args.attempt / "timeline" / "index.jsonl"):
        chunk_rows.extend(jsonl(args.attempt / "timeline" / item["path"]))
    full_rows = list(jsonl(frame_path))
    audit.same(len(chunk_rows), len(full_rows), "timeline_parity", "Timeline contains all original frames")
    for row, chunk_row in zip(full_rows, chunk_rows):
        for field in ("frame_index", "timestamp_seconds", "source_timestamp_seconds", "duration_seconds", "zone_counts", "total_in_zones"):
            audit.same(chunk_row.get(field), row.get(field), "timeline_parity", [row["frame_index"], field])
    # The input is never edited by this audit.
    audit.same(digest(args.source), source_sha, "input_unchanged", "Original video")
    audit.same(digest(args.timing), timing_sha, "input_unchanged", "Full PTS sidecar")
    report = {"status": "PASS" if not audit.errors else "FAIL", "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Engineering parity gate against frozen v5 on the exact same original decoded frames and saved real GPU raw detections",
        "not_accuracy_measurement": True, "not_independent_holdout": True, "main_test_media_opened": False,
        "source_id": task["source_id"], "split": task["split"], "source_sha256": source_sha, "timing_sha256": timing_sha,
        "original_frame_range": [first_index, end_index], "source_time_range": [start, end], "totals": dict(totals),
        "checks_by_category": dict(audit.checks), "check_count": sum(audit.checks.values()), "error_count": len(audit.errors),
        "reference_track_to_live_track_id_bijection_count": len(mapping), "runtime": {"opencv": cv2.__version__, "ultralytics": ultralytics.__version__, "numpy": np.__version__},
        "reference_code_hashes": {str(file): digest(file) for file in sorted(frozen_src.glob("*.py"))},
        "audit_script_sha256": digest(__file__), "run_provenance_sha256": digest(args.attempt / "provenance.json"),
        "input_records_sha256": digest(frame_path), "errors": audit.errors}
    (args.output / "parity_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "check_count", "error_count", "totals")}, ensure_ascii=False))
    return 0 if not audit.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
