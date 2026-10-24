"""CPU-only original timestamp and sequential-decoder evidence, never inference.

This deliberately reports irregular timestamps rather than converting them into
an assumed CFR grid. Every presented PTS and duration is retained on disk while
the in-memory summary stays bounded. Only an explicitly selected new
Validation source is accepted, before media inspection. All evidence is new.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


SYSTEM_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SYSTEM_ROOT / "src"))

from selection_binding import selected_entry, FROZEN_MANIFEST_SHA


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_native_envelope(output):
    """Bind already successful native evidence; never invent completion fields.

    This is a packaging step only. The raw report and standalone summaries keep
    every field emitted by the real audit. An error/mismatch or incomplete audit
    cannot acquire a completed envelope merely by calling this function.
    """
    output = Path(output).resolve()
    target = output / "audit_runtime_envelope.json"
    if target.exists():
        raise FileExistsError("Audit envelope already exists; evidence must never be overwritten")
    if (output / "audit_failure.json").exists():
        raise ValueError("A failed audit cannot produce a completed evidence envelope")
    names = ("audit_report.json", "prepare_time_audit.executed.py", "source_metadata.json",
             "ffprobe_frames.stderr.log", "decoder_differences.jsonl", "pts_summary.json", "decoder_summary.json")
    def read(name):
        return json.loads((output / name).read_text(encoding="utf-8"))
    report = read("audit_report.json")
    if (report.get("schema_version") != "full_source_time_preparation_v1" or
            report.get("status") != "completed" or report.get("no_inference") is not True or
            report.get("original_source_stat_unchanged") is not True or
            report.get("task", {}).get("split") not in {"Train", "Validation"}):
        raise ValueError("Native envelope requires an already completed independent development audit")
    pts, decoder = report.get("pts", {}), report.get("decoder", {})
    if pts != read("pts_summary.json") or decoder != read("decoder_summary.json"):
        raise ValueError("Native raw report and standalone summaries disagree")
    if pts.get("ffprobe_eof_verified") is not True or pts.get("ffprobe_error_count") != 0:
        raise ValueError("Native envelope requires actual ffprobe EOF with zero errors")
    if (decoder.get("eof_verified") is not True or decoder.get("all_frames_match") is not True or
            decoder.get("matches_ffprobe_frame_count") is not True or
            decoder.get("decoded_frames") != pts.get("frame_count")):
        raise ValueError("Native envelope requires actual complete decoder correspondence")
    for field in ("timestamp_mismatch_frames", "dimension_mismatch_frames", "frame_index_mismatch_frames", "nonmonotonic_timestamp_frames"):
        if decoder.get(field) != 0:
            raise ValueError(f"Native envelope cannot hide nonzero {field}")
    for name in ("ffprobe_frames.stderr.log", "decoder_differences.jsonl"):
        if (output / name).stat().st_size:
            raise ValueError(f"Native envelope cannot hide errors in {name}")
    source = Path(report["source"])
    if (source.stat().st_size != report["source_size_bytes"] or
            source.stat().st_mtime_ns != report["source_mtime_ns"] or
            sha256(source) != report["source_sha256"]):
        raise ValueError("Source changed before native evidence packaging")
    if sha256(output / "timing_frames.jsonl") != pts["timing_frames_sha256"]:
        raise ValueError("Timing evidence changed before native evidence packaging")
    if decoder.get("source_sha256") != report["source_sha256"] or decoder.get("timing_frames_sha256") != pts["timing_frames_sha256"]:
        raise ValueError("Decoder evidence is not bound to its exact source and timing sidecar")
    if sha256(output / "prepare_time_audit.executed.py") != report["audit_implementation_sha256"]:
        raise ValueError("Executed audit implementation snapshot hash mismatch")
    streams = [row for row in read("source_metadata.json").get("streams", []) if row.get("codec_type") == "video"]
    if len(streams) != 1 or streams[0] != report.get("stream"):
        raise ValueError("Raw metadata and native audit stream disagree")
    report["evidence_envelope"] = {
        "created_at": utc_now(), "posthoc_envelope": False,
        "method": "Native successful audit bundle: original report fields retained unchanged; only verified file paths and SHA256 bindings added. EOF, decoder comparison and source identity were emitted by the actual audit, not inferred during packaging.",
        "files": {name: {"path": str(output / name), "sha256": sha256(output / name)} for name in names},
        "timing_frames_path": str(output / "timing_frames.jsonl"),
    }
    write_new(target, report)
    return target


def parse_row(line):
    fields = dict(item.split("=", 1) for item in line.strip().split("|") if "=" in item)
    if not fields or (not {"pts", "pkt_pts"} & fields.keys() and any(k.startswith("side_data") for k in fields)):
        return None
    # Older ffprobe releases expose the original presentation timestamp as
    # pkt_pts. Both are explicit presentation ticks, never DTS or an inferred
    # best-effort timestamp. Conflicting simultaneous fields fail closed.
    if "pts" in fields and "pkt_pts" in fields:
        if fields["pts"] == "N/A" or fields["pkt_pts"] == "N/A" or int(fields["pts"]) != int(fields["pkt_pts"]):
            raise ValueError("Presented frame pts and pkt_pts disagree or are unusable")
    pts_field = "pts" if "pts" in fields else "pkt_pts"
    if pts_field not in fields or fields[pts_field] == "N/A":
        raise ValueError("Presented frame has no usable original PTS")
    result = {"pts": int(fields[pts_field])}
    for field in ("pkt_duration", "duration", "width", "height"):
        if fields.get(field) not in (None, "N/A"):
            result[field] = int(fields[field])
    return result


def limited_count(counts, key, limit=4096):
    if key in counts or len(counts) < limit:
        counts[key] += 1
    else:
        counts["other_distinct_values"] += 1


class TimestampSummary:
    def __init__(self, time_base, nominal_fps, average_fps):
        self.time_base = Fraction(time_base)
        self.nominal_fps = Fraction(nominal_fps)
        self.average_fps = Fraction(average_fps)
        self.nominal_ticks = 1 / self.nominal_fps / self.time_base
        self.average_ticks = 1 / self.average_fps / self.time_base
        self.count = 0
        self.first = self.previous = self.last_duration = None
        self.deltas = Counter()
        self.durations = Counter()
        self.dimensions = Counter()
        self.continuity = Counter()
        self.unknown_adjacent_coverage = 0
        self.nonmonotonic = self.missing_duration = self.conflicting_duration = 0
        self.nominal_off_grid = self.average_off_grid = 0
        self.nominal_max_error_ticks = self.average_max_error_ticks = Fraction(0)
        self.max_delta = self.min_delta = None
        self.gap_count = 0

    def accept(self, row):
        pts = row["pts"]
        if self.first is None:
            self.first = pts
        delta = None if self.previous is None else pts - self.previous
        if delta is not None:
            limited_count(self.deltas, delta)
            self.nonmonotonic += delta <= 0
            self.min_delta = delta if self.min_delta is None else min(self.min_delta, delta)
            self.max_delta = delta if self.max_delta is None else max(self.max_delta, delta)
            if self.last_duration is None:
                self.unknown_adjacent_coverage += 1
            else:
                limited_count(self.continuity, delta - self.last_duration)
        if "duration" in row and "pkt_duration" in row:
            self.conflicting_duration += row["duration"] != row["pkt_duration"]
        duration = row.get("duration", row.get("pkt_duration"))
        if duration is None or duration <= 0:
            self.missing_duration += 1
            self.last_duration = None
        else:
            limited_count(self.durations, duration)
            self.last_duration = duration
        limited_count(self.dimensions, f"{row.get('width')}x{row.get('height')}")
        nominal_error = abs(Fraction(pts - self.first) - self.count * self.nominal_ticks)
        average_error = abs(Fraction(pts - self.first) - self.count * self.average_ticks)
        self.nominal_off_grid += nominal_error > 1
        self.average_off_grid += average_error > 1
        self.nominal_max_error_ticks = max(self.nominal_max_error_ticks, nominal_error)
        self.average_max_error_ticks = max(self.average_max_error_ticks, average_error)
        gap = delta is not None and delta > self.nominal_ticks * Fraction(3, 2)
        self.gap_count += gap
        result = {"frame_index": self.count, **row,
                  "source_seconds": float((pts - self.first) * self.time_base),
                  "original_pts_seconds": float(pts * self.time_base),
                  "duration_seconds": None if self.last_duration is None else float(self.last_duration * self.time_base),
                  "pts_delta_ticks": delta}
        self.previous = pts
        self.count += 1
        return result, gap

    def result(self):
        if not self.count:
            raise ValueError("No presented frames returned")
        end = None if self.last_duration is None else self.previous + self.last_duration
        return {
            "frame_count": self.count,
            "time_base": str(self.time_base),
            "nominal_fps": str(self.nominal_fps),
            "average_fps_metadata": str(self.average_fps),
            "first_pts_ticks": self.first,
            "last_pts_ticks": self.previous,
            "first_pts_seconds": float(self.first * self.time_base),
            "last_pts_seconds": float(self.previous * self.time_base),
            "last_normalized_source_seconds": float((self.previous - self.first) * self.time_base),
            "final_frame_duration_ticks": self.last_duration,
            "presented_end_pts_seconds": None if end is None else float(end * self.time_base),
            "normalized_presented_duration_seconds": None if end is None else float((end - self.first) * self.time_base),
            "actual_count_divided_by_presented_duration_fps": None if end is None else float(Fraction(self.count, end - self.first) / self.time_base),
            "pts_delta_ticks_histogram": dict(sorted(self.deltas.items(), key=lambda pair: str(pair[0]))),
            "frame_duration_ticks_histogram": dict(sorted(self.durations.items(), key=lambda pair: str(pair[0]))),
            "dimensions_histogram": dict(self.dimensions),
            "adjacent_frame_end_to_next_pts_delta_ticks_histogram": dict(self.continuity),
            "adjacent_coverage_unknown_count": self.unknown_adjacent_coverage,
            "continuous_presentation_coverage": not self.unknown_adjacent_coverage and set(self.continuity) <= {0},
            "minimum_pts_delta_ticks": self.min_delta,
            "maximum_pts_delta_ticks": self.max_delta,
            "duplicate_or_backwards_pts_count": self.nonmonotonic,
            "missing_nonpositive_duration_count": self.missing_duration,
            "conflicting_duration_field_count": self.conflicting_duration,
            "frames_off_nominal_cfr_grid_more_than_one_tick": self.nominal_off_grid,
            "frames_off_average_cfr_grid_more_than_one_tick": self.average_off_grid,
            "max_nominal_grid_error_seconds": float(self.nominal_max_error_ticks * self.time_base),
            "max_average_grid_error_seconds": float(self.average_max_error_ticks * self.time_base),
            "gaps_longer_than_1_5_nominal_period_count": self.gap_count,
            "cfr_grid_demonstrated": self.nominal_off_grid == 0 and self.nonmonotonic == 0,
            "classification": "nominal_cfr_grid" if self.nominal_off_grid == 0 and self.nonmonotonic == 0 else "irregular_original_pts_requires_explicit_pts_handling",
        }


def audit_probe(args, stream):
    started = time.monotonic()
    command = [str(args.ffprobe), "-v", "error", "-threads", "2", "-select_streams", "v:0",
               "-show_frames", "-show_entries", "frame=pts,pkt_pts,pkt_duration,duration,width,height",
               "-of", "compact=p=0:nk=0", str(args.source)]
    write_new(args.output / "ffprobe_audit_invocation.json", {"command": command, "timeout_seconds": args.timeout_seconds,
                                                            "started_at": utc_now(), "threads": 2})
    stats = TimestampSummary(stream["time_base"], stream["r_frame_rate"], stream["avg_frame_rate"])
    timed_out = threading.Event()
    with (args.output / "ffprobe_frames.stderr.log").open("x") as errors, \
         (args.output / "timing_frames.jsonl").open("x") as frames, \
         (args.output / "pts_gaps.jsonl").open("x") as gaps:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True)
        def expire():
            timed_out.set()
            process.kill()
        timer = threading.Timer(args.timeout_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            for line in process.stdout:
                row = parse_row(line)
                if row is None:
                    continue
                row, gap = stats.accept(row)
                frames.write(json.dumps(row, separators=(",", ":")) + "\n")
                if gap:
                    gaps.write(json.dumps(row, separators=(",", ":")) + "\n")
                if stats.count % 10000 == 0:
                    print(json.dumps({"stage": "ffprobe", "frames": stats.count,
                                      "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
            code = process.wait()
            if timed_out.is_set():
                raise TimeoutError(f"ffprobe exceeded {args.timeout_seconds} seconds")
            if code:
                raise RuntimeError(f"ffprobe exit code {code}; see stderr log")
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
    summary = stats.result()
    if (args.output / "ffprobe_frames.stderr.log").stat().st_size:
        raise RuntimeError("ffprobe reported decoding errors; see retained stderr log")
    summary.update({"elapsed_seconds": time.monotonic() - started, "ended_at": utc_now(),
                    "ffprobe_eof_verified": True, "ffprobe_error_count": 0,
                    "timing_frames_sha256": sha256(args.output / "timing_frames.jsonl")})
    write_new(args.output / "pts_summary.json", summary)
    return summary


def audit_decoder(args, stream, pts_summary, source_sha256):
    # Imports remain here so parsing/self-test needs no numerical runtime.
    import cv2
    cv2.setNumThreads(2)
    if not hasattr(cv2, "CAP_PROP_N_THREADS"):
        raise RuntimeError("OpenCV lacks CAP_PROP_N_THREADS; cannot bound decoder threads using this environment")
    started = time.monotonic()
    capture = cv2.VideoCapture(str(args.source), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    if not capture.isOpened():
        raise RuntimeError("Cannot open selected source with 2-thread OpenCV FFmpeg decoder")
    initial = {"opencv_version": cv2.__version__, "backend": capture.getBackendName(),
               "reported_fps": capture.get(cv2.CAP_PROP_FPS), "reported_frame_count": capture.get(cv2.CAP_PROP_FRAME_COUNT),
               "reported_width": capture.get(cv2.CAP_PROP_FRAME_WIDTH), "reported_height": capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
               "requested_decoder_threads": 2, "reported_decoder_threads": capture.get(cv2.CAP_PROP_N_THREADS),
               "started_at": utc_now()}
    write_new(args.output / "decoder_invocation.json", initial)
    tick_s = float(Fraction(stream["time_base"]))
    count = bad_timestamps = bad_dimensions = bad_indices = backward = 0
    previous = first = last = None
    min_difference = math.inf
    max_difference = -math.inf
    max_absolute_difference = 0.0
    with (args.output / "decoder_differences.jsonl").open("x") as differences, \
         (args.output / "timing_frames.jsonl").open() as timing:
        try:
            for line in timing:
                if time.monotonic() - started > args.timeout_seconds:
                    raise TimeoutError(f"OpenCV audit exceeded {args.timeout_seconds} seconds")
                expected = json.loads(line)
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"OpenCV decode ended at {count}, ffprobe has {pts_summary['frame_count']} frames")
                source_seconds = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
                next_frame_index = capture.get(cv2.CAP_PROP_POS_FRAMES)
                if not math.isfinite(source_seconds):
                    raise ValueError(f"Decoder produced nonfinite timestamp at frame {count}")
                difference = source_seconds - expected["source_seconds"]
                min_difference = min(min_difference, difference)
                max_difference = max(max_difference, difference)
                max_absolute_difference = max(max_absolute_difference, abs(difference))
                wrong_time = abs(difference) > max(tick_s, 1e-6)
                wrong_dimensions = frame.shape[:2] != (int(stream["height"]), int(stream["width"]))
                wrong_index = abs(next_frame_index - (count + 1)) > 1e-6
                nonmonotonic = previous is not None and source_seconds <= previous
                bad_timestamps += wrong_time
                bad_dimensions += wrong_dimensions
                bad_indices += wrong_index
                backward += nonmonotonic
                if wrong_time or wrong_dimensions or wrong_index or nonmonotonic:
                    differences.write(json.dumps({"frame_index": count, "expected_source_seconds": expected["source_seconds"],
                        "decoder_source_seconds": source_seconds, "difference_seconds": difference,
                        "decoder_next_frame_index": next_frame_index, "shape": list(frame.shape),
                        "nonmonotonic": nonmonotonic}) + "\n")
                if first is None:
                    first = source_seconds
                previous = last = source_seconds
                count += 1
                if count % 10000 == 0:
                    print(json.dumps({"stage": "opencv", "frames": count,
                                      "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
            extra, _ = capture.read()
            if extra:
                raise ValueError("OpenCV decoded more frames than ffprobe")
        finally:
            capture.release()
    result = {**initial, "ended_at": utc_now(), "elapsed_seconds": time.monotonic() - started,
              "decoded_frames": count, "matches_ffprobe_frame_count": count == pts_summary["frame_count"],
              "first_source_seconds": first, "last_source_seconds": last,
              "timestamp_difference_min_seconds": min_difference, "timestamp_difference_max_seconds": max_difference,
              "timestamp_difference_max_absolute_seconds": max_absolute_difference,
              "timestamp_tolerance_seconds": max(tick_s, 1e-6),
              "timestamp_mismatch_frames": bad_timestamps, "dimension_mismatch_frames": bad_dimensions,
              "frame_index_mismatch_frames": bad_indices, "nonmonotonic_timestamp_frames": backward,
              "all_frames_match": not (bad_timestamps or bad_dimensions or bad_indices or backward),
              "eof_verified": True, "source_sha256": source_sha256,
              "timing_frames_sha256": pts_summary["timing_frames_sha256"]}
    write_new(args.output / "decoder_summary.json", result)
    return result


def self_test():
    assert parse_row("side_data_type=H.26x user data\n") is None
    assert parse_row("pts=12|duration=1|width=1280|height=720\n")["duration"] == 1
    assert parse_row("pts=12|pkt_duration=1\n")["pkt_duration"] == 1
    assert parse_row("pkt_pts=12|pkt_duration=1|width=1280|height=720\n")["pts"] == 12
    assert parse_row("pkt_pts=12|side_data_type=H.26x user data\n")["pts"] == 12
    assert parse_row("pts=12|pkt_pts=12|duration=1\n")["pts"] == 12
    for unusable in ("duration=1|width=1280|height=720", "pts=12|pkt_pts=13|duration=1",
                     "best_effort_timestamp=12|duration=1", "pkt_dts=12|duration=1",
                     "pts=N/A|pkt_pts=12|duration=1", "pkt_pts=N/A|duration=1"):
        try:
            parse_row(unusable)
            raise AssertionError(f"Missing/contradictory original PTS accepted: {unusable}")
        except ValueError:
            pass
    cfr = TimestampSummary("1/1000", "30/1", "30/1")
    for pts in (0, 33, 67, 100):
        cfr.accept({"pts": pts, "duration": 33, "width": 1280, "height": 720})
    assert cfr.result()["cfr_grid_demonstrated"]
    irregular = TimestampSummary("1/1000", "30/1", "30/1")
    for pts in (0, 33, 100):
        irregular.accept({"pts": pts, "duration": 33})
    assert not irregular.result()["cfr_grid_demonstrated"]
    assert irregular.result()["gaps_longer_than_1_5_nominal_period_count"] == 1
    assert irregular.result()["normalized_presented_duration_seconds"] == 0.133
    assert not irregular.result()["continuous_presentation_coverage"]
    contiguous = TimestampSummary("1/90000", "30/1", "30/1")
    for pts, duration in ((0, 1), (1, 2998), (2999, 6001), (9000, 3000)):
        contiguous.accept({"pts": pts, "duration": duration})
    assert contiguous.result()["continuous_presentation_coverage"]
    assert not contiguous.result()["cfr_grid_demonstrated"]
    absent_duration = TimestampSummary("1/1000", "30/1", "30/1")
    absent_duration.accept({"pts": 10})
    assert absent_duration.result()["normalized_presented_duration_seconds"] is None
    self_test_native_bundle()
    print("Synthetic software self-test passed: compact fields, rounded CFR, real gap, unknown tail.")


def self_test_native_bundle():
    """Synthetic text files only; round-trip through the real strict reader."""
    from copy import deepcopy
    from full_video.pts_timing import preflight_pts, iter_pts
    with tempfile.TemporaryDirectory(prefix="synthetic-native-audit-bundle-") as directory:
        root = Path(directory).resolve()
        source = root / "synthetic.mp4"
        source.write_bytes(b"SYNTHETIC SOFTWARE FIXTURE; NOT A VIDEO")
        rows = [{"frame_index": i, "pts": pts, "duration": duration, "width": 16, "height": 12}
                for i, (pts, duration) in enumerate(((100, 1), (101, 2999), (3100, 4500), (7600, 2000)))]
        timing = root / "timing_frames.jsonl"
        timing.write_text("".join(json.dumps(row) + "\n" for row in rows))
        snapshot = root / "prepare_time_audit.executed.py"
        snapshot.write_bytes(Path(__file__).read_bytes())
        task = {"source_id": source.name, "split": "Validation", "camera_id": "synthetic-camera", "input_path": str(source)}
        stream = {"codec_type": "video", "width": 16, "height": 12, "time_base": "1/90000", "start_pts": 100,
                  "duration_ts": 9500, "nb_frames": "4", "avg_frame_rate": "30/1", "r_frame_rate": "25/1"}
        pts = {"frame_count": 4, "time_base": "1/90000", "first_pts_ticks": 100, "last_pts_ticks": 7600,
               "final_frame_duration_ticks": 2000, "normalized_presented_duration_seconds": 9500 / 90000,
               "ffprobe_eof_verified": True, "ffprobe_error_count": 0, "timing_frames_sha256": sha256(timing)}
        decoder = {"decoded_frames": 4, "matches_ffprobe_frame_count": True, "backend": "FFMPEG",
                   "opencv_version": "synthetic-software-test", "requested_decoder_threads": 2,
                   "first_source_seconds": 0, "last_source_seconds": 7500 / 90000,
                   "timestamp_difference_max_absolute_seconds": 0, "timestamp_tolerance_seconds": 1 / 90000,
                   "timestamp_mismatch_frames": 0, "dimension_mismatch_frames": 0, "frame_index_mismatch_frames": 0,
                   "nonmonotonic_timestamp_frames": 0, "all_frames_match": True, "eof_verified": True,
                   "source_sha256": sha256(source), "timing_frames_sha256": sha256(timing),
                   "reported_width": 16, "reported_height": 12}
        report = {"schema_version": "full_source_time_preparation_v1", "status": "completed", "synthetic": True,
                  "task": task, "source": str(source), "source_size_bytes": source.stat().st_size,
                  "source_mtime_ns": source.stat().st_mtime_ns, "source_sha256": sha256(source),
                  "audit_implementation_sha256": sha256(snapshot), "manifest_sha256": "a" * 64,
                  "no_inference": True, "original_source_stat_unchanged": True, "ended_at": "synthetic-test-only",
                  "pts": pts, "decoder": decoder, "stream": stream}
        for name, data in (("audit_report.json", report), ("pts_summary.json", pts),
                           ("decoder_summary.json", decoder), ("source_metadata.json", {"streams": [stream]})):
            write_new(root / name, data)
        (root / "ffprobe_frames.stderr.log").write_text("")
        (root / "decoder_differences.jsonl").write_text("")
        # A packaging function must not manufacture success or repair evidence.
        bad = deepcopy(report)
        bad["status"] = "running"
        (root / "audit_report.json").write_text(json.dumps(bad))
        try:
            write_native_envelope(root)
            raise AssertionError("Incomplete native audit was accepted")
        except ValueError:
            pass
        (root / "audit_report.json").write_text(json.dumps(report))
        (root / "decoder_summary.json").write_text(json.dumps({**decoder, "decoded_frames": 3}))
        try:
            write_native_envelope(root)
            raise AssertionError("Contradictory summary was accepted")
        except ValueError:
            pass
        (root / "decoder_summary.json").write_text(json.dumps(decoder))
        (root / "ffprobe_frames.stderr.log").write_text("synthetic decode error")
        try:
            write_native_envelope(root)
            raise AssertionError("Decode error log was accepted")
        except ValueError:
            pass
        (root / "ffprobe_frames.stderr.log").write_text("")
        envelope = write_native_envelope(root)
        loaded = json.loads(envelope.read_text())
        assert {key: value for key, value in loaded.items() if key != "evidence_envelope"} == report
        assert loaded["evidence_envelope"]["posthoc_envelope"] is False
        config = {"input": {"kind": "video", "source_offset_seconds": 0}, "segment": None,
                  "time": {"mode": "audited_pts", "max_frame_duration_seconds": .06,
                           "audit_summary_path": str(envelope), "frame_timing_path": str(timing)}}
        camera = {"camera_id": task["camera_id"], "source_ids": [source.name], "layout": {"frame_width": 16, "frame_height": 12}}
        prepared = preflight_pts(config, task, camera)
        actual = list(iter_pts(prepared))
        assert len(actual) == 4 and actual[1]["timestamp_seconds"] == 1 / 90000
        assert [row["duration_ticks"] for row in actual] == [1, 2999, 4500, 2000]
        try:
            write_native_envelope(root)
            raise AssertionError("Existing native envelope was overwritten")
        except FileExistsError:
            pass
    print("Synthetic native bundle round-trip passed: strict reader, original fields, incomplete/mismatch/error rejection, no overwrite.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--source-id")
    parser.add_argument("--selection-sha256")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--src-root", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if any(value is None for value in (args.source, args.manifest, args.ffprobe, args.output, args.selection, args.source_id)):
        parser.error("--source, --source-id, --selection, --manifest, --ffprobe and --output are required")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and positive")
    if args.src_root:
        sys.path.insert(0,str(args.src_root))
    from full_video.batch import list_development
    # Selection, frozen CSV membership and exact identity precede media access.
    entry = selected_entry(args.selection, args.source_id)
    selection_sha = sha256(args.selection)
    if args.selection_sha256 and selection_sha != args.selection_sha256:
        raise ValueError("Selected-source manifest SHA256 mismatch")
    if sha256(args.manifest) != FROZEN_MANIFEST_SHA:
        raise ValueError("Official frozen split manifest changed")
    tasks = [row for row in list_development(args.manifest) if row["source_id"] == args.source_id]
    if len(tasks) != 1 or any(tasks[0][key] != entry[key] for key in ("source_id", "camera_id", "split")):
        raise ValueError("Selection differs from the official frozen development membership")
    source_root = args.source_root or args.source.parent.parent
    expected = source_root / entry["camera_id"] / entry["source_id"]
    if args.source.resolve() != expected.resolve() or args.source.parent.name != entry["camera_id"]:
        raise ValueError(f"Original camera/source path required: {expected}")
    if args.source.stat().st_size != entry["source_bytes"] or sha256(args.source) != entry["source_sha256"]:
        raise ValueError("Source bytes differ from frozen selected original")
    if not args.ffprobe.is_file() or not os.access(args.ffprobe, os.X_OK):
        raise ValueError("Configured ffprobe executable is unavailable")
    args.output.mkdir(parents=True, exist_ok=True)
    reserved = ("identity.json", "ffprobe_version.txt", "source_metadata.json", "ffprobe_audit_invocation.json",
                "ffprobe_frames.stderr.log", "timing_frames.jsonl", "pts_gaps.jsonl", "pts_summary.json",
                "decoder_invocation.json", "decoder_differences.jsonl", "decoder_summary.json", "audit_report.json", "audit_failure.json",
                "prepare_time_audit.executed.py", "audit_runtime_envelope.json")
    if any((args.output / name).exists() for name in reserved):
        raise FileExistsError("Audit output already contains an audit-owned evidence file; choose a new output directory")
    with (args.output / "prepare_time_audit.executed.py").open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    started = time.monotonic()
    source_stat = args.source.stat()
    identity = {"schema_version": "full_source_time_preparation_v1", "started_at": utc_now(),
                "task": tasks[0], "source": str(args.source.resolve()),
                "source_size_bytes": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns,
                "source_sha256": sha256(args.source), "ffprobe_path": str(args.ffprobe.resolve()),
                "audit_implementation_sha256": sha256(Path(__file__)),
                "ffprobe_sha256": sha256(args.ffprobe), "manifest_sha256": sha256(args.manifest),
                "no_inference": True, "main_test_media_accessed": False, "new_human_annotations": False,
                "selection_sha256": selection_sha, "selection_entry": entry,
                "timeout_seconds_per_stage": args.timeout_seconds}
    write_new(args.output / "identity.json", identity)
    try:
        version = subprocess.run([str(args.ffprobe), "-version"], capture_output=True, text=True, check=True, timeout=30)
        with (args.output / "ffprobe_version.txt").open("x") as handle:
            handle.write(version.stdout)
        metadata = subprocess.run([str(args.ffprobe), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(args.source)],
                                  capture_output=True, text=True, check=True, timeout=30)
        raw = json.loads(metadata.stdout)
        write_new(args.output / "source_metadata.json", raw)
        streams = [s for s in raw["streams"] if s["codec_type"] == "video"]
        if len(streams) != 1:
            raise ValueError("Exactly one video stream required")
        stream = streams[0]
        pts = audit_probe(args, stream)
        if any(pts[key] for key in ("duplicate_or_backwards_pts_count", "missing_nonpositive_duration_count", "conflicting_duration_field_count")) or not pts["continuous_presentation_coverage"]:
            raise ValueError("Original timing cannot pass the exact contiguous positive-duration gate; raw evidence retained")
        decoder = audit_decoder(args, stream, pts, identity["source_sha256"])
        if decoder["all_frames_match"] is not True:
            raise ValueError("Complete decoder audit found mismatches; evidence retained, no successful envelope created")
        if sha256(args.selection) != selection_sha:
            raise ValueError("Selected-source manifest changed during audit")
        after = args.source.stat()
        if (source_stat.st_size, source_stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Original source changed during audit")
        if sha256(Path(__file__)) != identity["audit_implementation_sha256"]:
            raise ValueError("Audit helper changed while running; restart in a new output directory")
        report = {**identity, "status": "completed", "stream": stream,
                  "ended_at": utc_now(), "total_elapsed_seconds": time.monotonic() - started,
                  "pts": pts, "decoder": decoder, "original_source_stat_unchanged": True,
                  "conclusion": "Timestamp and sequential decode evidence complete; CFR eligibility is the explicit pts.cfr_grid_demonstrated field. No inference was run."}
        write_new(args.output / "audit_report.json", report)
        envelope = write_native_envelope(args.output)
        print(json.dumps({"report": str(args.output / "audit_report.json"), "envelope": str(envelope),
                          "pts": pts, "decoder": decoder}, indent=2), flush=True)
    except Exception as error:
        write_new(args.output / "audit_failure.json", {"ended_at": utc_now(), "elapsed_seconds": time.monotonic() - started,
                                                     "type": type(error).__name__, "error": str(error)})
        raise


if __name__ == "__main__":
    main()
