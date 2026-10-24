"""Synthetic scheduler tests; no video, prediction, label, GPU or model is read."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

SYSTEM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SYSTEM / "src"))
from full_video import batch

MANIFEST = SYSTEM.parent / "03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv"
SOURCE = "Bellevue_150th_Newport__2017-09-10_18-08-24.mp4"


def prepared(fingerprint="a" * 64):
    return {"fingerprint": fingerprint, "task": {"source_id": SOURCE, "split": "Validation", "camera_id": "Bellevue_150th_Newport"},
            "run_version": "synthetic-unit-test", "test_data": "synthetic_software_test_only"}


def successful(config, attempt):
    (attempt / "frames.jsonl").write_text('{"synthetic":true,"frame_index":0}\n', encoding="utf-8")
    return {"synthetic_software_test_only": True, "frames": 1}


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full-video-batch-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_frozen_manifest_development_only(self):
        tasks = batch.list_development(MANIFEST)
        self.assertEqual(len(tasks), 98)
        self.assertEqual(sum(t["split"] == "Train" for t in tasks), 81)
        self.assertEqual(sum(t["split"] == "Validation" for t in tasks), 17)
        self.assertTrue(all(t["source_id"].startswith(t["camera_id"] + "__") for t in tasks))

    def _mutated_manifest(self, mutation):
        with MANIFEST.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            rows = list(reader)
        mutation(rows)
        path = self.root / "synthetic-mutated-manifest.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_duplicate_manifest_rejected(self):
        path = self._mutated_manifest(lambda rows: rows.append(rows[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            batch.list_development(path)

    def test_reassigned_test_split_rejected_even_same_counts(self):
        def swap(rows):
            train = next(row for row in rows if row["Formal Split"] == "Train")
            test = next(row for row in rows if row["Formal Split"] == "Main Test")
            train["Formal Split"], test["Formal Split"] = test["Formal Split"], train["Formal Split"]
        path = self._mutated_manifest(swap)
        with self.assertRaisesRegex(ValueError, "membership"):
            batch.list_development(path)

    def test_unapproved_manifest_rejected(self):
        path = self._mutated_manifest(lambda rows: rows[0].update({"Approval Status": "Draft"}))
        with self.assertRaisesRegex(ValueError, "approved frozen"):
            batch.list_development(path)

    def test_plan_stats_only_development_exact_camera_paths(self):
        accessed = []
        def record_stat(path):
            accessed.append(path)
            return False
        with patch.object(Path, "is_file", record_stat):
            report = batch.plan(MANIFEST, self.root / "videos", {"Bellevue_150th_Newport": {
                "camera_id": "Bellevue_150th_Newport", "source_ids": [SOURCE], "allowed_source_intervals_seconds": [[0, 3600]]}})
        allowed = {t["source_id"] for t in batch.list_development(MANIFEST)}
        self.assertEqual(report["source_count"], 98)
        self.assertTrue(all(p.name in allowed for p in accessed))
        target = next(t for t in report["tasks"] if t["source_id"] == SOURCE)
        self.assertEqual(Path(target["input_path"]), self.root / "videos/Bellevue_150th_Newport" / SOURCE)
        self.assertEqual(target["blockers"], ["input_missing"])
        other = next(t for t in report["tasks"] if t["camera_id"] != "Bellevue_150th_Newport")
        self.assertIn("camera_configuration_missing", other["blockers"])

    def test_plan_distinguishes_missing_registry_and_limited_camera_scope(self):
        report = batch.plan(MANIFEST, self.root, {
            "Bellevue_150th_Newport": {"config_path": None, "status": "missing"}})
        target = next(t for t in report["tasks"] if t["source_id"] == SOURCE)
        self.assertIn("camera_configuration_missing", target["blockers"])
        report = batch.plan(MANIFEST, self.root, {"Bellevue_150th_Newport": {
            "camera_id": "Bellevue_150th_Newport", "source_ids": [SOURCE], "allowed_source_intervals_seconds": [[1600, 1720]]}})
        target = next(t for t in report["tasks"] if t["source_id"] == SOURCE)
        self.assertIn("full_video_camera_scope_unverified", target["blockers"])
        other = next(t for t in report["tasks"] if t["camera_id"] == "Bellevue_150th_Newport" and t["source_id"] != SOURCE)
        self.assertIn("camera_source_not_in_scope", other["blockers"])

    def test_success_integrity_skip_and_changed_configuration_isolated(self):
        first = batch.run_one(prepared(), self.root, successful)
        self.assertEqual(first["status"], "completed")
        second = batch.run_one(prepared(), self.root, lambda *args: self.fail("must skip callback"))
        self.assertEqual(second["status"], "skipped_completed")
        changed = batch.run_one(prepared("b" * 64), self.root, successful)
        self.assertEqual(changed["status"], "completed")
        self.assertNotEqual(first["configuration_directory"], changed["configuration_directory"])
        self.assertTrue(Path(first["attempt_directory"]).is_dir())

    def test_tampered_output_not_skipped_and_retry_preserves_old_bytes(self):
        first = batch.run_one(prepared(), self.root, successful)
        old = Path(first["attempt_directory"]) / "frames.jsonl"
        old.write_text("corruption", encoding="utf-8")
        check = batch.run_one(prepared(), self.root, lambda *args: self.fail("must not execute without retry"))
        self.assertEqual(check["status"], "invalid_complete")
        rerun = batch.run_one(prepared(), self.root, successful, retry_failed=True)
        self.assertEqual(rerun["status"], "completed")
        self.assertTrue(rerun["attempt_directory"].endswith("attempt-000002"))
        self.assertEqual(old.read_text(), "corruption")

    def test_extra_or_missing_payload_detected(self):
        run = batch.run_one(prepared(), self.root, successful)
        attempt = Path(run["attempt_directory"])
        (attempt / "unexpected.json").write_text("{}")
        self.assertFalse(batch.verify_completion(attempt, "a" * 64, SOURCE)[0])
        (attempt / "unexpected.json").unlink()
        (attempt / "frames.jsonl").unlink()
        self.assertFalse(batch.verify_completion(attempt, "a" * 64, SOURCE)[0])

    def test_failure_record_and_whole_interval_retry(self):
        def fail(config, attempt):
            (attempt / "partial.jsonl").write_text("synthetic partial output")
            raise RuntimeError("synthetic failure")
        failed = batch.run_one(prepared(), self.root, fail)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue((Path(failed["attempt_directory"]) / "failure.json").is_file())
        blocked = batch.run_one(prepared(), self.root, successful)
        self.assertEqual(blocked["status"], "blocked_failed")
        retry = batch.run_one(prepared(), self.root, successful, retry_failed=True)
        self.assertEqual(retry["status"], "completed")
        status = json.loads((Path(retry["attempt_directory"]) / "status.json").read_text())
        self.assertFalse(status["tracking_continuity_across_attempts"])
        self.assertEqual(status["restart_policy"], "whole_configured_interval_new_tracking_state")
        self.assertTrue((Path(failed["attempt_directory"]) / "partial.jsonl").exists())

    def _interrupted(self, host=None, pid=987654321):
        directory = self.root / SOURCE / ("a" * 64)
        attempt = directory / "attempt-000001"
        attempt.mkdir(parents=True)
        batch.atomic_json(attempt / "status.json", {"status": "running", "fingerprint": "a" * 64})
        batch.atomic_json(directory / "lease.json", {"host": host or socket.gethostname(), "pid": pid,
                                                    "nonce": "synthetic-dead-owner", "attempt": attempt.name})
        return attempt

    def test_stale_local_running_recovery_requires_explicit_flag(self):
        original = self._interrupted()
        with patch.object(batch, "_pid_alive", return_value=False):
            result = batch.run_one(prepared(), self.root, successful)
            self.assertEqual(result["status"], "blocked_running")
            recovered = batch.run_one(prepared(), self.root, successful, recover_interrupted=True)
        self.assertEqual(recovered["status"], "completed")
        self.assertTrue(recovered["attempt_directory"].endswith("attempt-000002"))
        self.assertEqual(json.loads((original / "status.json").read_text())["status"], "interrupted")

    def test_exit_after_attempt_completed_repairs_parent_without_rerunning(self):
        # Exit an actual child at the precise non-atomic boundary between the
        # attempt and parent writes. This leaves a real dead-owner lease behind.
        script = textwrap.dedent("""\
            import json
            import os
            from pathlib import Path
            import sys
            sys.path.insert(0, sys.argv[1])
            from full_video import batch
            config = json.loads(sys.argv[3])
            root = Path(sys.argv[2])
            directory = root / config["task"]["source_id"] / config["fingerprint"]
            original_atomic_json = batch.atomic_json
            def interrupt_parent_write(path, value):
                if path == directory / "status.json" and value.get("status") == "completed":
                    os._exit(73)
                original_atomic_json(path, value)
            def execute(config, attempt):
                (attempt / "frames.jsonl").write_text('{"synthetic":true}\\n')
                return {"synthetic_software_test_only": True, "frames": 1}
            batch.atomic_json = interrupt_parent_write
            batch.run_one(config, root, execute)
            """)
        process = subprocess.run([sys.executable, "-c", script, str(SYSTEM / "src"), str(self.root),
                                  json.dumps(prepared())], capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 73, process.stderr)
        directory = self.root / SOURCE / ("a" * 64)
        attempt = directory / "attempt-000001"
        completed = json.loads((attempt / "status.json").read_text())
        self.assertEqual(completed["status"], "completed")
        self.assertIn("ended_at", completed)
        self.assertEqual(batch.collect_status(self.root)["counts"], {"running": 1})
        self.assertTrue((directory / "lease.json").exists())
        blocked = batch.run_one(prepared(), self.root, lambda *args: self.fail("must not execute"))
        self.assertEqual(blocked["status"], "blocked_running")
        with patch.object(batch, "_now", side_effect=AssertionError("recovery must preserve original times")):
            recovered = batch.run_one(prepared(), self.root, lambda *args: self.fail("must skip callback"),
                                      recover_interrupted=True)
        self.assertEqual(recovered["status"], "skipped_completed")
        self.assertEqual(recovered["attempt_directory"], str(attempt))
        self.assertEqual(json.loads((directory / "status.json").read_text()), completed)
        self.assertEqual(json.loads((attempt / "status.json").read_text()), completed)
        self.assertEqual(batch.collect_status(self.root)["counts"], {"completed": 1})
        self.assertFalse((directory / "lease.json").exists())
        self.assertEqual(batch._attempts(directory), [attempt])

    def test_verified_skip_repairs_missing_or_stale_parent_from_completed_attempt(self):
        for name in ("missing", "stale", "unreadable"):
            with self.subTest(parent_state=name):
                root = self.root / name
                first = batch.run_one(prepared(), root, successful)
                directory = Path(first["configuration_directory"])
                attempt = Path(first["attempt_directory"])
                completed = json.loads((attempt / "status.json").read_text())
                parent_path = directory / "status.json"
                if name == "missing":
                    parent_path.unlink()
                elif name == "stale":
                    batch.atomic_json(parent_path, {"status": "failed", "attempt": "attempt-999999",
                                                   "source_id": "wrong.mp4", "fingerprint": "b" * 64,
                                                   "started_at": "1900-01-01T00:00:00+00:00",
                                                   "ended_at": "2100-01-01T00:00:00+00:00"})
                else:
                    parent_path.write_text("{invalid parent JSON")
                with patch.object(batch, "_now", side_effect=AssertionError("skip must preserve original times")):
                    skipped = batch.run_one(prepared(), root, lambda *args: self.fail("must skip callback"))
                self.assertEqual(skipped["status"], "skipped_completed")
                self.assertEqual(skipped["attempt_directory"], str(attempt))
                self.assertEqual(json.loads(parent_path.read_text()), completed)
                self.assertEqual(batch.collect_status(root)["counts"], {"completed": 1})
                self.assertEqual(batch._attempts(directory), [attempt])

    def test_invalid_completion_does_not_promote_parent_during_recovery(self):
        first = batch.run_one(prepared(), self.root, successful)
        directory = Path(first["configuration_directory"])
        attempt = Path(first["attempt_directory"])
        interrupted_parent = json.loads((attempt / "status.json").read_text())
        interrupted_parent.update(status="running")
        interrupted_parent.pop("ended_at")
        batch.atomic_json(directory / "status.json", interrupted_parent)
        batch.atomic_json(directory / "lease.json", {"host": socket.gethostname(), "pid": 987654321,
                                                     "nonce": "synthetic-dead-owner", "attempt": attempt.name})
        (attempt / "frames.jsonl").write_text("synthetic corruption")
        with patch.object(batch, "_pid_alive", return_value=False):
            recovered = batch.run_one(prepared(), self.root, lambda *args: self.fail("must not execute"),
                                      recover_interrupted=True)
        self.assertEqual(recovered["status"], "invalid_complete")
        self.assertEqual(json.loads((directory / "status.json").read_text()), interrupted_parent)
        self.assertEqual(batch.collect_status(self.root)["counts"], {"running": 1})
        self.assertEqual(batch._attempts(directory), [attempt])

    def test_foreign_or_live_owner_is_never_stolen(self):
        self._interrupted(host="other-synthetic-host")
        with patch.object(batch, "_pid_alive", return_value=False):
            result = batch.run_one(prepared(), self.root, successful, recover_interrupted=True)
        self.assertEqual(result["status"], "blocked_running")

    def test_concurrent_same_configuration_callback_runs_once(self):
        started, finish = threading.Event(), threading.Event()
        results = []
        def slow(config, attempt):
            started.set()
            self.assertTrue(finish.wait(3))
            return successful(config, attempt)
        thread = threading.Thread(target=lambda: results.append(batch.run_one(prepared(), self.root, slow)))
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            competing = batch.run_one(prepared(), self.root, successful, recover_interrupted=True)
            self.assertEqual(competing["status"], "blocked_running")
        finally:
            finish.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "completed")

    def test_main_test_rejected_before_output_creation(self):
        config = prepared()
        config["task"]["split"] = "Main Test"
        with self.assertRaisesRegex(ValueError, "Main Test"):
            batch.run_one(config, self.root, successful)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_callback_failure_summary_cannot_complete(self):
        result = batch.run_one(prepared(), self.root, lambda *args: {"status": "failed"})
        self.assertEqual(result["status"], "failed")
        self.assertFalse((Path(result["attempt_directory"]) / "completion.json").exists())

    def test_explicit_bounds_and_dry_run_do_not_execute(self):
        def prepare(path, source):
            return prepared()
        report = batch.batch_run(self.root / "synthetic.json", [SOURCE], self.root,
                                 prepare, lambda *args: self.fail("dry run executed"), dry_run=True)
        self.assertEqual(report["counts"], {"preflight_passed": 1})
        self.assertEqual(list(self.root.iterdir()), [])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            batch.batch_run(self.root / "synthetic.json", [SOURCE, "second.mp4"], self.root,
                            lambda *args: self.fail("should fail before prepare"), successful)

    def test_preflight_failure_report_and_unique_reports(self):
        def prepare(path, source):
            raise ValueError("synthetic missing camera")
        report = batch.batch_run(self.root / "synthetic.json", [SOURCE], self.root, prepare, successful)
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["status"], "preflight_failed")
        first = batch.write_report(report, self.root)
        second = batch.write_report(report, self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(len(json.loads((first / "failures.json").read_text())["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
