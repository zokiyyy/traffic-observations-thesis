"""Read independently audited presentation intervals without assuming CFR.

The sidecar is evidence for this exact original source, not a replacement video.
Only original Train/Validation inputs are supported. Integer PTS and durations are
the time authority; average FPS is retained solely as descriptive metadata.
Missing intervals, overlaps, unknown tails and excessively held frames fail
closed. The live backend must still compare every decoded frame with these PTS.
This module uses only the standard library and never opens model checkpoints.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import time


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stat(path):
    stat = Path(path).stat()
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _int(value, name, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    return value


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _fraction(value, name):
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} must be a positive rational") from error
    if result <= 0:
        raise ValueError(f"{name} must be a positive rational")
    return result


def _equal_seconds(value, expected, name):
    if abs(_number(value, name) - float(expected)) > 1e-7:
        raise ValueError(f"{name} disagrees with original integer PTS")


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"{name} must be an explicit SHA256")
    return value


def _load(path):
    with Path(path).open(encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError(f"Audit JSON must be an object: {path}")
    return result


def _scope(config, task, camera):
    # The data isolation guard precedes even inspection of evidence paths.
    if task.get("split") not in {"Train", "Validation"}:
        raise ValueError("Only Train/Validation sources are permitted; Main Test cannot be opened")
    if config.get("input", {}).get("kind") != "video":
        raise ValueError("Audited PTS requires input.kind=video")
    if not isinstance(task.get("source_id"), str) or not task["source_id"] or task.get("camera_id") != camera.get("camera_id"):
        raise ValueError("Task source/camera must match its explicit camera configuration")
    if not isinstance(camera.get("source_ids"), list) or task["source_id"] not in camera["source_ids"]:
        raise ValueError("Source is not explicitly listed in camera.source_ids")
    if config.get("time", {}).get("mode") != "audited_pts":
        raise ValueError("Explicit time.mode=audited_pts is required")
    if _number(config["input"].get("source_offset_seconds", 0), "input.source_offset_seconds") != 0:
        raise ValueError("Audited PTS supports only original full sources with source_offset_seconds=0")
    segment = config.get("segment")
    if segment is not None:
        if not isinstance(segment, dict) or set(segment) != {"start_seconds", "end_seconds"}:
            raise ValueError("Without explicit start and end bounds, audited PTS accepts full sources only (segment=null)")
        start = _number(segment["start_seconds"], "segment.start_seconds")
        end = _number(segment["end_seconds"], "segment.end_seconds")
        if not 0 <= start < end:
            raise ValueError("PTS segment requires 0 <= start_seconds < end_seconds")
    if "fps" in config["input"]:
        raise ValueError("Remove input.fps for audited_pts; integer PTS, not an asserted FPS, determine time")


def _evidence_files(summary, summary_path):
    """Verify the immutable report/metadata/logs wrapped by this audit version."""
    envelope = summary.get("evidence_envelope", {})
    entries = envelope.get("files", {})
    required = {"audit_report.json", "prepare_time_audit.executed.py", "source_metadata.json",
                "ffprobe_frames.stderr.log", "decoder_differences.jsonl", "pts_summary.json", "decoder_summary.json"}
    if not isinstance(entries, dict) or required - entries.keys():
        raise ValueError("Audit evidence envelope must bind the original report, executed helper, metadata and full decoder logs")
    verified = {}
    for name in sorted(required):
        entry = entries[name]
        path = Path(entry.get("path", "")).expanduser().resolve()
        # This schema's evidence bundle consists only of these named local text
        # files; evidence cannot redirect reads into arbitrary dataset media.
        if path.parent != summary_path.parent or path.name != name:
            raise ValueError(f"Audit evidence path must be {name} beside the audit envelope")
        expected = _sha(entry.get("sha256"), f"{name} SHA256")
        before = _stat(path)
        if _hash(path) != expected or _stat(path) != before:
            raise ValueError(f"Audit evidence hash mismatch or file changed: {name}")
        if name in {"ffprobe_frames.stderr.log", "decoder_differences.jsonl"} and before["size_bytes"] != 0:
            raise ValueError(f"Audit evidence contains decode errors or mismatches: {name}")
        verified[str(path)] = {"sha256": expected, "stat": before}
    original = _load(summary_path.parent / "audit_report.json")
    for key in ("schema_version", "task", "source", "source_sha256", "source_size_bytes", "source_mtime_ns",
                "manifest_sha256", "no_inference", "original_source_stat_unchanged", "ended_at"):
        if summary.get(key) != original.get(key):
            raise ValueError(f"Audit envelope contradicts original report: {key}")
    for group, filename in (("pts", "pts_summary.json"), ("decoder", "decoder_summary.json")):
        historical = original.get(group)
        if not isinstance(historical, dict) or historical != _load(summary_path.parent / filename):
            raise ValueError(f"Original {group} evidence disagrees with standalone summary")
        if any(summary.get(group, {}).get(key) != value for key, value in historical.items()):
            raise ValueError(f"Audit envelope contradicts original {group} evidence")
    metadata = _load(summary_path.parent / "source_metadata.json")
    streams = [row for row in metadata.get("streams", []) if row.get("codec_type") == "video"]
    if len(streams) != 1 or streams[0] != summary.get("stream"):
        raise ValueError("Audit envelope stream differs from the single original video stream")
    return verified


def _timing_rows(path, expected, checked=None):
    """Validate while streaming; EOF checks happen only after all rows are read."""
    tick = Fraction(expected["time_base_num"], expected["time_base_den"])
    duration_limit = Fraction(str(expected["max_frame_duration_seconds"]))
    first = previous = previous_end = last_duration = None
    count, maximum = 0, 0
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"Blank PTS sidecar row at line {line_number}; incomplete evidence")
            try:
                row = json.loads(line)
            except ValueError as error:
                raise ValueError(f"Malformed PTS sidecar row at line {line_number}") from error
            if not isinstance(row, dict):
                raise ValueError(f"PTS sidecar line {line_number} must be an object")
            if _int(row.get("frame_index"), "frame_index", 0) != count:
                raise ValueError(f"Missing/duplicate frame_index at PTS sidecar row {count}")
            pts = _int(row.get("pts"), f"Frame {count} original pts")
            duration = _int(row.get("duration", row.get("pkt_duration")), f"Frame {count} duration", 1)
            if "duration" in row and "pkt_duration" in row and row["duration"] != row["pkt_duration"]:
                raise ValueError(f"Conflicting duration fields at frame {count}")
            if duration * tick > duration_limit:
                raise ValueError(f"Frame {count} exceeds explicit max_frame_duration_seconds; long holds are not silently valid coverage")
            if first is None:
                first = pts
            if previous is not None:
                if pts <= previous:
                    raise ValueError(f"Frame {count} has duplicate/backward PTS")
                if pts != previous_end:
                    raise ValueError(f"Frame {count} has a PTS interval gap/overlap; missing-time support is required, no filling")
            if (_int(row.get("width"), "frame width", 1), _int(row.get("height"), "frame height", 1)) != (expected["width"], expected["height"]):
                raise ValueError(f"PTS sidecar dimensions mismatch at frame {count}")
            normalized = (pts - first) * tick
            for key, value in (("source_seconds", normalized), ("original_pts_seconds", pts * tick),
                               ("duration_seconds", duration * tick)):
                if key in row:
                    _equal_seconds(row[key], value, f"Frame {count} {key}")
            if "pts_delta_ticks" in row and row["pts_delta_ticks"] != (None if previous is None else pts - previous):
                raise ValueError(f"PTS delta evidence differs at frame {count}")
            previous, previous_end, last_duration = pts, pts + duration, duration
            maximum = max(maximum, duration)
            count += 1
            if count > expected["input_frame_count"]:
                raise ValueError("PTS sidecar has extra frames beyond audited count")
            yield {"frame_index": count - 1, "source_timestamp_seconds": float(normalized),
                   "timestamp_seconds": float(normalized), "duration_seconds": float(duration * tick),
                   "pts": pts, "duration_ticks": duration, "original_pts_seconds": float(pts * tick)}
    if not count or count != expected["input_frame_count"]:
        raise ValueError("PTS sidecar frame count differs from completed full audit")
    if (first, previous, last_duration) != (expected["input_first_pts_ticks"], expected["input_last_pts_ticks"], expected["final_frame_duration_ticks"]):
        raise ValueError("PTS sidecar first/last/final duration differs from completed audit")
    if previous_end - first != expected["input_duration_ticks"]:
        raise ValueError("PTS sidecar duration differs from complete source coverage")
    if checked is not None:
        checked.update({"maximum_frame_duration_ticks": maximum,
                        "maximum_frame_duration_seconds": float(maximum * tick),
                        "pts_audit_frames": count})


def preflight_pts(config: dict, task: dict, camera: dict) -> dict:
    """Validate exact source/evidence hashes and all intervals, without decoding.

    Evidence schema is ``full_source_time_preparation_v1`` with explicit full
    ffprobe EOF and sequential OpenCV EOF/comparison evidence. The existing
    one-off audit helper may wrap its immutable original report in an envelope;
    no timestamp or completion may be inferred from nominal duration or FPS.
    """
    _scope(config, task, camera)
    started = time.perf_counter()
    time_config = config["time"]
    maximum_duration = _number(time_config.get("max_frame_duration_seconds"), "time.max_frame_duration_seconds")
    if maximum_duration <= 0:
        raise ValueError("time.max_frame_duration_seconds must be positive and explicitly reviewed")
    for field in ("audit_summary_path", "frame_timing_path"):
        if not isinstance(time_config.get(field), str) or not time_config[field]:
            raise ValueError(f"time.{field} is required for audited_pts")
    source = Path(task["input_path"]).expanduser().resolve()
    summary_path = Path(time_config["audit_summary_path"]).expanduser().resolve()
    timing_path = Path(time_config["frame_timing_path"]).expanduser().resolve()
    paths = (source, summary_path, timing_path)
    if len(set(paths)) != 3:
        raise ValueError("Source, audit summary and timing sidecar must be distinct files")
    stats = {str(path): _stat(path) for path in paths}
    summary = _load(summary_path)
    if summary.get("schema_version") != "full_source_time_preparation_v1" or summary.get("status") != "completed":
        raise ValueError("A completed full_source_time_preparation_v1 audit is required")
    if summary.get("no_inference") is not True or summary.get("original_source_stat_unchanged") is not True:
        raise ValueError("Audit must be independent of inference and confirm an unchanged original source")
    identity = summary.get("task", {})
    if any(identity.get(key) != task.get(key) for key in ("source_id", "split", "camera_id")):
        raise ValueError("Audit source/split/camera identity differs from the frozen development task")
    if summary.get("source") != str(source):
        raise ValueError("Audit original source path differs from the configured source")
    if summary.get("source_size_bytes") != stats[str(source)]["size_bytes"]:
        raise ValueError("Source size differs from audit evidence")
    if config.get("split_manifest_sha256") is not None and summary.get("manifest_sha256") != config["split_manifest_sha256"]:
        raise ValueError("Audit frozen split manifest SHA256 differs from current configuration")
    source_sha = _hash(source)
    if source_sha != _sha(summary.get("source_sha256"), "audit.source_sha256"):
        raise ValueError("Source SHA256 differs from completed audit")
    if config["input"].get("sha256", source_sha) != source_sha:
        raise ValueError("Input SHA256 differs from input.sha256")
    evidence_files = _evidence_files(summary, summary_path)
    pts = summary.get("pts", {})
    decoder = summary.get("decoder", {})
    stream = summary.get("stream", {})
    if pts.get("ffprobe_eof_verified") is not True or _int(pts.get("ffprobe_error_count"), "ffprobe_error_count", 0) != 0:
        raise ValueError("ffprobe full EOF and zero decode errors must be established")
    if decoder.get("eof_verified") is not True or decoder.get("all_frames_match") is not True or decoder.get("matches_ffprobe_frame_count") is not True:
        raise ValueError("Independent full sequential decoder EOF/comparison must be complete")
    if decoder.get("backend") != "FFMPEG" or not isinstance(decoder.get("opencv_version"), str) or not decoder["opencv_version"].strip():
        raise ValueError("Audit must identify the FFMPEG backend and explicit OpenCV version")
    decoder_threads = _int(decoder.get("requested_decoder_threads"), "decoder.requested_decoder_threads", 1)
    frame_count = _int(pts.get("frame_count"), "audited frame_count", 1)
    if _int(decoder.get("decoded_frames"), "decoder.decoded_frames", 1) != frame_count:
        raise ValueError("Decoder frame count differs from original PTS count")
    for field in ("timestamp_mismatch_frames", "dimension_mismatch_frames", "frame_index_mismatch_frames", "nonmonotonic_timestamp_frames"):
        if _int(decoder.get(field), f"decoder.{field}", 0) != 0:
            raise ValueError(f"Independent decoder evidence has nonzero {field}")
    timing_sha = _hash(timing_path)
    if timing_sha != _sha(pts.get("timing_frames_sha256"), "pts.timing_frames_sha256"):
        raise ValueError("Frame timing SHA256 differs from completed audit")
    if decoder.get("source_sha256") != source_sha or decoder.get("timing_frames_sha256") != timing_sha:
        raise ValueError("Decoder evidence is not bound to this exact source and PTS sidecar SHA256")
    if stream.get("codec_type") != "video":
        raise ValueError("Audit requires an explicit video stream")
    rotations = [stream.get("tags", {}).get("rotate", 0)] + [row.get("rotation", 0) for row in stream.get("side_data_list", [])]
    if any(float(value) % 360 != 0 for value in rotations):
        raise ValueError("Rotated input is unsupported; ROI coordinates must match decoder output")
    dimensions = (_int(stream.get("width"), "stream.width", 1), _int(stream.get("height"), "stream.height", 1))
    if dimensions != (camera.get("layout", {}).get("frame_width"), camera.get("layout", {}).get("frame_height")):
        raise ValueError("Audited input dimensions differ from camera ROI dimensions")
    if (decoder.get("reported_width"), decoder.get("reported_height")) != dimensions:
        raise ValueError("Decoder dimensions differ from original stream dimensions")
    tick = _fraction(pts.get("time_base"), "pts.time_base")
    if tick != _fraction(stream.get("time_base"), "stream.time_base"):
        raise ValueError("Stream and audited PTS time bases differ")
    tolerance = _number(decoder.get("timestamp_tolerance_seconds"), "decoder timestamp tolerance")
    error = _number(decoder.get("timestamp_difference_max_absolute_seconds"), "decoder maximum timestamp error")
    if not 0 <= error <= tolerance <= max(float(tick), 1e-6) + 1e-12:
        raise ValueError("Decoder timestamp correspondence exceeds one tick; unsupported decoder time mode")
    first = _int(pts.get("first_pts_ticks"), "first PTS")
    last = _int(pts.get("last_pts_ticks"), "last PTS")
    tail = _int(pts.get("final_frame_duration_ticks"), "Final frame duration", 1)
    duration_ticks = last + tail - first
    if duration_ticks <= 0:
        raise ValueError("Audited presentation duration must be positive")
    _equal_seconds(pts.get("normalized_presented_duration_seconds"), duration_ticks * tick, "Audited full duration")
    _equal_seconds(decoder.get("first_source_seconds"), 0, "Decoder first source time")
    _equal_seconds(decoder.get("last_source_seconds"), (last - first) * tick, "Decoder final source time")
    # Some real files report a container duration ending at the last PTS rather
    # than its presentation interval end. Accept only these two exact tick
    # conventions, and record which was demonstrated. Never clip or invent the
    # final duration to make the two values match.
    container_ticks = _int(stream.get("duration_ts"), "stream.duration_ts", 1)
    if _int(stream.get("start_pts"), "stream.start_pts") != first or container_ticks not in {duration_ticks, last - first}:
        raise ValueError("Audited PTS coverage differs from complete stream start/duration ticks")
    # nb_frames describes container metadata, not necessarily presented frames.
    # The independent complete ffprobe/OpenCV audit and complete sidecar are
    # authoritative. Their EOF, decode, timestamp and coverage checks above and
    # below remain mandatory even when the container count differs.
    raw_declared_count = stream.get("nb_frames")
    if type(raw_declared_count) is int:
        declared_count = raw_declared_count
    elif isinstance(raw_declared_count, str) and re.fullmatch(r"[0-9]+", raw_declared_count):
        declared_count = int(raw_declared_count)
    else:
        raise ValueError("Explicit positive integer stream frame count metadata is required")
    if declared_count <= 0:
        raise ValueError("Explicit positive integer stream frame count metadata is required")
    count_delta = declared_count - frame_count
    fps = _fraction(stream.get("avg_frame_rate"), "metadata average frame rate")
    result = {
        "source_id": task["source_id"], "split": task["split"], "camera_id": task["camera_id"],
        "input_path": str(source), "input_sha256": source_sha, "input_stat": stats[str(source)],
        "input_frame_count": frame_count, "frame_count": frame_count,
        "container_declared_frame_count": declared_count,
        "audited_frame_count": frame_count,
        "container_minus_audited_frame_count": count_delta,
        "container_frame_count_matches_audit": count_delta == 0,
        "frame_count_authority": "complete original-PTS ffprobe audit, independently matched by sequential OpenCV decode through EOF; never container nb_frames",
        "frame_count_metadata_warning": (
            "Container nb_frames differs from independently audited presented frames; the discrepancy is retained and no frame or duration is inserted or removed."
            if count_delta else None),
        "first_frame_index": 0, "end_frame_index_exclusive": frame_count,
        "input_source_offset_seconds": 0.0, "source_start_seconds": 0.0,
        "source_end_seconds": float(duration_ticks * tick), "input_duration_seconds": float(duration_ticks * tick),
        "input_duration_ticks": duration_ticks, "input_first_pts_ticks": first, "input_last_pts_ticks": last,
        "input_first_pts_seconds": float(first * tick), "input_last_pts_seconds": float(last * tick),
        "final_frame_duration_ticks": tail, "width": dimensions[0], "height": dimensions[1],
        "container_duration_ticks": container_ticks, "container_duration_seconds": float(container_ticks * tick),
        "container_duration_semantics": "full_presentation_coverage" if container_ticks == duration_ticks else "ends_at_final_pts_excluding_its_reported_duration",
        "presentation_tail_beyond_container_seconds": float((duration_ticks - container_ticks) * tick),
        "fps": float(fps), "fps_num": fps.numerator, "fps_den": fps.denominator,
        "fps_role": "container average metadata only; never timestamp or duration authority",
        "time_base_num": tick.numerator, "time_base_den": tick.denominator,
        "time_mode": "audited_pts", "time_semantics": "(original integer PTS - first PTS) * time_base; original per-frame durations retained exactly",
        "max_frame_duration_seconds": maximum_duration, "decoder_timestamp_tolerance_seconds": tolerance,
        "audit_summary_path": str(summary_path), "audit_summary_sha256": _hash(summary_path),
        "audit_summary_stat": stats[str(summary_path)], "frame_timing_path": str(timing_path),
        "frame_timing_sha256": timing_sha, "frame_timing_stat": stats[str(timing_path)],
        "pts_backend_sha256": _hash(__file__), "audited_evidence_files": evidence_files,
        "audited_decoder": {"backend": "FFMPEG", "opencv_version": decoder["opencv_version"],
                            "requested_decoder_threads": decoder_threads},
    }
    # Always validate the complete evidence, including a segment's unused tail.
    # Segment bounds refer to exact original-source PTS boundaries. Decimal
    # floating point representations may differ by <= 1e-7 s, but never by a
    # significant fraction of a tick. Neither CFR rounding nor partial-frame
    # cropping is accepted.
    segment = config.get("segment")
    bound_ticks = None
    boundary_indices = {}
    if segment is not None:
        bound_ticks = {}
        for key, value in segment.items():
            exact = Fraction(str(value)) / tick
            ticks = round(exact)
            tolerance = min(Fraction(1, 10_000_000), tick / 4)
            if abs(Fraction(str(value)) - ticks * tick) > tolerance:
                raise ValueError(f"segment.{key} must align to an exact original PTS boundary")
            if not 0 <= ticks <= duration_ticks:
                raise ValueError(f"segment.{key} lies outside the audited source")
            bound_ticks[key] = ticks
    for row in _timing_rows(timing_path, result, result):
        if bound_ticks is not None:
            for key, ticks in bound_ticks.items():
                if row["pts"] - first == ticks:
                    boundary_indices[key] = row["frame_index"]
    if bound_ticks is not None:
        if bound_ticks["end_seconds"] == duration_ticks:
            boundary_indices["end_seconds"] = frame_count
        if set(boundary_indices) != {"start_seconds", "end_seconds"}:
            raise ValueError("PTS segment bounds must coincide with original frame boundaries; no interpolation or CFR cropping")
        start_index, end_index = boundary_indices["start_seconds"], boundary_indices["end_seconds"]
        if start_index >= end_index:
            raise ValueError("Selected original-PTS segment must contain at least one frame")
        result.update({
            "first_frame_index": start_index, "end_frame_index_exclusive": end_index,
            "frame_count": end_index - start_index,
            "source_start_seconds": float(bound_ticks["start_seconds"] * tick),
            "source_end_seconds": float(bound_ticks["end_seconds"] * tick),
            "segment_start_ticks_from_first_pts": bound_ticks["start_seconds"],
            "segment_end_ticks_from_first_pts": bound_ticks["end_seconds"],
            "segment_selection": "exact_original_pts_boundaries_after_full_sidecar_validation",
        })
    if any(_stat(path) != stats[str(path)] for path in paths):
        raise ValueError("Source or PTS audit evidence changed during preflight")
    if any(_stat(path) != value["stat"] for path, value in evidence_files.items()):
        raise ValueError("Independent audit evidence changed during preflight")
    result["preflight_elapsed_seconds"] = time.perf_counter() - started
    return result


def iter_pts(preflight: dict):
    """Stream audited times; caller must exhaust to enforce count/tail checks."""
    if preflight.get("split") not in {"Train", "Validation"}:
        raise ValueError("Only Train/Validation sources are permitted; Main Test cannot be opened")
    if preflight.get("time_mode") != "audited_pts":
        raise ValueError("A successful audited_pts preflight is required")
    for prefix in ("audit_summary", "frame_timing"):
        path = Path(preflight[f"{prefix}_path"])
        if _stat(path) != preflight[f"{prefix}_stat"] or _hash(path) != preflight[f"{prefix}_sha256"]:
            raise ValueError(f"{prefix} evidence changed since preflight")
    if _stat(preflight["input_path"]) != preflight["input_stat"]:
        raise ValueError("Source changed since PTS preflight")
    for path, value in preflight.get("audited_evidence_files", {}).items():
        if _stat(path) != value["stat"] or _hash(path) != value["sha256"]:
            raise ValueError("Independent audit evidence changed since preflight")
    # Yield the prefix for sequential original-source decoding; callers discard
    # frames before first_frame_index without inference. Exhaust the complete
    # timing sidecar after the selected segment so tail/EOF checks still run.
    for row in _timing_rows(preflight["frame_timing_path"], preflight):
        if row["frame_index"] < preflight["end_frame_index_exclusive"]:
            yield {**row, "timestamp_seconds": row["source_timestamp_seconds"] - preflight["source_start_seconds"]}
    for prefix in ("audit_summary", "frame_timing"):
        if _stat(preflight[f"{prefix}_path"]) != preflight[f"{prefix}_stat"]:
            raise ValueError(f"{prefix} evidence changed during timing iteration")
