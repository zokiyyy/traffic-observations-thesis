"""Regression and geometric safety checks for explicitly shared ROI boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM / "src"))

from roi_geometry import assign_box, assign_point, assignment_metadata, validate_layout
from render_video_tracking_pilot import read_zones, validate_record
from summarize_video_tracking_pilot import assign_zone


def layout(*polygons, policy="zone_order"):
    value = {"assignment_point": "bottom_center", "frame_width": 100, "frame_height": 100,
             "zones": [{"zone_name": f"zone{i}", "polygon_points": points}
                       for i, points in enumerate(polygons)]}
    if policy is not None:
        value["boundary_tie_break"] = policy
    return value


LEFT = [[10, 10], [30, 10], [30, 30], [10, 30]]
RIGHT = [[30, 10], [50, 10], [50, 30], [30, 30]]


class GeometryTests(unittest.TestCase):
    def test_shared_edge_and_three_way_vertex_have_one_owner(self):
        value = layout(LEFT, RIGHT, [[30, 30], [50, 30], [50, 50], [30, 50]])
        polygons = validate_layout(value)
        for point in ((30, 20), (30, 30)):
            self.assertEqual(assign_point(point, polygons, "zone_order"), "zone0")
            self.assertEqual(assign_point(point, polygons[::-1], "zone_order"),
                             "zone2" if point == (30, 30) else "zone1")

    def test_vertex_only_touch_allowed(self):
        polygons = validate_layout(layout(LEFT, [[30, 30], [50, 30], [50, 50], [30, 50]]))
        self.assertEqual(assign_point((30, 30), polygons, "zone_order"), "zone0")

    def test_sloping_shared_edge_is_not_pixel_rounded(self):
        polygons = validate_layout(layout([[10, 10], [30, 20], [10, 30]],
                                         [[10, 10], [30, 5], [30, 20]]))
        self.assertEqual(assign_point((20.25, 15.125), polygons, "zone_order"), "zone0")
        self.assertEqual(assign_point((20.25, 15.124), polygons, "zone_order"), "zone1")
        self.assertEqual(assign_point((20.25, 15.126), polygons, "zone_order"), "zone0")

    def test_internal_overlap_is_rejected_even_without_contained_vertices(self):
        horizontal = [[10, 20], [50, 20], [50, 30], [10, 30]]
        vertical = [[25, 5], [35, 5], [35, 50], [25, 50]]
        with self.assertRaisesRegex(ValueError, "interiors overlap"):
            validate_layout(layout(horizontal, vertical))

    def test_containment_identical_and_subpixel_overlap_rejected(self):
        cases = [LEFT, [[15, 15], [20, 15], [20, 20], [15, 20]],
                 [[29.999999, 10], [50, 10], [50, 30], [29.999999, 30]]]
        for other in cases:
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, "interiors overlap"):
                validate_layout(layout(LEFT, other))

    def test_actual_inside_overlap_never_hidden_by_assignment_order(self):
        unvalidated = [("a", np.asarray(LEFT)), ("b", np.asarray(LEFT))]
        with self.assertRaisesRegex(ValueError, "Overlapping zone assignment"):
            assign_point((20, 20), unvalidated, "zone_order")

    def test_self_intersection_and_adjacent_backtracking_rejected(self):
        cases = [
            [[10, 10], [40, 40], [10, 40], [40, 10]],
            [[10, 10], [40, 10], [30, 10], [30, 40], [10, 40]],
            [[10, 10], [40, 10], [40, 40], [25, 10], [10, 40]],
        ]
        for points in cases:
            with self.subTest(points=points), self.assertRaisesRegex(ValueError, "Self-"):
                validate_layout(layout(points))

    def test_concave_polygons_touch_along_multiple_edges(self):
        concave = [[10, 10], [50, 10], [50, 20], [20, 20], [20, 50], [10, 50]]
        inside_notch = [[20, 20], [50, 20], [50, 50], [20, 50]]
        polygons = validate_layout(layout(concave, inside_notch))
        self.assertEqual(assign_point((20, 30), polygons, "zone_order"), "zone0")
        self.assertEqual(assign_point((30, 30), polygons, "zone_order"), "zone1")

    def test_collinear_vertices_and_clockwise_polygons_allowed(self):
        with_collinear = [[10, 10], [20, 10], [30, 10], [30, 30], [10, 30], [10, 10]]
        polygons = validate_layout(layout(with_collinear[::-1], RIGHT[::-1]))
        self.assertEqual(assign_point((30, 20), polygons, "zone_order"), "zone0")

    def test_real_outside_point_and_subpixel_gap_stay_outside(self):
        right_gap = [[30.00001, 10], [50, 10], [50, 30], [30.00001, 30]]
        polygons = validate_layout(layout(LEFT, right_gap))
        for point in ((5, 20), (30.000005, 20), (110, 20), (25, 31)):
            self.assertEqual(assign_point(point, polygons, "zone_order"), "outside_roi")

    def test_legacy_default_rejects_shared_raster_edge_and_shared_hit(self):
        with self.assertRaisesRegex(ValueError, "pixel scale"):
            validate_layout(layout(LEFT, RIGHT, policy=None))
        polygons = [("a", np.asarray(LEFT)), ("b", np.asarray(RIGHT))]
        with self.assertRaisesRegex(ValueError, "Overlapping zone assignment"):
            assign_box([29, 10, 31, 20], polygons)

    def test_unknown_policy_and_invalid_geometry_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_layout(layout(LEFT, policy="nearest"))
        for points in ([[[10, 10], [10, 10], [20, 20]]], [[[10, 10], [100, 20], [10, 30]]]):
            with self.assertRaises(ValueError):
                validate_layout(layout(*points))

    def test_renderer_and_summary_use_same_shared_edge_owner(self):
        value = layout(LEFT, RIGHT)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "zones.json"
            path.write_text(json.dumps(value))
            loaded = read_zones(path)
        box = [28.0, 12.0, 32.0, 20.0]
        self.assertEqual(assign_zone(box, validate_layout(value), "zone_order"), "zone0")
        record = {"frame_index": 0, "timestamp_seconds": 0.0, "source_timestamp_seconds": 10.0,
                  "tracks_primary": [{"track_id": 1, "box_xyxy": box, "confidence": .9,
                                       "assigned_zone": "zone0"}],
                  "zone_counts": {"zone0": 1, "zone1": 0}, "total_in_zones": 1, "outside_zone_count": 0}
        validate_record(record, 0, loaded, 100, 100)
        record["tracks_primary"][0]["assigned_zone"] = "zone1"
        with self.assertRaisesRegex(ValueError, "geometry"):
            validate_record(record, 0, loaded, 100, 100)
        self.assertEqual(assignment_metadata(loaded)["boundary_tie_break"], "zone_order")

    def test_legacy_saved_video_counts_unchanged_for_all_frames(self):
        pilot = SYSTEM / "outputs/video_pipeline/yolo11s_newport_val_120s_v1_2026-09-23"
        zones_path = pilot / "input/zones.json"
        records_path = pilot / "analysis_vehicle_dedup/frames_with_zones.jsonl"
        if not zones_path.is_file() or not records_path.is_file():
            self.skipTest("Published local pilot fixture not available")
        value = json.loads(zones_path.read_text())
        polygons = validate_layout(value)
        self.assertEqual(value.get("boundary_tie_break", "error"), "error")
        frames_checked = 0
        with records_path.open() as source:
            for line in source:
                record = json.loads(line)
                counts = {name: 0 for name, _ in polygons}
                outside = 0
                for track in record["tracks_primary"]:
                    assigned = assign_box(track["box_xyxy"], polygons)
                    self.assertEqual(assigned, track["assigned_zone"])
                    if assigned == "outside_roi":
                        outside += 1
                    else:
                        counts[assigned] += 1
                self.assertEqual(counts, record["zone_counts"])
                self.assertEqual(outside, record["outside_zone_count"])
                frames_checked += 1
        self.assertEqual(frames_checked, 3600)


if __name__ == "__main__":
    unittest.main()
