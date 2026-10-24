"""Bounded-memory, interval-based current-observation counts for long videos.

No decoder or time mode is inferred here. The caller supplies verified source
timestamps and durations. Intervals are half-open, clipped to the requested
analysis range, and must be ordered and non-overlapping. Unobserved time is
written explicitly as missing, never as a fabricated zero-count frame.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

try:
    from ..roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout
except ImportError:  # ``python -m full_video`` with src on PYTHONPATH.
    from roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout


SCHEMA_VERSION = "full-video-v1"
TIME_EPSILON = 1e-9
COUNT_DEFINITION = (
    "Current-frame observed active tracks whose box bottom-center lies in a selected ROI; "
    "no lost-track extrapolation, cumulative unique-vehicle count, flow, or traffic state. "
    "Means are weighted by observed valid duration, excluding missing intervals."
)
REQUIRED_METADATA = ("source", "split", "run", "model_version", "roi_version", "rules_version")


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


class _Sum:
    """Compensated sum with constant storage."""
    def __init__(self):
        self.value = 0.0
        self.correction = 0.0

    def add(self, value):
        adjusted = value - self.correction
        total = self.value + adjusted
        self.correction = (total - self.value) - adjusted
        self.value = total


class _Stats:
    def __init__(self, names):
        self.valid = _Sum()
        self.missing = _Sum()
        self.sums = {name: _Sum() for name in names}
        self.zero = {name: _Sum() for name in names}
        self.minimum = {name: None for name in names}
        self.maximum = {name: None for name in names}
        self.valid_intersections = 0
        self.missing_intersections = 0

    def add(self, duration, counts):
        if counts is None:
            self.missing.add(duration)
            self.missing_intersections += 1
            return
        self.valid.add(duration)
        self.valid_intersections += 1
        for name, count in counts.items():
            self.sums[name].add(duration * count)
            if count == 0:
                self.zero[name].add(duration)
            self.minimum[name] = count if self.minimum[name] is None else min(self.minimum[name], count)
            self.maximum[name] = count if self.maximum[name] is None else max(self.maximum[name], count)

    def zone(self, name):
        return {
            "mean_current_vehicles": self.sums[name].value / self.valid.value if self.valid.value else None,
            "min_current_vehicles": self.minimum[name],
            "max_current_vehicles": self.maximum[name],
            "valid_zero_duration_seconds": self.zero[name].value,
            "observed_zero": self.maximum[name] == 0 if self.valid.value else None,
        }


class StreamWriter:
    """Write frames/windows/chunks without keeping the full video in memory.

    ``output`` must be a new, already-created attempt directory. Existing output
    filenames are rejected. ``frame_index`` is the index in the input video file;
    an encoded development clip can have a different frame rate from its source,
    so it is not presented as the original-source frame index. Verified source
    time is supplied separately. ``timestamp_seconds`` is computed relative to
    analysis start, and a supplied value must agree. A frame crossing a boundary
    contributes only its intersecting duration. Frame/window intersection counts
    must not be summed as unique frames across windows.

    Use as a context manager or call ``close`` after errors. ``finish`` is required
    for a complete run; it accounts for an unobserved tail as missing.
    """
    def __init__(self, output: Path, metadata: dict, layout: dict,
                 start_seconds: float, end_seconds: float,
                 window_seconds=10, chunk_frames=900):
        self.output = Path(output)
        self.start = _finite(start_seconds, "start_seconds")
        self.end = _finite(end_seconds, "end_seconds")
        self.window_seconds = _finite(window_seconds, "window_seconds")
        self.chunk_frames = _integer(chunk_frames, "chunk_frames", 1)
        if self.start < 0 or self.end <= self.start or self.window_seconds <= 0:
            raise ValueError("Require 0 <= start_seconds < end_seconds and positive window_seconds")
        step = min(self.window_seconds, self.end - self.start)
        if self.start + step <= self.start or self.end - step >= self.end:
            raise ValueError("window_seconds is not representable at this source timestamp scale")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        for name in REQUIRED_METADATA:
            if name not in metadata or metadata[name] in (None, ""):
                raise ValueError(f"metadata.{name} is required")
        if metadata["split"] not in ("Train", "Validation"):
            raise ValueError("StreamWriter is restricted to Train/Validation development sources")
        self.metadata = {key: metadata[key] for key in REQUIRED_METADATA}
        for key in ("camera_id", "config_sha256", "weights_sha256", "roi_sha256", "time_mode", "synthetic",
                    "frame_index_semantics", "frame_index_origin", "input_source_offset_seconds",
                    "source_id", "model_sha256", "configuration_sha256", "method_status"):
            if key in metadata:
                self.metadata[key] = metadata[key]
        _json(self.metadata)
        self.polygons = validate_layout(layout)
        self.policy = boundary_policy(layout)
        self.zone_names = [name for name, _ in self.polygons]
        self.assignment = assignment_metadata(layout)
        if not self.output.is_dir():
            raise ValueError("output must be an existing new attempt directory")
        targets = ("frames.jsonl", "windows.jsonl", "summary.json", "timeline")
        if any((self.output / name).exists() for name in targets):
            raise FileExistsError("StreamWriter refuses to overwrite any existing result")
        (self.output / "timeline").mkdir()
        self._frames = (self.output / "frames.jsonl").open("x", encoding="utf-8")
        self._windows = (self.output / "windows.jsonl").open("x", encoding="utf-8")
        self._index = (self.output / "timeline" / "index.jsonl").open("x", encoding="utf-8")
        self._chunk = None
        self._chunk_count = 0
        self._chunk_rows = 0
        self._chunk_start = None
        self._chunk_end = None
        self._window_index = 0
        self._window = _Stats(self.zone_names)
        self._global = _Stats(self.zone_names)
        self._cursor = self.start
        self._last_input_end = None
        self._last_frame_index = None
        self.frame_count = 0
        self.valid_frame_count = 0
        self.missing_interval_count = 0
        self.frames_with_no_tracks = 0
        self.frames_with_no_in_zone_tracks = 0
        self._finished = False
        self._closed = False
        self._summary = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _common(self):
        return {"schema_version": SCHEMA_VERSION, **self.metadata,
                "traffic_state": "not_computed", "traffic_state_zh": "未计算"}

    def _quality(self, stats, duration):
        valid, missing = stats.valid.value, stats.missing.value
        return {
            "validity": "missing" if valid == 0 else "partial" if missing > TIME_EPSILON else "valid",
            "valid_duration_seconds": valid,
            "missing_duration_seconds": missing,
            "valid_fraction": min(1.0, max(0.0, valid / duration)),
            "valid_frame_intersections": stats.valid_intersections,
            "missing_interval_intersections": stats.missing_intersections,
        }

    def _flush_window(self):
        start = self.start + self._window_index * self.window_seconds
        end = min(self.end, self.start + (self._window_index + 1) * self.window_seconds)
        duration = end - start
        quality = self._quality(self._window, duration)
        for name in self.zone_names:
            self._windows.write(_json({
                **self._common(), "window_index": self._window_index, "zone_name": name,
                "window_start_seconds": start - self.start,
                "window_end_seconds_exclusive": end - self.start,
                "source_start_seconds": start, "source_end_seconds_exclusive": end,
                "duration_seconds": duration,
                "is_tail_window": duration < self.window_seconds - TIME_EPSILON,
                **self._window.zone(name), **quality,
            }))
        self._window_index += 1
        self._window = _Stats(self.zone_names)

    def _accumulate(self, start, end, counts):
        self._global.add(end - start, counts)
        position = start
        while position < end:
            window_end = min(self.end, self.start + (self._window_index + 1) * self.window_seconds)
            piece_end = min(end, window_end)
            if piece_end <= position:
                raise RuntimeError("Non-progressing time window; unsupported numerical time scale")
            self._window.add(piece_end - position, counts)
            position = piece_end
            if position == window_end:
                self._flush_window()

    def _close_chunk(self):
        if self._chunk is None:
            return
        self._chunk.close()
        self._index.write(_json({
            **self._common(), "chunk_index": self._chunk_count - 1,
            "path": f"part-{self._chunk_count - 1:06d}.jsonl", "records": self._chunk_rows,
            "source_start_seconds": self._chunk_start, "source_end_seconds_exclusive": self._chunk_end,
            "start_seconds": self._chunk_start - self.start,
            "end_seconds_exclusive": self._chunk_end - self.start,
        }))
        self._index.flush()
        self._chunk = None

    def _emit(self, record, counts):
        if self._chunk is None:
            self._chunk = (self.output / "timeline" / f"part-{self._chunk_count:06d}.jsonl").open("x", encoding="utf-8")
            self._chunk_count += 1
            self._chunk_rows = 0
            self._chunk_start = record["source_timestamp_seconds"]
        self._frames.write(_json(record))
        timeline = {key: record[key] for key in (
            "record_type", "frame_index", "timestamp_seconds", "source_timestamp_seconds", "duration_seconds",
            "valid", "validity", "missing_reason", "zone_counts", "total_in_zones", "outside_zone_count",
            "all_current_tracks", "raw_detection_count")}
        self._chunk.write(_json({**self._common(), **timeline}))
        self._chunk_rows += 1
        self._chunk_end = record["source_timestamp_seconds"] + record["duration_seconds"]
        if self._chunk_rows == self.chunk_frames:
            self._close_chunk()
        self._accumulate(record["source_timestamp_seconds"], self._chunk_end, counts)
        self._cursor = self._chunk_end

    def _missing(self, start, end):
        self.missing_interval_count += 1
        self._emit({**self._common(), "record_type": "missing_interval", "frame_index": None,
                    "timestamp_seconds": start - self.start, "source_timestamp_seconds": start,
                    "duration_seconds": end - start, "valid": False, "validity": "missing",
                    "missing_reason": "unobserved_interval", "tracks_primary": [],
                    "zone_counts": None, "total_in_zones": None, "outside_zone_count": None,
                    "all_current_tracks": None, "raw_detection_count": None}, None)

    def add(self, record):
        if self._closed or self._finished:
            raise RuntimeError("Cannot add to a closed or finished StreamWriter")
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        frame_index = _integer(record.get("frame_index"), "frame_index")
        source_start = _finite(record.get("source_timestamp_seconds"), "source_timestamp_seconds")
        duration = _finite(record.get("duration_seconds"), "duration_seconds")
        source_end = source_start + duration
        if source_start < 0 or duration <= 0 or not math.isfinite(source_end) or source_end <= source_start:
            raise ValueError("Require nonnegative timestamp and positive representable finite duration")
        if self._last_frame_index is not None and frame_index <= self._last_frame_index:
            raise ValueError("frame_index must increase strictly in input-file coordinates")
        if self._last_input_end is not None and source_start < self._last_input_end - TIME_EPSILON:
            raise ValueError("Overlapping or non-monotonic source intervals")
        if source_start >= self.end or source_end <= self.start:
            raise ValueError("Frame interval does not intersect the requested analysis range")
        if "timestamp_seconds" in record:
            relative = _finite(record["timestamp_seconds"], "timestamp_seconds")
            if abs(relative - (source_start - self.start)) > TIME_EPSILON:
                raise ValueError("timestamp_seconds must be relative to analysis start")
        if not isinstance(record.get("valid"), bool):
            raise ValueError("valid must be a boolean; missing is not zero vehicles")
        valid = record["valid"]
        tracks = record.get("tracks_primary")
        if not isinstance(tracks, list):
            raise ValueError("tracks_primary must be a list of current observations")
        if not valid and tracks:
            raise ValueError("Invalid observations cannot contain active tracks")
        if not valid and (not isinstance(record.get("missing_reason"), str) or not record["missing_reason"].strip()):
            raise ValueError("Invalid observations require a nonempty missing_reason")
        counts = {name: 0 for name in self.zone_names} if valid else None
        copied_tracks, ids, outside = [], set(), 0
        for track in tracks:
            if not isinstance(track, dict):
                raise ValueError("Each track must be an object")
            track_id = _integer(track.get("track_id"), "track_id", 1)
            if track_id in ids:
                raise ValueError("Duplicate track ID in one frame")
            ids.add(track_id)
            if track.get("class_name") not in ("car", "motorcycle", "bus", "truck"):
                raise ValueError("Unsupported vehicle class in current observations")
            confidence = _finite(track.get("confidence"), "confidence")
            if not 0 <= confidence <= 1:
                raise ValueError("confidence must be in [0, 1]")
            box = track.get("box_xyxy")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise ValueError("box_xyxy must contain four finite numbers")
            box = [_finite(value, "box_xyxy") for value in box]
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError("box_xyxy must have positive width and height")
            zone = assign_box(box, self.polygons, self.policy)
            if zone == "outside_roi":
                outside += 1
            else:
                counts[zone] += 1
            copied_tracks.append({**track, "assigned_zone": zone})
        if "raw_detections" in record and not isinstance(record["raw_detections"], list):
            raise ValueError("raw_detections must be a list if supplied")
        # Validate the whole incoming payload before advancing state or writing a gap.
        _json(record)
        start, end = max(self.start, source_start), min(self.end, source_end)
        if abs(start - self._cursor) <= TIME_EPSILON:
            start = self._cursor
        boundary_index = round((end - self.start) / self.window_seconds)
        boundary = self.start + boundary_index * self.window_seconds
        if abs(end - boundary) <= TIME_EPSILON and self.start < boundary <= self.end:
            end = boundary
        if abs(end - self.end) <= TIME_EPSILON:
            end = self.end
        if start < self._cursor or end <= start:
            raise ValueError("Frame has no non-overlapping effective duration")
        if start > self._cursor:
            self._missing(self._cursor, start)
        normalized = {
            **record, **self._common(), "record_type": "frame", "frame_index": frame_index,
            "input_source_timestamp_seconds": source_start, "input_duration_seconds": duration,
            "source_timestamp_seconds": start, "timestamp_seconds": start - self.start,
            "duration_seconds": end - start, "valid": valid,
            "validity": "valid" if valid else "missing", "missing_reason": None if valid else record["missing_reason"],
            "tracks_primary": copied_tracks, "zone_counts": counts,
            "total_in_zones": sum(counts.values()) if valid else None,
            "outside_zone_count": outside if valid else None,
            "all_current_tracks": len(tracks) if valid else None,
            "raw_detection_count": len(record["raw_detections"]) if valid and "raw_detections" in record else None,
        }
        self._emit(normalized, counts)
        self._last_input_end = source_end
        self._last_frame_index = frame_index
        self.frame_count += 1
        self.valid_frame_count += int(valid)
        self.frames_with_no_tracks += int(valid and not tracks)
        self.frames_with_no_in_zone_tracks += int(valid and sum(counts.values()) == 0)

    def finish(self):
        if self._finished:
            return self._summary
        if self._closed:
            raise RuntimeError("Cannot finish a closed incomplete StreamWriter")
        if self._cursor < self.end:
            self._missing(self._cursor, self.end)
        self._close_chunk()
        self._frames.flush()
        self._windows.flush()
        self._index.flush()
        duration = self.end - self.start
        self._summary = {
            **self._common(), "status": "completed", "frame_count": self.frame_count,
            "valid_frame_count": self.valid_frame_count, "invalid_frame_count": self.frame_count - self.valid_frame_count,
            "generated_missing_interval_count": self.missing_interval_count,
            "frames_with_no_tracks": self.frames_with_no_tracks,
            "frames_with_no_in_zone_tracks": self.frames_with_no_in_zone_tracks,
            "duration_seconds": duration, "source_start_seconds": self.start,
            "source_end_seconds_exclusive": self.end, "source_offset_seconds": self.start,
            "time_origin": "analysis_start", "interval_convention": "[start, end)",
            "frame_index_origin": "input_file",
            "time_roundoff_tolerance_seconds": TIME_EPSILON,
            "window_seconds": self.window_seconds, "window_count": self._window_index,
            "timeline_chunk_count": self._chunk_count, "timeline_chunk_record_limit": self.chunk_frames,
            "timeline_index": "timeline/index.jsonl", "count_definition": COUNT_DEFINITION,
            "zone_assignment_policy": self.assignment, "zones": {name: self._global.zone(name) for name in self.zone_names},
            **self._quality(self._global, duration),
            "limitations": ["Traffic state is not computed; no congestion or accuracy score is generated.",
                            "Track IDs identify current observations; no claim of unique physical vehicles or seamless recovery.",
                            "Synthetic data, when marked synthetic, validates software behavior only."],
        }
        (self.output / "summary.json").write_text(_json(self._summary), encoding="utf-8")
        self._finished = True
        self.close()
        return self._summary

    def close(self):
        if not self._closed:
            for handle in (self._chunk, self._frames, self._windows, self._index):
                if handle is not None:
                    handle.close()
            self._closed = True
