"""Audit a camera-specific background candidate with a second cached resolution.

This development-only preselector never performs inference or tracking. Raw
640 detections and any historical tracks remain unchanged. Only detections whose
box centres lie strictly inside an explicitly reviewed physical-background
polygon need class-agnostic confirmation from cached 1280 detections. Boundary
points are conservatively left untouched. Replaying the tracker is a separate
required step; old tracks in this intermediate file are not corrected results.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from itertools import zip_longest

from roi_geometry import _float64_point_position, validate_layout
from run_video_tracking_pilot import (
    CLASS_NAMES, EXPECTED_MODEL_SHA, fingerprint, now, update_status,
    validate_manifest, write_json,
)


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def validate_policy(policy):
    """Validate explicit scope and geometry; return its float64 polygon.

    No policy, polygon, or confidence fallback is supplied. The clip binding is
    enforced by the CLI; callers using the pure function for another diagnostic
    dataset must record that separate scope themselves.
    """
    if not isinstance(policy, dict) or type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1:
        raise ValueError("An explicit schema_version 1 background policy is required")
    if policy.get("development_only") is not True:
        raise ValueError("Policy must explicitly declare development_only=true")
    if not isinstance(policy.get("camera_id"), str) or not policy["camera_id"].strip():
        raise ValueError("Policy camera_id must be a nonempty string")
    if not re.fullmatch(r"[0-9a-f]{64}", str(policy.get("source_clip_sha256", ""))):
        raise ValueError("Policy must bind an explicit source_clip_sha256")
    for field in ("frame_width", "frame_height"):
        if type(policy.get(field)) is not int or policy[field] <= 0:
            raise ValueError(f"Policy {field} must be a positive integer")
    for field in ("confirmation_iou", "confirm_min_confidence"):
        value = _finite_number(policy.get(field), field)
        if not 0 < value <= 1:
            raise ValueError(f"Policy {field} must be in (0, 1]")
    polygon = policy.get("reviewed_background_polygon_xy")
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError("reviewed_background_polygon_xy must be an explicit list of at least three points")
    for point in polygon:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Background polygon vertices must have two coordinates")
        for coordinate in point:
            _finite_number(coordinate, "Background polygon coordinate")
    # Reuse the established float64 simple-polygon validator without a raster mask.
    layout = {
        "assignment_point": "bottom_center", "boundary_tie_break": "zone_order",
        "frame_width": policy["frame_width"], "frame_height": policy["frame_height"],
        "zones": [{"zone_name": "reviewed_background", "polygon_points": polygon}],
    }
    try:
        return validate_layout(layout)[0][1]
    except (TypeError, KeyError, IndexError) as exc:
        raise ValueError("Malformed reviewed_background_polygon_xy") from exc


def _validate_detections(detections, label, width=None, height=None):
    if not isinstance(detections, list):
        raise ValueError(f"{label} detections must be a list")
    for index, detection in enumerate(detections):
        if not isinstance(detection, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        box = detection.get("box_xyxy")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(f"{label}[{index}] needs a four-coordinate box")
        x1, y1, x2, y2 = [_finite_number(v, f"{label}[{index}] box") for v in box]
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"{label}[{index}] box must have positive area")
        if width is not None and not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError(f"{label}[{index}] raw detector box is outside the source frame")
        score = _finite_number(detection.get("confidence"), f"{label}[{index}] confidence")
        if not 0 <= score <= 1:
            raise ValueError(f"{label}[{index}] confidence must be in [0, 1]")
        if type(detection.get("class_id")) is not int or detection["class_id"] < 0:
            raise ValueError(f"{label}[{index}] class_id must be a nonnegative integer")
        if not isinstance(detection.get("class_name"), str) or not detection["class_name"]:
            raise ValueError(f"{label}[{index}] class_name must be nonempty")


def _iou(a, b):
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union


def candidate_confirmation(raw, confirmation, policy):
    """Return original kept indices and audit records for strictly inside candidates.

    A candidate is kept if ANY eligible 1280 box reaches the inclusive IoU
    threshold, regardless of class, speed, history, track ID, or time. This is
    independent many-to-many support, not an identity association. Every matching
    index is retained in the audit so ambiguous support is visible.
    """
    polygon = validate_policy(policy)
    _validate_detections(raw, "raw", policy["frame_width"], policy["frame_height"])
    _validate_detections(confirmation, "confirmation", policy["frame_width"], policy["frame_height"])
    kept, records = [], []
    minimum = policy["confirm_min_confidence"]
    threshold = policy["confirmation_iou"]
    eligible = [i for i, d in enumerate(confirmation) if d["confidence"] >= minimum]
    for index, detection in enumerate(raw):
        box = detection["box_xyxy"]
        centre = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        if _float64_point_position(centre, polygon) != 1:
            kept.append(index)
            continue
        overlaps = [_iou(box, d["box_xyxy"]) for d in confirmation]
        best_all = max(range(len(confirmation)), key=lambda j: overlaps[j]) if confirmation else None
        best = max(eligible, key=lambda j: overlaps[j]) if eligible else None
        matches = [j for j in eligible if overlaps[j] >= threshold]
        retain = bool(matches)
        if retain:
            kept.append(index)
        records.append({
            "raw_index": index, "candidate_point": centre, "point_semantics": "box_center_strictly_inside",
            "raw_detection": detection.copy(), "kept": retain,
            "reason": "confirmed_at_second_resolution" if retain else "unconfirmed_background_candidate",
            "max_iou": overlaps[best] if best is not None else 0.0,
            "best_confirmation_index": best,
            "best_confirmation_detection": confirmation[best].copy() if best is not None else None,
            "eligible_confirmation_count": len(eligible), "eligible_matching_indices": matches,
            "multiple_matching_detections": len(matches) > 1,
            "all_confirmation_max_iou": overlaps[best_all] if best_all is not None else 0.0,
            "all_confirmation_best_index": best_all,
        })
    return kept, records


def validate_input_configs(original, confirmation, manifest, policy, manifest_sha256):
    """Fail closed on a wrong clip, checkpoint, split, run shape, or inference setup."""
    validate_policy(policy)
    for name, config in (("original", original), ("confirmation", confirmation)):
        validate_manifest(manifest, config["source"])
        if config["source"]["sha256"] != policy["source_clip_sha256"]:
            raise ValueError(f"{name} source differs from policy clip binding")
        if config["model"]["sha256"] != EXPECTED_MODEL_SHA:
            raise ValueError(f"{name} model differs from frozen YOLO11s checkpoint")
        if config["source_split"] != manifest["source_split"]:
            raise ValueError(f"{name} source split differs from Validation manifest")
        if config["manifest"]["sha256"] != manifest_sha256:
            raise ValueError(f"{name} manifest hash mismatch")
        for field in ("frame_count", "width", "height", "clip_start_seconds", "clip_end_seconds"):
            if config[field] != manifest[field]:
                raise ValueError(f"{name} {field} differs from manifest")
        if not math.isclose(float(config["fps"]), float(manifest["fps"]), rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"{name} FPS differs from manifest")
        if {int(k): v for k, v in config["class_names"].items()} != CLASS_NAMES:
            raise ValueError(f"{name} class mapping differs from frozen model")
    if (policy["frame_width"], policy["frame_height"]) != (manifest["width"], manifest["height"]):
        raise ValueError("Policy dimensions differ from clip dimensions")
    if "source_video_filename" in policy and policy["source_video_filename"] != manifest["source_split"]["Source Video Filename"]:
        raise ValueError("Policy source video filename differs from manifest")
    a, b = original["predict_settings"], confirmation["predict_settings"]
    if a.get("imgsz") != 640 or b.get("imgsz") != 1280:
        raise ValueError("This preselector requires explicit original 640 and confirmation 1280 runs")
    exclude = {"imgsz", "device"}
    if {k: v for k, v in a.items() if k not in exclude} != {k: v for k, v in b.items() if k not in exclude}:
        raise ValueError("Prediction settings may differ only in imgsz and device")
    if a.get("conf") != 0.10 or a.get("agnostic_nms") is not False or a.get("half") is not False:
        raise ValueError("Expected original confidence 0.10, class-aware NMS, and fp32 inference")
    if policy["confirm_min_confidence"] < b["conf"]:
        raise ValueError("Confirmation policy threshold cannot be below cached detector threshold")
    for field in ("ultralytics", "torch"):
        if not original.get("versions", {}).get(field) or original["versions"][field] != confirmation.get("versions", {}).get(field):
            raise ValueError(f"Inference {field} versions must match exactly")


def _validate_frame(record, index, manifest, label):
    if type(record.get("frame_index")) is not int or record["frame_index"] != index:
        raise ValueError(f"{label} frame sequence mismatch at {index}")
    clip_time = index / manifest["fps"]
    for field, expected in (("timestamp_seconds", clip_time),
                            ("source_timestamp_seconds", manifest["clip_start_seconds"] + clip_time)):
        value = _finite_number(record.get(field), f"{label} {field}")
        if not math.isclose(value, expected, rel_tol=0, abs_tol=1e-7):
            raise ValueError(f"{label} {field} mismatch at frame {index}")
    _validate_detections(record.get("raw_detections"), f"{label} raw")
    for detection in record["raw_detections"]:
        if CLASS_NAMES.get(detection["class_id"]) != detection["class_name"]:
            raise ValueError(f"{label} raw class mapping mismatch")
        if detection["confidence"] < 0.10 - 1e-7:
            raise ValueError(f"{label} raw confidence is below its inference threshold")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("records", "config", "confirmation-records", "confirmation-config", "manifest", "policy", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    for key, value in vars(args).items():
        setattr(args, key, value.expanduser().resolve())
    return args


def run(args):
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite preselection output: {args.output}")
    for name in ("records", "config", "confirmation_records", "confirmation_config", "manifest", "policy"):
        if not getattr(args, name).is_file():
            raise FileNotFoundError(getattr(args, name))
    original = json.loads(args.config.read_text())
    confirmation = json.loads(args.confirmation_config.read_text())
    manifest = json.loads(args.manifest.read_text())
    policy = json.loads(args.policy.read_text())
    manifest_fp, policy_fp = fingerprint(args.manifest), fingerprint(args.policy)
    validate_input_configs(original, confirmation, manifest, policy, manifest_fp["sha256"])
    expected, processed = manifest["frame_count"], 0
    args.output.mkdir(parents=True, exist_ok=False)
    started = now()
    try:
        config = {
            **original, "purpose": "Development-only cached background confirmation preselection; tracker replay required",
            "started_at_utc": started, "stage": "background_confirmation_preselection",
            "historical_tracks": "Preserved unchanged from original input; not corrected results. Replay before counting.",
            "original_inference_config": fingerprint(args.config), "input_records": fingerprint(args.records),
            "confirmation_inference_config": fingerprint(args.confirmation_config),
            "confirmation_records": fingerprint(args.confirmation_records), "manifest": manifest_fp,
            "script": fingerprint(Path(__file__).resolve()),
            "geometry_script": fingerprint(Path(__file__).with_name("roi_geometry.py")),
            "background_confirmation": {
                "policy": policy_fp, "policy_value": policy,
                "candidate_rule": "640 box centre strictly inside reviewed physical-background polygon; boundary untouched",
                "confirmation_rule": "Any 1280 box with score >= confirm_min_confidence and class-agnostic IoU >= confirmation_iou",
                "raw_detections": "Original 640 list unchanged, indices retain their original meanings",
                "class_speed_history_rules": "None; stationary vehicles and any class receive the same geometric confirmation rule",
                "limitation": "Second-resolution failure can reject a real object; this is not ground truth or a general camera policy",
            },
            "confirmation_inference_versions": confirmation["versions"],
        }
        write_json(args.output / "config.json", config)
        write_json(args.output / "policy.json", policy)
        totals = Counter()
        update_status(args.output, state="running", processed_frames=0, expected_frames=expected)
        with args.records.open() as base, args.confirmation_records.open() as high, (args.output / "frames.jsonl").open("x") as target:
            for index, pair in enumerate(zip_longest(base, high)):
                left, right = pair
                if left is None or right is None:
                    raise ValueError("Original and confirmation record counts differ")
                if index >= expected:
                    raise ValueError("Input has more frames than complete manifest sequence")
                rec, ref = json.loads(left), json.loads(right)
                _validate_frame(rec, index, manifest, "original")
                _validate_frame(ref, index, manifest, "confirmation")
                if "background_confirmation" in rec:
                    raise ValueError("Refusing to apply confirmation twice to an already processed record")
                kept, candidates = candidate_confirmation(rec["raw_detections"], ref["raw_detections"], policy)
                removed = [item for item in candidates if not item["kept"]]
                output = {**rec, "background_confirmation": {
                    "policy_sha256": policy_fp["sha256"], "kept_detection_indices": kept,
                    "candidate_records": candidates, "removed": removed,
                }}
                target.write(json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                totals.update(raw_observations=len(rec["raw_detections"]), kept_observations=len(kept),
                              candidate_observations=len(candidates), removed_observations=len(removed),
                              frames_with_candidates=bool(candidates), frames_with_removals=bool(removed))
                processed = index + 1
                if processed % 300 == 0:
                    target.flush()
                    update_status(args.output, state="running", processed_frames=processed, expected_frames=expected)
        if processed != expected:
            raise ValueError(f"Input ended after {processed} frames, expected {expected}")
        summary = {
            "state": "complete", "started_at_utc": started, "finished_at_utc": now(),
            "frame_count": processed, "policy_sha256": policy_fp["sha256"], **dict(totals),
            "tracking_replay_required": True, "not_accuracy_metrics": True,
            "frames_file": fingerprint(args.output / "frames.jsonl"),
        }
        write_json(args.output / "summary.json", summary)
        update_status(args.output, state="complete", processed_frames=processed, expected_frames=expected, exit_code=0)
        print(f"Completed background preselection: {args.output}", flush=True)
        return summary
    except Exception as exc:
        update_status(args.output, state="failed", processed_frames=processed, expected_frames=expected,
                      error_type=type(exc).__name__, error=str(exc), exit_code=1)
        raise


if __name__ == "__main__":
    run(parse_args())
