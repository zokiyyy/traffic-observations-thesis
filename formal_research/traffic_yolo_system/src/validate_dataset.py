"""Read-only validation for configured formal YOLO dataset splits."""

from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path

import yaml
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / "configs" / "data.yaml"
CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


def label_directory(image_dir: Path) -> Path:
    """Match YOLO's images -> labels layout, including external split roots."""
    parts = list(image_dir.parts)
    image_components = [index for index, part in enumerate(parts) if part == "images"]
    if not image_components:
        raise ValueError(f"image directory must contain an 'images' component: {image_dir}")
    parts[image_components[-1]] = "labels"
    return Path(*parts)


def validate_split(dataset_root: Path, image_rel: str, split: str) -> list[str]:
    errors: list[str] = []
    image_dir = dataset_root / Path(image_rel).expanduser()
    try:
        label_dir = label_directory(image_dir)
    except ValueError as exc:
        return [f"{split}: {exc}"]
    for kind, directory in (("image", image_dir), ("label", label_dir)):
        if not directory.is_dir():
            errors.append(f"{split}: missing {kind} directory {directory}")
    if errors:
        return errors

    image_paths = sorted(p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    label_paths = sorted(p for p in label_dir.glob("*.txt") if p.is_file())
    images = {p.stem: p for p in image_paths}
    labels = {p.stem: p for p in label_paths}
    if not images:
        errors.append(f"{split}: image directory is empty: {image_dir}")
    if not labels:
        errors.append(f"{split}: label directory is empty: {label_dir}")
    if len(images) != len(image_paths):
        errors.append(f"{split}: duplicate image stems")
    if len(images) != len(labels):
        errors.append(f"{split}: image/label count mismatch ({len(images)}/{len(labels)})")
    missing = sorted(images.keys() - labels.keys())
    extra = sorted(labels.keys() - images.keys())
    errors.extend(f"{split}: missing label {stem}" for stem in missing)
    errors.extend(f"{split}: extra label {stem}" for stem in extra)

    counts = Counter()
    empty = 0
    for label_path in labels.values():
        try:
            lines = label_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(f"{label_path.name}: unreadable label ({exc})")
            continue
        if not any(line.strip() for line in lines):
            empty += 1
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 5:
                errors.append(f"{label_path.name}:{line_number}: expected 5 fields")
                continue
            try:
                class_id = int(fields[0])
                values = [float(value) for value in fields[1:]]
            except ValueError:
                errors.append(f"{label_path.name}:{line_number}: non-numeric value")
                continue
            if class_id not in CLASS_NAMES:
                errors.append(f"{label_path.name}:{line_number}: invalid class {class_id}")
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
                errors.append(f"{label_path.name}:{line_number}: invalid normalized coordinates")
            if values[2] <= 0 or values[3] <= 0:
                errors.append(f"{label_path.name}:{line_number}: non-positive width/height")
            x, y, width, height = values
            # Permit only the small edge error introduced by decimal rounding.
            if (
                x - width / 2 < -1e-6
                or y - height / 2 < -1e-6
                or x + width / 2 > 1 + 1e-6
                or y + height / 2 > 1 + 1e-6
            ):
                errors.append(f"{label_path.name}:{line_number}: box extends beyond image")
            if class_id in CLASS_NAMES:
                counts[CLASS_NAMES[class_id]] += 1
    for image_path in images.values():
        try:
            with Image.open(image_path) as image:
                image.verify()
        except Exception as exc:  # pragma: no cover - exact decoder errors vary
            errors.append(f"{image_path.name}: unreadable image ({exc})")
    print(f"{split}: images={len(images)}, labels={len(labels)}, empty_txt={empty}, objects={sum(counts.values())}")
    print(f"{split} class counts: {dict(counts)}")
    return errors


def main(data_path: Path = DEFAULT_DATA, split: str = "all") -> int:
    data_path = Path(data_path)
    try:
        config = yaml.safe_load(data_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        print(f"Validation: FAIL\n- Cannot read dataset config: {exc}")
        return 1
    if not isinstance(config, dict):
        print("Validation: FAIL\n- Dataset config must be a mapping")
        return 1

    errors: list[str] = []
    names = config.get("names")
    if isinstance(names, list):
        names = dict(enumerate(names))
    if names != CLASS_NAMES:
        errors.append(f"class mapping must be exactly {CLASS_NAMES}; received {names!r}")

    root_value = config.get("path")
    if not isinstance(root_value, str) or not root_value.strip():
        errors.append("dataset 'path' must be a non-empty directory path")
        dataset_root = None
    else:
        dataset_root = Path(root_value).expanduser()
        if not dataset_root.is_absolute():
            dataset_root = data_path.resolve().parent / dataset_root
        if not dataset_root.is_dir():
            errors.append(f"missing dataset root directory {dataset_root}")

    selected = [name for name in SPLITS if name in config] if split == "all" else [split]
    if not selected:
        errors.append("no train, val, or test splits configured")
    for selected_split in selected:
        image_rel = config.get(selected_split)
        if selected_split not in SPLITS:
            errors.append(f"unsupported split {selected_split!r}")
        elif not isinstance(image_rel, str) or not image_rel.strip():
            errors.append(f"{selected_split}: split must specify an image directory")
        elif dataset_root is not None:
            errors.extend(validate_split(dataset_root, image_rel, selected_split))
    if errors:
        print("Validation: FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("Validation: PASS")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--split", choices=(*SPLITS, "all"), default="all")
    args = parser.parse_args()
    raise SystemExit(main(args.data, args.split))
