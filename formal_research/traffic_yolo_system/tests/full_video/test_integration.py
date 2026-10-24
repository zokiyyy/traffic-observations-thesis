"""Four-frame synthetic integration fixtures, never inference or Main Test reads.

The real existing geometry implementation is used; run with traffic-yolo Python.
All generated files are temporary and explicitly labelled software-test fixtures.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SYSTEM / "src"))
from full_video import batch, cache, configuration, runner
from full_video.__main__ import main
from full_video.common import sha256

MANIFEST = SYSTEM.parent / "03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv"
SOURCE = "Bellevue_150th_Newport__2017-09-10_18-08-24.mp4"
CAMERA = "Bellevue_150th_Newport"


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full-video-synthetic-integration-")
        self.root = Path(self.temp.name)
        self.config_path = self.root / "synthetic-config.json"
        self.records = [{"synthetic": True, "frame_index": index, "timestamp_seconds": index / 2,
                         "source_timestamp_seconds": 1600 + index / 2,
                         "raw_detections": [], "tracks_primary": []} for index in range(4)]
        self.write_records()
        self.layout = {"camera_id": CAMERA, "frame_width": 100, "frame_height": 100, "assignment_point": "bottom_center",
                       "boundary_tie_break": "zone_order", "zones": [
                           {"zone_name": "synthetic_zone", "polygon_points": [[0, 0], [99, 0], [99, 99], [0, 99]]}]}
        self.camera = {"schema_version": 1, "camera_id": CAMERA, "source_ids": [SOURCE],
                       "roi_version": "synthetic_software_test_only", "layout_path": "synthetic-layout.json",
                       "allowed_source_intervals_seconds": [[1600, 1602]],
                       "applicability_basis": "Synthetic software test, no real scene claim",
                       "background_filter": {"mode": "none"}}
        self.write("synthetic-layout.json", self.layout)
        self.write("synthetic-camera.json", self.camera)
        self.write("synthetic-registry.json", {"schema_version": 1, "cameras": {CAMERA: {"config_path": "synthetic-camera.json"}}})
        identity = {"Source Video Filename": SOURCE, "Formal Split": "Validation", "Intersection": CAMERA}
        timing = {"width": 100, "height": 100, "frame_count": 4, "fps": 2,
                  "clip_start_seconds": 1600, "clip_end_seconds": 1602}
        self.manifest = {**timing, "source_split": identity, "split_manifest_sha256": sha256(MANIFEST),
                         "clip_sha256": "c" * 64, "processing": "synthetic CFR software test"}
        self.historical = {**timing, "source_split": identity, "source": {"sha256": "c" * 64},
                           "time_semantics": "CFR synthetic software test", "model": {"sha256": "a" * 64},
                           "predict_settings": {"conf": 0.1, "device": "cpu", "save": False, "verbose": False},
                           "tracker_settings_both_arms": {"track_buffer": 30}}
        self.write("synthetic-manifest.json", self.manifest)
        self.write("synthetic-historical.json", self.historical)
        self.write("synthetic-status.json", {"state": "complete", "processed_frames": 4})
        self.config = {"schema_version": 1, "development_only": True, "run_version": "synthetic-software-test",
                       "rules_version": "synthetic-rules-not-computed", "method_status": "synthetic_software_test_only",
                       "split_manifest": str(MANIFEST), "source_root": str(self.root / "videos"),
                       "camera_registry": "synthetic-registry.json", "output_root": str(self.root / "outputs"),
                       "model": {"path": "not-loaded-synthetic-model.pt", "sha256": "a" * 64, "version": "synthetic"},
                       "input": {"kind": "pilot_cache", "source_id": SOURCE, "records": "synthetic-frames.jsonl",
                                 "manifest": "synthetic-manifest.json", "historical_config": "synthetic-historical.json",
                                 "historical_status": "synthetic-status.json", "historical_summary": "synthetic-summary.json"},
                       "detector": {"conf": 0.1}, "tracker": {"track_buffer": 30}, "dedup": {"mode": "none"},
                       "aggregation": {"window_seconds": 0.75, "chunk_frames": 2}, "progress_every_frames": 2}
        self.save_config()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, filename, value):
        (self.root / filename).write_text(json.dumps(value), encoding="utf-8")

    def write_records(self):
        path = self.root / "synthetic-frames.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in self.records), encoding="utf-8")
        self.write("synthetic-summary.json", {"frames_file": {"sha256": sha256(path)}})

    def save_config(self):
        self.write(self.config_path.name, self.config)

    def prepare(self):
        value = configuration.prepare(self.config_path, SOURCE)
        # This field marks temporary software-test output, not a model evaluation.
        value["metadata"]["synthetic"] = True
        return value

    def test_main_test_is_rejected_before_camera_cache_model_or_video_access(self):
        with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
            source = next(row["Source Video Filename"] for row in csv.DictReader(handle) if row["Formal Split"] == "Main Test")
        with patch.object(configuration, "camera_for", side_effect=AssertionError("camera access must not occur")), \
             patch.object(cache, "preflight_cache", side_effect=AssertionError("cache access must not occur")), \
             patch.object(configuration, "sha256", side_effect=AssertionError("media/model hashes must not occur")):
            with self.assertRaisesRegex(ValueError, "SOURCE_NOT_DEVELOPMENT"):
                configuration.prepare(self.config_path, source)
        self.assertFalse((self.root / "outputs").exists())

    def test_original_video_offset_cannot_be_fabricated(self):
        self.config["input"] = {"kind": "video", "source_offset_seconds": 1600}
        self.save_config()
        with self.assertRaisesRegex(ValueError, "must have source_offset_seconds=0"):
            configuration.load_config(self.config_path)

    def test_misspelled_segment_and_invalid_progress_are_rejected(self):
        self.config["segment"] = {"start": 1600}
        self.save_config()
        with self.assertRaisesRegex(ValueError, "segment accepts only"):
            configuration.load_config(self.config_path)
        self.config.pop("segment")
        self.config["progress_every_frames"] = 0
        self.save_config()
        with self.assertRaisesRegex(ValueError, "progress_every_frames"):
            configuration.load_config(self.config_path)

    def test_real_writer_and_cache_pipeline_complete_skip_and_config_change(self):
        prepared = self.prepare()
        run = batch.run_one(prepared, self.root / "outputs", runner.execute)
        self.assertEqual(run["status"], "completed")
        attempt = Path(run["attempt_directory"])
        self.assertEqual(run["summary"]["frame_count"], 4)
        windows = [json.loads(line) for line in (attempt / "windows.jsonl").read_text().splitlines()]
        self.assertEqual(len(windows), 3)
        self.assertTrue(batch.verify_completion(attempt, prepared["fingerprint"], SOURCE)[0])
        again = batch.run_one(self.prepare(), self.root / "outputs", lambda *args: self.fail("must skip unchanged configuration"))
        self.assertEqual(again["status"], "skipped_completed")
        self.config["aggregation"]["chunk_frames"] = 1
        self.save_config()
        changed = self.prepare()
        self.assertNotEqual(changed["fingerprint"], prepared["fingerprint"])
        rerun = batch.run_one(changed, self.root / "outputs", runner.execute)
        self.assertEqual(rerun["status"], "completed")
        self.assertNotEqual(rerun["attempt_directory"], run["attempt_directory"])

    def test_cached_detector_tracker_model_and_dedup_changes_are_rejected(self):
        cases = [("detector", {"conf": 0.2}), ("tracker", {"track_buffer": 40}),
                 ("dedup", {"mode": "cross_class_greedy_iou", "iou_threshold": 0.7}),
                 ("model", {"path": "unused.pt", "sha256": "b" * 64, "version": "synthetic"})]
        for key, replacement in cases:
            with self.subTest(key=key):
                original = self.config[key]
                self.config[key] = replacement
                self.save_config()
                with self.assertRaisesRegex(ValueError, "Cache|Cached"):
                    self.prepare()
                self.config[key] = original

    def test_missing_cached_frame_fails_actual_run_and_does_not_publish_completion(self):
        self.records.pop()
        self.write_records()
        prepared = self.prepare()
        result = batch.run_one(prepared, self.root / "outputs", runner.execute)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ended early", result["error"])
        self.assertFalse((Path(result["attempt_directory"]) / "completion.json").exists())

    def test_wrong_cached_time_fails_instead_of_fabricating_continuity(self):
        self.records[1]["source_timestamp_seconds"] += 0.1
        self.write_records()
        result = batch.run_one(self.prepare(), self.root / "outputs", runner.execute)
        self.assertEqual(result["status"], "failed")
        self.assertIn("timestamp mismatch", result["error"])

    def test_changed_input_after_preparation_fails_before_counting(self):
        prepared = self.prepare()
        self.records[0]["synthetic_mutation"] = True
        self.write_records()
        result = batch.run_one(prepared, self.root / "outputs", runner.execute)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Input changed after preflight", result["error"])
        self.assertFalse((Path(result["attempt_directory"]) / "frames.jsonl").exists())

    def test_camera_scope_and_newport_policy_cannot_fall_back(self):
        self.camera["source_ids"] = []
        self.write("synthetic-camera.json", self.camera)
        with self.assertRaisesRegex(ValueError, "CAMERA_SCOPE_MISMATCH"):
            self.prepare()
        self.camera["source_ids"] = [SOURCE]
        self.camera["allowed_source_intervals_seconds"] = [[1600, 1601]]
        self.write("synthetic-camera.json", self.camera)
        with self.assertRaisesRegex(ValueError, "ROI_SCOPE_NOT_VERIFIED"):
            self.prepare()

    def set_roi_evidence(self, record=None):
        record = record if record is not None else {
            "camera_id": CAMERA, "source": SOURCE, "split": "Validation",
            "manifest_sha256": sha256(MANIFEST), "synthetic": True}
        self.write("synthetic-roi-review.json", record)
        evidence = self.root / "synthetic-roi-review.json"
        self.camera["applicability_evidence"] = [{"path": evidence.name, "sha256": sha256(evidence)}]
        self.write("synthetic-camera.json", self.camera)
        return evidence.resolve()

    def test_declared_roi_evidence_is_bound_before_and_after_preflight(self):
        evidence = self.set_roi_evidence()
        prepared = self.prepare()
        self.assertEqual(prepared["input_hashes"][str(evidence)], sha256(evidence))
        runner.check_unchanged(prepared)
        with evidence.open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_CHANGED"):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "Input changed after preflight"):
            runner.check_unchanged(prepared)
        self.camera["applicability_evidence"][0]["sha256"] = sha256(evidence)
        self.write("synthetic-camera.json", self.camera)
        self.assertNotEqual(self.prepare()["fingerprint"], prepared["fingerprint"])

    def test_missing_roi_evidence_rejected(self):
        evidence = self.set_roi_evidence()
        prepared = self.prepare()
        evidence.unlink()
        with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_UNAVAILABLE"):
            self.prepare()
        with self.assertRaises(OSError):
            runner.check_unchanged(prepared)

    def test_roi_evidence_top_level_and_nested_identity_conflicts_rejected(self):
        identity = {"camera_id": CAMERA, "source_id": SOURCE, "split": "Validation"}
        for container in (None, "plan", "task"):
            for key, value in (("camera_id", "other-camera"), ("source_id", "other.mp4"),
                               ("split", "Main Test"), ("manifest_sha256", "0" * 64)):
                record = {**identity, key: value}
                if container:
                    record = {**identity, container: record}
                with self.subTest(container=container, field=key):
                    self.set_roi_evidence(record)
                    with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_SCOPE_MISMATCH"):
                        self.prepare()

    def test_roi_evidence_accepts_scoped_plan_and_audit_records(self):
        identity = {"camera_id": CAMERA, "source_id": SOURCE, "split": "Validation"}
        for record in ({"plan": identity}, {"source": SOURCE, "task": identity}):
            with self.subTest(record=record):
                evidence = self.set_roi_evidence(record)
                self.assertIn(str(evidence), self.prepare()["input_hashes"])

    def test_malformed_or_unscoped_roi_evidence_rejected(self):
        for entries in ([], None, "bad", [{}], [{"path": "x", "sha256": "bad"}]):
            with self.subTest(entries=entries):
                self.camera["applicability_evidence"] = entries
                self.write("synthetic-camera.json", self.camera)
                with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_INVALID"):
                    self.prepare()
        for record in ({"status": "ready"}, {"source": SOURCE}, {"camera_id": CAMERA}):
            with self.subTest(record=record):
                self.set_roi_evidence(record)
                with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_SCOPE_UNVERIFIED"):
                    self.prepare()

    def test_roi_evidence_change_during_preflight_rejected(self):
        evidence = self.set_roi_evidence()
        actual_preflight = cache.preflight_cache
        def mutate_after_camera_check(*args):
            result = actual_preflight(*args)
            with evidence.open("a") as handle:
                handle.write("\n")
            return result
        with patch.object(cache, "preflight_cache", side_effect=mutate_after_camera_check):
            with self.assertRaisesRegex(ValueError, "ROI_EVIDENCE_CHANGED"):
                self.prepare()

    def test_unknown_cache_time_mode_and_non_aligned_segment_rejected(self):
        self.manifest["processing"] = "unknown timing"
        self.write("synthetic-manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "CFR evidence"):
            self.prepare()
        self.manifest["processing"] = "synthetic CFR software test"
        self.write("synthetic-manifest.json", self.manifest)
        self.config["segment"] = {"start_seconds": 1600.1, "end_seconds": 1602}
        self.save_config()
        with self.assertRaisesRegex(ValueError, "align"):
            self.prepare()

    def test_elapsed_preflight_measurement_does_not_change_fingerprint(self):
        baseline = self.prepare()["preflight"]
        self.config["input"] = {"kind": "video"}
        self.config["segment"] = {"start_seconds": 1600, "end_seconds": 1602}
        source_path = self.root / "videos" / CAMERA / SOURCE
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(b"synthetic placeholder; never decoded")
        (self.root / "not-loaded-synthetic-model.pt").write_bytes(b"synthetic placeholder; never loaded")
        self.save_config()
        with patch("full_video.video.preflight_video", return_value={**baseline, "preflight_elapsed_seconds": 1.2}):
            first = self.prepare()
        with patch("full_video.video.preflight_video", return_value={**baseline, "preflight_elapsed_seconds": 9.8}):
            second = self.prepare()
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_cli_preflight_failure_persists_report_and_nonzero_exit(self):
        with patch.object(configuration, "prepare", side_effect=ValueError("synthetic missing configuration")), redirect_stdout(io.StringIO()) as output:
            code = main(["run", "--config", str(self.config_path), "--source", SOURCE])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(len(report["failures"]), 1)
        files = list((self.root / "outputs/batch_reports").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["failures"], report["failures"])

    def test_cli_integrity_failure_is_not_reported_as_success(self):
        with patch.object(configuration, "prepare", return_value={}), \
             patch.object(batch, "run_one", return_value={"source_id": SOURCE, "status": "invalid_complete"}), \
             redirect_stdout(io.StringIO()) as output:
            code = main(["run", "--config", str(self.config_path), "--source", SOURCE])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["failures"][0]["status"], "invalid_complete")


if __name__ == "__main__":
    unittest.main()
