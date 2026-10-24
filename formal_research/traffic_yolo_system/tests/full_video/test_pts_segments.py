"""Synthetic irregular-PTS boundary tests; no dataset/model/GPU reads."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_pts_timing as fixtures
from full_video import pts_timing


class ExactPtsSegmentTests(unittest.TestCase):
    setUp = fixtures.AuditedPtsTests.setUp
    save = fixtures.AuditedPtsTests.save
    preflight = fixtures.AuditedPtsTests.preflight

    def segment(self, start=1 / 90000, end=7500 / 90000):
        self.config["segment"] = {"start_seconds": start, "end_seconds": end}

    def test_segment_preserves_original_indices_ticks_and_full_metadata(self):
        self.segment()
        value = self.preflight()
        self.assertEqual(value["first_frame_index"], 1)
        self.assertEqual(value["end_frame_index_exclusive"], 3)
        self.assertEqual(value["frame_count"], 2)
        self.assertEqual(value["input_frame_count"], 4)
        self.assertEqual(value["input_duration_ticks"], 9500)
        rows = list(pts_timing.iter_pts(value))
        self.assertEqual([row["frame_index"] for row in rows], [0, 1, 2])
        self.assertEqual([row["pts"] for row in rows], [100, 101, 3100])
        self.assertEqual([row["duration_ticks"] for row in rows], [1, 2999, 4500])
        self.assertLess(rows[0]["timestamp_seconds"], 0)
        self.assertEqual(rows[1]["timestamp_seconds"], 0)
        self.assertEqual(rows[2]["source_timestamp_seconds"], 3000 / 90000)
        self.assertAlmostEqual(rows[2]["timestamp_seconds"], 2999 / 90000, places=14)

    def test_full_source_explicit_bounds_equal_null(self):
        full = self.preflight()
        self.segment(0, 9500 / 90000)
        selected = self.preflight()
        self.assertEqual(list(pts_timing.iter_pts(full)), list(pts_timing.iter_pts(selected)))
        for key in ("frame_count", "source_start_seconds", "source_end_seconds"):
            self.assertEqual(full[key], selected[key])

    def test_single_original_final_frame_preserves_tail(self):
        self.segment(7500 / 90000, 9500 / 90000)
        value = self.preflight()
        self.assertEqual(value["frame_count"], 1)
        self.assertEqual(value["first_frame_index"], 3)
        self.assertEqual(list(pts_timing.iter_pts(value))[-1]["duration_ticks"], 2000)

    def test_tick_aligned_inside_a_frame_is_rejected(self):
        self.segment(2 / 90000, 7500 / 90000)
        with self.assertRaisesRegex(ValueError, "frame boundaries"):
            self.preflight()

    def test_nominal_cfr_rounding_is_rejected(self):
        self.segment(.01, .06)
        with self.assertRaisesRegex(ValueError, "frame boundaries"):
            self.preflight()

    def test_non_tick_decimal_is_rejected(self):
        self.segment(.010005, 7500 / 90000)
        with self.assertRaisesRegex(ValueError, "exact original PTS"):
            self.preflight()

    def test_partial_extra_nan_equal_and_reversed_bounds_rejected(self):
        for value in ({"start_seconds": 0}, {"start_seconds": 0, "end_seconds": .1, "fps": 30},
                      {"start_seconds": float("nan"), "end_seconds": .1},
                      {"start_seconds": .1, "end_seconds": .1},
                      {"start_seconds": .2, "end_seconds": .1}):
            self.config["segment"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preflight()

    def test_bounds_outside_source_are_rejected(self):
        self.segment(0, 10000 / 90000)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.preflight()

    def test_bad_unused_tail_still_fails_preflight(self):
        self.segment(0, 3000 / 90000)
        self.rows[3]["pts"] += 1
        self.save()
        with self.assertRaisesRegex(ValueError, "gap/overlap"):
            self.preflight()

    def test_iteration_checks_tail_even_after_last_yielded_segment_frame(self):
        self.segment(0, 3000 / 90000)
        value = self.preflight()
        original = pts_timing._timing_rows

        def corrupted_tail(*args, **kwargs):
            for row in original(*args, **kwargs):
                if row["frame_index"] == 3:
                    raise ValueError("unused tail checked")
                yield row

        with patch.object(pts_timing, "_timing_rows", corrupted_tail):
            iterator = pts_timing.iter_pts(value)
            self.assertEqual(next(iterator)["frame_index"], 0)
            self.assertEqual(next(iterator)["frame_index"], 1)
            with self.assertRaisesRegex(ValueError, "unused tail checked"):
                next(iterator)

    def test_main_test_segment_guard_precedes_reads(self):
        self.segment()
        self.task["split"] = "Main Test"
        with patch.object(Path, "open", side_effect=AssertionError("no protected reads")), \
             patch.object(Path, "stat", side_effect=AssertionError("no protected reads")):
            with self.assertRaisesRegex(ValueError, "Main Test cannot be opened"):
                self.preflight()


if __name__ == "__main__":
    unittest.main()
