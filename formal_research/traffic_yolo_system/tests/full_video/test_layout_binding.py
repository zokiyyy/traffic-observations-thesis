"""Configuration-only software tests; no dataset media, labels or GPU access."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SYSTEM / "src"))
from full_video.batch import list_development
from full_video.configuration import camera_for, load_config, layout_identity

MANIFEST = SYSTEM.parent / "03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv"
SNAPSHOT = SYSTEM / "configs/full_video/newport_roi_v2_snapshot.json"
NEWPORT = "Bellevue_150th_Newport"


class LayoutBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full-video-synthetic-layout-binding-")
        self.root = Path(self.temp.name)
        self.tasks = list_development(MANIFEST)
        self.layout = json.loads(SNAPSHOT.read_text())
        self.reference = next(t for t in self.tasks if t["source_id"] == self.layout["source_video"])
        self.config = {"camera_registry": str(self.root / "registry.json"), "split_manifest": str(MANIFEST)}

    def tearDown(self):
        self.temp.cleanup()

    def bind(self, task, layout=None, source_ids=None):
        layout_path = SNAPSHOT
        if layout is not None:
            layout_path = self.root / "synthetic-layout.json"
            layout_path.write_text(json.dumps(layout))
        camera = {"camera_id": task["camera_id"], "source_ids": source_ids or [task["source_id"]],
                  "roi_version": self.layout["layout_version"], "layout_path": str(layout_path),
                  "allowed_source_intervals_seconds": [[0, task["manifest_duration_s"]]],
                  "applicability_basis": "Synthetic configuration test only, no scene applicability claim",
                  "background_filter": {"mode": "none"}}
        (self.root / "camera.json").write_text(json.dumps(camera))
        (self.root / "registry.json").write_text(json.dumps({"schema_version": 1,
            "cameras": {task["camera_id"]: {"config_path": "camera.json"}}}))
        return camera_for(self.config, task)

    def test_real_newport_geometry_rejected_for_eastgate_and_116th(self):
        for camera in ("Bellevue_150th_Eastgate", "Bellevue_116th_NE12th"):
            task = next(t for t in self.tasks if t["camera_id"] == camera)
            with self.subTest(camera=camera), self.assertRaisesRegex(ValueError, "LAYOUT_CAMERA_MISMATCH"):
                self.bind(task)

    def test_reference_source_may_differ_for_declared_same_camera_train_task(self):
        task = next(t for t in self.tasks if t["camera_id"] == NEWPORT and t["split"] == "Train")
        self.assertNotEqual(task["source_id"], self.reference["source_id"])
        result = self.bind(task, source_ids=[task["source_id"], self.reference["source_id"]])
        self.assertEqual(result["layout_identity"]["camera_id"], NEWPORT)
        self.assertEqual(result["layout_identity"]["reference_source_id"], self.reference["source_id"])
        self.assertEqual(result["layout"]["source_split"], "Validation")

    def test_existing_newport_camera_configuration_regression(self):
        config = load_config(SYSTEM / "configs/full_video/newport_cache_v4.json")
        result = camera_for(config, self.reference)
        self.assertEqual(result["layout_identity"]["basis"], "frozen_reference_source")
        self.assertFalse(result["layout_identity"]["reference_media_opened"])

    def test_explicit_camera_id_without_reference_and_mismatch(self):
        layout = {key: value for key, value in self.layout.items() if key != "source_video"}
        layout["camera_id"] = NEWPORT
        self.assertEqual(self.bind(self.reference, layout)["layout_identity"]["basis"], "explicit_camera_id")
        layout["camera_id"] = "Bellevue_150th_Eastgate"
        with self.assertRaisesRegex(ValueError, "LAYOUT_CAMERA_MISMATCH"):
            self.bind(self.reference, layout)

    def test_explicit_camera_must_agree_with_known_reference(self):
        layout = {**self.layout, "camera_id": "Bellevue_150th_Eastgate"}
        task = next(t for t in self.tasks if t["camera_id"] == "Bellevue_150th_Eastgate")
        with self.assertRaisesRegex(ValueError, "LAYOUT_IDENTITY_CONFLICT"):
            self.bind(task, layout)
        self.assertEqual(self.bind(self.reference, {**self.layout, "camera_id": NEWPORT})[
            "layout_identity"]["basis"], "explicit_camera_id_and_frozen_reference")

    def test_unknown_or_malformed_identity_fails_closed(self):
        cases = ({}, {"source_video": "unknown-source.mp4"}, {"source_video": ""},
                 {"camera_id": None}, {"camera_id": ""},
                 {"camera_id": NEWPORT, "source_video": "unknown-source.mp4"})
        for identity in cases:
            layout = {key: value for key, value in self.layout.items() if key not in ("camera_id", "source_video")}
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "LAYOUT_IDENTITY_UNVERIFIED"):
                self.bind(self.reference, {**layout, **identity})

    def test_reference_split_or_manifest_hash_conflict_is_rejected(self):
        for field, value in (("source_split", "Train"), ("split_manifest_sha256", "0"*64)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "LAYOUT_IDENTITY_CONFLICT"):
                self.bind(self.reference, {**self.layout, field: value})

    def test_legacy_reference_requires_frozen_manifest(self):
        with self.assertRaisesRegex(ValueError, "requires the configured frozen split_manifest"):
            layout_identity({}, self.reference, self.layout)

    def test_reference_path_is_only_metadata_and_never_opened(self):
        layout = {**self.layout, "source_video": "/do-not-open-reference-video/" + self.layout["source_video"]}
        actual_open = Path.open
        opened = []
        def guarded_open(path, *args, **kwargs):
            opened.append(str(path))
            if path.suffix.lower() in (".mp4", ".jpg", ".txt", ".jsonl"):
                self.fail(f"Identity resolution opened dataset material: {path}")
            return actual_open(path, *args, **kwargs)
        with patch.object(Path, "open", guarded_open):
            result = layout_identity(self.config, self.reference, layout)
        self.assertEqual(result["camera_id"], NEWPORT)
        self.assertTrue(opened)
        self.assertTrue(all(path == str(MANIFEST) for path in opened))


if __name__ == "__main__":
    unittest.main()
