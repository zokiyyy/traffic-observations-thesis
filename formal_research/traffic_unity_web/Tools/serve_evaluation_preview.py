#!/usr/bin/env python3
"""Preview the evaluation revision while preserving earlier builds."""
import argparse
from pathlib import Path
from http.server import ThreadingHTTPServer
import serve_preview

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    serve_preview.ROOT = Path(__file__).resolve().parents[1] / "Builds/Evaluation_2026-10-09_v4/Web"
    if not (serve_preview.ROOT / "index.html").is_file():
        raise FileNotFoundError("Build the evaluation revision first")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), serve_preview.RangeHandler)
    print(f"Evaluation preview: http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()
