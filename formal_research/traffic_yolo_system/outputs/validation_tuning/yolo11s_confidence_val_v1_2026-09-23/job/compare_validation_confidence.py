"""Compare three fixed detection thresholds on a frozen Validation manifest.

This is an operating-point diagnostic, not training, AP evaluation, tracking or
Main Test evaluation. Predictions are made independently at each threshold.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from check_comparison_reference import (
    CLASS_NAMES, decode_predictions, evaluate_predictions, fingerprint, read_ground_truth,
)


THRESHOLDS = (0.20, 0.25, 0.35)
EXPECTED_COUNTS = {"car": 1621, "motorcycle": 8, "bus": 22, "truck": 85}


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")


def update_status(output, **data):
    path = output / "status.json"
    temporary = output / "status.tmp"
    temporary.write_text(json.dumps({"updated_at_utc": now(), **data}, indent=2) + "\n")
    temporary.replace(path)


def metric_counts(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def aggregate(records):
    """Sum object counts before computing micro metrics; include empty images."""
    totals = Counter()
    classes = {name: Counter() for name in CLASS_NAMES.values()}
    absolute_count_error = signed_count_error = 0
    empty_images = empty_fp = empty_images_with_predictions = 0
    wrong_class_overlap_matches = 0
    for record in records:
        evaluation = record["evaluation"]
        total = evaluation["class_aware_matching"]["total"]
        totals.update(total)
        for name, values in evaluation["class_aware_matching"]["per_class"].items():
            classes[name].update(values)
        n_gt, n_pred = record["gt_count"], len(evaluation["detections"])
        absolute_count_error += abs(n_pred - n_gt)
        signed_count_error += n_pred - n_gt
        if n_gt == 0:
            empty_images += 1
            empty_fp += n_pred
            empty_images_with_predictions += int(n_pred > 0)
        spatial = evaluation["spatial_diagnostic_not_correct_detections"]["class_agnostic_greedy_matching"]
        wrong_class_overlap_matches += sum(not pair["same_class"] for pair in spatial["matches"])
    n = len(records)
    return {
        "images": n,
        "gt_count": totals["tp"] + totals["fn"],
        "prediction_count": totals["tp"] + totals["fp"],
        **metric_counts(totals["tp"], totals["fp"], totals["fn"]),
        "per_class": {
            name: {"gt_count": counts["gt_count"], "prediction_count": counts["prediction_count"],
                   **metric_counts(counts["tp"], counts["fp"], counts["fn"])}
            for name, counts in classes.items()
        },
        "empty_gt_images": empty_images,
        "predictions_on_empty_gt_images": empty_fp,
        "empty_gt_images_with_predictions": empty_images_with_predictions,
        "count_mae_per_image": absolute_count_error / n if n else None,
        "count_mean_signed_error": signed_count_error / n if n else None,
        "wrong_class_spatial_matches_diagnostic_only": wrong_class_overlap_matches,
    }


def summarize(records):
    summary = {"all": aggregate(records)}
    for field in ("lighting", "intersection", "source_video"):
        groups = defaultdict(list)
        for record in records:
            groups[record["metadata"][field]].append(record)
        summary["by_" + field] = {key: aggregate(value) for key, value in sorted(groups.items())}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root, manifest_path, model_path, output = [p.expanduser().resolve() for p in
                                              (args.dataset_root, args.manifest, args.model, args.output)]
    if output.exists():
        raise FileExistsError(f"Output already exists; refusing overwrite: {output}")
    manifest = json.loads(manifest_path.read_text())
    entries = manifest["images"]
    if manifest["split"] != "Validation" or len(entries) != 197:
        raise ValueError("Only the frozen 197-image Validation manifest is permitted")
    if manifest["expected_gt_counts"] != EXPECTED_COUNTS:
        raise ValueError("Unexpected Validation class support")
    if model_path.suffix != ".pt" or fingerprint(model_path)["sha256"] != manifest["model_sha256"]:
        raise ValueError("Checkpoint SHA256 mismatch")
    names = [entry["filename"] for entry in entries]
    if len(set(names)) != 197 or any(Path(name).name != name or "__validation__" not in name for name in names):
        raise ValueError("Duplicate or non-Validation filenames")
    image_dir, label_dir = root / "images/val", root / "labels/val"
    if {p.name for p in image_dir.glob("*.jpg")} != set(names):
        raise ValueError("Validation image inventory differs from manifest")
    if {p.stem for p in label_dir.glob("*.txt")} != {Path(n).stem for n in names}:
        raise ValueError("Validation label inventory differs from manifest")
    for entry in entries:
        if entry["split"] != "Validation":
            raise ValueError("Non-Validation source in manifest")
        image_path, label_path = image_dir / entry["filename"], label_dir / (Path(entry["filename"]).stem + ".txt")
        for path, key in ((image_path, "image_sha256"), (label_path, "label_sha256")):
            if fingerprint(path)["sha256"] != entry[key]:
                raise ValueError(f"Input checksum mismatch: {path.name}")

    import cv2
    import torch
    from ultralytics import YOLO, __version__ as ultralytics_version

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; no CPU fallback")
    output.mkdir(parents=True)
    started = now()
    update_status(output, status="running", started_at_utc=started, completed_images=0)
    try:
        torch.manual_seed(42)
        settings = {"device": 0, "imgsz": 640, "iou": 0.7, "half": False,
                    "augment": False, "rect": True, "batch": 1, "max_det": 300,
                    "agnostic_nms": False, "classes": None, "save": False, "verbose": False}
        config = {
            "created_at_utc": started, "split": "Validation", "image_count": 197,
            "model": fingerprint(model_path), "manifest": fingerprint(manifest_path),
            "scripts": {p.name: fingerprint(p) for p in
                        (Path(__file__).resolve(), Path(__file__).with_name("check_comparison_reference.py"))},
            "thresholds": THRESHOLDS, "predict_settings": settings,
            "matching": {"class_aware": True, "match_iou": 0.5,
                         "method": "Descending-IoU greedy one-to-one matching within each image, same class; ties GT index then prediction index",
                         "averaging": "Object-level micro: sum TP/FP/FN across all 197 images before calculating P/R/F1",
                         "not_ap": "Not AP/mAP and not Ultralytics max-F1 operating point or class-mean P/R",
                         "nms_iou_is_different": "0.7 is prediction NMS; 0.5 is GT matching"},
            "scope": {"training": False, "main_test": False, "tracking": False,
                      "spatial_filter": "None; full-image predictions, existing roadway-only labels remain unchanged",
                      "fp_interpretation": "Unmatched predictions relative to existing labels; includes possible out-of-scope real vehicles, duplicates, class/localization errors"},
            "environment": {"python": sys.version, "torch": torch.__version__, "ultralytics": ultralytics_version,
                            "opencv": cv2.__version__, "gpu": torch.cuda.get_device_name(0)},
        }
        write_json(output / "config.json", config)
        model = YOLO(str(model_path))
        if model.task != "detect" or model.names != CLASS_NAMES:
            raise ValueError("Expected project four-class detection model")
        records = {f"{conf:.2f}": [] for conf in THRESHOLDS}
        gt_counts = Counter()
        start_clock = time.perf_counter()
        with (output / "predictions_and_matches.jsonl").open("x", encoding="utf-8") as handle:
            for index, entry in enumerate(entries):
                image = cv2.imread(str(image_dir / entry["filename"]))
                if image is None:
                    raise ValueError(f"Cannot decode {entry['filename']}")
                height, width = image.shape[:2]
                gt = read_ground_truth(label_dir / (Path(entry["filename"]).stem + ".txt"), width, height)
                actual = Counter(obj["class_name"] for obj in gt)
                if {name: actual[name] for name in CLASS_NAMES.values()} != entry["gt_counts"]:
                    raise ValueError("Per-image ground truth count mismatch")
                gt_counts.update(actual)
                if index == 0:
                    model.predict(image.copy(), conf=0.20, **settings)
                row = {"filename": entry["filename"], "metadata": entry,
                       "width": width, "height": height, "ground_truth": gt, "thresholds": {}}
                # Rotate call order. No timings from this diagnostic are a speed benchmark.
                order = THRESHOLDS[index % 3:] + THRESHOLDS[:index % 3]
                for conf in order:
                    result = model.predict(image.copy(), conf=conf, **settings)[0]
                    if model.predictor.device.type != "cuda":
                        raise RuntimeError("Predictor is not on CUDA")
                    predictions = decode_predictions(result)
                    if any(obj["confidence"] + 1e-7 < conf for obj in predictions):
                        raise RuntimeError("Prediction below requested threshold")
                    evaluation = evaluate_predictions(gt, predictions)
                    key = f"{conf:.2f}"
                    row["thresholds"][key] = evaluation
                    records[key].append({"filename": entry["filename"], "metadata": entry,
                                         "gt_count": len(gt), "evaluation": evaluation})
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                if (index + 1) % 25 == 0 or index + 1 == len(entries):
                    handle.flush()
                    update_status(output, status="running", started_at_utc=started, completed_images=index + 1)
                    print(f"Processed {index + 1}/197 images at all three thresholds", flush=True)
        if dict(gt_counts) != EXPECTED_COUNTS:
            raise ValueError(f"Unexpected final GT counts: {gt_counts}")
        summary = {"status": "completed", "created_at_utc": now(), "split": "Validation",
                   "image_count": 197, "gt_counts": dict(gt_counts), "thresholds": {},
                   "elapsed_seconds": time.perf_counter() - start_clock}
        for key, rows in records.items():
            summary["thresholds"][key] = summarize(rows)
        write_json(output / "summary.json", summary)
        update_status(output, status="completed", started_at_utc=started, completed_images=197,
                      finished_at_utc=now(), returncode=0)
        for key, result in summary["thresholds"].items():
            print(key, json.dumps(result["all"], ensure_ascii=False), flush=True)
    except Exception as exc:
        update_status(output, status="failed", started_at_utc=started, error=str(exc), returncode=1)
        raise


if __name__ == "__main__":
    main()
