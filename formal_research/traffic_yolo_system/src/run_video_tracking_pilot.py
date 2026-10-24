"""Run a frozen Validation clip through YOLO11s and two ByteTrack inputs.

This development pilot saves every decoded frame, including empty frames. The
primary tracker receives low-score detections; the comparison tracker receives
only high-score detections. These are continuity diagnostics, not tracking
accuracy, traffic flow, or congestion measurements. No training is performed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
EXPECTED_MODEL_SHA = "07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334"
FROZEN_SPLIT_SHA = "8e1b8806f5a1f2f8280b71169d45ba5837bc710db26f0550610124fcc4f1f3cd"
ORIGINAL_PILOT_SOURCE = "Bellevue_150th_Newport__2017-09-11_08-08-31.mp4"
TRACKER_SETTINGS = {
    "tracker_type": "bytetrack",
    "track_high_thresh": 0.35,
    "track_low_thresh": 0.10,
    "new_track_thresh": 0.35,
    "track_buffer": 30,
    "match_thresh": 0.8,
    "fuse_score": True,
}
PREDICT_SETTINGS = {
    "conf": 0.10,
    "imgsz": 640,
    "iou": 0.7,
    "half": False,
    "agnostic_nms": False,
    "max_det": 300,
    "augment": False,
    "rect": True,
    "batch": 1,
    "classes": None,
    "save": False,
    "verbose": False,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def update_status(output, **values):
    temporary = output / "status.tmp"
    temporary.write_text(json.dumps({"updated_at_utc": now(), **values}, indent=2) + "\n")
    temporary.replace(output / "status.json")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New results directory; never overwritten")
    parser.add_argument("--device", default="0", choices=["0"])
    parser.add_argument("--imgsz", type=int, default=640, choices=[640, 960, 1280],
                        help="Explicit Validation comparison size; other detector settings stay fixed")
    parser.add_argument("--expected-model-sha", default=EXPECTED_MODEL_SHA)
    args = parser.parse_args(argv)
    for key in ("source", "model", "manifest", "output"):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    if len(args.expected_model_sha) != 64 or any(c not in "0123456789abcdef" for c in args.expected_model_sha):
        parser.error("--expected-model-sha must be 64 lowercase hexadecimal characters")
    return args


def validate_manifest(manifest, source_fingerprint):
    split = manifest.get("source_split", {})
    if not isinstance(split, dict) or split.get("Formal Split") != "Validation":
        raise ValueError("Only a source explicitly assigned to Validation is permitted")
    if manifest.get("clip_sha256") != source_fingerprint["sha256"]:
        raise ValueError("Clip SHA256 does not match the Validation clip manifest")
    if (manifest.get("frame_count"), manifest.get("width"), manifest.get("height")) != (3600, 1280, 720):
        raise ValueError("This pilot requires the approved 3600-frame, 1280 x 720 clip")
    if not math.isclose(float(manifest["fps"]), 30.0, abs_tol=1e-9):
        raise ValueError("This pilot requires the approved constant 30 FPS clip")
    if "transfer_scope" not in manifest:
        # Preserve the original pilot entry point. New clips require the explicit,
        # independently frozen split evidence below; scope never broadens silently.
        if (manifest.get("clip_start_seconds"), manifest.get("clip_end_seconds")) != (1600, 1720):
            raise ValueError("This pilot requires the approved 1600-1720 second Validation segment")
        if split.get("Source Video Filename") != ORIGINAL_PILOT_SOURCE:
            raise ValueError("Unexpected Validation source video")
        return

    scope = manifest["transfer_scope"]
    if not isinstance(scope, dict) or type(scope.get("schema_version")) is not int or scope["schema_version"] != 1:
        raise ValueError("Transfer scope requires schema_version 1")
    if scope.get("kind") != "fixed_rules_newport_validation_transfer":
        raise ValueError("Unexpected Validation transfer scope")
    if scope.get("selected_before_inference") is not True or scope.get("no_rule_tuning") is not True:
        raise ValueError("Transfer requires preselection and unchanged processing rules")
    csv_text = scope.get("split_manifest_csv_text")
    if not isinstance(csv_text, str) or scope.get("split_manifest_sha256") != FROZEN_SPLIT_SHA:
        raise ValueError("Transfer requires the frozen split manifest and its approved SHA256")
    if hashlib.sha256(csv_text.encode("utf-8")).hexdigest() != FROZEN_SPLIT_SHA:
        raise ValueError("Embedded split manifest SHA256 does not match the frozen split")
    rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"), newline="")))
    filename = split.get("Source Video Filename")
    matching = [row for row in rows if row.get("Source Video Filename") == filename]
    if len(matching) != 1 or matching[0] != split:
        raise ValueError("Source split must exactly match one row of the frozen split manifest")
    if split.get("Approval Status") != "Approved and Frozen" or split.get("Intersection") != "Bellevue_150th_Newport":
        raise ValueError("Transfer is limited to approved Newport Validation sources")
    source_video = manifest.get("source_video")
    if not isinstance(source_video, str) or Path(source_video).name != filename:
        raise ValueError("Source video path must identify the frozen source filename")

    def finite_number(value):
        return type(value) in (int, float) and math.isfinite(value)

    start, end = manifest.get("clip_start_seconds"), manifest.get("clip_end_seconds")
    if not finite_number(start) or not finite_number(end) or start < 0 or end - start != 120:
        raise ValueError("Transfer clip must have finite nonnegative times and last exactly 120 seconds")
    if not finite_number(manifest["fps"]) or manifest["fps"] != 30:
        raise ValueError("Transfer clip must declare numeric constant 30 FPS")
    duration_parts = split["Duration"].split(":")
    source_duration = sum(int(part) * multiplier for part, multiplier in zip(duration_parts, (3600, 60, 1)))
    if len(duration_parts) != 3 or end > source_duration:
        raise ValueError("Transfer clip extends beyond the frozen source duration")
    if filename == ORIGINAL_PILOT_SOURCE and start < 1720 and end > 1600:
        raise ValueError("Transfer clip cannot overlap the original 1600-1720 second pilot")


def decode_detections(boxes):
    """Keep raw detections before ByteTrack replaces boxes with Kalman estimates."""
    detections = []
    for values in boxes.data.tolist():
        if len(values) != 6 or not all(math.isfinite(float(v)) for v in values):
            raise ValueError("Expected finite six-column detection boxes")
        x1, y1, x2, y2, score, class_value = map(float, values)
        class_id = int(class_value)
        if class_value != class_id or class_id not in CLASS_NAMES:
            raise ValueError(f"Unexpected model class {class_value}")
        if not (x2 > x1 and y2 > y1 and 0.10 - 1e-7 <= score <= 1):
            raise ValueError("Invalid box geometry or detector score")
        detections.append({
            "box_xyxy": [x1, y1, x2, y2], "class_id": class_id,
            "class_name": CLASS_NAMES[class_id], "confidence": score,
        })
    return detections


def decode_tracks(tracks, original_indices, raw_detections):
    """Map track-local detection indices back to the frame's raw detection list."""
    output = []
    seen = set()
    for values in tracks:
        values = [float(v) for v in values]
        if len(values) != 8 or not all(math.isfinite(v) for v in values):
            raise ValueError("ByteTrack output must be xyxy, ID, score, class, detection index")
        x1, y1, x2, y2, id_value, score, class_value, local_index_value = values
        track_id, class_id, local_index = int(id_value), int(class_value), int(local_index_value)
        if id_value != track_id or track_id <= 0 or track_id in seen:
            raise ValueError("Invalid or duplicate track ID within a frame")
        if class_value != class_id or class_id not in CLASS_NAMES:
            raise ValueError("Unexpected tracked class")
        if local_index != local_index_value or not 0 <= local_index < len(original_indices):
            raise ValueError("Invalid tracker detection index")
        detection_index = int(original_indices[local_index])
        raw = raw_detections[detection_index]
        if raw["class_id"] != class_id or not math.isclose(raw["confidence"], score, abs_tol=1e-6):
            raise ValueError("Track observation does not correspond to its raw detection")
        if not (x2 > x1 and y2 > y1):
            raise ValueError("Invalid tracked box geometry")
        seen.add(track_id)
        output.append({
            "box_xyxy": [x1, y1, x2, y2], "track_id": track_id,
            "class_id": class_id, "class_name": CLASS_NAMES[class_id],
            "confidence": score, "detection_index": detection_index,
        })
    return output


class TrackDiagnostics:
    """Describe observed track fragments without assuming a real-world identity."""

    def __init__(self):
        self.tracks = {}
        self.frame_count = 0
        self.empty_frames = 0
        self.observation_count = 0
        self.low_confidence_observations = 0
        self.frame_max = 0
        self.classes = Counter()

    def update(self, frame_index, observations):
        if frame_index != self.frame_count:
            raise ValueError("Diagnostic frame sequence must include every frame exactly once")
        self.frame_count += 1
        self.empty_frames += not observations
        self.observation_count += len(observations)
        self.frame_max = max(self.frame_max, len(observations))
        for observation in observations:
            identity = observation["track_id"]
            category = observation["class_name"]
            self.classes[category] += 1
            self.low_confidence_observations += observation["confidence"] < 0.35 - 1e-7
            if identity not in self.tracks:
                self.tracks[identity] = {
                    "track_id": identity, "first_frame": frame_index, "last_frame": frame_index,
                    "observations": 0, "gap_events": 0, "unobserved_frames_between_observations": 0,
                    "maximum_gap_frames": 0, "class_label_changes_between_observations": 0,
                    "last_class_name": category, "class_observations": Counter(),
                }
            track = self.tracks[identity]
            if track["observations"]:
                gap = frame_index - track["last_frame"] - 1
                if gap < 0:
                    raise ValueError("Track observed more than once within a frame")
                track["gap_events"] += gap > 0
                track["unobserved_frames_between_observations"] += gap
                track["maximum_gap_frames"] = max(track["maximum_gap_frames"], gap)
                track["class_label_changes_between_observations"] += category != track["last_class_name"]
            track["last_frame"] = frame_index
            track["last_class_name"] = category
            track["observations"] += 1
            track["class_observations"][category] += 1

    def summary(self, fps):
        tracks = []
        for _, values in sorted(self.tracks.items()):
            row = dict(values)
            row["class_observations"] = dict(sorted(values["class_observations"].items()))
            row["observed_span_frames"] = row["last_frame"] - row["first_frame"] + 1
            row["observed_span_seconds"] = row["observed_span_frames"] / fps
            row["first_timestamp_seconds"] = row["first_frame"] / fps
            row["last_timestamp_seconds"] = row["last_frame"] / fps
            tracks.append(row)
        return {
            "decoded_frames": self.frame_count,
            "frames_without_active_observation": self.empty_frames,
            "track_observations": self.observation_count,
            "observations_below_0_35": self.low_confidence_observations,
            "average_active_observations_per_frame": self.observation_count / self.frame_count if self.frame_count else 0,
            "maximum_active_observations_in_frame": self.frame_max,
            "distinct_track_ids_not_unique_vehicles": len(tracks),
            "tracks_with_observation_gaps": sum(row["gap_events"] > 0 for row in tracks),
            "observation_gap_events": sum(row["gap_events"] for row in tracks),
            "tracks_with_class_label_changes": sum(row["class_label_changes_between_observations"] > 0 for row in tracks),
            "class_label_changes_between_observations": sum(row["class_label_changes_between_observations"] for row in tracks),
            "observation_counts_by_class": {name: self.classes[name] for name in CLASS_NAMES.values()},
            "tracks": tracks,
        }


def run(args):
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing results: {args.output}")
    # Validate existing paths before model construction to prevent weight downloads.
    for path in (args.source, args.model, args.manifest):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output.mkdir(parents=True, exist_ok=False)
    started_at = now()
    cap = None
    processed = 0
    try:
        update_status(args.output, state="validating", processed_frames=0)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        source_hash, model_hash = fingerprint(args.source), fingerprint(args.model)
        validate_manifest(manifest, source_hash)
        if args.model.suffix != ".pt" or model_hash["sha256"] != args.expected_model_sha:
            raise ValueError("Model checkpoint SHA256 mismatch")

        os.environ["YOLO_AUTOINSTALL"] = "false"
        import cv2
        import numpy as np
        import torch
        import ultralytics
        from ultralytics import YOLO
        from ultralytics.trackers.byte_tracker import BYTETracker

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU required; CPU fallback is not permitted")
        cap = cv2.VideoCapture(str(args.source))
        if not cap.isOpened():
            raise RuntimeError("Unable to open the Validation clip")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if (width, height, declared_frames) != (manifest["width"], manifest["height"], manifest["frame_count"]):
            raise ValueError("Video dimensions/frame count do not match manifest")
        if not math.isclose(fps, float(manifest["fps"]), abs_tol=1e-6):
            raise ValueError("Video FPS does not match manifest")
        model = YOLO(str(args.model))
        if model.task != "detect" or dict(model.names) != CLASS_NAMES:
            raise ValueError(f"Expected formal four-class detection model, got {model.names}")
        primary = BYTETracker(SimpleNamespace(**TRACKER_SETTINGS))
        high_input = BYTETracker(SimpleNamespace(**TRACKER_SETTINGS))
        primary_diagnostics, high_diagnostics = TrackDiagnostics(), TrackDiagnostics()
        tracker_module = importlib.import_module(BYTETracker.__module__)
        tracker_source = Path(tracker_module.__file__).resolve()
        configuration = {
            "purpose": "Validation video backend pilot; no Main Test, training, flow, or congestion evaluation",
            "started_at_utc": started_at,
            "source": source_hash, "model": model_hash, "manifest": fingerprint(args.manifest),
            "script": fingerprint(Path(__file__).resolve()),
            "source_split": manifest["source_split"],
            "clip_start_seconds": manifest["clip_start_seconds"],
            "clip_end_seconds": manifest["clip_end_seconds"],
            "fps": fps, "frame_count": declared_frames, "width": width, "height": height,
            "time_semantics": f"CFR clip frame_index / fps; source time is clip time + {manifest['clip_start_seconds']} seconds",
            "class_names": CLASS_NAMES, "predict_settings": {**PREDICT_SETTINGS, "imgsz": args.imgsz, "device": args.device},
            "tracker_settings_both_arms": TRACKER_SETTINGS,
            "tracker_parameters_are": "Explicit pilot settings, not claimed defaults or an optimum",
            "primary_input": "All detector outputs at confidence >= 0.10; low-score association retained",
            "high_input_reference": "Same detections filtered to confidence >= 0.35 before tracking",
            "comparison_limit": "Filters one common NMS output; not two independent detector threshold runs",
            "track_output_semantics": "Current active observations only; lost Kalman-only predictions are not counted",
            "track_box_semantics": "ByteTrack Kalman-updated box; detection_index links raw detector box",
            "class_semantics": "ByteTrack matching is not class-locked; observation class may change",
            "id_semantics": "IDs are local diagnostics in each arm, not matched across arms or unique real vehicles",
            "no_ground_truth_tracking_metrics": ["IDF1", "MOTA", "ID switches", "fragmentation accuracy"],
            "versions": {"python": sys.version, "ultralytics": ultralytics.__version__,
                         "torch": torch.__version__, "cuda": torch.version.cuda, "opencv": cv2.__version__},
            "gpu": torch.cuda.get_device_name(0), "tracker_implementation": fingerprint(tracker_source),
        }
        write_json(args.output / "config.json", configuration)
        raw_count, raw_empty, saturated_frames = 0, 0, 0
        raw_classes = Counter()
        differences, primary_more, high_more = 0, 0, 0
        start = time.perf_counter()
        update_status(args.output, state="running", processed_frames=0, expected_frames=declared_frames)
        with (args.output / "frames.jsonl").open("x", encoding="utf-8") as handle:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if processed >= declared_frames or frame.shape[:2] != (height, width):
                    raise ValueError("Unexpected extra frame or frame dimensions")
                result = model.predict(frame, device=args.device,
                                       **{**PREDICT_SETTINGS, "imgsz": args.imgsz})[0]
                boxes = result.boxes.cpu().numpy()
                raw = decode_detections(boxes)
                indices = np.arange(len(raw), dtype=np.int64)
                high_mask = boxes.conf >= TRACKER_SETTINGS["track_high_thresh"]
                # Both trackers advance even on frames with no detections.
                primary_tracks = decode_tracks(primary.update(boxes, frame), indices, raw)
                reference_tracks = decode_tracks(high_input.update(boxes[high_mask], frame), indices[high_mask], raw)
                if primary.frame_id != processed + 1 or high_input.frame_id != processed + 1:
                    raise RuntimeError("Tracker did not advance exactly once for each decoded frame")
                primary_diagnostics.update(processed, primary_tracks)
                high_diagnostics.update(processed, reference_tracks)
                n_primary, n_high = len(primary_tracks), len(reference_tracks)
                differences += n_primary != n_high
                primary_more += n_primary > n_high
                high_more += n_high > n_primary
                raw_count += len(raw)
                raw_empty += not raw
                saturated_frames += len(raw) == PREDICT_SETTINGS["max_det"]
                raw_classes.update(item["class_name"] for item in raw)
                source_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                record = {
                    "frame_index": processed, "timestamp_seconds": processed / fps,
                    "source_timestamp_seconds": manifest["clip_start_seconds"] + processed / fps,
                    "decoder_position_msec": source_msec if math.isfinite(source_msec) else None,
                    "raw_detections": raw, "tracks_primary": primary_tracks,
                    "tracks_high_input": reference_tracks,
                }
                handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
                processed += 1
                if processed % 300 == 0:
                    handle.flush()
                    update_status(args.output, state="running", processed_frames=processed,
                                  expected_frames=declared_frames, elapsed_seconds=time.perf_counter() - start)
                    print(f"Processed {processed}/{declared_frames} Validation frames", flush=True)
        if processed != declared_frames:
            raise RuntimeError(f"Decoded {processed} frames, expected {declared_frames}; possible early decoder failure")
        elapsed = time.perf_counter() - start
        summary = {
            "state": "complete", "started_at_utc": started_at, "finished_at_utc": now(),
            "frame_count": processed, "fps": fps, "duration_seconds": processed / fps,
            "raw_detector_observations": raw_count, "frames_without_raw_detections": raw_empty,
            "frames_at_max_det_limit": saturated_frames,
            "raw_observations_by_class": {name: raw_classes[name] for name in CLASS_NAMES.values()},
            "primary": primary_diagnostics.summary(fps), "high_input_reference": high_diagnostics.summary(fps),
            "arms_comparison_diagnostic_only": {
                "frames_with_different_observation_counts": differences,
                "frames_primary_has_more_observations": primary_more,
                "frames_high_input_has_more_observations": high_more,
                "not_a_tracking_accuracy_comparison": True,
            },
            "elapsed_seconds_including_model_first_prediction": elapsed,
            "processing_frames_per_second_not_a_controlled_benchmark": processed / elapsed,
            "frames_file": fingerprint(args.output / "frames.jsonl"),
        }
        write_json(args.output / "summary.json", summary)
        update_status(args.output, state="complete", processed_frames=processed, expected_frames=declared_frames,
                      elapsed_seconds=elapsed, exit_code=0)
        print(f"Completed Validation tracking pilot: {args.output}", flush=True)
        return summary
    except Exception as exc:
        update_status(args.output, state="failed", processed_frames=processed,
                      error_type=type(exc).__name__, error=str(exc), exit_code=1)
        raise
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    run(parse_args())
