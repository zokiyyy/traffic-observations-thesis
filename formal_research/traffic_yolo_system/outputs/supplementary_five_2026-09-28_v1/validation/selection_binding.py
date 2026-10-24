"""Explicit five-source supplementary development scope; metadata checks precede media access."""
import hashlib
import json
from pathlib import Path
import re

MODEL_SHA = "07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334"
FROZEN_MANIFEST_SHA = "8e1b8806f5a1f2f8280b71169d45ba5837bc710db26f0550610124fcc4f1f3cd"
NEW_SOURCES = {'Bellevue_150th_Newport': 'Bellevue_150th_Newport__2017-09-10_18-08-24.mp4', 'Bellevue_116th_NE12th': 'Bellevue_116th_NE12th__2017-09-11_07-08-32.mp4', 'Bellevue_150th_Eastgate': 'Bellevue_150th_Eastgate__2017-09-11_06-08-30.mp4', 'Bellevue_150th_SE38th': 'Bellevue_150th_SE38th__2017-09-10_19-08-25.mp4', 'Bellevue_Bellevue_NE8th': 'Bellevue_Bellevue_NE8th__2017-09-11_02-08-29.mp4'}
SOURCE_SPLITS = {'Bellevue_150th_Newport__2017-09-10_18-08-24.mp4': 'Validation', 'Bellevue_116th_NE12th__2017-09-11_07-08-32.mp4': 'Train', 'Bellevue_150th_Eastgate__2017-09-11_06-08-30.mp4': 'Validation', 'Bellevue_150th_SE38th__2017-09-10_19-08-25.mp4': 'Validation', 'Bellevue_Bellevue_NE8th__2017-09-11_02-08-29.mp4': 'Validation'}
SOURCE_HASHES = {'Bellevue_150th_Newport__2017-09-10_18-08-24.mp4': '5d7eff08712d5750a018b4fa82be85754d47c777a07754d54b3d54cb2dd3c587', 'Bellevue_116th_NE12th__2017-09-11_07-08-32.mp4': '4dde510ff91c2ec58e1cd06e27cbd923d8976c0156905a4625097653415eca4e', 'Bellevue_150th_Eastgate__2017-09-11_06-08-30.mp4': '5c101e5e3cb6f64e2304f9666337b9b3e3f46330ce5300c474c73f6a94dd6647', 'Bellevue_150th_SE38th__2017-09-10_19-08-25.mp4': 'e0e4d9d1e69c2f8b2ef356bdfb99c0e37baafb96db0645952bab4bc7b40e09c4', 'Bellevue_Bellevue_NE8th__2017-09-11_02-08-29.mp4': 'f4492d61971979f21f7f09a5a90b0ed458ea7c4a9dd6fbe1ae30831c951fa5fb'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def selected_entry(path, source_id):
    # Exact user-authorized supplementary list. Prior five completed sources and
    # every other dataset file are outside this batch.
    if source_id not in NEW_SOURCES.values():
        raise ValueError("Source is outside the five authorized supplementary sources")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Frozen selection requires entries[]")
    matches = [e for e in entries if e.get("source_id") == source_id]
    if len(matches) != 1:
        raise ValueError("Selected source must occur exactly once")
    entry = matches[0]
    if entry.get("split") != SOURCE_SPLITS[source_id] or entry.get("action") != "new":
        raise ValueError("Only exact new Train/Validation entries can be processed; Main Test cannot be read")
    if NEW_SOURCES.get(entry.get("camera_id")) != source_id:
        raise ValueError("Selection source/camera mismatch")
    if not re.fullmatch("[0-9a-f]{64}", str(entry.get("source_sha256", ""))):
        raise ValueError("Selection requires an explicit source SHA256")
    if entry["source_sha256"] != SOURCE_HASHES[source_id]:
        raise ValueError("Selected source SHA256 differs from the frozen screened source")
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
