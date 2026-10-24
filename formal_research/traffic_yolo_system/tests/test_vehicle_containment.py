"""Bounded geometry and provenance tests; no dataset or model is loaded."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM / "src"))
from vehicle_containment import suppress_contained_vehicles, validate_policy


def policy():
    return {
        "schema_version": 2, "heavy_classes": [2, 3], "min_containment": 0.9,
        "min_area_ratio": 1.2, "max_bottom_gap_fraction": 0.15,
        "min_keeper_confidence": 0.35, "ambiguity_min_containment": 0.5,
        "development_only": True,
        "representative_policy": "confident_same_class_outer_then_low_confidence",
    }


def detection(box=(0, 0, 100, 100), confidence=0.8, class_id=2):
    return {"box_xyxy": list(box), "confidence": confidence, "class_id": class_id,
            "class_name": {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}[class_id]}


class VehicleContainmentTests(unittest.TestCase):
    def test_confident_same_heavy_class_uses_original_indices(self):
        for classes in ((2, 2), (3, 3)):
            raw = [detection(class_id=0), detection(confidence=0.9, class_id=classes[0]),
                   detection(class_id=1), detection((20, 20, 80, 100), 0.4, classes[1])]
            kept, removed, unresolved = suppress_contained_vehicles(raw, [1, 3], policy())
            with self.subTest(classes=classes):
                self.assertEqual(kept, [1])
                self.assertEqual(len(removed), 1)
                self.assertEqual((removed[0]["removed_index"], removed[0]["kept_index"]), (3, 1))
                self.assertEqual(removed[0]["containment"], 1)
                self.assertEqual(removed[0]["bottom_gap_fraction"], 0)
                self.assertEqual(removed[0]["class_ids"], {"removed": classes[1], "kept": classes[0]})
                self.assertEqual(removed[0]["stage"], "A")
                self.assertEqual(unresolved, [])

    def test_confident_outer_box_survives_more_confident_same_class_fragment(self):
        raw = [detection(confidence=0.4), detection((20, 20, 80, 100), 0.9, 2)]
        kept, removed, _ = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0])
        self.assertEqual((removed[0]["removed_index"], removed[0]["kept_index"]), (1, 0))
        self.assertEqual(removed[0]["stage"], "A")

    def test_eastgate_full_bus_regression_preserves_complete_localization(self):
        # The v1 confidence-only representative rule removed the full bus in
        # Validation F0472. The outer box must survive even with a lower score.
        raw = [detection((543.5885009765625, 83.05764770507812,
                          628.6190185546875, 123.01080322265625), 0.46697375178337097),
               detection((543.2921752929688, 89.8724136352539,
                          577.2319946289062, 122.84005737304688), 0.5503694415092468)]
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(removed[0]["removed_index"], 1)
        self.assertEqual(removed[0]["kept_index"], 0)
        self.assertEqual(unresolved, [])

    def test_confident_different_classes_are_not_merged(self):
        for classes in ((2, 3), (3, 2)):
            raw = [detection(confidence=0.4, class_id=classes[0]),
                   detection((20, 20, 80, 100), 0.9, classes[1])]
            kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
            with self.subTest(classes=classes):
                self.assertEqual(kept, [0, 1])
                self.assertEqual(removed, [])
                self.assertIn("confident_different_class_pair_not_merged",
                              unresolved[0]["failed_conditions"])

    def test_low_score_any_heavy_class_can_be_suppressed_by_confident_survivor(self):
        for classes in ((2, 2), (3, 3), (2, 3), (3, 2)):
            raw = [detection(confidence=0.2, class_id=classes[0]),
                   detection((20, 20, 80, 100), 0.6, classes[1])]
            kept, removed, _ = suppress_contained_vehicles(raw, [0, 1], policy())
            with self.subTest(classes=classes):
                self.assertEqual(kept, [1])
                self.assertEqual((removed[0]["removed_index"], removed[0]["kept_index"]), (0, 1))
                self.assertEqual(removed[0]["stage"], "B")

    def test_removed_confident_fragment_cannot_suppress_low_score_box(self):
        raw = [detection((0, 0, 100, 100), 0.6),
               detection((0, 15, 100, 94), 0.8),
               detection((0, 60, 100, 90), 0.2, 3)]
        self.assertEqual(suppress_contained_vehicles(raw, [1, 2], policy())[0], [1])
        kept, removed, _ = suppress_contained_vehicles(raw, [0, 1, 2], policy())
        self.assertEqual(kept, [0, 2])
        self.assertEqual([(item["removed_index"], item["kept_index"]) for item in removed], [(1, 0)])

    def test_low_score_boxes_cannot_suppress_each_other(self):
        raw = [detection(confidence=0.3), detection((20, 20, 80, 100), 0.1)]
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(removed, [])
        self.assertIn("keeper_confidence_below_minimum", unresolved[0]["failed_conditions"])

    def test_adjacent_vehicles_are_kept(self):
        raw = [detection(), detection((95, 0, 195, 100), 0.7, 3)]
        self.assertEqual(suppress_contained_vehicles(raw, [0, 1], policy()), ([0, 1], [], []))

    def test_cars_and_motorcycles_never_participate(self):
        for pair in ((0, 2), (2, 0), (1, 3), (3, 1), (0, 0), (1, 1)):
            raw = [detection(class_id=pair[0]), detection((20, 20, 80, 100), 0.4, pair[1])]
            with self.subTest(pair=pair):
                self.assertEqual(suppress_contained_vehicles(raw, [0, 1], policy()), ([0, 1], [], []))

    def test_containment_boundary_is_inclusive(self):
        # Intersection/min(area) = 90/100 exactly, areas differ by 1.5.
        keeper = detection((0, 0, 15, 10), 0.8)
        at_limit = detection((6, 0, 16, 10), 0.5, 2)
        kept, removed, _ = suppress_contained_vehicles([keeper, at_limit], [0, 1], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(removed[0]["containment"], 0.9)
        outside = detection((6.001, 0, 16.001, 10), 0.5, 2)
        kept, removed, unresolved = suppress_contained_vehicles([keeper, outside], [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(removed, [])
        self.assertIn("containment_below_minimum", unresolved[0]["failed_conditions"])

    def test_area_ratio_boundary_is_inclusive(self):
        raw = [detection((0, 0, 12, 10)), detection((0, 0, 10, 10), 0.5)]
        kept, removed, _ = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(removed[0]["area_ratio"], 1.2)
        raw[0]["box_xyxy"][2] = 11.999
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertIn("area_ratio_below_minimum", unresolved[0]["failed_conditions"])

    def test_bottom_gap_boundary_is_inclusive(self):
        raw = [detection((0, 0, 100, 115)), detection((10, 0, 90, 100), 0.5)]
        kept, removed, _ = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(removed[0]["bottom_gap_fraction"], 0.15)
        raw[0]["box_xyxy"][3] = 115.001
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertIn("bottom_gap_above_maximum", unresolved[0]["failed_conditions"])

    def test_keeper_confidence_boundary_is_inclusive(self):
        raw = [detection(confidence=0.35), detection((20, 20, 80, 100), 0.1)]
        self.assertEqual(suppress_contained_vehicles(raw, [0, 1], policy())[0], [0])
        raw[0]["confidence"] = 0.349999
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(removed, [])
        self.assertIn("keeper_confidence_below_minimum", unresolved[0]["failed_conditions"])

    def test_stage_a_equal_area_and_confidence_ties_choose_earlier_keeper(self):
        raw = [detection((0, 0, 100, 100), 0.8), detection((10, 0, 110, 100), 0.8),
               detection((20, 20, 80, 100), 0.5)]
        kept, removed, _ = suppress_contained_vehicles(raw, [0, 1, 2], policy())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(removed[0]["kept_index"], 0)

    def test_stage_b_keeper_score_then_raw_index_priority(self):
        raw = [detection((0, 0, 100, 100), 0.8), detection((10, 0, 110, 100), 0.9, 3),
               detection((20, 20, 80, 100), 0.1)]
        self.assertEqual(suppress_contained_vehicles(raw, [0, 1, 2], policy())[1][0]["kept_index"], 1)
        raw[1]["confidence"] = 0.8
        self.assertEqual(suppress_contained_vehicles(raw, [0, 1, 2], policy())[1][0]["kept_index"], 0)

    def test_suppressed_box_cannot_start_a_suppression_chain(self):
        # A suppresses B. B could suppress C, but retained A cannot (bottom gap).
        raw = [detection((0, 0, 100, 100), 0.9),
               detection((0, 15, 100, 94), 0.8),
               detection((0, 60, 100, 90), 0.7)]
        self.assertEqual(suppress_contained_vehicles(raw, [1, 2], policy())[0], [1])
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1, 2], policy())
        self.assertEqual(kept, [0, 2])
        self.assertEqual([(x["removed_index"], x["kept_index"]) for x in removed], [(1, 0)])
        self.assertEqual([x["indices"] for x in unresolved], [[0, 2]])

    def test_unresolved_threshold_and_diagnostic_wording(self):
        raw = [detection((0, 0, 100, 100)), detection((50, 0, 150, 100), 0.7, 3)]
        kept, removed, unresolved = suppress_contained_vehicles(raw, [0, 1], policy())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(removed, [])
        self.assertEqual(unresolved[0]["containment"], 0.5)
        self.assertEqual(unresolved[0]["reason"], "unresolved_geometric_overlap_not_confirmed_duplicate")
        self.assertEqual(unresolved[0]["preferred_keeper_index"], 0)
        raw[1]["box_xyxy"][0] = 50.001
        raw[1]["box_xyxy"][2] = 150.001
        self.assertEqual(suppress_contained_vehicles(raw, [0, 1], policy())[2], [])

    def test_inputs_remain_verbatim_and_no_frame_state_is_kept(self):
        raw = [dict(detection(), frame_index=999, track_id=50, stopped=True),
               detection((20, 20, 80, 100), 0.5)]
        eligible, p = [0, 1], policy()
        before = deepcopy((raw, eligible, p))
        result = suppress_contained_vehicles(raw, eligible, p)
        self.assertEqual((raw, eligible, p), before)
        # Empty or skipped frames do not alter later decisions in this stateless filter.
        self.assertEqual(suppress_contained_vehicles([], [], p), ([], [], []))
        self.assertEqual(suppress_contained_vehicles(raw, eligible, p), result)
        self.assertEqual(suppress_contained_vehicles(raw, [], p), ([], [], []))

    def test_bad_policy_is_rejected(self):
        mutations = [("schema_version", True), ("schema_version", 1),
                     ("representative_policy", "confidence_only"),
                     ("heavy_classes", [3, 2]), ("heavy_classes", [2, 3, 0]),
                     ("heavy_classes", [2, True]), ("development_only", False),
                     ("min_containment", 0.8), ("min_area_ratio", 1.19),
                     ("max_bottom_gap_fraction", 0.16), ("min_keeper_confidence", 0.1),
                     ("ambiguity_min_containment", 0.4), ("min_containment", float("nan")),
                     ("min_containment", float("inf")), ("min_containment", True)]
        for key, value in mutations:
            p = policy()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_policy(p)
        for key in policy():
            p = policy()
            del p[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_policy(p)
        with self.assertRaises(ValueError):
            validate_policy(None)

    def test_invalid_detection_or_indices_fail_closed(self):
        invalid = [None, {**detection(), "class_id": True}, {**detection(), "class_id": 4},
                   {**detection(), "class_name": "car"}, {**detection(), "confidence": float("nan")},
                   {**detection(), "confidence": float("inf")}, {**detection(), "confidence": -0.1},
                   {**detection(), "confidence": 1.1}, {**detection(), "confidence": True},
                   detection((0, 0, 0, 10)), detection((0, 10, 10, 0)),
                   detection((-1, 0, 10, 10)), detection((0, 0, float("nan"), 10)),
                   detection((0, 0, float("inf"), 10)), detection((0, 0, 1e308, 1e308)),
                   detection((0, 0, 1e-308, 1e-308)),
                   {**detection(), "box_xyxy": [0, 0, 1]}]
        for item in invalid:
            with self.subTest(detection=item), self.assertRaises(ValueError):
                suppress_contained_vehicles([item], [0], policy())
        raw = [detection(), detection()]
        for indices in ([1, 0], [0, 0], [-1], [2], [True], [0.0], (0,), None):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                suppress_contained_vehicles(raw, indices, policy())


if __name__ == "__main__":
    unittest.main()
