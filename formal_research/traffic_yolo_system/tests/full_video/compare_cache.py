"""Compare a 120-second development-cache attempt with saved pilot counts.

Reads JSON/JSONL/CSV only. This is an implementation-definition check, not a new
model evaluation, full-video experiment, or Main Test run. No images, labels,
weights, or video are opened. Frame rows are compared as streams.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from itertools import zip_longest
import json
import math
from pathlib import Path
import sys


TOLERANCE = 1e-9


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_lines(path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def csv_lines(path):
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close_number(left, right, field):
    left, right = float(left), float(right)
    require(math.isfinite(left) and math.isfinite(right), f"Non-finite {field}")
    difference = abs(left - right)
    require(difference <= TOLERANCE, f"Mismatch {field}: {left!r} vs {right!r}")
    return difference


def compare(attempt, reference):
    paths = {
        "attempt_summary": attempt / "summary.json",
        "attempt_frames": attempt / "frames.jsonl",
        "attempt_windows": attempt / "windows.jsonl",
        "reference_summary": reference / "summary.json",
        "reference_frames": reference / "frame_zone_counts.csv",
        "reference_windows": reference / "zone_counts_10s.csv",
    }
    actual, expected = read(paths["attempt_summary"]), read(paths["reference_summary"])
    require(actual["split"] in ("Train", "Validation"), "Attempt must be a development source")
    require(expected["source_split"] in ("Train", "Validation"), "Reference must be a development source")
    require(actual["split"] == expected["source_split"], "Split mismatch")
    require(actual["source"] == expected["source_video"], "Source identity mismatch")
    require(actual.get("roi_sha256") == expected["inputs_sha256"]["zones"],
            "ROI file differs; this comparison requires the exact historical Newport layout")
    # Inspect development provenance before opening or hashing frame predictions.
    hashes = {name: digest(path) for name, path in paths.items()}
    require(actual["frame_count"] == expected["frame_count"] == 3600,
            "This comparison is scoped to the existing 3,600-frame development cache")
    close_number(actual["duration_seconds"], 120, "attempt duration")
    close_number(expected["duration_seconds"], 120, "reference duration")
    close_number(expected["fps"], 30, "reference fps")
    require(actual["valid_frame_count"] == 3600, "Missing/invalid frames in cache attempt")
    require(actual["generated_missing_interval_count"] == 0, "Unexpected generated missing intervals")
    close_number(actual["valid_duration_seconds"], 120, "valid duration")
    close_number(actual["missing_duration_seconds"], 0, "missing duration")
    require(actual["traffic_state"] == "not_computed", "Must not fabricate a traffic state")
    zones = list(expected["zones"])
    require(len(zones) == 4, "Expected the four-region Newport cache definition")
    require(set(actual["zones"]) == set(zones), "Zone names differ; explicit migration is needed")
    require(actual["zone_assignment_policy"] == expected["zone_assignment_policy"],
            "Geometry assignment policy differs")
    max_frame_time_difference = 0
    frame_rows = 0
    for actual_frame, expected_frame in zip_longest(json_lines(paths["attempt_frames"]), csv_lines(paths["reference_frames"])):
        require(actual_frame is not None and expected_frame is not None, "Frame-row count mismatch")
        require(actual_frame["record_type"] == "frame" and actual_frame["valid"], "Unexpected missing cache frame")
        require(actual_frame["frame_index"] == int(expected_frame["frame_index"]), f"Frame index mismatch at row {frame_rows}")
        for time in ("timestamp_seconds", "source_timestamp_seconds"):
            max_frame_time_difference = max(max_frame_time_difference,
                close_number(actual_frame[time], expected_frame[time], f"frame {frame_rows} {time}"))
        for zone in zones:
            require(actual_frame["zone_counts"][zone] == int(expected_frame[zone]),
                    f"Frame {frame_rows} zone count mismatch: {zone}")
        for current, old in (("total_in_zones", "total_in_zones"), ("outside_zone_count", "outside_roi"),
                             ("all_current_tracks", "all_current_tracks"), ("raw_detection_count", "raw_detection_count")):
            require(actual_frame[current] == int(expected_frame[old]), f"Frame {frame_rows} mismatch: {current}")
        frame_rows += 1
    require(frame_rows == 3600, "Incorrect compared frame count")

    window_rows, max_window_mean_difference = 0, 0
    for actual_window, expected_window in zip_longest(json_lines(paths["attempt_windows"]), csv_lines(paths["reference_windows"])):
        require(actual_window is not None and expected_window is not None, "Window-row count mismatch")
        require(actual_window["zone_name"] == expected_window["zone_name"], f"Window zone mismatch at row {window_rows}")
        for field in ("window_start_seconds", "window_end_seconds_exclusive"):
            close_number(actual_window[field], expected_window[field], f"window {window_rows} {field}")
        max_window_mean_difference = max(max_window_mean_difference,
            close_number(actual_window["mean_current_vehicles"], expected_window["mean_current_vehicles"],
                         f"window {window_rows} mean"))
        for field in ("min_current_vehicles", "max_current_vehicles"):
            require(actual_window[field] == int(expected_window[field]), f"Window {window_rows} mismatch: {field}")
        require(actual_window["valid_frame_intersections"] == int(expected_window["frames"]), "Window frame count mismatch")
        require(actual_window["validity"] == "valid", "Unexpected missing window")
        window_rows += 1
    require(window_rows == 48, "Expected 12 ten-second windows times four regions")

    max_summary_mean_difference = 0
    summary_comparison = {}
    for zone in zones:
        left, right = actual["zones"][zone], expected["zones"][zone]
        delta = close_number(left["mean_current_vehicles"], right["mean_current_vehicles"], f"summary {zone} mean")
        max_summary_mean_difference = max(max_summary_mean_difference, delta)
        for field in ("min_current_vehicles", "max_current_vehicles"):
            require(left[field] == right[field], f"Summary {zone} mismatch: {field}")
        summary_comparison[zone] = {"streaming": {key: left[key] for key in right}, "pilot": right,
                                    "mean_absolute_difference": delta}
    require(hashes == {name: digest(path) for name, path in paths.items()}, "An input changed during comparison; retry against a stable snapshot")
    return {
        "status": "passed", "validation_type": "existing_development_cache_definition_equivalence",
        "synthetic": False, "attempt": str(attempt.resolve()), "reference": str(reference.resolve()),
        "frame_rows_compared": frame_rows, "window_zone_rows_compared": window_rows,
        "zone_count": len(zones), "duration_seconds": 120, "absolute_tolerance": TOLERANCE,
        "max_frame_timestamp_difference": max_frame_time_difference,
        "max_window_mean_difference": max_window_mean_difference,
        "max_summary_mean_difference": max_summary_mean_difference,
        "summary_comparison": summary_comparison, "input_sha256": hashes,
        "field_mapping": {
            "pilot CSV named region columns": "frames.jsonl zone_counts[region]",
            "pilot outside_roi": "frames.jsonl outside_zone_count",
            "pilot frames": "windows.jsonl valid_frame_intersections",
            "frame_index": "same decoded input-clip index; not asserted to be original-source frame number",
            "timestamp_seconds": "relative to analysis start; source_timestamp_seconds retains original source offset",
            "ROI": "same named four-region layout and shared geometry policy; ROI v2 may be referenced by a v4 replay",
        },
        "limitations_zh": ["仅验证现有 Newport 120 秒 / 3,600 帧开发缓存的统计定义一致。",
                           "未进行整小时视频推理、115 段分析、模型准确率评价或 Main Test 运行。",
                           "未读取图像、人工标签、权重或视频，不需要新增人工标注。",
                           "最终模型、ROI 和过滤规则仍需单独冻结；缓存一致不代表它们已冻结。"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="A new report JSON path; existing files are refused")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    try:
        report = compare(args.attempt, args.reference)
        exit_code = 0
    except (ValueError, KeyError, OSError, TypeError) as error:
        report = {"status": "failed", "error": str(error), "validation_type": "existing_development_cache_definition_equivalence"}
        exit_code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key in (
        "status", "error", "frame_rows_compared", "window_zone_rows_compared", "zone_count",
        "max_window_mean_difference", "max_summary_mean_difference")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
