"""Synthetic software tests only: no dataset media, YOLO, model or GPU access."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from full_video import pts_timing


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True) + "\n")


class AuditedPtsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-pts-adapter-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "synthetic.mp4"
        self.source.write_bytes(b"SYNTHETIC SOFTWARE FIXTURE, NOT A VIDEO")
        self.timing = self.root / "timing_frames.jsonl"
        self.summary_path = self.root / "audit_runtime_envelope.json"
        self.task = {"source_id": self.source.name, "split": "Validation", "camera_id": "synthetic-camera",
                     "input_path": str(self.source)}
        self.camera = {"camera_id": self.task["camera_id"], "source_ids": [self.source.name],
                       "layout": {"frame_width": 16, "frame_height": 12}}
        self.config = {"input": {"kind": "video", "source_offset_seconds": 0}, "segment": None,
                       "time": {"mode": "audited_pts", "max_frame_duration_seconds": .06,
                                "audit_summary_path": str(self.summary_path), "frame_timing_path": str(self.timing)}}
        # Nonuniform intervals, first interval only one tick, and nonzero
        # original PTS establish that neither FPS nor frame index drives time.
        pairs = [(100, 1), (101, 2999), (3100, 4500), (7600, 2000)]
        self.rows = [{"frame_index": i, "pts": pts, "duration": duration, "width": 16, "height": 12}
                     for i, (pts, duration) in enumerate(pairs)]
        self.summary = {
            "schema_version": "full_source_time_preparation_v1", "status": "completed", "synthetic": True,
            "task": {key: self.task[key] for key in ("source_id", "split", "camera_id")},
            "source": str(self.source), "source_size_bytes": self.source.stat().st_size,
            "source_mtime_ns": self.source.stat().st_mtime_ns, "source_sha256": digest(self.source),
            "manifest_sha256": "a" * 64, "no_inference": True, "original_source_stat_unchanged": True,
            "ended_at": "2026-01-01T00:00:01+00:00",
            "pts": {"frame_count": 4, "time_base": "1/90000", "first_pts_ticks": 100,
                    "last_pts_ticks": 7600, "final_frame_duration_ticks": 2000,
                    "normalized_presented_duration_seconds": 9500 / 90000,
                    "ffprobe_eof_verified": True, "ffprobe_error_count": 0},
            "decoder": {"decoded_frames": 4, "matches_ffprobe_frame_count": True,
                        "backend": "FFMPEG", "opencv_version": "synthetic-software-test", "requested_decoder_threads": 2,
                        "first_source_seconds": 0, "last_source_seconds": 7500 / 90000,
                        "timestamp_difference_max_absolute_seconds": 0,
                        "timestamp_tolerance_seconds": 1 / 90000,
                        "timestamp_mismatch_frames": 0, "dimension_mismatch_frames": 0,
                        "frame_index_mismatch_frames": 0, "nonmonotonic_timestamp_frames": 0,
                        "all_frames_match": True, "eof_verified": True,
                        "source_sha256": digest(self.source), "reported_width": 16, "reported_height": 12},
            "stream": {"codec_type": "video", "width": 16, "height": 12,
                       "time_base": "1/90000", "start_pts": 100, "duration_ts": 9500,
                       "nb_frames": "4", "avg_frame_rate": "30/1", "r_frame_rate": "25/1"},
        }
        self.save()

    def save(self):
        self.timing.write_text("".join(json.dumps(row) + "\n" for row in self.rows))
        self.summary["pts"]["timing_frames_sha256"] = digest(self.timing)
        self.summary["decoder"]["timing_frames_sha256"] = digest(self.timing)
        original = deepcopy(self.summary)
        original.pop("evidence_envelope", None)
        original.pop("status", None)
        original.pop("stream", None)
        for key in ("ffprobe_eof_verified", "ffprobe_error_count"):
            original["pts"].pop(key, None)
        for key in ("eof_verified", "source_sha256", "timing_frames_sha256"):
            original["decoder"].pop(key, None)
        dump(self.root / "audit_report.json", original)
        dump(self.root / "pts_summary.json", original["pts"])
        dump(self.root / "decoder_summary.json", original["decoder"])
        dump(self.root / "source_metadata.json", {"streams": [self.summary["stream"]]})
        (self.root / "prepare_time_audit.executed.py").write_text("# Synthetic software evidence fixture only\n")
        (self.root / "ffprobe_frames.stderr.log").write_text("")
        (self.root / "decoder_differences.jsonl").write_text("")
        names = ("audit_report.json", "pts_summary.json", "decoder_summary.json", "source_metadata.json",
                 "prepare_time_audit.executed.py", "ffprobe_frames.stderr.log", "decoder_differences.jsonl")
        self.summary["evidence_envelope"] = {"files": {name: {"path": str(self.root / name),
                                                             "sha256": digest(self.root / name)} for name in names}}
        dump(self.summary_path, self.summary)

    def preflight(self):
        return pts_timing.preflight_pts(self.config, self.task, self.camera)

    def test_vfr_original_ticks_and_tail_preserved(self):
        result = self.preflight()
        timings = list(pts_timing.iter_pts(result))
        self.assertEqual(result["time_mode"], "audited_pts")
        self.assertEqual(result["input_first_pts_ticks"], 100)
        self.assertEqual(result["input_duration_ticks"], 9500)
        self.assertEqual(result["frame_count"], 4)
        self.assertEqual([row["duration_ticks"] for row in timings], [1, 2999, 4500, 2000])
        self.assertEqual(timings[1]["timestamp_seconds"], 1 / 90000)
        self.assertEqual(timings[2]["source_timestamp_seconds"], 3000 / 90000)
        self.assertNotEqual(timings[1]["timestamp_seconds"], 1 / result["fps"])
        self.assertEqual(sum(Fraction(row["duration_ticks"], 90000) for row in timings), Fraction(9500, 90000))

    def test_container_can_end_at_last_pts_without_cutting_tail(self):
        self.summary["stream"]["duration_ts"] = 7500
        self.save()
        result = self.preflight()
        self.assertEqual(result["source_end_seconds"], 9500 / 90000)
        self.assertEqual(result["presentation_tail_beyond_container_seconds"], 2000 / 90000)
        self.assertEqual(result["container_duration_semantics"], "ends_at_final_pts_excluding_its_reported_duration")

    def test_unknown_container_duration_relation_rejected(self):
        self.summary["stream"]["duration_ts"] = 9400
        self.save()
        with self.assertRaisesRegex(ValueError, "complete stream"):
            self.preflight()

    def test_main_test_rejected_before_any_evidence_access(self):
        self.task["split"] = "Main Test"
        with patch.object(Path, "open", side_effect=AssertionError("Protected input must not be read")), \
             patch.object(Path, "stat", side_effect=AssertionError("Protected input must not be inspected")):
            with self.assertRaisesRegex(ValueError, "Main Test cannot be opened"):
                self.preflight()
            with self.assertRaisesRegex(ValueError, "Main Test cannot be opened"):
                list(pts_timing.iter_pts({"split": "Main Test"}))

    def test_unbound_camera_source_rejected(self):
        self.camera["camera_id"] = "other-camera"
        with self.assertRaisesRegex(ValueError, "camera"):
            self.preflight()

    def test_identity_split_and_manifest_hash_must_match(self):
        for group, key, value in (("task", "split", "Train"), ("task", "camera_id", "other-camera"),
                                  ("task", "source_id", "other-source.mp4")):
            with self.subTest(key=key):
                prior = self.summary[group][key]
                self.summary[group][key] = value
                self.save()
                with self.assertRaisesRegex(ValueError, "identity"):
                    self.preflight()
                self.summary[group][key] = prior
        self.save()
        self.config["split_manifest_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "manifest SHA256"):
            self.preflight()

    def test_source_sha_tamper_rejected(self):
        self.source.write_bytes(b"x" * self.source.stat().st_size)
        with self.assertRaisesRegex(ValueError, "Source SHA256"):
            self.preflight()

    def test_missing_pts_and_unknown_final_duration_rejected(self):
        for field, index in (("pts", 1), ("duration", -1)):
            with self.subTest(field=field):
                value = self.rows[index].pop(field)
                self.save()
                with self.assertRaises(ValueError):
                    self.preflight()
                self.rows[index][field] = value

    def test_missing_duplicate_and_out_of_order_indices_rejected(self):
        for index in (0, 3):
            self.rows[1]["frame_index"] = index
            self.save()
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "frame_index"):
                self.preflight()

    def test_duplicate_backward_gap_and_overlap_rejected(self):
        for pts in (100, 99, 102):
            self.rows[1]["pts"] = pts
            self.save()
            with self.subTest(pts=pts), self.assertRaisesRegex(ValueError, "backward|gap/overlap"):
                self.preflight()
        self.rows[1]["pts"] = 101
        self.rows[0]["duration"] = 2
        self.save()
        with self.assertRaisesRegex(ValueError, "gap/overlap"):
            self.preflight()

    def test_missing_and_extra_sidecar_rows_rejected(self):
        last = self.rows.pop()
        self.save()
        with self.assertRaisesRegex(ValueError, "frame count"):
            self.preflight()
        self.rows.extend([last, {**last, "frame_index": 4, "pts": 9600}])
        self.save()
        with self.assertRaisesRegex(ValueError, "extra frames"):
            self.preflight()

    def test_final_duration_summary_mismatch_rejected(self):
        self.rows[-1]["duration"] = 1999
        self.save()
        with self.assertRaisesRegex(ValueError, "final duration"):
            self.preflight()

    def test_duration_fields_and_types_rejected(self):
        for value in (True, 1.5, 0, None):
            self.rows[0]["duration"] = value
            self.save()
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "duration"):
                self.preflight()
        self.rows[0]["duration"] = 1
        self.rows[0]["pkt_duration"] = 2
        self.save()
        with self.assertRaisesRegex(ValueError, "Conflicting duration"):
            self.preflight()

    def test_long_frame_requires_explicit_quality_limit(self):
        self.config["time"].pop("max_frame_duration_seconds")
        with self.assertRaisesRegex(ValueError, "max_frame_duration_seconds"):
            self.preflight()
        self.config["time"]["max_frame_duration_seconds"] = .04
        with self.assertRaisesRegex(ValueError, "long holds"):
            self.preflight()

    def test_dimensions_and_rotation_rejected(self):
        self.rows[1]["width"] = 17
        self.save()
        with self.assertRaisesRegex(ValueError, "dimensions mismatch"):
            self.preflight()
        self.rows[1]["width"] = 16
        self.summary["stream"]["tags"] = {"rotate": 90}
        self.save()
        with self.assertRaisesRegex(ValueError, "Rotated"):
            self.preflight()

    def test_roi_dimensions_rejected(self):
        self.camera["layout"]["frame_width"] = 17
        with self.assertRaisesRegex(ValueError, "ROI dimensions"):
            self.preflight()

    def test_segment_offset_and_fps_assumptions_rejected(self):
        self.config["segment"] = {"start_seconds": 0}
        with self.assertRaisesRegex(ValueError, "full sources only"):
            self.preflight()
        self.config["segment"] = None
        self.config["input"]["source_offset_seconds"] = 1
        with self.assertRaisesRegex(ValueError, "source_offset_seconds=0"):
            self.preflight()
        self.config["input"]["source_offset_seconds"] = 0
        self.config["input"]["fps"] = 30
        with self.assertRaisesRegex(ValueError, "Remove input.fps"):
            self.preflight()

    def test_incomplete_audit_eof_and_decode_mismatch_rejected(self):
        for group, key, value in (("pts", "ffprobe_eof_verified", False), ("decoder", "eof_verified", False),
                                 ("decoder", "timestamp_mismatch_frames", 1), ("decoder", "decoded_frames", 3),
                                 ("decoder", "dimension_mismatch_frames", 1)):
            with self.subTest(key=key):
                prior = self.summary[group][key]
                self.summary[group][key] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.preflight()
                self.summary[group][key] = prior

    def test_decoder_time_tolerance_must_not_be_loosened(self):
        self.summary["decoder"]["timestamp_tolerance_seconds"] = .05
        self.save()
        with self.assertRaisesRegex(ValueError, "one tick"):
            self.preflight()

    def test_audited_decoder_identity_and_bounded_threads_preserved(self):
        self.assertEqual(self.preflight()["audited_decoder"],
                         {"backend": "FFMPEG", "opencv_version": "synthetic-software-test", "requested_decoder_threads": 2})
        for key, value in (("backend", "OTHER"), ("opencv_version", ""),
                           ("requested_decoder_threads", 0), ("requested_decoder_threads", True)):
            prior = self.summary["decoder"][key]
            self.summary["decoder"][key] = value
            self.save()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.preflight()
            self.summary["decoder"][key] = prior

    def test_decoder_source_binding_rejected(self):
        self.summary["decoder"]["source_sha256"] = "b" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "not bound"):
            self.preflight()

    def test_sidecar_and_original_evidence_tamper_rejected(self):
        self.timing.write_text(self.timing.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Frame timing SHA256"):
            self.preflight()
        self.save()
        (self.root / "decoder_summary.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            self.preflight()

    def test_envelope_cannot_rewrite_historical_report(self):
        self.summary["pts"]["frame_count"] = 5
        dump(self.summary_path, self.summary)
        with self.assertRaisesRegex(ValueError, "contradicts original"):
            self.preflight()

    def test_evidence_cannot_redirect_to_arbitrary_file(self):
        self.summary["evidence_envelope"]["files"]["source_metadata.json"]["path"] = str(self.source)
        dump(self.summary_path, self.summary)
        with self.assertRaisesRegex(ValueError, "Audit evidence path"):
            self.preflight()

    def test_iter_checks_tamper_even_if_stat_restored(self):
        result = self.preflight()
        stat = self.timing.stat()
        self.timing.write_text(self.timing.read_text().replace('"pts": 101', '"pts": 102'))
        os.utime(self.timing, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "frame_timing evidence changed"):
            list(pts_timing.iter_pts(result))

    def test_iter_checks_source_changed(self):
        result = self.preflight()
        self.source.write_bytes(b"different source")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            list(pts_timing.iter_pts(result))

    def test_reported_float_times_are_crosschecked_against_ticks(self):
        self.rows[1]["source_seconds"] = .033333
        self.save()
        with self.assertRaisesRegex(ValueError, "disagrees with original integer PTS"):
            self.preflight()


if __name__ == "__main__":
    unittest.main()
