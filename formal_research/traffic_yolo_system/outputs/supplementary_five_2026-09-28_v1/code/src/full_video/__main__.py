"""Bounded CLI: plan, dry-run, run, retry-failed, status. No Main Test mode."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from .common import write_json, resolve


def main(argv=None):
    # Existing read-only geometry modules are siblings of this package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from .batch import plan, run_one, collect_status
    from .configuration import load_config, prepare, registry_entries
    from .runner import execute
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "dry-run", "run", "retry-failed", "status"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source", action="append", help="Explicit frozen development source filename; repeat for bounded sequential batch")
    parser.add_argument("--limit", type=int, default=1, help="Maximum attempted videos in this call; default 1")
    parser.add_argument("--report", type=Path, help="New JSON report; never overwrite")
    parser.add_argument("--recover-interrupted", action="store_true", help="Recover proven dead local owner, restart entire video; never continue IDs")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.report and args.report.exists():
        parser.error("--report already exists; choose a new path")
    config = load_config(args.config)
    if args.command == "plan":
        cameras = registry_entries(config)
        for entry in cameras.values():
            if entry.get("config_path"):
                entry["config_path"] = str(resolve(Path(config["camera_registry"]).parent, entry["config_path"]))
        result = plan(Path(config["split_manifest"]), Path(config["source_root"]), cameras)
    elif args.command == "status":
        result = collect_status(Path(config["output_root"]))
    else:
        if not args.source:
            parser.error("Explicit --source required; this CLI never starts all 98/115 automatically")
        if len(set(args.source)) != len(args.source):
            parser.error("Duplicate --source")
        if len(args.source) > args.limit:
            parser.error("Selected sources exceed --limit; set an explicit bounded limit")
        rows = []
        for source in args.source:
            try:
                prepared = prepare(args.config, source)
                if args.command == "dry-run":
                    rows.append({"source_id": source, "state": "ready", "fingerprint": prepared["fingerprint"],
                                 "task": prepared["task"], "preflight": prepared["preflight"],
                                 "method_status": prepared["metadata"]["method_status"],
                                 "readiness_scope": "input_preflight_only",
                                 "inference_enabled": prepared["metadata"]["inference_enabled"],
                                 "inference_started": False})
                else:
                    rows.append(run_one(prepared, Path(config["output_root"]), execute,
                        retry_failed=args.command == "retry-failed", recover_interrupted=args.recover_interrupted))
            except Exception as exc:
                rows.append({"source_id": source, "state": "failed", "stage": "prepare_or_execute",
                             "error_type": type(exc).__name__, "error": str(exc)})
        result = {"command": args.command, "rows": rows,
                  "failures": [row for row in rows if row.get("state", row.get("status")) not in ("ready", "completed", "skipped_completed")]}
    if args.report is None and args.command != "status":
        import uuid
        args.report = Path(config["output_root"]) / "batch_reports" / f"{args.command}-{uuid.uuid4().hex}.json"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if isinstance(result, dict) and result.get("failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
