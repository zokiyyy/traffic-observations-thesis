"""Safety and provenance checks for the optional background confirmation stage."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM / "src"))
from confirm_background_candidates import (
    candidate_confirmation, validate_policy, validate_input_configs, _validate_frame, run,
)


def policy():
    return {
        "schema_version": 1, "camera_id": "fixture_camera",
        "source_clip_sha256": "a" * 64, "frame_width": 100, "frame_height": 100,
        "reviewed_background_polygon_xy": [[10, 10], [50, 10], [50, 50], [10, 50]],
        "confirmation_iou": 0.5, "confirm_min_confidence": 0.1,
        "development_only": True,
    }


def detection(box=(20, 20, 40, 40), confidence=0.8, class_id=3):
    return {"box_xyxy": list(box), "confidence": confidence, "class_id": class_id,
            "class_name": {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}[class_id]}


class BackgroundConfirmationTests(unittest.TestCase):
    def test_real_occluding_truck_kept_by_geometric_support(self):
        raw = [detection()]
        high = [detection((21, 20, 41, 40), 0.25)]
        before = deepcopy((raw, high))
        kept, audit = candidate_confirmation(raw, high, policy())
        self.assertEqual(kept, [0])
        self.assertTrue(audit[0]["kept"])
        self.assertEqual(audit[0]["eligible_matching_indices"], [0])
        self.assertEqual((raw, high), before)

    def test_stationary_vehicle_is_not_penalized(self):
        raw = [dict(detection(), track_id=999, speed=0, stopped_seconds=1000)]
        high = [detection()]
        for unused_time in (0, 30, 1000):
            raw[0]["timestamp"] = unused_time
            kept, audit = candidate_confirmation(raw, high, policy())
            self.assertEqual(kept, [0])
            self.assertTrue(audit[0]["kept"])

    def test_outside_and_exact_boundary_points_untouched_without_confirmation(self):
        raw = [detection((60, 60, 80, 80)), detection((0, 20, 20, 40)),
               detection((0, 0, 20, 20)), detection((40, 40, 60, 60))]
        kept, audit = candidate_confirmation(raw, [], policy())
        self.assertEqual(kept, [0, 1, 2, 3])
        self.assertEqual(audit, [])

    def test_inside_candidate_can_be_confirmed_by_different_class(self):
        kept, audit = candidate_confirmation([detection(class_id=3)], [detection(class_id=0)], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(audit[0]["best_confirmation_detection"]["class_id"], 0)

    def test_iou_and_confidence_thresholds_are_inclusive(self):
        # Half of the candidate area, wholly contained: IoU exactly 0.5.
        half = detection((20, 20, 30, 40), confidence=0.1)
        kept, audit = candidate_confirmation([detection()], [half], policy())
        self.assertEqual(kept, [0])
        self.assertEqual(audit[0]["max_iou"], 0.5)
        under_score = deepcopy(half)
        under_score["confidence"] = 0.099999
        kept, audit = candidate_confirmation([detection()], [under_score], policy())
        self.assertEqual(kept, [])
        self.assertEqual(audit[0]["max_iou"], 0)
        self.assertEqual(audit[0]["all_confirmation_max_iou"], 0.5)
        self.assertEqual(audit[0]["all_confirmation_best_index"], 0)
        self.assertIsNone(audit[0]["best_confirmation_index"])
        under_overlap = detection((20, 20, 29.999, 40), confidence=0.1)
        self.assertEqual(candidate_confirmation([detection()], [under_overlap], policy())[0], [])

    def test_ambiguous_weak_matches_do_not_add_up_to_confirmation(self):
        high = [detection((20, 20, 27, 40)), detection((33, 20, 40, 40))]
        kept, audit = candidate_confirmation([detection()], high, policy())
        self.assertEqual(kept, [])
        self.assertAlmostEqual(audit[0]["max_iou"], 0.35)
        self.assertEqual(audit[0]["best_confirmation_index"], 0)
        self.assertEqual(audit[0]["eligible_matching_indices"], [])
        self.assertEqual(audit[0]["reason"], "unconfirmed_background_candidate")

    def test_multiple_valid_support_boxes_retained_and_explicitly_audited(self):
        kept, audit = candidate_confirmation([detection()], [detection(), detection(class_id=0)], policy())
        self.assertEqual(kept, [0])
        self.assertTrue(audit[0]["multiple_matching_detections"])
        self.assertEqual(audit[0]["eligible_matching_indices"], [0, 1])

    def test_empty_confirmation_and_original_indices(self):
        raw = [detection((60, 60, 80, 80)), detection(), detection((60, 10, 80, 30))]
        kept, audit = candidate_confirmation(raw, [], policy())
        self.assertEqual(kept, [0, 2])
        self.assertEqual([r["raw_index"] for r in audit], [1])
        self.assertEqual(audit[0]["candidate_point"], [30, 30])
        self.assertEqual(audit[0]["max_iou"], 0)
        self.assertEqual(candidate_confirmation([], [], policy()), ([], []))

    def test_malformed_geometry_and_scores_fail_closed(self):
        malformed = [detection((20, 20, 20, 40)), detection((20, 20, 10, 40)),
                     detection((20, 20, float("nan"), 40)), detection((-1, 20, 40, 40)),
                     detection((20, 20, 101, 40)), detection(confidence=float("inf")),
                     detection(confidence=-0.1), {**detection(), "box_xyxy": [1, 2, 3]},
                     {**detection(), "class_id": True}]
        for item in malformed:
            with self.subTest(item=item), self.assertRaises(ValueError):
                candidate_confirmation([item], [], policy())
            with self.subTest(confirmation=item), self.assertRaises(ValueError):
                candidate_confirmation([], [item], policy())

    def test_policy_requires_explicit_scope_thresholds_and_simple_polygon(self):
        mutations = [("development_only", False), ("camera_id", ""), ("schema_version", True),
                     ("source_clip_sha256", "invalid"), ("frame_width", 100.5),
                     ("confirmation_iou", 0), ("confirm_min_confidence", float("nan")),
                     ("reviewed_background_polygon_xy", [[10, 10], [50, 50], [10, 50], [50, 10]]),
                     ("reviewed_background_polygon_xy", [[10, 10], [50, 50], [100, 50]])]
        for key, value in mutations:
            p = policy()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_policy(p)
        for missing in ("confirmation_iou", "reviewed_background_polygon_xy", "confirm_min_confidence"):
            p = policy()
            del p[missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                validate_policy(p)

    def test_frame_time_and_order_guards(self):
        manifest = {"fps": 30, "clip_start_seconds": 1600}
        r = {"frame_index": 30, "timestamp_seconds": 1.0,
             "source_timestamp_seconds": 1601.0, "raw_detections": [detection()]}
        _validate_frame(r, 30, manifest, "fixture")
        for field, value in (("frame_index", 31), ("timestamp_seconds", 1.1),
                             ("source_timestamp_seconds", 1602), ("timestamp_seconds", float("nan"))):
            bad = {**r, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                _validate_frame(bad, 30, manifest, "fixture")

    def test_clip_model_manifest_settings_and_version_guard(self):
        # Read frozen provenance only, never use Main Test or run model inference.
        pilot = SYSTEM / "outputs/video_pipeline/yolo11s_newport_val_120s_v1_2026-09-23"
        base = json.loads((pilot / "results/config.json").read_text())
        manifest = json.loads((pilot / "input/clip_manifest.json").read_text())
        high = deepcopy(base)
        high["predict_settings"]["imgsz"] = 1280
        p = policy()
        p.update(source_clip_sha256=base["source"]["sha256"], frame_width=1280, frame_height=720)
        digest = base["manifest"]["sha256"]
        validate_input_configs(base, high, manifest, p, digest)
        mutations = [(("model", "sha256"), "b" * 64), (("source", "sha256"), "c" * 64),
                     (("manifest", "sha256"), "d" * 64), (("predict_settings", "conf"), 0.35),
                     (("versions", "ultralytics"), "wrong"), (("versions", "torch"), "wrong")]
        for keys, value in mutations:
            bad = deepcopy(high)
            bad[keys[0]][keys[1]] = value
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                validate_input_configs(base, bad, manifest, p, digest)
        wrong_split = deepcopy(manifest)
        wrong_split["source_split"]["Formal Split"] = "Main Test"
        with self.assertRaises(ValueError):
            validate_input_configs(base, high, wrong_split, p, digest)

    def test_complete_stream_preserves_raw_tracks_and_refuses_reuse_or_missing_frame(self):
        pilot = SYSTEM / "outputs/video_pipeline/yolo11s_newport_val_120s_v1_2026-09-23"
        base = json.loads((pilot / "results/config.json").read_text())
        high_config = deepcopy(base)
        high_config["predict_settings"]["imgsz"] = 1280
        p = policy()
        p.update(source_clip_sha256=base["source"]["sha256"], frame_width=1280, frame_height=720)
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            args = SimpleNamespace(records=tmp / "base.jsonl", config=tmp / "config.json",
                                   confirmation_records=tmp / "high.jsonl", confirmation_config=tmp / "high_config.json",
                                   manifest=tmp / "manifest.json", policy=tmp / "policy.json", output=tmp / "output")
            args.config.write_text(json.dumps(base))
            args.confirmation_config.write_text(json.dumps(high_config))
            args.manifest.write_bytes((pilot / "input/clip_manifest.json").read_bytes())
            args.policy.write_text(json.dumps(p))
            with args.records.open("w") as first, args.confirmation_records.open("w") as second:
                for index in range(3600):
                    common = {"frame_index": index, "timestamp_seconds": index / 30,
                              "source_timestamp_seconds": 1600 + index / 30}
                    raw = [detection()] if index in (100, 101) else []
                    # Historical tracks are deliberately untouched in this intermediate stage.
                    original = {**common, "raw_detections": raw, "tracks_primary": [{"historical": True}]}
                    high = {**common, "raw_detections": raw if index == 101 else []}
                    first.write(json.dumps(original) + "\n")
                    second.write(json.dumps(high) + "\n")
            summary = run(args)
            self.assertEqual(summary["frame_count"], 3600)
            self.assertEqual(summary["removed_observations"], 1)
            self.assertTrue(summary["tracking_replay_required"])
            output = [json.loads(line) for line in (args.output / "frames.jsonl").read_text().splitlines()]
            original = [json.loads(line) for line in args.records.read_text().splitlines()]
            for before, after in zip(original, output):
                self.assertEqual({k: v for k, v in after.items() if k != "background_confirmation"}, before)
                self.assertEqual(after["background_confirmation"]["policy_sha256"], summary["policy_sha256"])
            self.assertEqual(output[100]["background_confirmation"]["kept_detection_indices"], [])
            self.assertEqual(output[101]["background_confirmation"]["kept_detection_indices"], [0])
            with self.assertRaises(FileExistsError):
                run(args)
            args.output = tmp / "incomplete"
            args.confirmation_records.write_text(args.confirmation_records.read_text().splitlines()[0] + "\n")
            with self.assertRaisesRegex(ValueError, "record counts differ"):
                run(args)
            self.assertEqual(json.loads((args.output / "status.json").read_text())["state"], "failed")


if __name__ == "__main__":
    unittest.main()
