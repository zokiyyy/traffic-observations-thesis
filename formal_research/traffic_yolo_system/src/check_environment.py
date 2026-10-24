"""Read-only checks for the formal YOLO training environment."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

import torch
import yaml

if __package__:
    from .runtime_checks import load_training_config, require_device
    from .validate_dataset import IMAGE_SUFFIXES, label_directory
else:
    from runtime_checks import load_training_config, require_device
    from validate_dataset import IMAGE_SUFFIXES, label_directory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "training_config.yaml"


def main(config_path: Path = DEFAULT_CONFIG) -> int:
    print(f"Python: {sys.version}")
    print(f"Interpreter: {sys.executable}")
    print(f"PyTorch: {torch.__version__}")
    try:
        print(f"Ultralytics: {version('ultralytics')}")
        config = load_training_config(config_path)
        device = require_device(config["training"].get("device"))
        print(f"Configured device available: {device}")
        if device == "cuda:0":
            print(f"GPU: {torch.cuda.get_device_name(0)}")
            print(f"PyTorch CUDA build: {torch.version.cuda}")
        data_path = Path(config["data"]).expanduser()
        model_path = Path(config["model"]).expanduser()
        if not model_path.is_file():
            raise FileNotFoundError(f"Model weights not found: {model_path}")
        if not data_path.is_file():
            raise FileNotFoundError(f"Dataset config not found: {data_path}")
        print(f"Model exists: {model_path}")
        print(f"Dataset config exists: {data_path}")
        data = yaml.safe_load(data_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Dataset config must be a mapping.")
        root_value = data.get("path")
        if not isinstance(root_value, str) or not root_value.strip():
            raise ValueError("Dataset 'path' must be a non-empty directory path.")
        dataset_root = Path(root_value).expanduser()
        if not dataset_root.is_absolute():
            dataset_root = data_path.resolve().parent / dataset_root
        if not dataset_root.is_dir():
            raise FileNotFoundError(f"Dataset root not found: {dataset_root}")

        for split in ("train", "val"):
            image_rel = data.get(split)
            if not isinstance(image_rel, str) or not image_rel.strip():
                raise ValueError(f"Dataset '{split}' must specify an image directory.")
            image_dir = dataset_root / Path(image_rel).expanduser()
            label_dir = label_directory(image_dir)
            for directory in (image_dir, label_dir):
                if not directory.is_dir():
                    raise FileNotFoundError(f"{split}: directory not found: {directory}")
            images = [p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
            labels = [p for p in label_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt"]
            print(f"{split}: images={len(images)}, labels={len(labels)}")
            if not images or not labels:
                raise ValueError(f"{split}: image and label directories must not be empty.")
            if len(images) != len(labels) or {p.stem for p in images} != {p.stem for p in labels}:
                raise ValueError(f"{split}: image/label filenames or counts do not match.")
    except (OSError, ValueError, RuntimeError, yaml.YAMLError, ModuleNotFoundError) as exc:
        print(f"Environment check: FAIL\n- {exc}")
        return 1
    print("Environment check: PASS")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    raise SystemExit(main(parser.parse_args().config))
