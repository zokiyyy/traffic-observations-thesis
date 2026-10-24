"""Explicit four-source development scope; metadata checks precede media access."""
import hashlib
import json
from pathlib import Path
import re

MODEL_SHA = "07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334"
FROZEN_MANIFEST_SHA = "8e1b8806f5a1f2f8280b71169d45ba5837bc710db26f0550610124fcc4f1f3cd"
NEW_SOURCES = {
    "Bellevue_116th_NE12th": "Bellevue_116th_NE12th__2017-09-10_23-08-29.mp4",
    "Bellevue_150th_Eastgate": "Bellevue_150th_Eastgate__2017-09-11_17-08-33.mp4",
    "Bellevue_150th_SE38th": "Bellevue_150th_SE38th__2017-09-11_14-08-32.mp4",
    "Bellevue_Bellevue_NE8th": "Bellevue_Bellevue_NE8th__2017-09-11_12-08-31.mp4",
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def selected_entry(path, source_id):
    # This list is the user's authorized bounded batch; reuse Newport is never
    # accepted for new processing by these tools, nor are other dataset files.
    if source_id not in NEW_SOURCES.values():
        raise ValueError("Source is outside the four authorized new development sources; Newport is reuse-only")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Frozen selection requires entries[]")
    matches = [e for e in entries if e.get("source_id") == source_id]
    if len(matches) != 1:
        raise ValueError("Selected source must occur exactly once")
    entry = matches[0]
    if entry.get("split") != "Validation" or entry.get("action") != "new":
        raise ValueError("Only explicit new Validation entries can be processed; Main Test cannot be read")
    if NEW_SOURCES.get(entry.get("camera_id")) != source_id:
        raise ValueError("Selection source/camera mismatch")
    if not re.fullmatch("[0-9a-f]{64}", str(entry.get("source_sha256", ""))):
        raise ValueError("Selection requires an explicit source SHA256")
    return entry


def bind_prepared(entry, prepared):
    task, preflight, config, camera = (prepared[k] for k in ("task", "preflight", "config", "camera"))
    for field in ("source_id", "camera_id", "split"):
        if task.get(field) != entry[field]:
            raise ValueError(f"Run differs from selected {field}")
    if preflight.get("input_sha256") != entry["source_sha256"]:
        raise ValueError("Run original source hash differs from selection")
    if config["model"].get("sha256") != MODEL_SHA:
        raise ValueError("Run changed the frozen trained model")
    if camera.get("camera_id") != entry["camera_id"] or task["source_id"] not in camera.get("source_ids", []):
        raise ValueError("Run camera/layout wrapper does not bind the selected source")
    if camera.get("layout_identity", {}).get("camera_id") != entry["camera_id"]:
        raise ValueError("Road geometry has no independently checked matching camera identity")
    return task, config, camera, preflight
