"""Synthetic state-derivation boundary checks; no media, model or Test data."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from traffic_state_demo import (SOURCE_ID, TICKS_PER_SECOND, MotionHistory, aggregate_frames,
                                build_windows, calibrate, classify_windows, density_level, split_ticks,
                                validate_source, weighted_median)


def track(identity=1, zone="road_left", x=0):
    return {"track_id": identity, "assigned_zone": zone,
            "box_xyxy": [x, 0, x + 10, 10]}


def window(index, count=10, motion=1, coverage=1, complete=True):
    return {"index": index, "start_s": index * 10.,
            "end_s": index * 10. + (10 if complete else .015), "complete": complete,
            "zones": [{"zone_id": "road_left", "count_mean": count,
                       "motion_median_px_s": motion, "motion_coverage": coverage,
                       "motion_vehicle_seconds": 10 if coverage else 0,
                       "motion_available": complete and coverage >= .5}]}


class TimingAndHistoryTests(unittest.TestCase):
    def test_one_tick_and_vfr_cross_window_preserve_every_tick(self):
        self.assertEqual(list(split_ticks(0, 1)), [(0, 1)])
        self.assertEqual(list(split_ticks(899999, 9002)), [(0, 1), (1, 9001)])

    def test_one_tick_motion_is_unknown_until_one_second(self):
        history = MotionHistory(["road_left"])
        self.assertIsNone(history.update(0, 0, [track(x=0)])[1][1])
        self.assertIsNone(history.update(1, 1, [track(x=4)])[1][1])
        result = history.update(2, 90001, [track(x=14)])
        self.assertAlmostEqual(result[1][1], 10.)

    def test_missing_observation_and_invalid_frame_reset_history(self):
        history = MotionHistory(["road_left"])
        history.update(0, 0, [track()])
        history.update(1, 45000, [])
        self.assertIsNone(history.update(2, 90000, [track(x=1)])[1][1])
        history.update(3, 100000, [], valid=False)
        self.assertIsNone(history.update(4, 190000, [track(x=2)])[1][1])

    def test_zone_change_and_nonconsecutive_frame_reset_history(self):
        history = MotionHistory(["road_left", "road_top"])
        history.update(0, 0, [track()])
        self.assertIsNone(history.update(1, 90000, [track(zone="road_top")])[1][1])
        self.assertIsNone(history.update(3, 180000, [track(zone="road_top")])[1][1])

    def test_historical_point_older_than_maximum_span_is_not_used(self):
        history = MotionHistory(["road_left"])
        history.update(0, 0, [track()])
        self.assertIsNone(history.update(1, 108001, [track(x=10)])[1][1])

    def test_motion_histories_do_not_retain_disappeared_ids(self):
        history = MotionHistory(["road_left"])
        for index in range(80):
            history.update(index, index * 3000, [track()])
        self.assertLessEqual(len(history.histories[1]["points"]), 38)
        history.update(80, 240000, [])
        self.assertEqual(history.histories, {})

    def test_weighted_median_uses_duration_not_number_of_tracks(self):
        self.assertEqual(weighted_median([(1, 1), (5, 8), (100, 1)]), 5)
        self.assertEqual(weighted_median([]), -1)

    def test_aggregate_vfr_count_duration_and_tail(self):
        rows = [
            {"frame_index": 0, "pts": 0, "duration_ticks": 899999,
             "valid": True, "tracks_primary": [track()], "zone_counts": {"road_left": 1}},
            {"frame_index": 1, "pts": 899999, "duration_ticks": 3,
             "valid": True, "tracks_primary": [track(), track(2)], "zone_counts": {"road_left": 2}},
        ]
        result = aggregate_frames(rows, ["road_left"])
        self.assertEqual(result["duration_ticks"], 900002)
        self.assertEqual(result["counts"][0]["road_left"], 900001)
        self.assertEqual(result["counts"][1]["road_left"], 4)
        self.assertEqual(result["valid_ticks"], {0: 900000, 1: 2})

    def test_duplicate_id_and_time_gap_fail(self):
        history = MotionHistory(["road_left"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            history.update(0, 0, [track(), track()])
        row = {"frame_index": 0, "pts": 0, "duration_ticks": 1,
               "valid": True, "tracks_primary": [], "zone_counts": {"road_left": 0}}
        with self.assertRaisesRegex(ValueError, "contiguous"):
            aggregate_frames([row, {**row, "frame_index": 1, "pts": 2}], ["road_left"])


class ClassificationTests(unittest.TestCase):
    def test_collapsed_calibration_is_explicitly_unknown_with_no_streak(self):
        rows = [window(i, count=4) for i in range(4)]
        classify_windows(rows, {"road_left": {"density_low_upper": 4,
                           "density_high_lower": 4, "motion_low_upper": 2}})
        for w in rows:
            row = w["zones"][0]
            self.assertEqual(row["traffic_state"], "calibration_insufficient")
            self.assertEqual(row["density_level"], "unknown")
            self.assertEqual(row["high_count_duration_s"], 0)
            self.assertEqual(row["joint_duration_s"], 0)

    def test_calibration_excludes_tail_and_requires_thirty_motion_windows(self):
        rows = [window(i, count=i + 1) for i in range(29)] + [window(29, count=999, complete=False)]
        limits = calibrate(rows, ["road_left"])["road_left"]
        self.assertEqual(limits["density_calibration_windows"], 29)
        self.assertEqual(limits["motion_low_upper"], -1)
        rows.append(window(30, count=30))
        self.assertEqual(calibrate(rows, ["road_left"])["road_left"]["motion_low_upper"], 1)

    def test_window_builder_requires_coverage_and_crosschecks_original_mean(self):
        original = {"source_id": SOURCE_ID, "split": "Validation", "window_index": 0,
                    "zone_name": "road_left", "source_start_seconds": 0.,
                    "source_end_seconds_exclusive": 10., "valid_duration_seconds": 10.,
                    "mean_current_vehicles": 2.}
        aggregate = {"duration_ticks": 900000, "valid_ticks": {0: 900000},
                     "counts": {0: {"road_left": 1800000}},
                     "motions": {(0, "road_left"): {1: [540000., 540000]}}}
        row = build_windows([original], aggregate, ["road_left"])[0]["zones"][0]
        self.assertEqual(row["motion_coverage"], .3)
        self.assertFalse(row["motion_available"])
        self.assertEqual(row["motion_median_px_s"], -1)
        with self.assertRaisesRegex(ValueError, "mean differs"):
            build_windows([{**original, "mean_current_vehicles": 3}], aggregate, ["road_left"])

    def test_equal_quantiles_do_not_invent_three_levels(self):
        self.assertEqual(density_level(0, 4, 4), "empty")
        self.assertEqual(density_level(4, 4, 4), "unknown")
        self.assertEqual(density_level(8, 4, 4), "unknown")

    def test_thirty_second_high_and_joint_persistence(self):
        rows = [window(i) for i in range(3)]
        classify_windows(rows, {"road_left": {"density_low_upper": 1,
                           "density_high_lower": 5, "motion_low_upper": 2}})
        self.assertEqual([x["zones"][0]["traffic_state"] for x in rows],
                         ["no_persistent_signal", "no_persistent_signal", "high_count_low_motion_persistent"])
        self.assertEqual(rows[-1]["zones"][0]["joint_duration_s"], 30)

    def test_missing_window_breaks_persistence(self):
        rows = [window(i) for i in (0, 1, 3)]
        classify_windows(rows, {"road_left": {"density_low_upper": 1,
                           "density_high_lower": 5, "motion_low_upper": 2}})
        self.assertEqual(rows[-1]["zones"][0]["high_count_duration_s"], 10)
        self.assertEqual(rows[-1]["zones"][0]["joint_duration_s"], 10)

    def test_low_coverage_is_unknown_motion_not_zero(self):
        rows = [window(i, coverage=0, motion=-1) for i in range(3)]
        classify_windows(rows, {"road_left": {"density_low_upper": 1,
                           "density_high_lower": 5, "motion_low_upper": 2}})
        self.assertEqual(rows[0]["zones"][0]["traffic_state"], "motion_insufficient")
        self.assertEqual(rows[-1]["zones"][0]["traffic_state"], "high_count_persistent")
        self.assertEqual(rows[-1]["zones"][0]["joint_duration_s"], 0)
        self.assertEqual(rows[-1]["zones"][0]["motion_median_px_s"], -1)

    def test_tail_window_not_classified_and_resets_duration(self):
        rows = [window(i) for i in range(3)] + [window(3, complete=False)]
        classify_windows(rows, {"road_left": {"density_low_upper": 1,
                           "density_high_lower": 5, "motion_low_upper": 2}})
        last = rows[-1]["zones"][0]
        self.assertEqual(last["traffic_state"], "insufficient_window")
        self.assertEqual(last["density_level"], "unknown")
        self.assertEqual(last["high_count_duration_s"], 0)

    def test_only_explicit_approved_validation_source(self):
        with self.assertRaisesRegex(ValueError, "Validation"):
            validate_source({"source_id": "anything", "split": "Main Test"})
        with self.assertRaisesRegex(ValueError, "source"):
            validate_source({"source_id": "unapproved.mp4", "split": "Validation"})


if __name__ == "__main__":
    unittest.main()
