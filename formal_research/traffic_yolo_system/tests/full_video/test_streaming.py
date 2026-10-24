"""Synthetic software tests only; these do not measure model accuracy."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from full_video.streaming import StreamWriter


LAYOUT = {
    "frame_width": 100, "frame_height": 100, "assignment_point": "bottom_center",
    "boundary_tie_break": "zone_order",
    "zones": [
        {"zone_name": "left", "polygon_points": [[0, 0], [50, 0], [50, 99], [0, 99]]},
        {"zone_name": "right", "polygon_points": [[50, 0], [99, 0], [99, 99], [50, 99]]},
    ],
}
METADATA = {"source": "synthetic_software_test", "split": "Train", "run": "synthetic-v1",
            "model_version": "not_used", "roi_version": "synthetic-v1", "rules_version": "counts-v1",
            "synthetic": True}


def frame(index, start, duration, left=0, right=0, valid=True):
    tracks = []
    for x, count in ((10, left), (65, right)):
        for _ in range(count):
            tracks.append({"track_id": len(tracks) + 1, "class_name": "car", "confidence": .8,
                           "box_xyxy": [x, 5, x + 10, 20]})
    return {"frame_index": index, "source_timestamp_seconds": start,
            "duration_seconds": duration, "valid": valid, "tracks_primary": tracks,
            "missing_reason": None if valid else "decode_failure"}


def read_rows(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle]


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full-video-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def writer(self, start=100, end=125, **kwargs):
        result = StreamWriter(self.output, METADATA, LAYOUT, start, end, **kwargs)
        self.addCleanup(result.close)
        return result

    def test_boundary_tail_missing_and_true_zero(self):
        writer = self.writer(chunk_frames=2)
        writer.add(frame(3000, 100, 5, left=2))
        writer.add(frame(3150, 105, 5))
        writer.add(frame(3300, 110, 2, valid=False))
        writer.add(frame(3360, 112, 3, right=4))
        writer.add(frame(3630, 121, 2))
        summary = writer.finish()
        self.assertAlmostEqual(summary["valid_duration_seconds"], 15)
        self.assertAlmostEqual(summary["missing_duration_seconds"], 10)
        self.assertEqual(summary["frame_count"], 5)
        self.assertEqual(summary["valid_frame_count"], 4)
        self.assertEqual(summary["generated_missing_interval_count"], 2)
        self.assertEqual(summary["window_count"], 3)
        self.assertAlmostEqual(summary["zones"]["left"]["mean_current_vehicles"], 10 / 15)
        rows = read_rows(self.output / "windows.jsonl")
        left = [row for row in rows if row["zone_name"] == "left"]
        self.assertEqual([row["duration_seconds"] for row in left], [10, 10, 5])
        self.assertEqual([row["validity"] for row in left], ["valid", "partial", "partial"])
        self.assertEqual(left[-1]["is_tail_window"], True)
        self.assertEqual(left[-1]["observed_zero"], True)
        self.assertEqual(left[-1]["mean_current_vehicles"], 0)
        records = read_rows(self.output / "frames.jsonl")
        missing = [row for row in records if not row["valid"]]
        self.assertTrue(all(row["zone_counts"] is None for row in missing))
        self.assertTrue(all(row["total_in_zones"] is None for row in missing))
        self.assertEqual([row["frame_index"] for row in records if row["record_type"] == "frame"],
                         [3000, 3150, 3300, 3360, 3630])
        index = read_rows(self.output / "timeline" / "index.jsonl")
        self.assertEqual([row["records"] for row in index], [2, 2, 2, 1])
        self.assertEqual(index[-1]["source_end_seconds_exclusive"], 125)
        for chunk in index:
            self.assertEqual(len(read_rows(self.output / "timeline" / chunk["path"])), chunk["records"])

    def test_entire_missing_video_and_tail_emit_null(self):
        result = self.writer().finish()
        self.assertEqual(result["frame_count"], 0)
        self.assertEqual(result["validity"], "missing")
        self.assertEqual(result["missing_duration_seconds"], 25)
        self.assertEqual(result["zones"]["left"]["mean_current_vehicles"], None)
        rows = read_rows(self.output / "windows.jsonl")
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row["validity"] == "missing" for row in rows))
        self.assertTrue(all(row["min_current_vehicles"] is None for row in rows))
        self.assertTrue(all(row["traffic_state"] == "not_computed" for row in rows))

    def test_clamping_to_requested_segment_and_crossing_windows(self):
        writer = self.writer(start=100, end=112)
        writer.add(frame(2990, 99, 4, left=2))
        writer.add(frame(3080, 103, 20, left=4))
        result = writer.finish()
        self.assertEqual(result["valid_duration_seconds"], 12)
        self.assertEqual(result["missing_duration_seconds"], 0)
        self.assertEqual(result["zones"]["left"]["mean_current_vehicles"], 3.5)
        records = read_rows(self.output / "frames.jsonl")
        self.assertEqual([row["duration_seconds"] for row in records], [3, 9])
        self.assertEqual(records[0]["input_source_timestamp_seconds"], 99)
        self.assertEqual(records[0]["source_timestamp_seconds"], 100)
        self.assertEqual(records[0]["timestamp_seconds"], 0)
        self.assertEqual(read_rows(self.output / "windows.jsonl")[-1]["duration_seconds"], 2)

    def test_cfr_30_fps_matches_pilot_arithmetic_mean(self):
        writer = self.writer(start=1600, end=1720)
        values = []
        for index in range(3600):
            count = (index * 7) % 9
            values.append(count)
            writer.add(frame(48000 + index, 1600 + index / 30, 1 / 30, left=count))
        summary = writer.finish()
        self.assertAlmostEqual(summary["zones"]["left"]["mean_current_vehicles"], sum(values) / len(values), delta=1e-9)
        self.assertAlmostEqual(summary["valid_duration_seconds"], 120, delta=1e-9)
        self.assertEqual(summary["generated_missing_interval_count"], 0)
        windows = [row for row in read_rows(self.output / "windows.jsonl") if row["zone_name"] == "left"]
        self.assertEqual(len(windows), 12)
        for index, window in enumerate(windows):
            selected = values[index * 300:(index + 1) * 300]
            self.assertEqual(window["valid_frame_intersections"], 300)
            self.assertAlmostEqual(window["mean_current_vehicles"], sum(selected) / len(selected), delta=1e-9)
            self.assertEqual(window["min_current_vehicles"], min(selected))
            self.assertEqual(window["max_current_vehicles"], max(selected))

    def test_duration_weighting_and_prefix_missing(self):
        writer = self.writer(start=100, end=110)
        writer.add(frame(1, 102, 2, left=1))
        writer.add(frame(2, 104, 6, left=3))
        result = writer.finish()
        self.assertEqual(result["zones"]["left"]["mean_current_vehicles"], 2.5)
        self.assertEqual(result["valid_duration_seconds"], 8)
        self.assertEqual(result["missing_duration_seconds"], 2)

    def test_shared_geometry_assigns_boundary_to_first_zone(self):
        writer = self.writer(start=100, end=101)
        record = frame(0, 100, 1, left=1)
        record["tracks_primary"][0]["box_xyxy"] = [40, 5, 60, 20]
        writer.add(record)
        result = writer.finish()
        self.assertEqual(result["zones"]["left"]["mean_current_vehicles"], 1)
        self.assertEqual(result["zones"]["right"]["mean_current_vehicles"], 0)
        self.assertNotIn("assigned_zone", record["tracks_primary"][0])

    def test_invalid_inputs_do_not_advance_writer(self):
        writer = self.writer(start=100, end=102)
        invalid = []
        for key, value in (("duration_seconds", 0), ("duration_seconds", float("nan")),
                           ("source_timestamp_seconds", float("inf")), ("valid", 1),
                           ("frame_index", -1), ("timestamp_seconds", 1)):
            item = frame(0, 100, 1)
            item[key] = value
            invalid.append(item)
        item = frame(0, 100, 1, left=1)
        item["tracks_primary"].append(dict(item["tracks_primary"][0]))
        invalid.append(item)
        item = frame(0, 100, 1, left=1)
        item["tracks_primary"][0]["confidence"] = float("inf")
        invalid.append(item)
        item = frame(0, 100, 1, left=1)
        item["tracks_primary"][0]["track_id"] = 0
        invalid.append(item)
        for box in ([10, 5, 10, 20], [10, 20, 30, 20]):
            item = frame(0, 100, 1, left=1)
            item["tracks_primary"][0]["box_xyxy"] = box
            invalid.append(item)
        item = frame(0, 100, 1, valid=False)
        item.pop("missing_reason")
        invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                writer.add(item)
        self.assertEqual(writer.frame_count, 0)
        writer.add(frame(0, 100, 1))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            writer.add(frame(1, 100.5, 1))
        with self.assertRaisesRegex(ValueError, "increase strictly"):
            writer.add(frame(0, 101, 1))
        writer.add(frame(1, 101, 1))
        self.assertEqual(writer.finish()["valid_duration_seconds"], 2)

    def test_cannot_overwrite_and_finish_is_idempotent(self):
        writer = self.writer()
        result = writer.finish()
        self.assertEqual(writer.finish(), result)
        with self.assertRaises(RuntimeError):
            writer.add(frame(0, 100, 1))
        with self.assertRaises(FileExistsError):
            StreamWriter(self.output, METADATA, LAYOUT, 100, 125)

    def test_main_test_is_rejected_before_output_creation(self):
        with self.assertRaisesRegex(ValueError, "development"):
            StreamWriter(self.output, {**METADATA, "split": "Main Test"}, LAYOUT, 0, 10)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_outside_analysis_or_inconsistent_metadata_rejected(self):
        writer = self.writer()
        with self.assertRaisesRegex(ValueError, "does not intersect"):
            writer.add(frame(0, 99, 1))
        with self.assertRaisesRegex(ValueError, "does not intersect"):
            writer.add(frame(0, 125, 1))
        with self.assertRaisesRegex(ValueError, "relative"):
            writer.add({**frame(0, 100, 1), "timestamp_seconds": 100})

    def test_provenance_metadata_survives_every_output(self):
        extra = {"source_id": "synthetic-source", "model_sha256": "synthetic-no-model",
                 "configuration_sha256": "synthetic-no-config", "method_status": "not_frozen",
                 "frame_index_origin": "input_file", "input_source_offset_seconds": 100}
        writer = StreamWriter(self.output, {**METADATA, **extra}, LAYOUT, 100, 101)
        self.addCleanup(writer.close)
        writer.add(frame(0, 100, 1))
        summary = writer.finish()
        for row in (summary, read_rows(self.output / "frames.jsonl")[0],
                    read_rows(self.output / "windows.jsonl")[0],
                    read_rows(self.output / "timeline" / "index.jsonl")[0],
                    read_rows(self.output / "timeline" / "part-000000.jsonl")[0]):
            for key, value in extra.items():
                self.assertEqual(row[key], value)

    def test_failure_close_does_not_publish_summary(self):
        writer = self.writer()
        writer.add(frame(0, 100, 1))
        writer.close()
        self.assertFalse((self.output / "summary.json").exists())
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            writer.finish()

    def test_unrepresentable_time_scale_and_overflow_are_rejected(self):
        for start, end, window in ((100, 110, 1e-30), (10**1000, 10**1001, 1)):
            with self.subTest(start=start), self.assertRaises(ValueError):
                StreamWriter(self.output, METADATA, LAYOUT, start, end, window_seconds=window)
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
