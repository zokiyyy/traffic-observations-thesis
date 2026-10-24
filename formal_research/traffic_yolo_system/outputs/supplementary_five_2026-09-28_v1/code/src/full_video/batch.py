"""Bounded development-only scheduling, immutable attempts and restart recovery.

No detector/video dependency is imported here. Completion means a callback returned
successfully and every recorded output still has its recorded bytes; it is not a
claim about traffic accuracy. Retries always restart the configured interval from
its beginning with new tracking state. Mid-video continuation is not supported.
"""
from __future__ import annotations

import csv
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import traceback
from typing import Any, Callable, Iterator, Mapping
import uuid


FROZEN_MEMBERSHIP_SHA256 = "ba81120ffb9df3b33961433bee8efd27ed9616493bca229f5cd8899beb03442b"
SPLIT_COUNTS = {"Train": 81, "Validation": 17, "Main Test": 17}
DEVELOPMENT_SPLITS = frozenset(("Train", "Validation"))
CONTROL_FILES = frozenset(("status.json", "run.log.jsonl", "completion.json", "failure.json"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def atomic_json(path: Path, value: Any) -> None:
    """Atomic replacement of this module's own state, with no partial JSON reads."""
    path = Path(path)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, default=_jsonable, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError(f"Expected object in {path}")
    return result


def _safe_source(source_id: str) -> str:
    if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.mp4", source_id):
        raise ValueError("source_id must be an exact, safe manifest .mp4 filename")
    return source_id


def list_development(manifest: Path) -> list[dict[str, Any]]:
    """Read only frozen CSV metadata; never open any Main Test media or labels."""
    with Path(manifest).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    identity = []
    tasks = []
    for row in rows:
        name = _safe_source(row.get("Source Video Filename", ""))
        if name in seen:
            raise ValueError(f"Duplicate source in manifest: {name}")
        seen.add(name)
        split = row.get("Formal Split")
        if split not in SPLIT_COUNTS:
            raise ValueError(f"Unknown split for {name}: {split}")
        if row.get("Approval Status") != "Approved and Frozen" or row.get("Split Version") != "Formal Split v1":
            raise ValueError(f"Manifest source is not the approved frozen v1: {name}")
        camera = row.get("Intersection", "")
        if not camera or not name.startswith(camera + "__"):
            raise ValueError(f"Source/camera mismatch: {name}")
        duration = row.get("Duration", "")
        if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", duration):
            raise ValueError(f"Invalid manifest duration: {name}")
        hours, minutes, seconds = map(int, duration.split(":"))
        duration_s = hours * 3600 + minutes * 60 + seconds
        if minutes >= 60 or seconds >= 60 or duration_s <= 0:
            raise ValueError(f"Invalid manifest duration: {name}")
        counts[split] += 1
        identity.append((name, camera, split))
        if split in DEVELOPMENT_SPLITS:
            tasks.append({"source_id": name, "split": split, "camera_id": camera,
                          "manifest_duration_s": duration_s, "split_version": "Formal Split v1"})
    if dict(counts) != SPLIT_COUNTS:
        raise ValueError(f"Frozen split count mismatch: {dict(counts)}; expected {SPLIT_COUNTS}")
    digest = hashlib.sha256(json.dumps(sorted(identity), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if digest != FROZEN_MEMBERSHIP_SHA256:
        raise ValueError("Frozen source membership mismatch; split reassignment is not allowed")
    return tasks


def plan(manifest: Path, source_root: Path, cameras: Mapping[str, Any] | None = None,
         source_root_layout: str = "camera") -> dict[str, Any]:
    """Inventory all 98 development sources using exact paths, without video reads.

    `cameras` maps camera_id to an absolute configuration path, registry entry
    containing config_path, or camera configuration object. Relative registry
    paths must be resolved by the caller. Only configuration JSON is read.
    """
    if source_root_layout not in ("camera", "flat"):
        raise ValueError("source_root_layout must be camera or flat")
    cameras = cameras or {}
    result = []
    for task in list_development(manifest):
        source = Path(source_root)
        if source_root_layout == "camera":
            source /= task["camera_id"]
        source /= task["source_id"]
        config = cameras.get(task["camera_id"])
        config_path = config.get("config_path") if isinstance(config, dict) else config
        direct_config = isinstance(config, dict) and "source_ids" in config and "camera_id" in config
        config_exists = direct_config or bool(config_path) and Path(config_path).is_file()
        camera = config if direct_config else None
        config_error = None
        if config_exists and camera is None:
            try:
                camera = _read_json(Path(config_path))
            except (OSError, ValueError) as error:
                config_error = str(error)
        blockers = []
        input_exists = source.is_file()
        if not input_exists:
            blockers.append("input_missing")
        if not config_exists:
            blockers.append("camera_configuration_missing")
        elif config_error:
            blockers.append("camera_configuration_unreadable")
        elif camera.get("camera_id") != task["camera_id"] or task["source_id"] not in camera.get("source_ids", []):
            blockers.append("camera_source_not_in_scope")
        else:
            intervals = camera.get("allowed_source_intervals_seconds", [])
            if not any(isinstance(pair, list) and len(pair) == 2 and
                       all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in pair) and
                       pair[0] <= 0 and pair[1] >= task["manifest_duration_s"] for pair in intervals):
                blockers.append("full_video_camera_scope_unverified")
        result.append({**task, "input_path": str(source), "input_exists": input_exists,
                       "camera_configuration": config, "status": "ready_for_preflight" if not blockers else "blocked",
                       "declared_camera_intervals_seconds": (camera or {}).get("allowed_source_intervals_seconds", []),
                       "configuration_error": config_error, "blockers": blockers})
    return {"schema_version": "full_video_batch_plan_v1", "created_at": _now(),
            "mode": "development_metadata_only", "source_count": len(result), "split_counts": SPLIT_COUNTS,
            "main_test_media_accessed": False, "task_count_by_status": dict(Counter(x["status"] for x in result)),
            "tasks": result}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_files(attempt: Path) -> dict[str, Path]:
    result = {}
    for path in attempt.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Output symlinks are not supported: {path}")
        if path.is_file():
            name = path.relative_to(attempt).as_posix()
            if name not in CONTROL_FILES:
                result[name] = path
    return result


def _receipt(attempt: Path, fingerprint: str, source_id: str) -> dict[str, Any]:
    return {"schema_version": "full_video_completion_v1", "fingerprint": fingerprint,
            "source_id": source_id, "attempt": attempt.name, "created_at": _now(),
            "files": {name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
                      for name, path in sorted(_payload_files(attempt).items())}}


def verify_completion(attempt: Path, fingerprint: str, source_id: str) -> tuple[bool, str]:
    """Stream hashes on skip, detecting truncated, missing and additional outputs."""
    try:
        status = _read_json(attempt / "status.json")
        receipt = _read_json(attempt / "completion.json")
        if status.get("status") != "completed" or status.get("fingerprint") != fingerprint:
            return False, "completion_status_mismatch"
        if (receipt.get("fingerprint") != fingerprint or receipt.get("source_id") != source_id
                or receipt.get("attempt") != attempt.name):
            return False, "completion_identity_mismatch"
        files = _payload_files(attempt)
        expected = receipt.get("files")
        if not isinstance(expected, dict) or not expected or set(files) != set(expected):
            return False, "output_file_set_mismatch"
        for name, path in sorted(files.items()):
            if path.stat().st_size != expected[name]["bytes"] or sha256_file(path) != expected[name]["sha256"]:
                return False, f"output_integrity_mismatch:{name}"
        return True, "verified"
    except (OSError, ValueError, KeyError, TypeError) as error:
        return False, f"completion_unreadable:{type(error).__name__}:{error}"


@contextmanager
def _guard(directory: Path) -> Iterator[None]:
    """Short local POSIX mutex serializes lease creation/recovery (never inference)."""
    with (directory / ".lease.guard").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return True  # unknown owner cannot be proven dead
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _event(attempt: Path, event: str, **details: Any) -> None:
    with (attempt / "run.log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": _now(), "event": event, **details}, ensure_ascii=False, default=_jsonable) + "\n")


def _attempts(directory: Path) -> list[Path]:
    return sorted((p for p in directory.iterdir() if p.is_dir() and re.fullmatch(r"attempt-\d{6}", p.name)), key=lambda p: p.name)


def run_one(prepared: dict[str, Any], output_root: Path,
            execute: Callable[[dict[str, Any], Path], dict[str, Any]], *,
            retry_failed: bool = False, recover_interrupted: bool = False) -> dict[str, Any]:
    """Run/skip exactly one prepared development task, never overwrite an attempt.

    The preparation callback must validate source/config/time identity before this
    call. A failure is returned as data and retained on disk. Dead local owners
    require explicit recovery; foreign-host or live owners are never displaced.
    """
    task = prepared["task"]
    source_id = _safe_source(task["source_id"])
    if task.get("split") not in DEVELOPMENT_SPLITS:
        raise ValueError("Only Train and Validation tasks may execute; Main Test is isolated")
    fingerprint = prepared.get("fingerprint", "")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("prepared.fingerprint must be a full lowercase SHA256")
    run_version = prepared.get("run_version", prepared.get("config", {}).get("run_version"))
    directory = Path(output_root) / source_id / fingerprint
    directory.mkdir(parents=True, exist_ok=True)
    lease_path = directory / "lease.json"
    outcome = {"source_id": source_id, "split": task["split"], "fingerprint": fingerprint,
               "run_version": run_version, "configuration_directory": str(directory)}
    nonce = uuid.uuid4().hex
    recovered = False
    with _guard(directory):
        if lease_path.exists():
            lease = _read_json(lease_path)
            owner_dead = lease.get("host") == socket.gethostname() and not _pid_alive(lease.get("pid"))
            if not recover_interrupted or not owner_dead:
                return {**outcome, "status": "blocked_running", "lease": lease,
                        "reason": "Explicit recovery is required for a proven-dead local owner; live/foreign/unknown owners cannot be recovered."}
            old_name = lease.get("attempt", "")
            if re.fullmatch(r"attempt-\d{6}", old_name) and (directory / old_name).is_dir():
                old_attempt = directory / old_name
                old_status = _read_json(old_attempt / "status.json") if (old_attempt / "status.json").is_file() else {}
                # A crash after completed status but before lease removal can still
                # be verified and skipped below; don't invalidate its good output.
                if old_status.get("status") != "completed":
                    old_status.update(status="interrupted", ended_at=_now(), recovered_by_pid=os.getpid())
                    atomic_json(old_attempt / "status.json", old_status)
                    _event(old_attempt, "interrupted_owner_recovered", previous_owner=lease,
                           restart_policy="whole_configured_interval_new_tracking_state")
            lease_path.unlink()
            recovered = True
        attempts = _attempts(directory)
        if attempts:
            latest = attempts[-1]
            try:
                previous = _read_json(latest / "status.json")
            except (OSError, ValueError):
                previous = {"status": "interrupted"}
            if previous.get("status") == "completed":
                valid, reason = verify_completion(latest, fingerprint, source_id)
                if valid:
                    summary = _read_json(latest / "result_summary.json")
                    # A process can exit after the attempt completed but before
                    # updating this parent index. Repair it under the same guard,
                    # using the verified attempt's original completion times;
                    # neither stale parent state nor recovery time is authoritative.
                    completed_state = {**previous, **outcome, "status": "completed", "attempt": latest.name}
                    atomic_json(directory / "status.json", completed_state)
                    return {**outcome, "status": "skipped_completed", "attempt_directory": str(latest),
                            "integrity": "verified", "summary": summary}
                if not retry_failed:
                    return {**outcome, "status": "invalid_complete", "attempt_directory": str(latest), "reason": reason}
            elif previous.get("status") in ("running", "interrupted"):
                if not (recover_interrupted and (recovered or previous.get("status") == "interrupted")):
                    return {**outcome, "status": "blocked_interrupted", "attempt_directory": str(latest),
                            "reason": "Explicit interrupted-task recovery is required"}
            elif not retry_failed:
                return {**outcome, "status": "blocked_failed", "attempt_directory": str(latest),
                        "reason": "Use retry_failed to restart this video from the configured interval beginning"}
        number = int(attempts[-1].name.split("-")[-1]) + 1 if attempts else 1
        if number > 999999:
            raise ValueError("Attempt limit exceeded")
        attempt = directory / f"attempt-{number:06d}"
        attempt.mkdir()
        lease = {"host": socket.gethostname(), "pid": os.getpid(), "nonce": nonce,
                 "attempt": attempt.name, "started_at": _now(), "fingerprint": fingerprint}
        atomic_json(lease_path, lease)
        state = {**outcome, "status": "running", "attempt": attempt.name, "started_at": lease["started_at"],
                 "restart_policy": "whole_configured_interval_new_tracking_state", "tracking_continuity_across_attempts": False}
        atomic_json(attempt / "status.json", state)
        atomic_json(directory / "status.json", state)
    try:
        atomic_json(attempt / "prepared.json", prepared)
        _event(attempt, "started", restart_policy=state["restart_policy"])
        summary = execute(prepared, attempt)
        if not isinstance(summary, dict):
            raise TypeError("execute must return a JSON-serializable summary dict")
        if summary.get("status", "completed") != "completed":
            raise ValueError("execute returned a non-completed summary; this attempt cannot be completed")
        atomic_json(attempt / "result_summary.json", summary)
        atomic_json(attempt / "completion.json", _receipt(attempt, fingerprint, source_id))
        state.update(status="completed", ended_at=_now())
        atomic_json(attempt / "status.json", state)
        atomic_json(directory / "status.json", state)
        _event(attempt, "completed")
        return {**outcome, "status": "completed", "attempt_directory": str(attempt), "summary": summary}
    except BaseException as error:
        failure = {**outcome, "status": "failed", "attempt_directory": str(attempt), "at": _now(),
                   "error_type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc(),
                   "restart_policy": "whole_configured_interval_new_tracking_state"}
        atomic_json(attempt / "failure.json", failure)
        state.update(status="failed", ended_at=_now(), error_type=type(error).__name__, error=str(error))
        atomic_json(attempt / "status.json", state)
        atomic_json(directory / "status.json", state)
        _event(attempt, "failed", error_type=type(error).__name__, error=str(error))
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return failure
    finally:
        with _guard(directory):
            if lease_path.exists() and _read_json(lease_path).get("nonce") == nonce:
                lease_path.unlink()


def batch_run(config_path: Path, source_ids: list[str], output_root: Path,
              prepare: Callable[[Path, str], dict[str, Any]],
              execute: Callable[[dict[str, Any], Path], dict[str, Any]], *, limit: int = 1,
              retry_failed: bool = False, recover_interrupted: bool = False,
              dry_run: bool = False) -> dict[str, Any]:
    """Sequential, explicitly bounded dispatch; dry-run never calls execute."""
    if not source_ids or len(set(source_ids)) != len(source_ids):
        raise ValueError("Provide explicit, nonempty, unique development source_ids")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 98:
        raise ValueError("limit must be an explicit integer in [1, 98] (default 1)")
    if len(source_ids) > limit:
        raise ValueError(f"Selected {len(source_ids)} sources exceeds limit={limit}; no task was started")
    results = []
    for source_id in source_ids:
        _safe_source(source_id)
        try:
            prepared = prepare(Path(config_path), source_id)
            if prepared.get("task", {}).get("source_id") != source_id:
                raise ValueError("Prepared task source does not match selected source")
            if prepared.get("task", {}).get("split") not in DEVELOPMENT_SPLITS:
                raise ValueError("Only Train/Validation sources are allowed")
            if dry_run:
                results.append({"source_id": source_id, "status": "preflight_passed", "prepared": prepared})
            else:
                results.append(run_one(prepared, output_root, execute,
                                       retry_failed=retry_failed, recover_interrupted=recover_interrupted))
        except Exception as error:
            results.append({"source_id": source_id, "status": "preflight_failed", "error_type": type(error).__name__, "error": str(error)})
    failures = [r for r in results if r["status"] not in ("completed", "skipped_completed", "preflight_passed")]
    return {"schema_version": "full_video_batch_report_v1", "created_at": _now(), "dry_run": dry_run,
            "selection_count": len(source_ids), "execution_limit": limit, "results": results,
            "failures": failures, "counts": dict(Counter(r["status"] for r in results)),
            "retry_policy": "whole_configured_interval_new_tracking_state"}


def write_report(report: dict[str, Any], directory: Path) -> Path:
    """Write a unique report and explicit failure list without replacing older ones."""
    target = Path(directory) / ("report-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12])
    target.mkdir(parents=True, exist_ok=False)
    atomic_json(target / "report.json", report)
    atomic_json(target / "failures.json", {"failures": report.get("failures", [])})
    return target


def collect_status(output_root: Path) -> dict[str, Any]:
    """Inspect only this runner's own output layout; no source data is traversed."""
    tasks = []
    for path in sorted(Path(output_root).glob("*.mp4/*/status.json")):
        if re.fullmatch(r"[a-f0-9]{64}", path.parent.name):
            try:
                tasks.append(_read_json(path))
            except (OSError, ValueError) as error:
                tasks.append({"status": "status_unreadable", "path": str(path), "error": str(error)})
    return {"schema_version": "full_video_status_v1", "created_at": _now(), "tasks": tasks,
            "counts": dict(Counter(t.get("status", "unknown") for t in tasks))}
