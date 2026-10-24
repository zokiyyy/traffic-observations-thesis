#!/usr/bin/env python3
"""Link ten verified MP4s for local preview, or copy them for deployment."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


WEB = Path(__file__).resolve().parents[1]
DESKTOP_CATALOG = WEB.parent / "traffic_unity_demo/Assets/StreamingAssets/five_sources/catalog.json"
WEB_CATALOG = WEB / "Assets/StreamingAssets/five_sources/catalog.json"
OUTPUT = WEB / "Builds/TrafficWebTen/media"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--copy", action="store_true", help="Copy all 9.23 GiB into the deployment folder")
    args = parser.parse_args()
    desktop = json.loads(DESKTOP_CATALOG.read_text(encoding="utf-8"))["entries"]
    web = json.loads(WEB_CATALOG.read_text(encoding="utf-8"))["entries"]
    if len(desktop) != 10 or len(web) != 10:
        raise ValueError("Expected ten recordings in both catalogs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for local, hosted in zip(desktop, web):
        if local["id"] != hosted["id"] or local["video_sha256"] != hosted["video_sha256"]:
            raise ValueError("Catalog source identity mismatch")
        source = Path(local["video_path"])
        if not source.is_file() or sha256(source) != local["video_sha256"]:
            raise ValueError(f"Source video is missing or does not match its saved SHA256: {local['id']}")
        relative = Path(hosted["video_path"])
        if len(relative.parts) != 2 or relative.parts[0] != "media" or relative.suffix.lower() != ".mp4":
            raise ValueError("Invalid hosted video path")
        target = OUTPUT / relative.name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            if sha256(target) == local["video_sha256"]:
                print("Verified existing", target.name)
                continue
            raise ValueError(f"Refusing to replace mismatched media: {target}")
        if args.copy:
            shutil.copyfile(source, target)
            if sha256(target) != local["video_sha256"]:
                raise ValueError(f"Copied video failed SHA256 verification: {target}")
        else:
            target.symlink_to(source)
        print(("Copied" if args.copy else "Linked"), target.name)
    print("Ten exact source videos staged as a self-contained copy." if args.copy else
          "Ten exact source videos linked for local preview. Use --copy for a self-contained upload.")


if __name__ == "__main__":
    main()
