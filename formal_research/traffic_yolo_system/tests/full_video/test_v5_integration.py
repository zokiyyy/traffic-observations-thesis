"""Synthetic v5 adapter checks; no dataset image, checkpoint or GPU is opened."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import test_video as video_fixtures
import test_integration as cache_fixtures
from test_video import (_FakeBoxes, detection, config_template,
                        task_template, camera_template)
from full_video import cache, configuration, video
from confirm_background_candidates import candidate_confirmation
from replay_vehicle_tracking_pilot import suppress_cross_class_duplicates
from vehicle_containment import suppress_contained_vehicles


def containment_policy():
    return {"schema_version": 2, "representative_policy": "confident_same_class_outer_then_low_confidence",
            "heavy_classes": [2, 3], "development_only": True, "min_containment": .9,
            "min_area_ratio": 1.2, "max_bottom_gap_fraction": .15,
            "min_keeper_confidence": .35, "ambiguity_min_containment": .5}


def attach_policies(root, config, task, camera, source_sha):
    background = {"schema_version": 1, "development_only": True, "camera_id": task["camera_id"],
                  "source_video_filename": task["source_id"], "source_clip_sha256": source_sha,
                  "model_sha256": config["model"]["sha256"], "frame_width": 16, "frame_height": 12,
                  "confirmation_iou": .5, "confirm_min_confidence": .35,
                  "reviewed_background_polygon_xy": [[0, 0], [2, 0], [2, 2], [0, 2]]}
    bg_path, ct_path = root / "background.json", root / "containment.json"
    bg_path.write_text(json.dumps(background))
    ct_path.write_text(json.dumps(containment_policy()))
    camera["background_filter"] = {"mode": "live_second_resolution", "confirmation_imgsz": 1280,
                                   "policy_path": str(bg_path), "policy_sha256": video._hash(bg_path),
                                   "policy": background}
    config["dedup"].update(containment_policy_path=str(ct_path),
                           containment_policy_sha256=video._hash(ct_path), containment_policy=containment_policy())
    return background


def sample_detections():
    # 0 is a background candidate; 2 duplicates 1 cross-class; 3 is a same-class
    # contained fragment. Only original indices 1 and 4 should reach tracking.
    return [detection(0, .7, [.2, .2, 1.8, 1.8]), detection(3, .9, [3, 2, 14, 11]),
            detection(2, .8, [3, 2, 14, 11]), detection(3, .6, [5, 4, 12, 10.7]),
            detection(0, .8, [13, 1, 15, 4])]


class V5SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-v5-policies-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config, self.task, self.camera = config_template(), task_template(), camera_template()
        self.bg = attach_policies(self.root, self.config, self.task, self.camera, "a" * 64)

    def policies(self):
        return video._method_policies(self.config, self.task, self.camera, "a" * 64, 16, 12)[0]

    def test_shared_pilot_order_and_original_index_mapping(self):
        raw, confirmation = sample_detections(), []
        original = copy.deepcopy(raw)
        kept, audit = video._select_detections(raw, confirmation, self.config, self.camera, self.policies())
        eligible, candidates = candidate_confirmation(raw, confirmation, self.bg)
        local_kept, cross_removed = suppress_cross_class_duplicates([raw[i] for i in eligible], .7)
        cross = [eligible[i] for i in local_kept]
        for row in cross_removed:
            row["removed_index"], row["kept_index"] = eligible[row["removed_index"]], eligible[row["kept_index"]]
        expected, ct_removed, unresolved = suppress_contained_vehicles(raw, cross, containment_policy())
        self.assertEqual(kept, [1, 4])
        self.assertEqual(kept, expected)
        self.assertEqual(audit["background_confirmation"]["candidate_records"], candidates)
        self.assertEqual(audit["duplicate_suppression"]["removed"], cross_removed)
        self.assertEqual(audit["duplicate_suppression"]["kept_detection_indices"], [1, 3, 4])
        self.assertEqual(audit["contained_vehicle_suppression"]["removed"], ct_removed)
        self.assertEqual(audit["contained_vehicle_suppression"]["unresolved_pairs"], unresolved)
        self.assertEqual(audit["tracker_input_detection_indices"], [1, 4])
        self.assertEqual(raw, original)

    def test_confirmation_is_class_agnostic_and_boundary_is_not_background(self):
        raw = [detection(0, .8, [.2, .2, 1.8, 1.8]), detection(0, .8, [1, 0, 3, 2])]
        confirmed = [detection(3, .4, [.2, .2, 1.8, 1.8])]
        kept, audit = video._select_detections(raw, confirmed, self.config, self.camera, self.policies())
        self.assertEqual(kept, [0, 1])
        self.assertEqual(len(audit["background_confirmation"]["candidate_records"]), 1)
        self.assertEqual(audit["confirmation_raw_detections"], confirmed)

    def test_missing_second_inference_is_not_treated_as_empty_prediction(self):
        with self.assertRaisesRegex(ValueError, "this frame"):
            video._select_detections([], None, self.config, self.camera, self.policies())

    def test_source_model_camera_and_dimensions_are_bound(self):
        for key, value in (("source_clip_sha256", "b" * 64), ("model_sha256", "b" * 64),
                           ("camera_id", "another"), ("source_video_filename", "another.mp4"),
                           ("frame_width", 17), ("frame_height", 13)):
            with self.subTest(key=key):
                altered = {**self.bg, key: value}
                policy = self.camera["background_filter"]
                Path(policy["policy_path"]).write_text(json.dumps(altered))
                policy["policy_sha256"] = video._hash(policy["policy_path"])
                policy["policy"] = altered
                with self.assertRaisesRegex(ValueError, key):
                    self.policies()

    def test_missing_policy_and_mutation_fail_closed(self):
        path = Path(self.camera["background_filter"]["policy_path"])
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.policies()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.policies()

    def test_containment_and_scope_settings_must_be_explicit(self):
        video._validate_scope(self.config, self.task, self.camera)
        video._validate_settings(self.config)
        self.config["dedup"]["iou_threshold"] = .8
        with self.assertRaisesRegex(ValueError, "preceding"):
            video._validate_settings(self.config)
        self.camera["background_filter"]["confirmation_imgsz"] = 960
        with self.assertRaisesRegex(ValueError, "1280"):
            video._validate_scope(self.config, self.task, self.camera)

    def test_containment_code_is_fingerprinted(self):
        hashes = configuration.code_fingerprints()
        self.assertTrue(any(Path(path).name == "vehicle_containment.py" for path in hashes))


class V5LiveAdapterTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing synthetic decoder/preflight fixture, replacing only
        # pixels and detector output to exercise live dual-resolution processing.
        self.fixture = video_fixtures.VideoIterationTests("test_prefix_skipped_without_inference_and_times_use_actual_fps")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.numpy_patch = patch.dict(sys.modules, {"numpy": np})
        self.numpy_patch.start()
        self.addCleanup(self.numpy_patch.stop)
        f = self.fixture
        source_sha = video._hash(f.task["input_path"])
        attach_policies(Path(f.directory.name), f.config, f.task, f.camera, source_sha)
        f.preflight["input_sha256"] = source_sha
        f.preflight["configuration_sha256"] = video._configuration_hash(f.config, f.camera)
        cap_read = f.cap.read
        def read_pixels():
            ok, unused = cap_read()
            return ok, np.full((12, 16, 3), f.cap.index, dtype=np.uint8) if ok else None
        f.cap.read = read_pixels
        f.model.predict = self.predict
        self.frame_hashes = []

    def predict(self, frame, **settings):
        self.fixture.calls.append(settings)
        self.frame_hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
        detections = sample_detections() if settings["imgsz"] == 640 else []
        rows = [[*r["box_xyxy"], r["confidence"], r["class_id"]] for r in detections]
        from types import SimpleNamespace
        return [SimpleNamespace(boxes=_FakeBoxes(rows))]

    def test_live_640_then_1280_same_pixels_tracker_indices_and_empty_confirmation(self):
        f = self.fixture
        rows = list(video.iter_video(f.config, f.task, f.camera, f.preflight))
        self.assertEqual([p["imgsz"] for p in f.calls], [640, 1280] * 3)
        for index, row in enumerate(rows):
            first, second = f.calls[index * 2:index * 2 + 2]
            self.assertEqual({**first, "imgsz": 1280}, second)
            self.assertEqual(self.frame_hashes[index * 2], self.frame_hashes[index * 2 + 1])
            self.assertEqual(row["decoded_frame_sha256"], self.frame_hashes[index * 2])
            self.assertEqual(row["confirmation_raw_detections"], [])
            self.assertEqual([t["detection_index"] for t in row["tracks_primary"]], [1, 4])
            self.assertEqual(row["tracker_input_detection_indices"], [1, 4])
            self.assertTrue(row["quality"]["confirmation_complete"])
        self.assertEqual([r["source_timestamp_seconds"] for r in rows], [100.2, 100.3, 100.4])
        self.assertTrue(f.cap.released)

    def test_bad_second_result_raises_without_emitting_a_valid_zero_frame(self):
        f = self.fixture
        original = f.model.predict
        f.model.predict = lambda frame, **settings: [] if settings["imgsz"] == 1280 else original(frame, **settings)
        with self.assertRaisesRegex(RuntimeError, "second-resolution"):
            list(video.iter_video(f.config, f.task, f.camera, f.preflight))
        self.assertTrue(f.cap.released)

    def test_background_none_keeps_exact_source_pixel_hash_without_second_inference(self):
        f = self.fixture
        f.camera["background_filter"] = {"mode": "none"}
        f.preflight["configuration_sha256"] = video._configuration_hash(f.config, f.camera)
        rows = list(video.iter_video(f.config, f.task, f.camera, f.preflight))
        self.assertEqual([settings["imgsz"] for settings in f.calls], [640] * 3)
        self.assertEqual([row["decoded_frame_sha256"] for row in rows], self.frame_hashes)
        self.assertEqual(rows[0]["decoded_frame_sha256"], hashlib.sha256(np.full((12, 16, 3), 3, dtype=np.uint8).tobytes()).hexdigest())
        self.assertNotIn("confirmation_raw_detections", rows[0])


class V5CacheBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cache_fixtures.IntegrationTest("test_real_writer_and_cache_pipeline_complete_skip_and_config_change")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def add_containment(self):
        f = self.fixture
        f.write("containment.json", containment_policy())
        digest = video._hash(f.root / "containment.json")
        f.config["dedup"].update(mode="cross_class_greedy_iou", iou_threshold=.7,
                                containment_policy_path="containment.json", containment_policy_sha256=digest)
        f.historical["duplicate_suppression"] = {"iou_threshold": .7}
        f.historical["contained_vehicle_suppression"] = {"policy": {"sha256": digest}, "parameters": containment_policy()}
        f.write("synthetic-historical.json", f.historical)
        for row in f.records:
            row["contained_vehicle_suppression"] = {"policy_sha256": digest, "input_detection_indices": [],
                "kept_detection_indices": [], "removed": [], "unresolved_pairs": [], "not_ground_truth_duplicates": True}
        f.write_records()
        return digest

    def prepare(self):
        f = self.fixture
        f.write("synthetic-config.json", f.config)
        return configuration.prepare(f.config_path, cache_fixtures.SOURCE)

    def test_loaded_policy_is_resolved_hashed_and_in_metadata(self):
        digest = self.add_containment()
        prepared = self.prepare()
        f = self.fixture
        self.assertEqual(prepared["config"]["dedup"]["containment_policy"], containment_policy())
        self.assertEqual(prepared["input_hashes"][str((f.root / "containment.json").resolve())], digest)
        rows = list(cache.iter_cache(prepared["config"], prepared["task"], prepared["camera"], prepared["preflight"]))
        self.assertEqual(len(rows), 4)

    def test_cache_cannot_silently_drop_containment_or_claim_it_was_applied(self):
        self.add_containment()
        requested = copy.deepcopy(self.fixture.config["dedup"])
        self.fixture.config["dedup"] = {"mode": "cross_class_greedy_iou", "iou_threshold": .7}
        with self.assertRaisesRegex(ValueError, "matching frozen policy"):
            self.prepare()
        self.fixture.config["dedup"] = requested
        self.fixture.historical.pop("contained_vehicle_suppression")
        self.fixture.write("synthetic-historical.json", self.fixture.historical)
        with self.assertRaisesRegex(ValueError, "not applied"):
            self.prepare()

    def test_cache_per_frame_containment_must_match(self):
        self.add_containment()
        self.fixture.records[1]["contained_vehicle_suppression"]["policy_sha256"] = "b" * 64
        self.fixture.write_records()
        prepared = self.prepare()
        with self.assertRaisesRegex(ValueError, "Per-frame containment"):
            list(cache.iter_cache(prepared["config"], prepared["task"], prepared["camera"], prepared["preflight"]))


if __name__ == "__main__":
    unittest.main()
