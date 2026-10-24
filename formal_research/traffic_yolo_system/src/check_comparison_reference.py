"""Check two local detectors against one manually labelled reference image.

This is a static single-frame diagnostic, not test-set performance or video
accuracy. Predictions require CUDA device 0; this script never trains a model.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
MATCH_IOU = 0.5


def box_iou(first, second):
    """IoU for continuous xyxy coordinates; no inclusive-pixel +1 convention."""
    overlap_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    overlap_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    intersection = overlap_width * overlap_height
    area_a = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    area_b = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def overlap_candidates(ground_truth, predictions, threshold=MATCH_IOU, class_aware=True):
    candidates = []
    for gt_index, gt in enumerate(ground_truth):
        for prediction_index, prediction in enumerate(predictions):
            same_class = gt["class_id"] == prediction["class_id"]
            if class_aware and not same_class:
                continue
            iou = box_iou(gt["box_xyxy"], prediction["box_xyxy"])
            if iou >= threshold:
                candidates.append({"gt_index": gt_index, "prediction_index": prediction_index,
                                   "iou": iou, "gt_class": gt["class_name"],
                                   "predicted_class": prediction["class_name"], "same_class": same_class})
    return sorted(candidates, key=lambda pair: (-pair["iou"], pair["gt_index"], pair["prediction_index"]))


def greedy_matching(ground_truth, predictions, threshold=MATCH_IOU, class_aware=True):
    """Choose highest-IoU eligible pairs without reusing either index."""
    used_gt, used_predictions, matches = set(), set(), []
    for pair in overlap_candidates(ground_truth, predictions, threshold, class_aware):
        gt_index, prediction_index = pair["gt_index"], pair["prediction_index"]
        if gt_index not in used_gt and prediction_index not in used_predictions:
            matches.append(pair)
            used_gt.add(gt_index)
            used_predictions.add(prediction_index)
    return {"matches": matches,
            "unmatched_gt_indices": [index for index in range(len(ground_truth)) if index not in used_gt],
            "unmatched_prediction_indices": [index for index in range(len(predictions)) if index not in used_predictions]}


def counts_by_class(objects):
    counts = Counter(item["class_name"] for item in objects)
    return {name: counts[name] for name in CLASS_NAMES.values()}


def evaluate_predictions(ground_truth, predictions):
    matched = greedy_matching(ground_truth, predictions)
    gt_counts, prediction_counts = counts_by_class(ground_truth), counts_by_class(predictions)
    true_positives = Counter(pair["gt_class"] for pair in matched["matches"])
    per_class = {name: {"gt_count": gt_counts[name], "prediction_count": prediction_counts[name],
                        "tp": true_positives[name], "fp": prediction_counts[name] - true_positives[name],
                        "fn": gt_counts[name] - true_positives[name]} for name in CLASS_NAMES.values()}
    return {"detections": predictions, "counts_by_class": prediction_counts,
            "class_aware_matching": {**matched, "per_class": per_class,
                                     "total": {"tp": len(matched["matches"]),
                                               "fp": len(matched["unmatched_prediction_indices"]),
                                               "fn": len(matched["unmatched_gt_indices"])}},
            "spatial_diagnostic_not_correct_detections": {
                "all_overlap_candidates": overlap_candidates(ground_truth, predictions, class_aware=False),
                "class_agnostic_greedy_matching": greedy_matching(ground_truth, predictions, class_aware=False)}}


def read_ground_truth(path, width, height):
    """Read YOLO class/cx/cy/w/h labels and retain source line numbers."""
    objects = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"Label line {line_number}: expected class cx cy width height")
        values = [float(field) for field in fields]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Label line {line_number}: non-finite value")
        class_value, cx, cy, bw, bh = values
        class_id = int(class_value)
        if class_value != class_id or class_id not in CLASS_NAMES:
            raise ValueError(f"Label line {line_number}: invalid class ID {class_value}")
        if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < bw <= 1 and 0 < bh <= 1):
            raise ValueError(f"Label line {line_number}: invalid normalized coordinates")
        normalized_xyxy = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]
        if min(normalized_xyxy) < -1e-6 or max(normalized_xyxy) > 1 + 1e-6:
            raise ValueError(f"Label line {line_number}: bounding box extends outside the image")
        objects.append({"index": len(objects), "source_line": line_number,
                        "class_id": class_id, "class_name": CLASS_NAMES[class_id],
                        "box_xywh_normalized": [cx, cy, bw, bh],
                        "box_xyxy": [normalized_xyxy[0] * width, normalized_xyxy[1] * height,
                                     normalized_xyxy[2] * width, normalized_xyxy[3] * height]})
    return objects


def decode_predictions(result):
    if result.boxes is None:
        raise RuntimeError("The model did not return detection boxes")
    objects = []
    for values in result.boxes.data.detach().cpu().tolist():
        if len(values) != 6 or not all(math.isfinite(value) for value in values):
            raise RuntimeError("Invalid prediction; expected finite xyxy, confidence, class ID")
        x1, y1, x2, y2, confidence, class_value = values
        class_id = int(class_value)
        if class_value != class_id or class_id not in CLASS_NAMES or x2 <= x1 or y2 <= y1:
            raise RuntimeError("Invalid predicted class or box")
        objects.append({"index": len(objects), "class_id": class_id, "class_name": CLASS_NAMES[class_id],
                        "box_xyxy": [x1, y1, x2, y2], "confidence": confidence})
    return objects


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path, help="Manual YOLO-format labels for this exact image")
    parser.add_argument("--model-n", required=True, type=Path)
    parser.add_argument("--model-s", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Directory for reference_check.json; existing JSON is never overwritten")
    args = parser.parse_args(argv)
    image_path, labels_path, n_path, s_path, output_dir = [path.expanduser().resolve() for path in
                                                        (args.image, args.labels, args.model_n, args.model_s, args.output)]
    output = output_dir / "reference_check.json"
    for path in (image_path, labels_path, n_path, s_path):
        if not path.is_file():
            raise FileNotFoundError(f"Required local input is missing: {path}")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    if n_path == s_path or any(path.suffix.lower() != ".pt" for path in (n_path, s_path)):
        raise ValueError("Provide two different existing local .pt checkpoints")

    import cv2
    import torch
    from ultralytics import YOLO, __version__ as ultralytics_version

    if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
        raise RuntimeError("CUDA device 0 is unavailable; refusing CPU fallback")
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Cannot decode reference image: {image_path}")
    height, width = image.shape[:2]
    ground_truth = read_ground_truth(labels_path, width, height)
    settings = {"device": 0, "imgsz": 640, "conf": 0.25, "iou": 0.7, "half": False,
                "augment": False, "rect": True, "batch": 1, "max_det": 300,
                "agnostic_nms": False, "classes": None, "save": False, "verbose": False}
    report = {
        "status": "completed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Static single-frame diagnostic against manual labels; not test-set performance, video accuracy, tracking accuracy or traffic flow",
        "image": {**fingerprint(image_path), "width": width, "height": height},
        "labels": fingerprint(labels_path), "classes": CLASS_NAMES, "predict_settings": settings,
        "matching": {"iou_threshold": MATCH_IOU, "coordinates": "xyxy in original image pixels; continuous areas without +1",
                     "method": "Filter same-class pairs at IoU >= 0.5, sort descending IoU, then greedily match one-to-one; ties by ascending GT index then prediction index",
                     "indices": "Zero-based indices into ground_truth.objects and model detections",
                     "counts": "Unmatched predictions are FP, unmatched GT objects are FN; wrong-class spatial overlap never counts as TP",
                     "spatial_only": "Repeat pairing without class filtering and list all IoU >= 0.5 overlap candidates; diagnostic only, excluded from TP/FP/FN"},
        "ground_truth": {"objects": ground_truth, "counts_by_class": counts_by_class(ground_truth)},
        "environment": {"python": sys.version, "torch": torch.__version__, "ultralytics": ultralytics_version,
                        "opencv": cv2.__version__, "gpu": torch.cuda.get_device_name(0)},
        "models": {},
    }
    for label, path in (("yolo11n", n_path), ("yolo11s", s_path)):
        model = YOLO(str(path))
        if model.task != "detect" or model.names != CLASS_NAMES:
            raise ValueError(f"{label}: checkpoint must use the expected four-class detection mapping")
        result = model.predict(image.copy(), **settings)[0]
        if model.predictor.device.type != "cuda":
            raise RuntimeError("Predictor is not on CUDA; refusing fallback")
        report["models"][label] = {"checkpoint": fingerprint(path), **evaluate_predictions(ground_truth, decode_predictions(result))}
        del result, model
        torch.cuda.empty_cache()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"Reference diagnostic saved: {output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Reference diagnostic failed: {exc}", file=sys.stderr)
        sys.exit(1)
