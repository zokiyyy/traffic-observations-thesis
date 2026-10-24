"""Run a frozen, standalone detector evaluation on the 184-image Main Test.

Required JSON config: model, expected_model_sha256, data, dataset_manifest,
output_dir, device. Paths are relative to the config file unless absolute.
The manifest has an ``entries`` list containing image_path, label_path,
image_sha256 and label_sha256. Entry paths are relative to the manifest.
Optional expected_data_sha256 and expected_manifest_sha256 pin both inputs.

This measures the detector using Ultralytics' AP evaluation, without tracking,
ROI filtering or application-specific suppression. P/R are class-mean values
at the AP evaluator's selected operating point, not fixed-.35 micro metrics.
No training is performed. Existing outputs are never reused.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback


CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
EXPECTED_COUNTS = [1946, 4, 13, 89]
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
DEFAULT_EVALUATION = {
    "imgsz": 640, "batch": 8, "conf": 0.001, "iou": 0.7,
    "max_det": 300, "augment": False, "half": False,
    "single_cls": False, "agnostic_nms": False, "rect": True,
    "split": "test", "save_json": False, "plots": True,
    "workers": 0, "save_txt": True, "save_conf": True,
    "verbose": True, "quantize": None, "seed": 42, "deterministic": True,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a non-empty path string")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def plain(value):
    """Convert metric scalars/arrays without silently replacing invalid values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite number in evaluation result")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "tolist"):
        return plain(value.tolist())
    if hasattr(value, "item"):
        return plain(value.item())
    raise TypeError(f"Cannot serialize result type {type(value).__name__}")


def write_json(path: Path, value, *, replace: bool = False) -> None:
    text = json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if replace:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    else:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)


def pin(path: Path, expected: str | None, label: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")
    actual = sha256(path)
    if expected is not None and actual != expected.lower():
        raise ValueError(f"SHA256 mismatch for {label}: {path}")
    return actual


def inspect_inputs(config: dict, config_path: Path) -> dict:
    import yaml
    from PIL import Image

    base = config_path.parent
    model = resolve(config["model"], base)
    data = resolve(config["data"], base)
    manifest_path = resolve(config["dataset_manifest"], base)
    model_sha = pin(model, config["expected_model_sha256"], "model")
    data_sha = pin(data, config.get("expected_data_sha256"), "dataset YAML")
    manifest_sha = pin(manifest_path, config.get("expected_manifest_sha256"), "manifest")
    dataset = yaml.safe_load(data.read_text(encoding="utf-8"))
    names = dataset.get("names")
    if isinstance(names, list):
        names = dict(enumerate(names))
    if names != CLASS_NAMES:
        raise ValueError(f"Wrong dataset class mapping: {names!r}")
    root = resolve(dataset["path"], data.parent)
    images_dir = resolve(dataset["test"], root)
    parts = list(images_dir.parts)
    if "images" not in parts:
        raise ValueError("Test image directory must use an images/labels layout")
    parts[len(parts) - 1 - parts[::-1].index("images")] = "labels"
    labels_dir = Path(*parts)
    actual_images = {p.resolve() for p in images_dir.rglob("*")
                     if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES}
    actual_labels = {p.resolve() for p in labels_dir.rglob("*.txt") if p.is_file()}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["entries"] if isinstance(manifest, dict) else manifest
    if not isinstance(entries, list) or len(entries) != 184:
        raise ValueError("Manifest must contain exactly 184 image/label entries")
    seen_images, seen_labels, image_hashes, source_ids = set(), set(), set(), set()
    counts = Counter()
    empty = 0
    records = []
    for entry in entries:
        if entry.get("split", "Main Test") not in {"Main Test", "test", "main_test"}:
            raise ValueError("Manifest includes a non-Main-Test entry")
        image_path = resolve(entry["image_path"], manifest_path.parent)
        label_path = resolve(entry["label_path"], manifest_path.parent)
        if image_path in seen_images or label_path in seen_labels:
            raise ValueError("Duplicate image or label in manifest")
        if image_path.stem != label_path.stem:
            raise ValueError("Image and label stems differ")
        image_sha = pin(image_path, entry["image_sha256"], "test image")
        label_sha = pin(label_path, entry["label_sha256"], "test label")
        if image_sha in image_hashes:
            raise ValueError("Duplicate test image content")
        image_hashes.add(image_sha)
        seen_images.add(image_path)
        seen_labels.add(label_path)
        source_id = entry.get("source_id", entry.get("source_video"))
        if source_id:
            source_ids.add(source_id)
        with Image.open(image_path) as image:
            image.verify()
        lines = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        empty += not lines
        for line in lines:
            fields = line.split()
            if len(fields) != 5:
                raise ValueError(f"Invalid YOLO label in {label_path}")
            cls_value, x, y, width, height = map(float, fields)
            values = [cls_value, x, y, width, height]
            if not all(math.isfinite(v) for v in values):
                raise ValueError(f"Non-finite label in {label_path}")
            cls = int(cls_value)
            if cls != cls_value or cls not in CLASS_NAMES:
                raise ValueError(f"Invalid class in {label_path}")
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1):
                raise ValueError(f"Invalid box dimensions in {label_path}")
            if min(x - width / 2, y - height / 2) < -1e-6 or max(x + width / 2, y + height / 2) > 1 + 1e-6:
                raise ValueError(f"Box beyond image in {label_path}")
            counts[cls] += 1
        record = {"image_path": str(image_path), "label_path": str(label_path),
                  "image_sha256": image_sha, "label_sha256": label_sha}
        for kind in ("image", "label"):
            if entry.get(f"original_{kind}_path"):
                original = resolve(entry[f"original_{kind}_path"], manifest_path.parent)
                pin(original, record[f"{kind}_sha256"], f"original {kind}")
                record[f"original_{kind}_path"] = str(original)
        records.append(record)
    if seen_images != actual_images or seen_labels != actual_labels:
        raise ValueError("Manifest does not exactly match test image/label inventories")
    if [counts[i] for i in CLASS_NAMES] != EXPECTED_COUNTS or empty != 23:
        raise ValueError(f"Unexpected test support: {dict(counts)}, empty={empty}")
    # Check original train/val image content directly; source-level disjointness
    # can additionally be supplied by the frozen manifest's source inventories.
    split_audit = {}
    for split in ("train", "val"):
        split_dir = resolve(dataset[split], root)
        paths = [p for p in split_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
        if not paths:
            raise ValueError(f"Missing/empty {split} image inventory")
        overlap = [str(p) for p in paths if p.resolve() in seen_images or sha256(p) in image_hashes]
        if overlap:
            raise ValueError(f"Test image leakage against {split}: {overlap[:3]}")
        supplied_sources = manifest.get(f"{split}_source_ids") if isinstance(manifest, dict) else None
        if supplied_sources is not None and set(supplied_sources) & source_ids:
            raise ValueError(f"Test source leakage against {split}")
        split_audit[split] = {"images_checked": len(paths), "test_content_overlap": 0,
                              "source_overlap_checked": supplied_sources is not None and bool(source_ids)}
    return {"model": str(model), "model_sha256": model_sha, "data": str(data),
            "data_sha256": data_sha, "manifest": str(manifest_path), "manifest_sha256": manifest_sha,
            "images": 184, "objects": 2052, "empty_labels": empty,
            "class_counts": {CLASS_NAMES[i]: counts[i] for i in CLASS_NAMES},
            "source_count": len(source_ids), "disjointness": split_audit,
            "externally_supplied_source_video_overlap": manifest.get("source_video_overlap") if isinstance(manifest, dict) else None,
            "files": records}


def verify_unchanged(audit: dict) -> None:
    for key, hash_key in (("model", "model_sha256"), ("data", "data_sha256"), ("manifest", "manifest_sha256")):
        pin(Path(audit[key]), audit[hash_key], key)
    for entry in audit["files"]:
        for key in ("image", "label"):
            pin(Path(entry[f"{key}_path"]), entry[f"{key}_sha256"], f"test {key}")
            if entry.get(f"original_{key}_path"):
                pin(Path(entry[f"original_{key}_path"]), entry[f"{key}_sha256"], f"original test {key}")


def validate_device(torch, device):
    if device == "cpu":
        return "cpu"
    if device == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS unavailable; no device fallback")
        return "mps"
    if device in (0, "0", "cuda", "cuda:0"):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; no device fallback")
        return 0
    raise ValueError("device must explicitly be cpu, mps, 0 or cuda:0")


def summarize(metrics) -> dict:
    box = metrics.box
    indices = [int(value) for value in plain(box.ap_class_index)]
    if set(indices) != set(CLASS_NAMES):
        raise ValueError(f"Expected AP for all four supported classes, got {indices}")
    per_class = []
    for row, cls in enumerate(indices):
        precision, recall, ap50, ap = box.class_result(row)
        p, r = float(precision), float(recall)
        per_class.append({"class_id": cls, "class_name": CLASS_NAMES[cls],
                          "ground_truth_objects": EXPECTED_COUNTS[cls],
                          "precision_at_evaluator_operating_point": p,
                          "recall_at_evaluator_operating_point": r,
                          "f1_at_evaluator_operating_point": 2 * p * r / (p + r) if p + r else 0.0,
                          "ap50": float(ap50), "ap50_95": float(ap),
                          "ap_by_iou_50_to_95": plain(box.all_ap[row])})
    return {"overall": {"precision_class_mean_at_evaluator_operating_point": float(box.mp),
                         "recall_class_mean_at_evaluator_operating_point": float(box.mr),
                         "map50": float(box.map50), "map50_95": float(box.map)},
            "per_class": per_class, "native_results_dict": plain(metrics.results_dict),
            "speed_ms_per_image": plain(metrics.speed),
            "precision_recall_definition": "Ultralytics AP evaluator class-mean P/R at its selected operating point; not fixed-confidence micro P/R.",
            "scope": "Full-image detector versus existing roadway-only annotations; no ROI/tracking/application suppression. Unmatched predictions may include out-of-annotation-scope real vehicles.",
            "inference_confidence_floor": 0.001,
            "not_evaluated": ["tracking", "region counts", "traffic-state accuracy", "fixed-conf 0.35 micro metrics"]}


def main(config_path: Path, check_only: bool = False) -> int:
    config_path = config_path.expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for required in ("model", "expected_model_sha256", "data", "dataset_manifest", "output_dir", "device"):
        if required not in config:
            raise ValueError(f"Missing config field: {required}")
    output = resolve(config["output_dir"], config_path.parent)
    if output.exists():
        raise FileExistsError(f"Refusing to reuse output directory: {output}")
    evaluation = dict(DEFAULT_EVALUATION)
    overrides = config.get("evaluation", {})
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_EVALUATION):
        raise ValueError("Unknown evaluation configuration keys")
    evaluation.update({key: config[key] for key in DEFAULT_EVALUATION if key in config})
    evaluation.update(overrides)
    fixed = {key: value for key, value in DEFAULT_EVALUATION.items() if key not in {"batch", "workers", "verbose"}}
    if any(evaluation[key] != value for key, value in fixed.items()):
        raise ValueError("Frozen AP protocol differs from the required detector settings")
    if type(evaluation["batch"]) is not int or evaluation["batch"] < 1:
        raise ValueError("batch must be a positive integer")
    if type(evaluation["workers"]) is not int or evaluation["workers"] < 0:
        raise ValueError("workers must be a nonnegative integer")
    started, start_clock = now(), time.perf_counter()
    if not check_only:
        output.mkdir(parents=True, exist_ok=False)
        shutil.copy2(config_path, output / "requested_config.json")
        write_json(output / "status.json", {"state": "preflight", "started_at_utc": started})
    audit = None
    try:
        audit = inspect_inputs(config, config_path)
        import torch
        import ultralytics
        if config.get("expected_ultralytics_version", ultralytics.__version__) != ultralytics.__version__:
            raise RuntimeError("Ultralytics version differs from frozen protocol")
        if config.get("expected_torch_version", torch.__version__.split("+")[0]) != torch.__version__.split("+")[0]:
            raise RuntimeError("PyTorch version differs from frozen protocol")
        num_threads = config.get("num_threads", 4)
        if type(num_threads) is not int or num_threads < 1:
            raise ValueError("num_threads must be a positive integer")
        torch.set_num_threads(num_threads)
        device = validate_device(torch, config["device"])
        environment = {"python": sys.version, "platform": platform.platform(),
                       "torch": torch.__version__, "ultralytics": ultralytics.__version__,
                       "cuda_runtime": torch.version.cuda, "requested_device": config["device"],
                       "device": device, "torch_num_threads": torch.get_num_threads(),
                       "gpu": torch.cuda.get_device_name(0) if device == 0 else None}
        if check_only:
            print(json.dumps({"check_only": True, "status": "PASS", "environment": environment,
                              "images": audit["images"], "objects": audit["objects"],
                              "model_sha256": audit["model_sha256"]}, indent=2))
            return 0
        write_json(output / "input_audit.json", audit)
        write_json(output / "environment.json", environment)
        write_json(output / "runner_identity.json", {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))})
        # Stage a private test copy so Ultralytics' label cache is written only
        # inside this evaluation output, never alongside the original dataset.
        import yaml
        staged = output / "dataset_snapshot"
        for kind in ("images", "labels"):
            (staged / kind / "test").mkdir(parents=True)
        for entry in audit["files"]:
            for key, folder in (("image", "images"), ("label", "labels")):
                source = Path(entry[f"{key}_path"])
                target = staged / folder / "test" / source.name
                if target.exists():
                    raise ValueError(f"Duplicate basename while staging: {target.name}")
                shutil.copy2(source, target)
                pin(target, entry[f"{key}_sha256"], "staged input")
        staged_data = yaml.safe_load(Path(audit["data"]).read_text(encoding="utf-8"))
        original_root = resolve(staged_data["path"], Path(audit["data"]).parent)
        for split in ("train", "val"):
            staged_data[split] = str(resolve(staged_data[split], original_root))
        staged_data.update({"path": str(staged), "test": "images/test"})
        staged_yaml = staged / "data.yaml"
        staged_yaml.write_text(yaml.safe_dump(staged_data, sort_keys=False), encoding="utf-8")
        kwargs = {**evaluation, "data": str(staged_yaml), "device": device,
                  "project": str(output), "name": "raw", "exist_ok": False}
        write_json(output / "requested_evaluation_args.json", kwargs)
        write_json(output / "status.json", {"state": "running", "started_at_utc": started}, replace=True)
        model = ultralytics.YOLO(audit["model"])
        if model.task != "detect" or model.names != CLASS_NAMES:
            raise ValueError("Checkpoint is not the expected four-class detector")
        captured = {}

        def capture_validator(validator):
            captured["validator"] = validator
            # Ultralytics may reset CPU thread count while selecting a device.
            torch.set_num_threads(num_threads)
            write_json(output / "effective_evaluation_args.json", vars(validator.args), replace=True)

        model.add_callback("on_val_start", capture_validator)
        model.add_callback("on_val_end", capture_validator)
        metrics = model.val(**kwargs)
        if Path(metrics.save_dir).resolve() != (output / "raw").resolve():
            raise ValueError(f"Unexpected native output directory: {metrics.save_dir}")
        validator = captured["validator"]
        if validator.seen != 184:
            raise ValueError(f"Evaluator processed {validator.seen} images, expected 184")
        loaded_dataset = validator.dataloader.dataset
        expected_basenames = {Path(entry["image_path"]).name for entry in audit["files"]}
        actual_basenames = {Path(path).name for path in loaded_dataset.im_files}
        if len(loaded_dataset) != 184 or actual_basenames != expected_basenames:
            raise ValueError("Evaluator loaded image inventory differs from frozen manifest")
        support = plain(metrics.nt_per_class)
        if support != EXPECTED_COUNTS:
            raise ValueError(f"Evaluator support differs from frozen labels: {support}")
        write_json(output / "effective_runtime.json", {
            "device": str(validator.device), "quantize": validator.args.quantize,
            "torch_num_threads": torch.get_num_threads(), "seen": validator.seen,
            "dataset_length": len(loaded_dataset), "image_inventory_matches_manifest": True,
            "dataloader_batch_size": validator.dataloader.batch_size,
            "dataset_rect": loaded_dataset.rect, "batch_shapes": loaded_dataset.batch_shapes,
            "nt_per_class": metrics.nt_per_class,
            "confusion_matrix": {"prediction_confidence_floor": evaluation["conf"],
                                 "matching_iou": 0.45,
                                 "note": "Native diagnostic matrix; matching differs from AP IoU .50:.95 and from a fixed-.35 operational evaluation."}})
        write_json(output / "metrics_arrays.json", {
            "all_ap": metrics.box.all_ap, "ap_class_index": metrics.box.ap_class_index,
            "p": metrics.box.p, "r": metrics.box.r, "f1": metrics.box.f1,
            "nt_per_class": metrics.nt_per_class, "nt_per_image": metrics.nt_per_image,
            "iou_thresholds": validator.iouv, "seen": validator.seen,
            "note": "Unrounded native metric arrays for aggregate verification; exported prediction TXT coordinates/confidences are rounded."})
        result = summarize(metrics)
        verify_unchanged(audit)
        result.update({"started_at_utc": started, "finished_at_utc": now(),
                       "elapsed_seconds": time.perf_counter() - start_clock,
                       "model_sha256": audit["model_sha256"], "dataset_manifest_sha256": audit["manifest_sha256"],
                       "split": "test", "images": 184, "objects": 2052, "empty_labels": 23,
                       "native_output": str(metrics.save_dir), "original_inputs_unchanged": True,
                       "prediction_exports": "Native raw/labels/*.txt: class cx cy w h confidence. Images with zero retained predictions may have no TXT; this is not a complete empty-image manifest."})
        write_json(output / "metrics.json", result)
        write_json(output / "status.json", {"state": "completed", "started_at_utc": started,
                                            "finished_at_utc": result["finished_at_utc"],
                                            "metrics": str(output / "metrics.json")}, replace=True)
        print(json.dumps({"status": "completed", "output": str(output), **result["overall"]}, indent=2))
        return 0
    except BaseException as exc:
        failure = {"state": "failed", "started_at_utc": started, "finished_at_utc": now(),
                   "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}
        if audit is not None:
            try:
                verify_unchanged(audit)
                failure["original_inputs_unchanged"] = True
            except Exception as integrity_error:
                failure["original_inputs_unchanged"] = False
                failure["integrity_error"] = str(integrity_error)
        if not check_only:
            write_json(output / "status.json", failure, replace=True)
        print(json.dumps(failure, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="Verify frozen inputs and device; do not load a model or create outputs.")
    args = parser.parse_args()
    raise SystemExit(main(args.config, args.check_only))
