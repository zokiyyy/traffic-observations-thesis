"""Run one complete attempt, publishing only after verified EOF and closure."""
from __future__ import annotations

import platform
import resource
import time
from pathlib import Path

from .common import sha256, write_json
from .configuration import code_fingerprints


def check_unchanged(prepared):
    for path, digest in prepared["input_hashes"].items():
        if sha256(path) != digest:
            raise ValueError(f"Input changed after preflight: {path}; re-prepare this task")
    if code_fingerprints() != prepared["implementation_hashes"]:
        raise ValueError("Implementation changed during this attempt; re-prepare/restart, no mixed-version completion")


def execute(prepared, attempt_dir):
    from .streaming import StreamWriter
    from .batch import atomic_json
    config, camera, task, preflight = (prepared[k] for k in ("config", "camera", "task", "preflight"))
    if config["input"]["kind"] == "video" and config.get("inference_enabled", True) is False:
        raise ValueError("INFERENCE_DISABLED: preparation-only candidate; integrate and verify the final method in a new version before enabling video inference")
    check_unchanged(prepared)
    write_json(Path(attempt_dir) / "provenance.json", prepared)
    started = time.perf_counter()
    if config["input"]["kind"] == "pilot_cache":
        from .cache import iter_cache as frames
    else:
        from .video import iter_video as frames
    writer = StreamWriter(Path(attempt_dir), prepared["metadata"], camera["layout"],
        preflight["source_start_seconds"], preflight["source_end_seconds"], **config["aggregation"])
    processed = 0
    try:
        for row in frames(config, task, camera, preflight):
            writer.add(row)
            processed += 1
            if processed % config.get("progress_every_frames", 300) == 0:
                atomic_json(Path(attempt_dir) / "progress.json", {
                    "processed_frames": processed, "expected_frames": preflight["frame_count"],
                    "elapsed_seconds": time.perf_counter()-started})
        if processed != preflight["frame_count"]:
            raise ValueError("Incomplete decoded sequence; this attempt is failed, not successful zero traffic")
        summary = writer.finish()
    except BaseException:
        writer.close()
        raise
    check_unchanged(prepared)
    elapsed = time.perf_counter()-started
    memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary["resource_measurement"] = {
        "elapsed_seconds_including_writes_and_final_integrity_check": elapsed,
        "frames_per_second_this_mode_only": processed/elapsed,
        "peak_process_rss_bytes": memory if platform.system() == "Darwin" else memory*1024,
        "rss_scope": "process lifetime high water mark; run isolated process for per-video measure",
        "mode": config["input"]["kind"], "output_bytes_before_receipt": sum(p.stat().st_size for p in Path(attempt_dir).rglob("*") if p.is_file()),
        "not_full_video_or_gpu_benchmark": config["input"]["kind"] == "pilot_cache",
    }
    write_json(Path(attempt_dir) / "run_summary.json", summary)
    return summary
