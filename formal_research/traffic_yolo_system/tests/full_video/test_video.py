"""Software-only tests: generated timestamps and fake inference/decoder objects.

These tests do not open any dataset video, load a checkpoint, or run a GPU.
They establish adapter behavior, not model accuracy or full-video throughput.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from full_video import pts_timing, video


def config_template():
    return {
        "input": {"kind": "video", "source_offset_seconds": 100},
        "segment": {"start_seconds": 100.2, "end_seconds": 100.5},
        "device": "cpu",
        "model": {"path": "/not/opened/model.pt", "sha256": "0" * 64, "version": "synthetic-software-test"},
        "detector": {"conf": .1, "imgsz": 640, "iou": .7, "half": False,
                     "agnostic_nms": False, "max_det": 10, "augment": False,
                     "rect": True, "batch": 1, "classes": None},
        "tracker": {"tracker_type": "bytetrack", "track_high_thresh": .35,
                    "track_low_thresh": .1, "new_track_thresh": .35,
                    "track_buffer": 30, "match_thresh": .8, "fuse_score": True},
        "dedup": {"mode": "cross_class_greedy_iou", "iou_threshold": .7},
    }


def task_template():
    return {"source_id": "synthetic.mp4", "split": "Validation", "camera_id": "synthetic_camera",
            "input_path": "/not/opened/synthetic.mp4"}


def camera_template():
    return {"source_ids": ["synthetic.mp4"], "camera_id": "synthetic_camera",
            "layout": {"frame_width": 16, "frame_height": 12}, "background_filter": {"mode": "none"}}


def detection(cls=0, score=.8, box=None):
    return {"box_xyxy": box or [1, 1, 5, 5], "class_id": cls,
            "class_name": video.CLASS_NAMES[cls], "confidence": score}


class TimeAuditTests(unittest.TestCase):
    def test_metadata_requires_known_matching_rates_precise_timebase_and_no_rotation(self):
        stream = {"codec_type": "video", "width": 16, "height": 12,
                  "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001", "time_base": "1/90000"}
        variants = ({"avg_frame_rate": "0/0"}, {"r_frame_rate": "30/1"},
                    {"time_base": "1/10"}, {"side_data_list": [{"rotation": 90}]})
        for changes in variants:
            response = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{**stream, **changes}]}))
            with self.subTest(changes=changes), patch.object(video.subprocess, "run", return_value=response):
                with self.assertRaises(ValueError):
                    video._probe_stream("unused", "synthetic")
        response = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [stream]}))
        with patch.object(video.subprocess, "run", return_value=response):
            _, fps, time_base = video._probe_stream("unused", "synthetic")
        self.assertEqual(fps, Fraction(30000, 1001))
        self.assertEqual(time_base, Fraction(1, 90000))

    def test_rational_fps_and_nonzero_container_pts(self):
        audit = video._audit_pts_rows((f"pts={90000 + i * 3003}|pkt_duration=3003\n" for i in range(7)),
                                      Fraction(30000, 1001), Fraction(1, 90000))
        self.assertEqual(audit["input_frame_count"], 7)
        self.assertEqual(audit["input_first_pts_seconds"], 1)
        self.assertAlmostEqual(audit["input_last_pts_seconds"], 1 + 6 * 1001 / 30000)

    def test_rounded_time_base_is_valid_cfr(self):
        rows = [f"pts={round(i * 1000 / 30)}|duration={round((i + 1) * 1000 / 30) - round(i * 1000 / 30)}" for i in range(120)]
        self.assertEqual(video._audit_pts_rows(rows, Fraction(30), Fraction(1, 1000))["pts_audit_frames"], 120)

    def test_missing_backward_duplicate_gap_and_vfr_rejected(self):
        examples = (
            ["pts=0|pkt_duration=100", "pts=N/A|pkt_duration=100"],
            ["pts=0|pkt_duration=100", "pts=0|pkt_duration=100"],
            ["pts=100|pkt_duration=100", "pts=0|pkt_duration=100"],
            ["pts=0|pkt_duration=100", "pts=200|pkt_duration=100"],
            ["pts=0|pkt_duration=100", "pts=103|pkt_duration=100"],
            ["pts=0|pkt_duration=200"],
            [],
        )
        for rows in examples:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                video._audit_pts_rows(rows, Fraction(10), Fraction(1, 1000))

    def test_unknown_final_duration_rejected(self):
        with self.assertRaisesRegex(ValueError, "Final frame duration"):
            video._audit_pts_rows(["pts=0", "pts=100"], Fraction(10), Fraction(1, 1000))

    def test_missing_earlier_duration_uses_next_pts_but_tail_must_be_known(self):
        self.assertEqual(video._audit_pts_rows(["pts=0", "pts=100|pkt_duration=100"],
                                              Fraction(10), Fraction(1, 1000))["input_frame_count"], 2)

    def test_stream_is_consumed_without_requiring_length(self):
        rows = (f"pts={i}|pkt_duration=1" for i in range(10000))
        audit = video._audit_pts_rows(rows, Fraction(30), Fraction(1, 30))
        self.assertEqual(audit["input_frame_count"], 10000)
        self.assertNotIn("timestamps", audit)

    def test_original_source_segment_and_alignment(self):
        config = config_template()
        segment = video._segment(config, 10, Fraction(10))
        self.assertEqual(segment["first_frame_index"], 2)
        self.assertEqual(segment["end_frame_index_exclusive"], 5)
        self.assertEqual(segment["frame_count"], 3)
        self.assertEqual(segment["source_start_seconds"], 100.2)
        config["segment"]["start_seconds"] = 100.21
        with self.assertRaisesRegex(ValueError, "align"):
            video._segment(config, 10, Fraction(10))

    def test_segment_last_frame_and_full_input_boundaries(self):
        config = config_template()
        config["segment"] = {"start_seconds": 100.9, "end_seconds": 101}
        self.assertEqual(video._segment(config, 10, Fraction(10))["frame_count"], 1)
        config["segment"] = None
        self.assertEqual(video._segment(config, 10, Fraction(10))["frame_count"], 10)
        for segment in ({"start_seconds": 99}, {"end_seconds": 102},
                        {"start_seconds": 100.5, "end_seconds": 100.5}):
            config["segment"] = segment
            with self.subTest(segment=segment), self.assertRaises(ValueError):
                video._segment(config, 10, Fraction(10))

    def test_pts_subprocess_streaming_error_and_timeout(self):
        # Small synthetic stand-in process exercises pipe/error cleanup only.
        with tempfile.TemporaryDirectory(prefix="synthetic-pts-process-") as directory:
            helper = Path(directory) / "synthetic_ffprobe"
            programs = (
                ("print('pts=0|duration=100')\nprint('pts=100|duration=100')\n", None),
                ("import sys\nprint('pts=0|duration=100')\nprint('synthetic decode error', file=sys.stderr)\n", ValueError),
                ("import time\nprint('pts=0|duration=100', flush=True)\ntime.sleep(2)\n", TimeoutError),
            )
            for program, expected_error in programs:
                helper.write_text(f"#!{sys.executable}\n" + program)
                helper.chmod(0o700)
                with self.subTest(expected_error=expected_error):
                    if expected_error:
                        with self.assertRaises(expected_error):
                            video._audit_pts(str(helper), "synthetic", Fraction(10), Fraction(1, 1000), .1)
                    else:
                        result = video._audit_pts(str(helper), "synthetic", Fraction(10), Fraction(1, 1000), 2)
                        self.assertEqual(result["input_frame_count"], 2)


class ScopeAndSettingsTests(unittest.TestCase):
    def test_main_test_rejected_before_file_access(self):
        task = task_template()
        task["split"] = "Main Test"
        with patch.object(Path, "is_file", side_effect=AssertionError("Must not inspect protected input")):
            with self.assertRaisesRegex(ValueError, "Main Test cannot be opened"):
                video.preflight_video(config_template(), task, camera_template())

    def test_wrong_camera_and_unbound_source_fail(self):
        for change in ({"camera_id": "different_camera"}, {"source_id": "different_source.mp4"}):
            task = {**task_template(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                video._validate_scope(config_template(), task, camera_template())

    def test_cached_camera_policy_is_never_silently_disabled_live(self):
        camera = camera_template()
        camera["background_filter"] = {"mode": "cached_policy_only", "camera_id": "synthetic_camera"}
        with self.assertRaisesRegex(ValueError, "full-source scope"):
            video._validate_scope(config_template(), task_template(), camera)
        camera["background_filter"] = {"mode": "unknown"}
        with self.assertRaises(ValueError):
            video._validate_scope(config_template(), task_template(), camera)

    def test_all_algorithm_settings_explicit(self):
        video._validate_settings(config_template())
        for group, field in (("detector", "conf"), ("tracker", "track_buffer"), ("dedup", "mode")):
            config = config_template()
            del config[group][field]
            with self.subTest(group=group, field=field), self.assertRaises(ValueError):
                video._validate_settings(config)

    def test_invalid_tracker_detector_relationship_rejected(self):
        config = config_template()
        config["detector"]["conf"] = .4
        with self.assertRaisesRegex(ValueError, "low-confidence"):
            video._validate_settings(config)
        config = config_template()
        config["detector"]["batch"] = 2
        with self.assertRaisesRegex(ValueError, "batch=1"):
            video._validate_settings(config)

    def test_missing_ffprobe_does_not_assume_30(self):
        with patch.object(video.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "will not assume frame_index/30"):
                video._ffprobe_path(config_template())

    def test_explicit_ffprobe_command_name_uses_path(self):
        config = {"time": {"ffprobe_path": "ffprobe"}}
        with patch.object(video.shutil, "which", return_value=sys.executable) as which:
            self.assertEqual(video._ffprobe_path(config), str(Path(sys.executable).resolve()))
            which.assert_called_once_with("ffprobe")


class DedupAndMemoryTests(unittest.TestCase):
    def test_cross_class_only_tie_order_and_strict_threshold(self):
        raw = [detection(0, .8), detection(1, .8), detection(0, .7)]
        kept, removed = video.suppress_cross_class_duplicates(raw, .7)
        self.assertEqual(kept, [0, 2])
        self.assertEqual(removed[0]["removed_index"], 1)
        self.assertEqual(removed[0]["kept_index"], 0)
        self.assertEqual(video.suppress_cross_class_duplicates(raw, 1), ([0, 1, 2], []))

    def test_legacy_replay_dedup_definition_matches(self):
        # Only pure existing functions are read/imported, no pilot outputs run.
        from replay_vehicle_tracking_pilot import suppress_cross_class_duplicates as legacy
        raw = [detection(0, .8), detection(1, .7), detection(2, .8, [0, 0, 6, 6]),
               detection(3, .1, [8, 8, 9, 9]), detection(0, .8)]
        for threshold in (.1, .7, 1):
            self.assertEqual(video.suppress_cross_class_duplicates(raw, threshold), legacy(raw, threshold))

    def test_pruning_preserves_live_and_lost_state(self):
        items = [SimpleNamespace(track_id=i) for i in range(10000)]
        tracker = SimpleNamespace(tracked_stracks=[items[99]], lost_stracks=[items[999]], removed_stracks=items.copy())
        video._prune_terminal_history(tracker)
        self.assertEqual([t.track_id for t in tracker.removed_stracks], [99, 999])
        self.assertEqual(tracker.tracked_stracks, [items[99]])
        self.assertEqual(tracker.lost_stracks, [items[999]])

    @unittest.skipUnless(importlib.util.find_spec("ultralytics") and importlib.util.find_spec("numpy"),
                         "Existing Ultralytics/NumPy environment required for CPU tracker equivalence check")
    def test_real_bytetrack_terminal_pruning_preserves_every_observation(self):
        # Real CPU ByteTrack on explicitly synthetic detections; no YOLO/model,
        # images, dataset files, or accuracy claims are involved.
        os.environ["YOLO_AUTOINSTALL"] = "false"
        import inspect
        import numpy as np
        from ultralytics.engine.results import Boxes
        from ultralytics.trackers.byte_tracker import BYTETracker
        settings = {**config_template()["tracker"], "track_buffer": 2}
        kwargs = {"frame_rate": 30} if "frame_rate" in inspect.signature(BYTETracker).parameters else {}
        def replay(prune):
            tracker = BYTETracker(SimpleNamespace(**settings), **kwargs)
            outputs, peak_removed = [], 0
            for frame in range(120):
                data = [[1 + frame % 10, 1, 5 + frame % 10, 5, .8, 0]] if frame % 10 < 3 else []
                boxes = Boxes(np.asarray(data, dtype=np.float32).reshape(-1, 6), (12, 16))
                outputs.append(tracker.update(boxes).copy())
                if prune:
                    video._prune_terminal_history(tracker)
                peak_removed = max(peak_removed, len(tracker.removed_stracks))
            return outputs, peak_removed
        baseline, baseline_removed = replay(False)
        bounded, bounded_removed = replay(True)
        for a, b in zip(baseline, bounded):
            np.testing.assert_array_equal(a, b)
        self.assertGreater(baseline_removed, bounded_removed)
        self.assertLessEqual(bounded_removed, 1)


class _FakeBoxes:
    def __init__(self, rows):
        self.rows = rows
        self.data = SimpleNamespace(tolist=lambda: self.rows)

    def __getitem__(self, indices):
        return _FakeBoxes([self.rows[i] for i in indices])

    def cpu(self):
        return self

    def numpy(self):
        return self


class _FakeTracker:
    instances = []

    def __init__(self, args):
        self.args = args
        self.frame_id = 0
        self.tracked_stracks, self.lost_stracks, self.removed_stracks = [], [], []
        self.__class__.instances.append(self)

    def update(self, boxes, frame):
        self.frame_id += 1
        return [[*row[:4], i + 1, row[4], row[5], i] for i, row in enumerate(boxes.rows)]


class _FakeCap:
    def __init__(self, count=6, fail_at=None):
        self.index, self.count, self.fail_at, self.released = 0, count, fail_at, False
        self.fps = 10
        self.timestamps_msec = None
        self.next_frame_indices = None

    def read(self):
        if self.index >= self.count or self.index == self.fail_at:
            return False, None
        self.index += 1
        return True, SimpleNamespace(shape=(12, 16, 3), dtype="uint8", tobytes=lambda: bytes([self.index]) * (12 * 16 * 3))

    def isOpened(self):
        return True

    def get(self, key):
        if key == 5:
            return self.fps
        if key == 0:
            return self.timestamps_msec[self.index - 1] if self.timestamps_msec is not None else (self.index - 1) * 100
        if key == 1:
            return self.next_frame_indices[self.index - 1] if self.next_frame_indices is not None else self.index
        raise ValueError(f"Unexpected decoder property: {key}")

    def release(self):
        self.released = True

    def getBackendName(self):
        return "FFMPEG"


class VideoIterationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-full-video-test-")
        self.addCleanup(self.directory.cleanup)
        self.config, self.task, self.camera = config_template(), task_template(), camera_template()
        source = (Path(self.directory.name) / "synthetic.mp4").resolve()
        model = (Path(self.directory.name) / "synthetic.pt").resolve()
        source.write_bytes(b"software fixture, not video")
        model.write_bytes(b"software fixture, not model")
        self.task["input_path"] = str(source)
        self.config["model"]["path"] = str(model)
        self.config["model"]["sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
        self.preflight = {
            **video._segment(self.config, 6, Fraction(10)),
            "source_id": self.task["source_id"], "split": self.task["split"], "camera_id": self.task["camera_id"],
            "width": 16, "height": 12, "fps_num": 10, "fps_den": 1,
            "time_base_num": 1, "time_base_den": 1000,
            "input_frame_count": 6, "time_mode": "audited_cfr",
            "input_path": str(source), "model_path": str(model),
            "input_stat": video._stat(source), "model_stat": video._stat(model),
            "model_sha256": self.config["model"]["sha256"],
            "configuration_sha256": video._configuration_hash(self.config, self.camera),
        }
        self.cap = _FakeCap()
        self.calls = []
        self.model = SimpleNamespace(task="detect", names=video.CLASS_NAMES, predict=self.predict)
        cv2, numpy, torch, ultralytics, trackers, byte = [ModuleType(name) for name in
            ("cv2", "numpy", "torch", "ultralytics", "ultralytics.trackers", "ultralytics.trackers.byte_tracker")]
        cv2.VideoCapture = lambda path, *args: self.cap
        cv2.__version__ = "synthetic-opencv"
        cv2.CAP_FFMPEG = 1900
        cv2.CAP_PROP_N_THREADS = 70
        cv2.CAP_PROP_FPS = 5
        cv2.CAP_PROP_POS_MSEC = 0
        cv2.CAP_PROP_POS_FRAMES = 1
        numpy.asarray = lambda values, dtype: values
        numpy.int64 = int
        numpy.uint8 = "uint8"
        numpy.ascontiguousarray = lambda value: value
        ultralytics.YOLO = lambda path: self.model
        byte.BYTETracker = _FakeTracker
        _FakeTracker.instances = []
        modules = {item.__name__: item for item in (cv2, numpy, torch, ultralytics, trackers, byte)}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def predict(self, frame, **settings):
        self.calls.append(settings)
        # Empty middle frame is an actual successful no-detection result.
        rows = [] if len(self.calls) == 2 else [[1, 1, 5, 5, .8, 0], [1, 1, 5, 5, .7, 1]]
        return [SimpleNamespace(boxes=_FakeBoxes(rows))]

    def test_prefix_skipped_without_inference_and_times_use_actual_fps(self):
        rows = list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([row["frame_index"] for row in rows], [2, 3, 4])
        self.assertEqual([row["timestamp_seconds"] for row in rows], [0, .1, .2])
        self.assertEqual([row["source_timestamp_seconds"] for row in rows], [100.2, 100.3, 100.4])
        self.assertEqual([row["duration_seconds"] for row in rows], [.1, .1, .1])
        self.assertEqual([len(row["tracks_primary"]) for row in rows], [1, 0, 1])
        self.assertTrue(all(row["valid"] for row in rows))
        self.assertEqual(_FakeTracker.instances[0].frame_id, 3)
        self.assertEqual(len(rows[0]["raw_detections"]), 2)
        self.assertEqual(rows[0]["tracks_primary"][0]["detection_index"], 0)
        self.assertTrue(self.cap.released)

    def test_decode_failure_is_failure_not_valid_zero(self):
        self.cap.fail_at = 3
        iterator = video.iter_video(self.config, self.task, self.camera, self.preflight)
        self.assertEqual(next(iterator)["frame_index"], 2)
        with self.assertRaisesRegex(RuntimeError, "missing time is not a true zero"):
            next(iterator)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.cap.released)

    def test_full_input_extra_frame_rejected(self):
        self.config["segment"] = None
        self.preflight.update(video._segment(self.config, 6, Fraction(10)))
        self.preflight["configuration_sha256"] = video._configuration_hash(self.config, self.camera)
        self.cap.count = 7
        with self.assertRaisesRegex(RuntimeError, "extra frames"):
            list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertTrue(self.cap.released)

    def test_stalled_decoder_timestamps_fail_before_inference(self):
        self.cap.timestamps_msec = [0] * 6
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.cap.released)

    def test_configuration_change_requires_new_preflight(self):
        self.config["detector"]["imgsz"] = 1280
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            next(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(self.calls, [])

    def test_changed_source_requires_new_preflight(self):
        Path(self.task["input_path"]).write_bytes(b"changed synthetic input")
        with self.assertRaisesRegex(ValueError, "changed since preflight"):
            next(video.iter_video(self.config, self.task, self.camera, self.preflight))

    def test_main_test_cannot_reuse_development_preflight(self):
        self.task["split"] = "Main Test"
        with self.assertRaisesRegex(ValueError, "Main Test cannot be opened"):
            next(video.iter_video(self.config, self.task, self.camera, self.preflight))

    def use_irregular_pts(self, ticks=None, time_base=Fraction(1, 1000)):
        ticks = [0, 1, 100, 200, 400, 500, 600] if ticks is None else ticks
        self.config["input"]["source_offset_seconds"] = 0
        self.config["segment"] = None
        self.config["time"] = {"mode": "audited_pts"}
        self.preflight.update(first_frame_index=0, end_frame_index_exclusive=6,
                              frame_count=6, time_mode="audited_pts", source_start_seconds=0,
                              source_end_seconds=float(ticks[-1] * time_base), input_source_offset_seconds=0,
                              time_base_num=time_base.numerator, time_base_den=time_base.denominator,
                              audited_decoder={"backend":"FFMPEG", "opencv_version":"synthetic-opencv",
                                               "requested_decoder_threads":2})
        self.preflight["configuration_sha256"] = video._configuration_hash(self.config, self.camera)
        self.cap.fps = 999
        self.cap.timestamps_msec = [float(tick * time_base * 1000) for tick in ticks[:-1]]
        return [{"frame_index": i, "timestamp_seconds": float(ticks[i] * time_base),
                 "source_timestamp_seconds": float(ticks[i] * time_base), "duration_seconds": float((ticks[i+1]-ticks[i]) * time_base),
                 "pts": ticks[i], "duration_ticks": ticks[i+1]-ticks[i]} for i in range(6)]

    def test_pts_iteration_uses_intervals_not_average_fps(self):
        timing = self.use_irregular_pts()
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
            rows = list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual([r["source_timestamp_seconds"] for r in rows], [0, .001, .1, .2, .4, .5])
        self.assertEqual([r["duration_seconds"] for r in rows], [.001, .099, .1, .2, .1, .1])
        self.assertEqual([r["pts"] for r in rows], [0, 1, 100, 200, 400, 500])
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(self.cap.released)

    def test_pts_decoder_mismatch_rejected(self):
        timing = self.use_irregular_pts()
        self.cap.timestamps_msec = [index * 100 for index in range(6)]
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
            with self.assertRaisesRegex(ValueError, "Decoder timestamp mismatch"):
                list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.cap.released)

    def test_pts_one_90000_tick_interval_preserves_timing_and_duration(self):
        timing = self.use_irregular_pts([0, 1, 2999, 5999, 9000, 12000, 15000], Fraction(1, 90000))
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
            rows = list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[1]["source_timestamp_seconds"], 1 / 90000)
        self.assertEqual(rows[1]["duration_seconds"], 2998 / 90000)
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(self.cap.released)

    def test_pts_duplicate_one_tick_timestamp_rejected_before_affected_inference(self):
        timing = self.use_irregular_pts([0, 1, 2999, 5999, 9000, 12000, 15000], Fraction(1, 90000))
        self.cap.timestamps_msec[1] = 0
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
            with self.assertRaisesRegex(ValueError, "strictly increasing at frame 1"):
                list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(_FakeTracker.instances[0].frame_id, 1)
        self.assertTrue(self.cap.released)

    def test_pts_backwards_timestamp_within_tolerance_rejected_before_affected_inference(self):
        timing = self.use_irregular_pts([0, 1, 2999, 5999, 9000, 12000, 15000], Fraction(1, 90000))
        self.cap.timestamps_msec[1] = -.00005
        self.assertLess(abs(self.cap.timestamps_msec[1] / 1000 - timing[1]["source_timestamp_seconds"]), 1 / 90000 + 1e-7)
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
            with self.assertRaisesRegex(ValueError, "strictly increasing at frame 1"):
                list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(_FakeTracker.instances[0].frame_id, 1)
        self.assertTrue(self.cap.released)

    def test_pts_wrong_or_nonfinite_decoder_index_rejected_before_affected_inference(self):
        timing = self.use_irregular_pts()
        for incorrect in (1, 3, 2.5, float("nan"), float("inf")):
            with self.subTest(decoder_next_index=incorrect):
                self.cap.index, self.cap.released = 0, False
                self.calls.clear()
                self.cap.next_frame_indices = [1, incorrect, 3, 4, 5, 6]
                with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing)):
                    with self.assertRaisesRegex(ValueError, "Decoder frame index mismatch at frame 1"):
                        list(video.iter_video(self.config, self.task, self.camera, self.preflight))
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(_FakeTracker.instances[-1].frame_id, 1)
                self.assertTrue(self.cap.released)

    def test_pts_short_sidecar_rejected(self):
        timing = self.use_irregular_pts()
        with patch.object(pts_timing, "iter_pts", return_value=(r for r in timing[:2])):
            with self.assertRaisesRegex(ValueError, "ended before"):
                list(video.iter_video(self.config, self.task, self.camera, self.preflight))
        self.assertTrue(self.cap.released)

    def test_preparation_candidate_blocks_inference_at_both_entrypoints(self):
        from full_video.runner import execute
        self.config["inference_enabled"] = False
        with self.assertRaisesRegex(ValueError, "INFERENCE_DISABLED"):
            next(video.iter_video(self.config, self.task, self.camera, self.preflight))
        prepared = {"config": self.config, "camera": self.camera, "task": self.task, "preflight": self.preflight}
        with self.assertRaisesRegex(ValueError, "INFERENCE_DISABLED"):
            execute(prepared, Path(self.directory.name)/"never-created")
        self.assertEqual(self.calls, [])
        self.assertFalse((Path(self.directory.name)/"never-created").exists())


if __name__ == "__main__":
    unittest.main()
