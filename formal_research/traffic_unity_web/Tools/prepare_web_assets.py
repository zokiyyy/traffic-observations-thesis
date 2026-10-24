#!/usr/bin/env python3
"""Create the ten-source browser catalog without modifying the desktop project."""

import hashlib
import json
from pathlib import Path


WEB = Path(__file__).resolve().parents[1]
DESKTOP = WEB.parent / "traffic_unity_demo"
SOURCE_ASSETS = DESKTOP / "Assets/StreamingAssets"
WEB_ASSETS = WEB / "Assets/StreamingAssets"
SOURCE_CATALOG = SOURCE_ASSETS / "five_sources/catalog.json"
WEB_CATALOG = WEB_ASSETS / "five_sources/catalog.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_local_paths(value):
    if isinstance(value, dict):
        return {key: clean_local_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_local_paths(item) for item in value]
    if isinstance(value, str) and value.startswith("/Users/"):
        return ""
    return value


def media_name(entry: dict) -> str:
    timestamp = entry["id"].split("__", 1)[1].removesuffix(".mp4")
    return f"{entry['label'].lower()}_{timestamp}.mp4"


def main():
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == "ten_source_catalog_v1"
    assert len(catalog["entries"]) == 10
    names = set()
    for entry in catalog["entries"]:
        source_video = Path(entry["video_path"])
        if not source_video.is_file():
            raise FileNotFoundError(source_video)
        name = media_name(entry)
        if name in names:
            raise ValueError(f"Duplicate web media name: {name}")
        names.add(name)
        data_path = entry["data_path"]
        original = (SOURCE_ASSETS / data_path).read_bytes()
        if digest(original) != entry["data_sha256"]:
            raise ValueError(f"Desktop analysis hash mismatch: {data_path}")
        data = clean_local_paths(json.loads(original))
        if data["source"]["id"] != entry["id"] or data["source"]["video_sha256"] != entry["video_sha256"]:
            raise ValueError(f"Source identity mismatch: {data_path}")
        data["source"]["video_path"] = "media/" + name
        web_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        output = WEB_ASSETS / data_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(web_bytes)
        entry["data_sha256"] = digest(web_bytes)
        entry["video_path"] = "media/" + name
        for key in ("source_state_path", "source_rules_path"):
            entry[key] = ""
        print(f"{entry['label']}: {name}, analysis {len(web_bytes):,} bytes, video {source_video.stat().st_size:,} bytes")

    WEB_CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Prepared {len(catalog['entries'])} recordings for browser loading")


if __name__ == "__main__":
    main()
