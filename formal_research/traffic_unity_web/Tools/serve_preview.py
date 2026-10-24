#!/usr/bin/env python3
"""Local HTTP preview with byte ranges for seeking in the ten hosted MP4s."""

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "Builds/TrafficWebTen"


class RangeHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_head(self):
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self._range = None
            return super().send_head()
        source = path.open("rb")
        size = path.stat().st_size
        self._range = None
        header = self.headers.get("Range", "")
        if header.startswith("bytes="):
            try:
                part = header[6:].split(",", 1)[0]
                first, last = part.split("-", 1)
                if first:
                    start = int(first)
                    end = min(size - 1, int(last)) if last else size - 1
                else:
                    count = int(last)
                    start, end = max(0, size - count), size - 1
                if start < 0 or start > end or start >= size:
                    raise ValueError
            except (ValueError, IndexError):
                source.close()
                self.send_error(416, "Invalid byte range")
                return None
            self._range = (start, end)
            source.seek(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            length = end - start + 1
        else:
            self.send_response(200)
            length = size
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return source

    def copyfile(self, source, outputfile):
        remaining = self._range[1] - self._range[0] + 1 if self._range else None
        while remaining is None or remaining > 0:
            block = source.read(min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024)
            if not block:
                break
            outputfile.write(block)
            if remaining is not None:
                remaining -= len(block)


def main():
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.debug:
        ROOT = ROOT.parent / "TrafficWebTenDebug"
    if not (ROOT / "index.html").is_file():
        raise FileNotFoundError("Build the Unity Web project first")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RangeHandler)
    print(f"Local preview: http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
