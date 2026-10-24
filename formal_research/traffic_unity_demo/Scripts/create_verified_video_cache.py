#!/usr/bin/env python3
"""Fully verify catalogue MP4 files and seed the Unity fast-loading cache."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "Assets" / "StreamingAssets" / "five_sources"
CATALOG = SOURCE_DIR / "catalog.json"
OUTPUT = SOURCE_DIR / "verified_videos.json"
UNIX_TO_DOTNET_TICKS = 621355968000000000


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=1024 * 1024) as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    catalog = json.loads(CATALOG.read_text())
    records = []
    for source in catalog["entries"]:
        video = Path(source["video_path"]).resolve(strict=True)
        before = video.stat()
        actual = digest_file(video)
        after = video.stat()
        if actual != source["video_sha256"]:
            raise RuntimeError(f"{source['label']}: MP4 SHA256 does not match the catalog")
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"{source['label']}: MP4 changed while hashing")
        records.append({
            "path": str(video),
            "sha256": actual,
            "length": after.st_size,
            "last_write_utc_ticks": UNIX_TO_DOTNET_TICKS + after.st_mtime_ns // 100,
            "creation_utc_ticks": 0,
        })
        print(f"Verified {source['label']}: {after.st_size:,} bytes")
    OUTPUT.write_text(json.dumps({"schema_version": "verified_video_cache_v1", "entries": records}, indent=2) + "\n")
    print(f"Saved {OUTPUT}")


if __name__ == "__main__":
    main()
