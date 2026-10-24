"""Small, read-only checks shared by the training entry points."""

from __future__ import annotations

from pathlib import Path

import torch
import yaml


def load_training_config(config_path: Path) -> dict:
    if not config_path.is_file():
        raise FileNotFoundError(f"Training config not found: {config_path}")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Training config must be a mapping.")
    for key in ("model", "data", "project", "experiment_name"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"Training config '{key}' must be a non-empty string.")
    if not isinstance(config.get("training"), dict):
        raise ValueError("Training config 'training' must be a mapping.")
    name = config["experiment_name"]
    if name in (".", "..") or Path(name).name != name:
        raise ValueError("experiment_name must be a single directory name.")
    return config


def require_device(device: object) -> str:
    """Accept the original MPS device or CUDA device 0, with no CPU fallback."""
    if device == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS is unavailable; refusing to fall back to CPU.")
        return "mps"
    if (type(device) is int and device == 0) or (type(device) is str and device == "0"):
        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            raise RuntimeError("CUDA device 0 is unavailable; refusing to fall back to CPU.")
        return "cuda:0"
    raise ValueError(f"Unsupported training.device {device!r}; use 'mps', 0, or '0'.")
