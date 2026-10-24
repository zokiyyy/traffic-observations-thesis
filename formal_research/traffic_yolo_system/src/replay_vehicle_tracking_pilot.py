"""Replay cached Validation detections with conservative cross-class deduplication.

No YOLO inference or GPU is used. All original detections are retained verbatim
in each saved record. Suppression only changes the input to two ByteTrack arms
for prototype vehicle-level counting; it is not a four-class AP improvement.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from run_video_tracking_pilot import (
    CLASS_NAMES, EXPECTED_MODEL_SHA, TRACKER_SETTINGS, TrackDiagnostics,
    decode_detections, decode_tracks, fingerprint, now, update_status,
    validate_manifest, write_json,
)


SUPPRESSION_IOU = 0.70


def box_iou(a, b):
    overlap_width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    overlap_height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = overlap_width * overlap_height
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - intersection
    if area_a <= 0 or area_b <= 0 or union <= 0:
        raise ValueError("Invalid box area for duplicate suppression")
    return intersection / union


def suppress_cross_class_duplicates(raw, threshold=SUPPRESSION_IOU):
    """Greedy descending-confidence suppression; same-class pairs stay untouched.

    Ties retain the earlier raw index. Each removal is explained by a previously
    retained, different-class box with strictly greater-than-threshold IoU.
    Kept indices are returned in original order for reproducible tracker input.
    """
    kept, removed = [], []
    for index in sorted(range(len(raw)), key=lambda i: (-raw[i]["confidence"], i)):
        candidate = raw[index]
        suppressor = None
        for prior_index in kept:
            prior = raw[prior_index]
            if prior["class_id"] == candidate["class_id"]:
                continue
            overlap = box_iou(candidate["box_xyxy"], prior["box_xyxy"])
            if overlap > threshold:
                suppressor = {
                    "removed_index": index, "kept_index": prior_index, "iou": overlap,
                    "classes": {"removed": candidate["class_name"], "kept": prior["class_name"]},
                }
                break
        if suppressor is None:
            kept.append(index)
        else:
            removed.append(suppressor)
    return sorted(kept), removed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--background-policy", type=Path,
                        help="Explicit opt-in for previously confirmed background preselection")
    parser.add_argument("--containment-policy", type=Path,
                        help="Explicit optional heavy-vehicle containment deduplication after original suppression")
    args = parser.parse_args(argv)
    for name in ("records", "config", "manifest", "output"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if args.background_policy is not None:
        args.background_policy = args.background_policy.expanduser().resolve()
    if args.containment_policy is not None:
        args.containment_policy = args.containment_policy.expanduser().resolve()
    return args


def run(args):
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing replay output: {args.output}")
    for path in (args.records, args.config, args.manifest):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output.mkdir(parents=True, exist_ok=False)
    processed = 0
    started = now()
    try:
        update_status(args.output, state="validating", processed_frames=0)
        original_config = json.loads(args.config.read_text())
        manifest = json.loads(args.manifest.read_text())
        containment_path = getattr(args, "containment_policy", None)
        containment_policy = None
        if containment_path is not None:
            from vehicle_containment import validate_policy as validate_containment_policy
            from vehicle_containment import suppress_contained_vehicles
            containment_policy = json.loads(containment_path.read_text())
            validate_containment_policy(containment_policy)
        background_policy_hash = None
        if args.background_policy is not None:
            from confirm_background_candidates import validate_policy
            policy = json.loads(args.background_policy.read_text())
            validate_policy(policy)
            if policy["source_clip_sha256"] != manifest["clip_sha256"]:
                raise ValueError("Background policy belongs to another clip")
            background_policy_hash = fingerprint(args.background_policy)["sha256"]
        validate_manifest(manifest, original_config["source"])
        if original_config["source_split"] != manifest["source_split"]:
            raise ValueError("Original run and replay manifest refer to different source splits")
        if original_config["manifest"]["sha256"] != fingerprint(args.manifest)["sha256"]:
            raise ValueError("Replay manifest differs from original inference manifest")
        if original_config["model"]["sha256"] != EXPECTED_MODEL_SHA:
            raise ValueError("Expected the frozen trained YOLO11s model")
        if original_config["tracker_settings_both_arms"] != TRACKER_SETTINGS:
            raise ValueError("Original tracker settings differ from the frozen pilot settings")
        if original_config["predict_settings"]["conf"] != 0.10:
            raise ValueError("Replay requires cached raw detector outputs at 0.10")
        if {int(k): v for k, v in original_config["class_names"].items()} != CLASS_NAMES:
            raise ValueError("Unexpected original model class mapping")

        os.environ["YOLO_AUTOINSTALL"] = "false"
        import numpy as np
        import torch
        import ultralytics
        from ultralytics.engine.results import Boxes
        from ultralytics.trackers.byte_tracker import BYTETracker

        if ultralytics.__version__ != original_config["versions"]["ultralytics"]:
            raise ValueError("Replay must use the same Ultralytics version as original inference")
        module = importlib.import_module(BYTETracker.__module__)
        tracker_fingerprint = fingerprint(Path(module.__file__).resolve())
        if tracker_fingerprint["sha256"] != original_config["tracker_implementation"]["sha256"]:
            raise ValueError("Replay ByteTrack implementation differs from original inference")
        fps, expected = float(manifest["fps"]), int(manifest["frame_count"])
        height, width = manifest["height"], manifest["width"]
        primary = BYTETracker(SimpleNamespace(**TRACKER_SETTINGS))
        high_input = BYTETracker(SimpleNamespace(**TRACKER_SETTINGS))
        primary_diagnostics, high_diagnostics = TrackDiagnostics(), TrackDiagnostics()
        config = {
            **original_config,
            "purpose": "CPU replay of cached Validation detections for prototype vehicle-level counting",
            "started_at_utc": started,
            "operation": "Cached detections + cross-class duplicate suppression + CPU ByteTrack; no YOLO inference",
            "original_inference_config": fingerprint(args.config),
            "input_records": fingerprint(args.records),
            "manifest": fingerprint(args.manifest),
            "script": fingerprint(Path(__file__).resolve()),
            "helper_script": fingerprint(Path(__file__).resolve().with_name("run_video_tracking_pilot.py")),
            "original_inference_versions": original_config["versions"],
            "versions": {"python": sys.version, "ultralytics": ultralytics.__version__,
                         "torch": torch.__version__, "numpy": np.__version__},
            "execution_device": "cpu", "gpu": None,
            "tracker_implementation": tracker_fingerprint,
            "predict_settings_are": "Historical original inference settings; model.predict was not called during replay",
            "duplicate_suppression": {
                "iou_threshold": SUPPRESSION_IOU, "comparison": "strictly greater than",
                "different_class_only": True, "order": "descending confidence, ascending raw index for ties",
                "same_class_pairs": "untouched; original class-aware detector NMS already applied",
                "raw_detections": "retained unchanged in every frame",
                "intent": "Conservative duplicate reduction for vehicle-level counts, not four-class AP tuning",
                "limitation": "Different overlapping real vehicles can still be suppressed; this is not a verified identity decision",
            },
            "primary_input": "Cross-class deduplicated cached detections at >=0.10; original raw indices retained",
            "high_input_reference": "Same deduplicated cached detections then filtered to >=0.35",
        }
        if background_policy_hash is not None:
            config["background_confirmation"] = {
                "policy": fingerprint(args.background_policy),
                "application": "Explicit preselection before cross-class dedup and tracking; raw detections retained",
                "confirmation_records_provenance": "Recorded in each frame and preprocessing config",
                "development_only": True,
            }
            config["operation"] = "Cached 640 detections + camera-specific 1280 background confirmation + cross-class dedup + CPU ByteTrack"
            config["primary_input"] = "Confirmed preselected original detections at >=0.10; original raw indices retained"
            config["high_input_reference"] = "Same preselected and deduplicated detections then filtered to >=0.35"
        if containment_policy is not None:
            config["contained_vehicle_suppression"] = {
                "policy": fingerprint(containment_path), "parameters": containment_policy,
                "implementation": fingerprint(Path(__file__).with_name("vehicle_containment.py")),
                "order": "After original cross-class suppression, before fresh ByteTrack replay",
                "uncertainty": "Unresolved overlapping heavy-vehicle pairs retained; they are candidate anomalies, not ground truth",
                "original_indices_preserved": True,
            }
            config["operation"] += " + optional heavy-vehicle containment suppression before tracking"
            config["primary_input"] = "Final eligible original raw indices after explicitly configured filters, at >=0.10"
            config["high_input_reference"] = "Same final detections then filtered to >=0.35"
        write_json(args.output / "config.json", config)
        start_time = time.perf_counter()
        raw_total = kept_total = removed_total = changed_frames = 0
        background_removed_total = background_changed_frames = 0
        containment_removed_total = containment_changed_frames = 0
        unresolved_pairs_total = unresolved_frames = 0
        raw_empty = saturated_frames = 0
        raw_class_counts, removal_pairs = Counter(), Counter()
        count_different = primary_more = high_more = 0
        update_status(args.output, state="running", processed_frames=0, expected_frames=expected)
        with args.records.open() as source, (args.output / "frames.jsonl").open("x") as target:
            for index, line in enumerate(source):
                original = json.loads(line)
                if index >= expected or original["frame_index"] != index:
                    raise ValueError("Input frame sequence differs from complete manifest sequence")
                if not math.isclose(original["timestamp_seconds"], index / fps, abs_tol=1e-7):
                    raise ValueError("Input clip timestamp mismatch")
                if not math.isclose(original["source_timestamp_seconds"], manifest["clip_start_seconds"] + index / fps, abs_tol=1e-7):
                    raise ValueError("Input source timestamp mismatch")
                raw = original["raw_detections"]
                data = np.asarray([
                    [*item["box_xyxy"], item["confidence"], item["class_id"]] for item in raw
                ], dtype=np.float32).reshape(-1, 6)
                boxes = Boxes(data, (height, width))
                validated = decode_detections(boxes)
                for a, b in zip(raw, validated):
                    if a["class_id"] != b["class_id"] or a["class_name"] != b["class_name"]:
                        raise ValueError("Cached raw class ID/name mismatch")
                    if not math.isclose(a["confidence"], b["confidence"], abs_tol=1e-7):
                        raise ValueError("Unexpected cached raw confidence precision")
                background = original.get("background_confirmation")
                if (background is not None) != (background_policy_hash is not None):
                    raise ValueError("Background preselection requires an explicit matching policy option")
                if background is not None:
                    if background["policy_sha256"] != background_policy_hash:
                        raise ValueError("Background preselection policy hash mismatch")
                    eligible = background["kept_detection_indices"]
                    if (eligible != sorted(set(eligible)) or
                            any(type(i) is not int or i < 0 or i >= len(raw) for i in eligible)):
                        raise ValueError("Invalid background-confirmation preselection")
                    local_kept, removed = suppress_cross_class_duplicates([raw[i] for i in eligible])
                    indices_list = [eligible[i] for i in local_kept]
                    for removal in removed:
                        removal["removed_index"] = eligible[removal["removed_index"]]
                        removal["kept_index"] = eligible[removal["kept_index"]]
                    background_removed_total += len(raw) - len(eligible)
                    background_changed_frames += len(eligible) != len(raw)
                else:
                    indices_list, removed = suppress_cross_class_duplicates(raw)
                cross_class_indices = list(indices_list)
                containment_record = None
                if containment_policy is not None:
                    indices_list, containment_removed, unresolved = suppress_contained_vehicles(
                        raw, cross_class_indices, containment_policy)
                    containment_record = {
                        "policy_sha256": config["contained_vehicle_suppression"]["policy"]["sha256"],
                        "input_detection_indices": cross_class_indices,
                        "kept_detection_indices": indices_list,
                        "removed": containment_removed,
                        "unresolved_pairs": unresolved,
                        "not_ground_truth_duplicates": True,
                    }
                    containment_removed_total += len(containment_removed)
                    containment_changed_frames += bool(containment_removed)
                    unresolved_pairs_total += len(unresolved)
                    unresolved_frames += bool(unresolved)
                indices = np.asarray(indices_list, dtype=np.int64)
                retained = boxes[indices]
                mask = retained.conf >= TRACKER_SETTINGS["track_high_thresh"]
                current = decode_tracks(primary.update(retained), indices, raw)
                reference = decode_tracks(high_input.update(retained[mask]), indices[mask], raw)
                if primary.frame_id != index + 1 or high_input.frame_id != index + 1:
                    raise RuntimeError("Replay trackers must advance on every frame, including empty ones")
                primary_diagnostics.update(index, current)
                high_diagnostics.update(index, reference)
                count_different += len(current) != len(reference)
                primary_more += len(current) > len(reference)
                high_more += len(reference) > len(current)
                raw_total += len(raw)
                kept_total += len(indices_list)
                removed_total += len(removed)
                changed_frames += bool(removed)
                raw_empty += not raw
                saturated_frames += len(raw) == original_config["predict_settings"]["max_det"]
                raw_class_counts.update(item["class_name"] for item in raw)
                removal_pairs.update(f"{r['classes']['removed']} suppressed by {r['classes']['kept']}" for r in removed)
                output_record = {
                    **original, "tracks_primary": current, "tracks_high_input": reference,
                    "duplicate_suppression": {"threshold": SUPPRESSION_IOU,
                                              "kept_detection_indices": cross_class_indices, "removed": removed},
                }
                if containment_record is not None:
                    output_record["contained_vehicle_suppression"] = containment_record
                target.write(json.dumps(output_record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                processed = index + 1
                if processed % 300 == 0:
                    target.flush()
                    update_status(args.output, state="running", processed_frames=processed, expected_frames=expected,
                                  elapsed_seconds=time.perf_counter() - start_time)
                    print(f"Replayed {processed}/{expected} cached Validation frames", flush=True)
        if processed != expected:
            raise ValueError(f"Input ended after {processed} frames, expected {expected}")
        if raw_total != kept_total + removed_total + background_removed_total + containment_removed_total:
            raise RuntimeError("Preselection and dedup observation accounting do not balance")
        elapsed = time.perf_counter() - start_time
        summary = {
            "state": "complete", "started_at_utc": started, "finished_at_utc": now(),
            "operation": config["operation"], "frame_count": processed, "fps": fps,
            "duration_seconds": processed / fps, "raw_detector_observations": raw_total,
            "frames_without_raw_detections": raw_empty, "frames_at_max_det_limit": saturated_frames,
            "raw_observations_by_class": {name: raw_class_counts[name] for name in CLASS_NAMES.values()},
            "suppression": {"raw_observations_kept": kept_total, "raw_observations_removed": removed_total,
                            "frames_with_removed_observations": changed_frames, "pairs": dict(removal_pairs)},
            "background_preselection": {"raw_observations_removed": background_removed_total,
                                        "frames_with_removed_observations": background_changed_frames,
                                        "not_confirmed_false_positive_count": True},
            "contained_vehicle_suppression": {
                "enabled": containment_policy is not None,
                "raw_observations_removed": containment_removed_total,
                "frames_with_removed_observations": containment_changed_frames,
                "unresolved_pair_observations": unresolved_pairs_total,
                "frames_with_unresolved_pairs": unresolved_frames,
                "not_unique_vehicles_or_verified_errors": True,
            },
            "primary": primary_diagnostics.summary(fps), "high_input_reference": high_diagnostics.summary(fps),
            "arms_comparison_diagnostic_only": {
                "frames_with_different_observation_counts": count_different,
                "frames_primary_has_more_observations": primary_more,
                "frames_high_input_has_more_observations": high_more,
                "not_a_tracking_accuracy_comparison": True,
            },
            "elapsed_seconds": elapsed, "frames_file": fingerprint(args.output / "frames.jsonl"),
            "limitations": ["Vehicle-level duplicate suppression can remove distinct heavily overlapping vehicles.",
                            "Original four-class detections are unchanged; no AP improvement is claimed.",
                            "Track IDs and observation counts are diagnostics, not flow or accuracy metrics."],
        }
        write_json(args.output / "summary.json", summary)
        update_status(args.output, state="complete", processed_frames=processed, expected_frames=expected,
                      elapsed_seconds=elapsed, exit_code=0)
        print(f"Completed CPU replay: {args.output}", flush=True)
        return summary
    except Exception as exc:
        update_status(args.output, state="failed", processed_frames=processed,
                      error_type=type(exc).__name__, error=str(exc), exit_code=1)
        raise


if __name__ == "__main__":
    run(parse_args())
