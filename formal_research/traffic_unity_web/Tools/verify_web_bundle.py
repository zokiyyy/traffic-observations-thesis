#!/usr/bin/env python3
"""Check the self-contained web delivery before any external upload."""

import hashlib
import json
from pathlib import Path


WEB = Path(__file__).resolve().parents[1]
ROOT = WEB / "Builds/TrafficWebTen"
CATALOG = ROOT / "StreamingAssets/five_sources/catalog.json"


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    if "function sizeDashboard()" not in html:
        raise ValueError("Responsive browser layout is missing")
    for name in ("TrafficWebTen.loader.js", "TrafficWebTen.framework.js", "TrafficWebTen.wasm", "TrafficWebTen.data"):
        if not (ROOT / "Build" / name).is_file():
            raise FileNotFoundError(name)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = catalog["entries"]
    if catalog["schema_version"] != "ten_source_catalog_v1" or len(entries) != 10:
        raise ValueError("The web bundle must contain ten recordings")
    ids = set()
    total_video = 0
    records = []
    for entry in entries:
        if entry["id"] in ids:
            raise ValueError("Duplicate source identity")
        ids.add(entry["id"])
        data_file = ROOT / "StreamingAssets" / entry["data_path"]
        if hash_file(data_file) != entry["data_sha256"]:
            raise ValueError("Analysis SHA256 mismatch: " + entry["id"])
        raw = data_file.read_bytes()
        if b"/Users/" in raw:
            raise ValueError("Local desktop path leaked into the web analysis")
        data = json.loads(raw)
        source = data["source"]
        if source["id"] != entry["id"] or source["video_sha256"] != entry["video_sha256"]:
            raise ValueError("Analysis/video identity mismatch")
        boxes = source.get("detection_boxes_path")
        if boxes and hash_file(ROOT / "StreamingAssets" / boxes) != source["detection_boxes_sha256"]:
            raise ValueError("Detection box SHA256 mismatch")
        video_file = ROOT / entry["video_path"]
        if video_file.is_symlink() or hash_file(video_file) != entry["video_sha256"]:
            raise ValueError("Video missing, linked, or SHA256 mismatch: " + entry["id"])
        total_video += video_file.stat().st_size
        records.append({"camera": entry["label"], "source_id": entry["id"],
                        "video_bytes": video_file.stat().st_size, "analysis_bytes": len(raw),
                        "has_detection_cache": bool(boxes)})
    for path in (ROOT / "StreamingAssets").rglob("*.json"):
        if b"/Users/" in path.read_bytes():
            raise ValueError("Local desktop path leaked into " + str(path))
    result = {"bundle_ready_for_upload": True,
              "recordings": records, "recording_count": len(records),
              "total_video_bytes": total_video, "external_url_created": False}
    output = WEB / "Builds/web_bundle_verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(records)} recordings and {total_video / 1024**3:.2f} GiB of copied videos")
    print(output)


if __name__ == "__main__":
    main()
