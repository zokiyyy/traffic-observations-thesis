"""Strict, bounded-memory development-video input for the full-video pipeline.

No inference is performed by preflight. It hashes local inputs and audits every
input frame PTS using ffprobe. CFR is checked strictly. Full-source irregular
PTS require a separately verified PTS/decode sidecar, never an assumed FPS.
Only unrotated single-video-stream inputs are accepted. The model is imported
only when ``iter_video`` is consumed. No package/model downloads are allowed.

Time contract: source_offset_seconds names the original-source time of the
input file's first presented frame. Container PTS are normalized by that first
PTS, which is retained in provenance. Segments use original-source seconds and
must align with CFR frame boundaries. Track IDs restart for every video/retry.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace


CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
DETECTOR_FIELDS = {
    "conf", "imgsz", "iou", "half", "agnostic_nms", "max_det", "augment",
    "rect", "batch", "classes",
}
TRACKER_FIELDS = {
    "tracker_type", "track_high_thresh", "track_low_thresh", "new_track_thresh",
    "track_buffer", "match_thresh", "fuse_score",
}


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _fraction(value, label):
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{label} must be a positive rational, e.g. 30000/1001") from exc
    if result <= 0:
        raise ValueError(f"{label} must be a positive rational")
    return result


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _stat(path):
    stat = Path(path).stat()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _configuration_hash(config, camera):
    return hashlib.sha256(json.dumps({"config": config, "camera": camera}, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _validate_scope(config, task, camera):
    # This guard deliberately precedes any input/video/model file access.
    if task.get("split") not in {"Train", "Validation"}:
        raise ValueError("Only Train/Validation sources are permitted; Main Test cannot be opened")
    if config.get("input", {}).get("kind") != "video":
        raise ValueError("The live backend requires input.kind=video")
    if not isinstance(task.get("source_id"), str) or not task["source_id"] or task.get("camera_id") != camera.get("camera_id"):
        raise ValueError("Task source_id/camera_id must match its explicit camera configuration")
    if not isinstance(camera.get("source_ids"), list) or task["source_id"] not in camera["source_ids"]:
        raise ValueError("Source is not explicitly listed in camera.source_ids; camera geometry cannot be extrapolated")
    background = camera.get("background_filter")
    if not isinstance(background, dict) or background.get("mode") not in {"none", "cached_policy_only", "live_second_resolution"}:
        raise ValueError("camera.background_filter.mode must be explicit: none, cached_policy_only or live_second_resolution")
    if background["mode"] == "cached_policy_only":
        if background.get("camera_id", camera["camera_id"]) != task["camera_id"]:
            raise ValueError("Background policy camera binding differs from task camera")
        raise ValueError(
            "Live background_filter=cached_policy_only is unsupported: the existing Newport policy is bound "
            "to a two-minute clip SHA and cached second-resolution detections. A reviewed full-source scope "
            "policy and a live confirmation implementation are required before enabling it on full videos. "
            "It is never silently disabled or applied to another camera."
        )
    if background["mode"] == "live_second_resolution":
        if not background.get("policy_path") or not re.fullmatch(r"[a-f0-9]{64}", str(background.get("policy_sha256", ""))):
            raise ValueError("Live background confirmation requires explicit policy_path and policy_sha256")
        if type(background.get("confirmation_imgsz")) is not int or background["confirmation_imgsz"] != 1280:
            raise ValueError("Frozen live background confirmation requires confirmation_imgsz=1280")
        if config.get("detector", {}).get("imgsz") != 640:
            raise ValueError("Live background confirmation requires the frozen primary imgsz=640")


def _validate_settings(config):
    detector = config.get("detector", {})
    tracker = config.get("tracker", {})
    for name, settings, fields in (("detector", detector, DETECTOR_FIELDS), ("tracker", tracker, TRACKER_FIELDS)):
        if not isinstance(settings, dict) or fields - settings.keys():
            raise ValueError(f"Explicit {name} fields required: {sorted(fields)}")
        extras = settings.keys() - fields - ({"save", "verbose"} if name == "detector" else set())
        if extras:
            raise ValueError(f"Unknown {name} settings: {sorted(extras)}")
    for field in ("conf", "iou"):
        if not 0 < _number(detector[field], f"detector.{field}") <= 1:
            raise ValueError(f"detector.{field} must be in (0,1]")
    for field in ("half", "agnostic_nms", "augment", "rect"):
        if type(detector[field]) is not bool:
            raise ValueError(f"detector.{field} must be boolean")
    for field in ("imgsz", "max_det"):
        if type(detector[field]) is not int or detector[field] <= 0:
            raise ValueError(f"detector.{field} must be a positive integer")
    if type(detector["batch"]) is not int or detector["batch"] != 1:
        raise ValueError("Streaming inference requires detector.batch=1")
    if detector.get("save", False) is not False or detector.get("verbose", False) is not False:
        raise ValueError("detector.save and detector.verbose must be false; results are streamed by this pipeline")
    classes = detector["classes"]
    if classes is not None and (not isinstance(classes, list) or not classes or
            any(type(c) is not int or c not in CLASS_NAMES for c in classes) or len(set(classes)) != len(classes)):
        raise ValueError("detector.classes must be null or an explicit unique list of formal class IDs")
    if tracker["tracker_type"] != "bytetrack":
        raise ValueError("Only the explicitly configured ByteTrack backend is supported")
    for field in ("track_high_thresh", "track_low_thresh", "new_track_thresh", "match_thresh"):
        if not 0 < _number(tracker[field], f"tracker.{field}") <= 1:
            raise ValueError(f"tracker.{field} must be in (0,1]")
    if tracker["track_low_thresh"] >= tracker["track_high_thresh"]:
        raise ValueError("tracker.track_low_thresh must be lower than track_high_thresh")
    if detector["conf"] > tracker["track_low_thresh"]:
        raise ValueError("detector.conf must not cut off the configured tracker low-confidence interval")
    if type(tracker["track_buffer"]) is not int or tracker["track_buffer"] < 0:
        raise ValueError("tracker.track_buffer must be a nonnegative integer number of frames")
    if type(tracker["fuse_score"]) is not bool:
        raise ValueError("tracker.fuse_score must be boolean")
    dedup = config.get("dedup", {})
    if not isinstance(dedup, dict) or dedup.get("mode") not in {"none", "cross_class_greedy_iou"}:
        raise ValueError("dedup.mode must be explicit: none or cross_class_greedy_iou")
    if dedup.keys() - {"mode", "iou_threshold", "containment_policy_path", "containment_policy_sha256", "containment_policy"}:
        raise ValueError("Unknown dedup configuration fields")
    if dedup["mode"] == "cross_class_greedy_iou":
        if not 0 < _number(dedup.get("iou_threshold"), "dedup.iou_threshold") <= 1:
            raise ValueError("dedup.iou_threshold must be in (0,1]")
    containment_fields = {"containment_policy_path", "containment_policy_sha256"}
    if containment_fields & dedup.keys():
        if not containment_fields <= dedup.keys() or not dedup.get("containment_policy_path") or not re.fullmatch(r"[a-f0-9]{64}", str(dedup.get("containment_policy_sha256", ""))):
            raise ValueError("Containment requires explicit policy path and SHA256")
        if dedup["mode"] != "cross_class_greedy_iou" or dedup["iou_threshold"] != .7:
            raise ValueError("Frozen containment requires preceding cross-class dedup at IoU 0.7")
    elif "containment_policy" in dedup:
        raise ValueError("Containment parameters require their explicit file and SHA256")
    if not isinstance(config.get("device"), str) or not config["device"].strip():
        raise ValueError("device must be an explicit nonempty string, e.g. cpu, mps, or 0")
    if config["device"] == "cpu" and detector["half"]:
        raise ValueError("CPU half-precision inference is unsupported by this backend")
    model = config.get("model", {})
    if not isinstance(model.get("version"), str) or not model["version"] or not re.fullmatch(r"[a-f0-9]{64}", str(model.get("sha256", ""))):
        raise ValueError("model.version and an exact lowercase model.sha256 are required")


def _bound_policy(settings, path_key, hash_key, data_key):
    """Recheck small policy files before inference; never trust a stale embedded copy."""
    path = Path(settings[path_key]).expanduser().resolve()
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != settings[hash_key]:
        raise ValueError(f"Policy SHA256 mismatch: {path}")
    policy = json.loads(payload)
    if data_key in settings and settings[data_key] != policy:
        raise ValueError(f"Embedded policy differs from its bound file: {path}")
    return policy


def _method_policies(config, task, camera, input_sha, width, height):
    """Load and bind the optional frozen rules to this exact full source/model."""
    policies, provenance = {}, {"order": []}
    background = camera["background_filter"]
    if background["mode"] == "live_second_resolution":
        from confirm_background_candidates import validate_policy
        policy = _bound_policy(background, "policy_path", "policy_sha256", "policy")
        validate_policy(policy)
        for key, expected in (("camera_id", task["camera_id"]), ("source_video_filename", task["source_id"]),
                              ("source_clip_sha256", input_sha), ("model_sha256", config["model"]["sha256"]),
                              ("frame_width", width), ("frame_height", height)):
            if policy.get(key) != expected:
                raise ValueError(f"Live background policy {key} differs from the exact source/model scope")
        if policy["confirmation_iou"] != .5 or policy["confirm_min_confidence"] != .35:
            raise ValueError("Live background confirmation thresholds must remain frozen at IoU .5/confidence .35")
        policies["background"] = policy
        provenance["order"].append("background_confirmation")
        provenance["background_confirmation"] = {
            "mode": background["mode"], "policy_sha256": background["policy_sha256"],
            "source_sha256": input_sha, "model_sha256": config["model"]["sha256"],
            "confirmation_imgsz": 1280, "confirmation_schedule": "every_selected_decoded_frame",
            "confirmation_detector_settings": {**config["detector"], "imgsz": 1280},
            "frame_digest": "sha256_of_C_order_decoded_BGR_uint8_pixels_before_inference",
        }
    dedup = config["dedup"]
    if dedup["mode"] == "cross_class_greedy_iou":
        provenance["order"].append("cross_class_duplicate_suppression")
    if "containment_policy_path" in dedup:
        from vehicle_containment import validate_policy
        policy = _bound_policy(dedup, "containment_policy_path", "containment_policy_sha256", "containment_policy")
        validate_policy(policy)
        policies["containment"] = policy
        provenance["order"].append("heavy_vehicle_containment")
        provenance["contained_vehicle_suppression"] = {"policy_sha256": dedup["containment_policy_sha256"]}
    provenance["order"].append("bytetrack")
    return policies, provenance


def _select_detections(raw, confirmation_raw, config, camera, policies):
    """Exact pilot order, retaining original 640 detection indices at every stage."""
    audit = {}
    eligible = list(range(len(raw)))
    if "background" in policies:
        if confirmation_raw is None:
            raise ValueError("Live background confirmation requires this frame's second-resolution detections")
        from confirm_background_candidates import candidate_confirmation
        eligible, candidates = candidate_confirmation(raw, confirmation_raw, policies["background"])
        audit["confirmation_raw_detections"] = confirmation_raw
        audit["background_confirmation"] = {
            "policy_sha256": camera["background_filter"]["policy_sha256"],
            "kept_detection_indices": eligible, "candidate_records": candidates,
            "removed": [item for item in candidates if not item["kept"]],
        }
    elif confirmation_raw is not None:
        raise ValueError("Second-resolution detections supplied without an explicit live policy")
    dedup = config["dedup"]
    if dedup["mode"] == "cross_class_greedy_iou":
        # Import the same pure function used by the frozen cached replay.
        from replay_vehicle_tracking_pilot import suppress_cross_class_duplicates as pilot_cross_class
        local_kept, removed = pilot_cross_class([raw[index] for index in eligible], dedup["iou_threshold"])
        kept = [eligible[index] for index in local_kept]
        for removal in removed:
            removal["removed_index"] = eligible[removal["removed_index"]]
            removal["kept_index"] = eligible[removal["kept_index"]]
    else:
        kept, removed = eligible, []
    audit["duplicate_suppression"] = {"mode": dedup["mode"], "threshold": dedup.get("iou_threshold"),
        "input_detection_indices": eligible, "kept_detection_indices": kept, "removed": removed}
    if "containment" in policies:
        from vehicle_containment import suppress_contained_vehicles
        final, removed, unresolved = suppress_contained_vehicles(raw, kept, policies["containment"])
        audit["contained_vehicle_suppression"] = {
            "policy_sha256": dedup["containment_policy_sha256"], "input_detection_indices": kept,
            "kept_detection_indices": final, "removed": removed, "unresolved_pairs": unresolved,
            "not_ground_truth_duplicates": True,
        }
        kept = final
    audit["tracker_input_detection_indices"] = kept
    return kept, audit


def _ffprobe_path(config):
    configured = config.get("time", {}).get("ffprobe_path")
    if configured:
        candidate = str(Path(configured).expanduser())
        binary = candidate if Path(candidate).is_file() else shutil.which(candidate)
    else:
        binary = shutil.which("ffprobe")
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(
            "ffprobe is unavailable. Set time.ffprobe_path to an existing executable. "
            "Video FPS metadata alone cannot prove CFR; the backend will not assume frame_index/30."
        )
    return str(Path(binary).resolve())


def _probe_stream(binary, path):
    command = [binary, "-v", "error", "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError(f"ffprobe could not inspect input: {result.stderr[:2000]}")
    try:
        streams = [s for s in json.loads(result.stdout)["streams"] if s.get("codec_type") == "video"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("ffprobe returned malformed stream metadata") from exc
    if len(streams) != 1:
        raise ValueError("Exactly one video stream is supported")
    stream = streams[0]
    rotation = [stream.get("tags", {}).get("rotate", 0)] + [
        item.get("rotation", 0) for item in stream.get("side_data_list", [])
    ]
    if any(float(value) % 360 != 0 for value in rotation):
        raise ValueError("Rotated input is unsupported: ROI dimensions and decoder coordinates must agree")
    fps = _fraction(stream.get("avg_frame_rate"), "average frame rate")
    if fps != _fraction(stream.get("r_frame_rate"), "declared frame rate"):
        raise ValueError("Frame-rate metadata disagrees; possible VFR is unsupported until explicit PTS handling exists")
    time_base = _fraction(stream.get("time_base"), "time base")
    if time_base >= 1 / fps / 2:
        raise ValueError("Timestamp resolution is too coarse to establish CFR safely")
    if int(stream.get("width", 0)) <= 0 or int(stream.get("height", 0)) <= 0:
        raise ValueError("Input has invalid frame dimensions")
    return stream, fps, time_base


def _audit_pts_rows(rows, fps, time_base):
    """Audit integer PTS with at most one tick of representational uncertainty.

    Rounded time bases can alternate interval lengths (e.g. 33/34 ms at 30 FPS),
    so the audit checks each timestamp against the rational grid, not equality
    of rounded durations. A missing/duplicate PTS, gap or drift is rejected.
    ``rows`` is streamed; no list of the video's timestamps is retained.
    """
    frame_ticks = 1 / fps / time_base
    count, first, previous, last_duration_known = 0, None, None, False
    for row in rows:
        if not row.strip():
            continue
        fields = dict(item.split("=", 1) for item in row.strip().split("|") if "=" in item)
        # ffprobe can print extra side-data-only lines for a frame: ignore those.
        if "pts" not in fields and ("side_data_type" in fields or "side_data" in fields):
            continue
        try:
            pts = int(fields["pts"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Frame {count} has no usable original PTS; unknown time mode is unsupported") from exc
        if first is None:
            first = pts
        if previous is not None and pts <= previous:
            raise ValueError(f"Frame {count} has duplicate/backward PTS")
        if abs(Fraction(pts - first) - count * frame_ticks) > 1:
            raise ValueError(f"Frame {count} is off the CFR time grid: VFR, missing frames, or discontinuity")
        duration = fields.get("pkt_duration", fields.get("duration"))
        last_duration_known = duration not in (None, "N/A", "0")
        if duration not in (None, "N/A", "0"):
            try:
                duration_ticks = int(duration)
            except ValueError as exc:
                raise ValueError(f"Frame {count} has malformed duration") from exc
            if duration_ticks <= 0 or abs(Fraction(duration_ticks) - frame_ticks) > 1:
                raise ValueError(f"Frame {count} duration differs from the CFR period")
        previous = pts
        count += 1
    if not count:
        raise ValueError("ffprobe returned no presented frames")
    if not last_duration_known:
        raise ValueError("Final frame duration is unknown; refusing to invent the final coverage interval")
    return {"input_frame_count": count, "input_first_pts_seconds": float(first * time_base),
            "input_last_pts_seconds": float(previous * time_base), "pts_audit_frames": count}


def _audit_pts(binary, path, fps, time_base, timeout):
    command = [binary, "-v", "error", "-select_streams", "v:0", "-show_frames",
               "-show_entries", "frame=pts,pkt_duration,duration", "-of", "compact=p=0:nk=0", str(path)]
    timed_out = threading.Event()
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True)
        def expire():
            if process.poll() is None:
                timed_out.set()
                process.kill()
        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdout is not None
            try:
                audit = _audit_pts_rows(process.stdout, fps, time_base)
            except ValueError:
                if timed_out.is_set():
                    raise TimeoutError(f"Full input PTS audit exceeded {timeout:g} seconds; no time assumptions were made")
                raise
            returncode = process.wait()
            if timed_out.is_set():
                raise TimeoutError(f"Full input PTS audit exceeded {timeout:g} seconds; no time assumptions were made")
            errors.seek(0)
            error_text = errors.read(2000).strip()
            if returncode or error_text:
                raise ValueError(f"PTS audit reported a decode error (exit {returncode}): {error_text}")
            return audit
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _segment(config, frame_count, fps):
    offset = _number(config["input"].get("source_offset_seconds", 0), "input.source_offset_seconds")
    if offset < 0:
        raise ValueError("input.source_offset_seconds must be nonnegative")
    input_duration = Fraction(frame_count, 1) / fps
    segment = config.get("segment") or {}
    first, last = 0, frame_count
    for key in ("start_seconds", "end_seconds"):
        if key not in segment or segment[key] is None:
            continue
        seconds = _number(segment[key], f"segment.{key}")
        index = (Fraction(str(seconds)) - Fraction(str(offset))) * fps
        rounded = round(index)
        # JSON decimal inputs can approximate rational frame edges to 1e-7 s.
        if abs(float(index - rounded) / float(fps)) > 1e-7:
            raise ValueError(f"segment.{key} must align to an audited frame boundary; no silent rounding/cropping")
        if not 0 <= rounded <= frame_count:
            raise ValueError(f"segment.{key} lies outside input source-time range")
        if key == "start_seconds":
            first = rounded
        else:
            last = rounded
    if first >= last:
        raise ValueError("Selected segment must contain at least one frame")
    return {"first_frame_index": first, "end_frame_index_exclusive": last,
            "frame_count": last - first, "input_source_offset_seconds": offset,
            "input_duration_seconds": float(input_duration),
            "source_start_seconds": offset + float(Fraction(first, 1) / fps),
            "source_end_seconds": offset + float(Fraction(last, 1) / fps)}


def _versions():
    versions = {"python": sys.version.split()[0]}
    for package in ("ultralytics", "torch", "opencv-python", "numpy"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def preflight_video(config: dict, task: dict, camera: dict) -> dict:
    """Validate a development source without model construction or inference.

    PTS audit and hashes read the complete input file, even for a selected clip;
    audit_timeout_seconds (default 300) bounds the metadata/decode audit. This is
    CPU work, not GPU inference. Missing dependencies remain explicit errors.
    """
    _validate_scope(config, task, camera)
    _validate_settings(config)
    time_config = config.get("time", {})
    mode = time_config.get("mode", "audited_cfr")
    if mode not in ("audited_cfr", "audited_pts"):
        raise ValueError("Only audited_cfr or independently verified audited_pts time modes are supported")
    timeout = _number(time_config.get("audit_timeout_seconds", 300), "time.audit_timeout_seconds")
    if timeout <= 0:
        raise ValueError("time.audit_timeout_seconds must be positive")
    source = Path(task["input_path"]).expanduser().resolve()
    model = Path(config["model"]["path"]).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Development input is missing: {source}")
    if model.suffix.lower() != ".pt" or not model.is_file():
        raise FileNotFoundError(f"A local .pt checkpoint is required, without automatic downloading: {model}")
    binary = _ffprobe_path(config)
    started = time.perf_counter()
    input_stat, model_stat = _stat(source), _stat(model)
    input_sha, model_sha = _hash(source), _hash(model)
    if model_sha != config["model"]["sha256"]:
        raise ValueError("Local checkpoint SHA256 differs from model.sha256")
    expected_sha = config["input"].get("sha256")
    if expected_sha is not None and input_sha != expected_sha:
        raise ValueError("Input SHA256 differs from input.sha256")
    if mode == "audited_cfr":
        stream, fps, time_base = _probe_stream(binary, source)
        dimensions = (int(stream["width"]), int(stream["height"]))
        layout = camera.get("layout", {})
        if dimensions != (layout.get("frame_width"), layout.get("frame_height")):
            raise ValueError("Audited input dimensions differ from camera ROI dimensions; resizing ROIs implicitly is forbidden")
        if "fps" in config["input"] and fps != _fraction(config["input"]["fps"], "input.fps"):
            raise ValueError("Audited FPS differs from explicit input.fps")
        audit = _audit_pts(binary, source, fps, time_base, timeout)
        declared = stream.get("nb_frames")
        if declared not in (None, "N/A") and int(declared) != audit["input_frame_count"]:
            raise ValueError("Container frame count differs from fully audited PTS count")
        timing = {**audit, **_segment(config, audit["input_frame_count"], fps),
                  "width": dimensions[0], "height": dimensions[1], "fps": float(fps),
                  "fps_num": fps.numerator, "fps_den": fps.denominator,
                  "time_base_num": time_base.numerator, "time_base_den": time_base.denominator,
                  "time_mode": mode, "pts_tolerance_ticks": 1,
                  "time_semantics": "source_offset + frame_index / audited rational CFR; first container PTS preserved"}
    else:
        from .pts_timing import preflight_pts
        timing = preflight_pts(config, task, camera)
        if timing["input_sha256"] != input_sha:
            raise ValueError("PTS evidence input differs from the current source")
    if _stat(source) != input_stat or _stat(model) != model_stat:
        raise ValueError("Input or checkpoint changed during preflight")
    _, method_provenance = _method_policies(config, task, camera, input_sha, timing["width"], timing["height"])
    dependencies = {name: importlib.util.find_spec(name) is not None for name in ("cv2", "numpy", "torch", "ultralytics")}
    if not all(dependencies.values()):
        raise RuntimeError(f"Existing inference environment is incomplete: {dependencies}; no dependencies were installed")
    versions = _versions()
    version_result = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10, check=True)
    versions["ffprobe"] = version_result.stdout.splitlines()[0]
    tracker_source = Path(importlib.metadata.distribution("ultralytics").locate_file("ultralytics/trackers/byte_tracker.py"))
    return {
        **timing,
        "source_id": task["source_id"], "split": task["split"], "camera_id": task["camera_id"],
        "input_path": str(source), "input_sha256": input_sha, "input_stat": input_stat,
        "model_path": str(model), "model_sha256": model_sha, "model_stat": model_stat,
        "versions": versions, "ffprobe_path": binary,
        "tracker_implementation": {"path": str(tracker_source), "sha256": _hash(tracker_source)},
        "tracker_buffer_units": "frames, independent of source FPS; no hidden 30 FPS assumption",
        "removed_track_history": "Terminal IDs absent from active/lost pools are pruned; active and lost tracker state is retained",
        "method_provenance": method_provenance,
        "preflight_elapsed_seconds": time.perf_counter() - started,
        "backend_sha256": _hash(__file__),
        "configuration_sha256": _configuration_hash(config, camera),
    }


def _box_iou(a, b):
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    if union <= 0:
        raise ValueError("Invalid box area for duplicate suppression")
    return intersection / union


def suppress_cross_class_duplicates(raw, threshold):
    """Match the legacy replay's strictly-greater IoU and stable confidence ties."""
    kept, removed = [], []
    for index in sorted(range(len(raw)), key=lambda i: (-raw[i]["confidence"], i)):
        candidate = raw[index]
        for earlier in kept:
            previous = raw[earlier]
            overlap = _box_iou(candidate["box_xyxy"], previous["box_xyxy"])
            if candidate["class_id"] != previous["class_id"] and overlap > threshold:
                removed.append({"removed_index": index, "kept_index": earlier, "iou": overlap,
                                "classes": {"removed": candidate["class_name"], "kept": previous["class_name"]}})
                break
        else:
            kept.append(index)
    return sorted(kept), removed


def _decode_detections(boxes, minimum):
    detections = []
    for values in boxes.data.tolist():
        if len(values) != 6 or not all(math.isfinite(float(v)) for v in values):
            raise ValueError("Expected finite six-column detector boxes")
        x1, y1, x2, y2, score, cls = map(float, values)
        class_id = int(cls)
        if cls != class_id or class_id not in CLASS_NAMES:
            raise ValueError(f"Unexpected formal model class {cls}")
        if not (x2 > x1 and y2 > y1 and minimum - 1e-7 <= score <= 1):
            raise ValueError("Invalid detector geometry or confidence")
        detections.append({"box_xyxy": [x1, y1, x2, y2], "class_id": class_id,
                           "class_name": CLASS_NAMES[class_id], "confidence": score})
    return detections


def _decode_tracks(tracks, indices, raw):
    output, seen = [], set()
    for values in tracks:
        if len(values) != 8 or not all(math.isfinite(float(v)) for v in values):
            raise ValueError("Unsupported ByteTrack output: expected xyxy, ID, confidence, class, local detection index")
        x1, y1, x2, y2, identity, score, cls, local = map(float, values)
        track_id, class_id, local_index = int(identity), int(cls), int(local)
        if identity != track_id or track_id <= 0 or track_id in seen:
            raise ValueError("Invalid or duplicate track ID within frame")
        if cls != class_id or class_id not in CLASS_NAMES or local != local_index or not 0 <= local_index < len(indices):
            raise ValueError("Invalid tracked class or detection index")
        index = int(indices[local_index])
        if raw[index]["class_id"] != class_id or not math.isclose(raw[index]["confidence"], score, abs_tol=1e-6):
            raise ValueError("Tracker observation no longer maps to its original raw detection")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Invalid tracked box geometry")
        seen.add(track_id)
        output.append({"box_xyxy": [x1, y1, x2, y2], "track_id": track_id,
                       "class_id": class_id, "class_name": CLASS_NAMES[class_id],
                       "confidence": score, "detection_index": index})
    return output


def _prune_terminal_history(tracker):
    # Old ByteTrack versions accumulated all removed tracks forever. They are
    # only needed for subtraction from the current pools on the following update.
    referenced = {t.track_id for t in tracker.tracked_stracks} | {t.track_id for t in tracker.lost_stracks}
    tracker.removed_stracks = [t for t in tracker.removed_stracks if t.track_id in referenced]


def iter_video(config: dict, task: dict, camera: dict, preflight: dict):
    """Yield current observed tracks, including valid empty frames, in order.

    Decode failure raises and leaves the task failed; it cannot become a valid
    zero-count frame. Prefix frames are decoded and discarded without inference
    to avoid imprecise seeking. Every selected frame updates one fresh tracker.
    There is no mid-video state resume and no claim of continuous IDs on retry.
    """
    _validate_scope(config, task, camera)
    _validate_settings(config)
    if config.get("inference_enabled", True) is False:
        raise ValueError("INFERENCE_DISABLED: preparation-only candidate requires a separately verified final method")
    for key in ("source_id", "split", "camera_id"):
        if preflight.get(key) != task.get(key):
            raise ValueError(f"Preflight {key} does not belong to this task")
    mode = preflight.get("time_mode")
    if mode not in ("audited_cfr", "audited_pts"):
        raise ValueError("A successful audited CFR or PTS preflight is required")
    if preflight.get("configuration_sha256") != _configuration_hash(config, camera):
        raise ValueError("Run or camera configuration changed since preflight")
    source, model_path = Path(task["input_path"]).expanduser().resolve(), Path(config["model"]["path"]).expanduser().resolve()
    if str(source) != preflight["input_path"] or str(model_path) != preflight["model_path"]:
        raise ValueError("Input/model path changed since preflight")
    if _stat(source) != preflight["input_stat"] or _stat(model_path) != preflight["model_stat"]:
        raise ValueError("Input/model changed since preflight; run preflight again")
    if config["model"]["sha256"] != preflight["model_sha256"]:
        raise ValueError("Checkpoint configuration changed since preflight")
    policies, method_provenance = _method_policies(config, task, camera, preflight.get("input_sha256"), preflight["width"], preflight["height"])
    if "method_provenance" in preflight and method_provenance != preflight["method_provenance"]:
        raise ValueError("Counting method provenance changed since preflight")
    fps = Fraction(preflight["fps_num"], preflight["fps_den"])
    pts_frames = None
    if mode == "audited_cfr":
        selected = _segment(config, preflight["input_frame_count"], fps)
        if any(selected[key] != preflight[key] for key in selected):
            raise ValueError("Time selection changed since preflight")
    else:
        from .pts_timing import iter_pts
        pts_frames = iter_pts(preflight)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    from ultralytics.trackers.byte_tracker import BYTETracker

    device = config["device"]
    if device == "cpu" and config["detector"]["half"]:
        raise ValueError("CPU half-precision inference is unsupported by this backend")
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("Explicit MPS device is unavailable; no device fallback is allowed")
    if device not in {"cpu", "mps"} and not torch.cuda.is_available():
        raise RuntimeError("Explicit CUDA device is unavailable; no CPU fallback is allowed")
    model = YOLO(str(model_path))
    if model.task != "detect" or dict(model.names) != CLASS_NAMES:
        raise ValueError(f"Expected formal four-class detection checkpoint, got {model.names}")
    # Across supported legacy/current Ultralytics, track_buffer means frames.
    # Older constructors scale buffer by frame_rate/30; explicit 30 preserves
    # the configured frame count and does not determine any output timestamp.
    tracker_kwargs = {"frame_rate": 30} if "frame_rate" in inspect.signature(BYTETracker).parameters else {}
    tracker = BYTETracker(SimpleNamespace(**config["tracker"]), **tracker_kwargs)
    if mode == "audited_pts":
        decoder = preflight["audited_decoder"]
        if cv2.__version__ != decoder["opencv_version"]:
            raise ValueError("OpenCV version differs from the full PTS decode audit; re-audit this environment")
        cap = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_N_THREADS, decoder["requested_decoder_threads"]])
    else:
        cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("Video decoder could not open the audited input")
    processed = 0
    decoder_first_msec = None
    decoder_previous_msec = None
    start, end = preflight["first_frame_index"], preflight["end_frame_index_exclusive"]
    try:
        if mode == "audited_pts" and cap.getBackendName() != preflight["audited_decoder"]["backend"]:
            raise ValueError("Decoder backend differs from the full PTS audit")
        if mode == "audited_cfr" and not math.isclose(float(cap.get(cv2.CAP_PROP_FPS)), float(fps), abs_tol=1e-6):
            raise ValueError("Decoder FPS differs from audited rational FPS")
        predict = {**config["detector"], "save": False, "verbose": False, "device": device}
        for frame_index in range(end):
            timing_row = next(pts_frames, None) if pts_frames is not None else None
            if pts_frames is not None and timing_row is None:
                raise ValueError("PTS timing records ended before the audited frame count")
            if timing_row is not None and timing_row["frame_index"] != frame_index:
                raise ValueError("PTS sidecar index differs from decoded input sequence")
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"Decode failed at input frame {frame_index}; missing time is not a true zero count")
            if frame.shape[:2] != (preflight["height"], preflight["width"]):
                raise ValueError(f"Decoded dimensions changed at input frame {frame_index}")
            decoder_next_index = float(cap.get(cv2.CAP_PROP_POS_FRAMES))
            if not math.isfinite(decoder_next_index) or abs(decoder_next_index - (frame_index + 1)) > 1e-6:
                raise ValueError(f"Decoder frame index mismatch at frame {frame_index}: expected next index {frame_index + 1}, got {decoder_next_index}")
            decoder_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC))
            if not math.isfinite(decoder_msec):
                raise ValueError("Decoder did not expose finite frame timestamps; unsupported decoder time mode")
            # Adjacent audited PTS may be only one tick apart. The timestamp
            # correspondence tolerance must never admit repeated/backward time.
            if decoder_previous_msec is not None and decoder_msec <= decoder_previous_msec:
                raise ValueError(f"Decoder timestamps must be strictly increasing at frame {frame_index}")
            if decoder_first_msec is None:
                decoder_first_msec = decoder_msec
            tick = preflight["time_base_num"] / preflight["time_base_den"]
            expected_time = timing_row["source_timestamp_seconds"] if timing_row is not None else float(Fraction(frame_index, 1) / fps)
            tolerance = tick + 1e-7 if timing_row is not None else min(float(1 / fps) / 3, max(tick + 1e-7, 1e-4))
            if abs((decoder_msec - decoder_first_msec) / 1000 - expected_time) > tolerance:
                raise ValueError(f"Decoder timestamp mismatch at frame {frame_index}: skipped/duplicated frame or unsupported timestamp reporting")
            decoder_previous_msec = decoder_msec
            if frame_index < start:
                continue
            if frame.dtype != np.uint8:
                raise ValueError("Decoded frame digest requires unmodified BGR uint8 pixels")
            frame_provenance = {"decoded_frame_sha256": hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()}
            results = model.predict(frame, **predict)
            if len(results) != 1:
                raise RuntimeError("Expected one prediction result per decoded frame")
            boxes = results[0].boxes.cpu().numpy()
            raw = _decode_detections(boxes, config["detector"]["conf"])
            confirmation_raw = None
            if "background" in policies:
                confirmations = model.predict(frame, **{**predict, "imgsz": 1280})
                if len(confirmations) != 1:
                    raise RuntimeError("Expected one second-resolution result for the same decoded frame")
                confirmation_raw = _decode_detections(confirmations[0].boxes.cpu().numpy(), config["detector"]["conf"])
            kept, selection_audit = _select_detections(raw, confirmation_raw, config, camera, policies)
            indices = np.asarray(kept, dtype=np.int64)
            observations = _decode_tracks(tracker.update(boxes[indices], frame), indices, raw)
            if tracker.frame_id != processed + 1:
                raise RuntimeError("Tracker did not advance once per selected frame")
            _prune_terminal_history(tracker)
            timestamp = timing_row["timestamp_seconds"] if timing_row is not None else float(Fraction(frame_index - start, 1) / fps)
            source_timestamp = timing_row["source_timestamp_seconds"] if timing_row is not None else preflight["input_source_offset_seconds"] + float(Fraction(frame_index, 1) / fps)
            yield {
                "frame_index": frame_index, "segment_frame_index": processed,
                "timestamp_seconds": timestamp, "source_timestamp_seconds": source_timestamp,
                "duration_seconds": timing_row["duration_seconds"] if timing_row is not None else float(1 / fps), "valid": True,
                **({"pts": timing_row["pts"], "duration_ticks": timing_row["duration_ticks"],
                    "time_base_num": preflight["time_base_num"], "time_base_den": preflight["time_base_den"]}
                   if timing_row is not None else {}),
                "decoder_position_msec": decoder_msec, "decoder_first_position_msec": decoder_first_msec,
                "raw_detections": raw, "tracks_primary": observations,
                **selection_audit, **frame_provenance,
                "quality": {"decoded": True, "inference_complete": True,
                            "time_mode": mode, "duration_semantics": "decoded_frame_presentation_interval",
                            "detector_max_det_reached": len(raw) == config["detector"]["max_det"],
                            **({"confirmation_complete": True,
                                "confirmation_max_det_reached": len(confirmation_raw) == config["detector"]["max_det"]}
                               if confirmation_raw is not None else {})},
            }
            processed += 1
        if end == preflight["input_frame_count"]:
            extra, _ = cap.read()
            if extra:
                raise RuntimeError("Decoder emitted extra frames beyond audited PTS count")
        if processed != preflight["frame_count"]:
            raise RuntimeError("Selected frame count differs from audited preflight")
        if pts_frames is not None and next(pts_frames, None) is not None:
            raise ValueError("Extra PTS timing records beyond decoded frames")
        if _stat(source) != preflight["input_stat"] or _stat(model_path) != preflight["model_stat"]:
            raise RuntimeError("Input/checkpoint changed during inference")
    finally:
        cap.release()
        if pts_frames is not None:
            pts_frames.close()
