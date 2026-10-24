"""Compare two local vehicle detectors on every frame of one constant-FPS clip.

Inference runs first; optional side-by-side mp4v rendering reads the saved JSONL.
Counts are detections in the current frame, never unique vehicles or traffic flow.
No training, frame sampling, model downloads, or CPU fallback is supported.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time


CLASS_COLORS_BGR = {
    "car": (80, 220, 80),
    "motorcycle": (255, 200, 70),
    "bus": (70, 180, 255),
    "truck": (220, 100, 220),
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Local constant-FPS video")
    parser.add_argument("--model-n", type=Path, help="Local YOLO11n best.pt; required for inference")
    parser.add_argument("--model-s", type=Path, help="Local YOLO11s best.pt; required for inference")
    parser.add_argument("--output", required=True, type=Path, help="New output directory, or existing results for --render-only")
    parser.add_argument("--device", default="0", choices=["0"], help="CUDA device 0 only")
    parser.add_argument("--imgsz", default=640, type=int)
    parser.add_argument("--conf", default=0.25, type=float)
    parser.add_argument("--iou", default=0.7, type=float)
    parser.add_argument("--no-render", action="store_true", help="Save detections/metadata without video")
    parser.add_argument("--render-only", action="store_true", help="Render existing successful results without torch, models, or CUDA")
    args = parser.parse_args(argv)
    if args.render_only and args.no_render:
        parser.error("--render-only and --no-render cannot be combined")
    if not args.render_only and (args.model_n is None or args.model_s is None):
        parser.error("Inference requires --model-n and --model-s")
    if args.imgsz <= 0 or args.imgsz % 32:
        parser.error("--imgsz must be a positive multiple of 32")
    for name in ("conf", "iou"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 < value <= 1:
            parser.error(f"--{name} must be finite and in (0, 1]")
    return args


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def open_video(cv2, path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"Cannot open video: {path}")
    return capture


def require_frame_size(frame, width, height):
    if frame.shape[:2] != (height, width):
        raise RuntimeError(f"Video frame size changed: {frame.shape[:2]} != {(height, width)}")


def prediction_record(result, frame_index, fps, source_time_ms, elapsed_ms, names):
    detections = []
    if result.boxes is None:
        raise RuntimeError("Expected detection boxes, received a different model task")
    for values in result.boxes.data.detach().cpu().tolist():
        if len(values) != 6 or not all(math.isfinite(value) for value in values):
            raise RuntimeError("Invalid detector output; expected finite xyxy, confidence, class ID")
        x1, y1, x2, y2, confidence, class_value = values
        class_id = int(class_value)
        if class_value != class_id or class_id not in names:
            raise RuntimeError(f"Invalid predicted class: {class_value}")
        detections.append({"box_xyxy": [x1, y1, x2, y2], "class_id": class_id,
                           "class_name": names[class_id], "confidence": confidence})
    counts = Counter(item["class_name"] for item in detections)
    return {
        "frame_index": frame_index,
        "timestamp_seconds": frame_index / fps,
        "source_position_msec": source_time_ms if math.isfinite(source_time_ms) else None,
        "current_frame_vehicle_count": len(detections),
        "counts_by_class": {name: counts[name] for name in names.values()},
        "detections": detections,
        "predict_wall_ms": elapsed_ms,
        "ultralytics_speed_ms": {key: float(value) for key, value in result.speed.items()},
    }


def draw_panel(cv2, frame, record, label, header_height):
    panel = frame.copy()
    scale = max(0.45, min(1.2, frame.shape[1] / 1800))
    thickness = max(1, round(frame.shape[1] / 700))
    for detection in record["detections"]:
        x1, y1, x2, y2 = [round(value) for value in detection["box_xyxy"]]
        color = CLASS_COLORS_BGR[detection["class_name"]]
        cv2.rectangle(panel, (x1, y1), (x2, y2), color, thickness)
        text = f"{detection['class_name']} {detection['confidence']:.2f}"
        cv2.putText(panel, text, (max(0, x1), max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(panel, text, (max(0, x1), max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)
    panel = cv2.copyMakeBorder(panel, header_height, 0, 0, 0, cv2.BORDER_CONSTANT, value=(24, 24, 24))
    font_scale = max(0.5, min(1.6, frame.shape[1] / 1300))
    lines = [f"{label}  |  t={record['timestamp_seconds']:.2f}s  |  frame {record['frame_index']}",
             f"Vehicles in this frame: {record['current_frame_vehicle_count']} (not cumulative)"]
    for index, text in enumerate(lines, 1):
        text_width = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0][0]
        fitted_scale = font_scale * min(1.0, max(1, frame.shape[1] - 24) / max(1, text_width))
        cv2.putText(panel, text, (12, round(header_height * index / 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, fitted_scale, (245, 245, 245), thickness, cv2.LINE_AA)
    return panel


def render_video(cv2, source, output, video, expected_frames):
    """Render from saved records only; model inference is not called here."""
    target = output / "comparison.mp4"
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite comparison video: {target}")
    capture = open_video(cv2, source)
    width, height, fps = video["width"], video["height"], video["fps"]
    header_height = max(84, round(height * 0.1))
    # Even dimensions keep the mp4v intermediate suitable for later H.264 conversion.
    output_height = height + header_height
    output_height += output_height % 2
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width * 2, output_height))
    if not writer.isOpened():
        capture.release()
        writer.release()
        raise RuntimeError("Cannot initialize mp4v writer; detections are saved. Retry rendering externally.")
    count = 0
    try:
        with (output / "predictions_yolo11n.jsonl").open() as n_file, (output / "predictions_yolo11s.jsonl").open() as s_file:
            for frame_index in range(expected_frames):
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"Rendering decode ended early at frame {frame_index}")
                require_frame_size(frame, width, height)
                records = [json.loads(handle.readline()) for handle in (n_file, s_file)]
                if any(record["frame_index"] != frame_index for record in records):
                    raise RuntimeError("Detection records and video frame indices do not match")
                panels = [draw_panel(cv2, frame, record, label, header_height)
                          for record, label in zip(records, ("YOLO11n", "YOLO11s"))]
                combined = cv2.hconcat(panels)
                if combined.shape[0] != output_height:
                    combined = cv2.copyMakeBorder(combined, 0, 1, 0, 0, cv2.BORDER_CONSTANT)
                writer.write(combined)
                count += 1
            if capture.read()[0] or n_file.readline() or s_file.readline():
                raise RuntimeError("Extra source frames or detection records found while rendering")
    finally:
        capture.release()
        writer.release()
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("Rendered video is missing or empty")
    return {"path": str(target), "codec": "mp4v", "frames": count, "fps": fps,
            "width": width * 2, "height": output_height, "audio": False}


def render_existing(source, output):
    """Use local video content with results copied from another machine."""
    for path in (source, output / "config.json", output / "summary.json",
                 output / "predictions_yolo11n.jsonl", output / "predictions_yolo11s.jsonl"):
        if not path.is_file():
            raise FileNotFoundError(f"Required rendering input is missing: {path}")
    config = json.loads((output / "config.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    if summary.get("status") != "completed" or summary.get("inference_status") != "completed" or summary.get("processed_frames", 0) < 1:
        raise ValueError("Only successfully completed inference can be rendered")
    if fingerprint(source)["sha256"] != config["source"]["sha256"]:
        raise ValueError("Source video content differs from the clip used for inference")
    import cv2

    summary["render"] = render_video(cv2, source, output, config["video"], summary["processed_frames"])
    summary["rendered_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "summary.json", summary)
    print(f"Rendering completed: {summary['render']['path']}")


def main(argv=None):
    args = parse_args(argv)
    source, output = (path.expanduser().resolve() for path in (args.source, args.output))
    if args.render_only:
        return render_existing(source, output)
    n_path, s_path = (path.expanduser().resolve() for path in (args.model_n, args.model_s))
    for path in (source, n_path, s_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required local file is missing: {path}")
    if n_path == s_path:
        raise ValueError("--model-n and --model-s must be different checkpoints")
    if any(path.suffix.lower() != ".pt" for path in (n_path, s_path)):
        raise ValueError("Models must be existing local .pt checkpoints")
    if output.exists():
        raise FileExistsError(f"Refusing to reuse output directory: {output}")

    import cv2
    import torch
    from ultralytics import YOLO, __version__ as ultralytics_version

    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise RuntimeError("CUDA device 0 is unavailable; refusing CPU fallback")
    capture = open_video(cv2, source)
    summary = {"status": "running", "processed_frames": 0}
    created_output = False
    try:
        ok, first_frame = capture.read()
        if not ok:
            raise RuntimeError("Video has no decodable frames")
        height, width = first_frame.shape[:2]
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Source FPS is invalid; provide a constant-FPS clip")
        video = {"width": width, "height": height, "fps": fps,
                 "reported_frame_count": round(frame_count) if math.isfinite(frame_count) and frame_count > 0 else None,
                 "timing": "constant-FPS; timestamp_seconds = frame_index / source FPS; no frames sampled"}
        settings = {"device": 0, "imgsz": args.imgsz, "conf": args.conf, "iou": args.iou,
                    "half": False, "augment": False, "rect": True, "batch": 1, "max_det": 300,
                    "agnostic_nms": False, "classes": None, "save": False, "verbose": False}
        models = {"yolo11n": YOLO(str(n_path)), "yolo11s": YOLO(str(s_path))}
        names = {int(key): str(value) for key, value in models["yolo11n"].names.items()}
        if any(model.task != "detect" or model.names != models["yolo11n"].names for model in models.values()):
            raise ValueError("Both checkpoints must be detectors with identical class ID/name mappings")
        if set(names.values()) != set(CLASS_COLORS_BGR) or len(names) != 4:
            raise ValueError("Expected exactly the four vehicle classes: car, motorcycle, bus, truck")
        output.mkdir(parents=True, exist_ok=False)
        created_output = True
        config = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "source": fingerprint(source), "models": {"yolo11n": fingerprint(n_path), "yolo11s": fingerprint(s_path)},
                  "video": video, "predict_settings": settings, "classes": names,
                  "class_colors_bgr": CLASS_COLORS_BGR,
                  "environment": {"python": sys.version, "torch": torch.__version__,
                                  "ultralytics": ultralytics_version, "opencv": cv2.__version__,
                                  "gpu": torch.cuda.get_device_name(0)},
                  "warmup": "One excluded predict call per model on the first source frame",
                  "execution_order": "n then s on even frames; s then n on odd frames",
                  "count_meaning": "Current-frame detections; repeated appearances are not unique vehicles or traffic flow",
                  "comparison_scope": "Qualitative prediction comparison; no ground-truth accuracy or tracking metrics",
                  "render_requested": not args.no_render}
        write_json(output / "config.json", config)
        write_json(output / "summary.json", summary)
        # Initialize/fuse each predictor before recording per-frame timing.
        for model in models.values():
            model.predict(first_frame.copy(), **settings)
            if model.predictor.device.type != "cuda":
                raise RuntimeError("Predictor is not on CUDA; refusing fallback")
        timings = {label: [] for label in models}
        totals = {label: Counter() for label in models}
        frame_index = 0
        frame = first_frame
        started = time.perf_counter()
        with (output / "predictions_yolo11n.jsonl").open("x", encoding="utf-8") as n_file, \
                (output / "predictions_yolo11s.jsonl").open("x", encoding="utf-8") as s_file:
            handles = {"yolo11n": n_file, "yolo11s": s_file}
            while True:
                require_frame_size(frame, width, height)
                source_time_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
                order = ("yolo11n", "yolo11s") if frame_index % 2 == 0 else ("yolo11s", "yolo11n")
                for label in order:
                    torch.cuda.synchronize(0)
                    tick = time.perf_counter()
                    result = models[label].predict(frame.copy(), **settings)[0]
                    torch.cuda.synchronize(0)
                    elapsed_ms = (time.perf_counter() - tick) * 1000
                    record = prediction_record(result, frame_index, fps, source_time_ms, elapsed_ms, names)
                    handles[label].write(json.dumps(record, allow_nan=False) + "\n")
                    timings[label].append(elapsed_ms)
                    totals[label].update(record["counts_by_class"])
                frame_index += 1
                summary["processed_frames"] = frame_index
                if frame_index % 100 == 0:
                    print(f"Compared {frame_index} frames", flush=True)
                ok, frame = capture.read()
                if not ok:
                    break
        summary.update({"inference_status": "completed", "inference_phase_seconds": time.perf_counter() - started,
                        "duration_seconds": frame_index / fps, "video": video, "models": {}})
        for label in models:
            summary["models"][label] = {
                "frames": frame_index,
                "mean_current_frame_vehicle_count": sum(totals[label].values()) / frame_index,
                "mean_current_frame_counts_by_class": {name: totals[label][name] / frame_index for name in names.values()},
                "mean_predict_wall_ms": statistics.mean(timings[label]),
                "median_predict_wall_ms": statistics.median(timings[label]),
                "timing_scope": "Warmed predict call including preprocessing/postprocessing; excludes decoding, JSON serialization and rendering; not an end-to-end FPS benchmark"}
        if video["reported_frame_count"] is not None and frame_index != video["reported_frame_count"]:
            raise RuntimeError(f"Decoded {frame_index} frames but video reports {video['reported_frame_count']}; possible truncated decode")
        capture.release()
        del result, model, models
        torch.cuda.empty_cache()
        if not args.no_render:
            summary["render"] = render_video(cv2, source, output, video, frame_index)
        summary["status"] = "completed"
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "summary.json", summary)
        print(f"Comparison completed: {frame_index} frames per model; outputs: {output}")
    except Exception as exc:
        if created_output:
            summary.update({"status": "failed", "error": str(exc)})
            write_json(output / "summary.json", summary)
        raise
    finally:
        capture.release()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Comparison failed: {exc}", file=sys.stderr)
        sys.exit(1)
