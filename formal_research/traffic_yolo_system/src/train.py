"""Run the formal YOLO baseline training experiment.

This entry point requires the configured MPS or CUDA device and never silently
falls back to CPU. Check-only mode does not load a model or start training.
"""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .runtime_checks import load_training_config, require_device
else:
    from runtime_checks import load_training_config, require_device


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "training_config.yaml"


def main(config_path: Path = DEFAULT_CONFIG, check_only: bool = False) -> int:
    config = load_training_config(config_path)
    data_path = Path(config["data"]).expanduser()
    model_path = Path(config["model"]).expanduser()
    output_dir = Path(config["project"]).expanduser() / config["experiment_name"]
    training = config["training"]

    device = require_device(training.get("device"))
    if not data_path.is_file():
        raise FileNotFoundError(f"Dataset config not found: {data_path}")
    if not model_path.is_file():
        raise FileNotFoundError(f"Model weights not found: {model_path}")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to reuse existing experiment output: {output_dir}")

    if check_only:
        print(f"Training preflight: PASS (device={device})")
        print(f"Model: {model_path}")
        print(f"Dataset config: {data_path}")
        print(f"New output directory: {output_dir}")
        print("Check only: no model was loaded and no training was started.")
        return 0

    from ultralytics import YOLO

    # The model and all runtime parameters are loaded from the approved YAML.
    model = YOLO(str(model_path))
    results = model.train(
        data=str(data_path),
        project=str(output_dir.parent),
        name=config["experiment_name"],
        **training,
    )
    print(f"Training completed. Output path: {results.save_dir}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check-only", action="store_true", help="Check inputs/device without loading a model or training.")
    args = parser.parse_args()
    raise SystemExit(main(args.config, args.check_only))
