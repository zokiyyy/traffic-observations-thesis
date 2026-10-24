"""Scope checks use only the frozen split metadata, never Main Test media."""

from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM / "src"))
from run_video_tracking_pilot import FROZEN_SPLIT_SHA, ORIGINAL_PILOT_SOURCE, validate_manifest

SPLIT_FILE = SYSTEM.parent / "03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv"
OLD_MANIFEST = SYSTEM / "outputs/video_pipeline/yolo11s_newport_val_120s_dedup_v5_2026-09-24/input/clip_manifest.json"


class PilotTransferScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv_bytes = SPLIT_FILE.read_bytes()
        cls.csv_text = cls.csv_bytes.decode("utf-8")
        cls.rows = list(csv.DictReader(io.StringIO(cls.csv_text.lstrip("\ufeff"), newline="")))
        cls.original = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))

    def manifest(self, filename="Bellevue_150th_Newport__2017-09-11_15-08-32.mp4", start=1600):
        # Reusing field structure is intentional; no referenced media is read.
        manifest = deepcopy(self.original)
        manifest["source_split"] = deepcopy(next(row for row in self.rows if row["Source Video Filename"] == filename))
        manifest["source_video"] = "/source/" + filename
        manifest["clip_start_seconds"] = start
        manifest["clip_end_seconds"] = start + 120
        manifest["transfer_scope"] = {
            "schema_version": 1,
            "kind": "fixed_rules_newport_validation_transfer",
            "split_manifest_csv_text": self.csv_text,
            "split_manifest_sha256": FROZEN_SPLIT_SHA,
            "selected_before_inference": True,
            "no_rule_tuning": True,
        }
        return manifest

    def check(self, manifest):
        validate_manifest(manifest, {"sha256": self.original["clip_sha256"]})

    def test_actual_official_split_bytes_match_frozen_hash(self):
        self.assertEqual(hashlib.sha256(self.csv_bytes).hexdigest(), FROZEN_SPLIT_SHA)

    def test_original_scope_still_passes_without_transfer_opt_in(self):
        self.check(self.original)
        modified = deepcopy(self.original)
        modified["clip_start_seconds"], modified["clip_end_seconds"] = 1720, 1840
        with self.assertRaises(ValueError):
            self.check(modified)

    def test_another_frozen_validation_source_passes(self):
        manifest = self.manifest()
        before = deepcopy(manifest)
        self.check(manifest)
        self.assertEqual(manifest, before)

    def test_same_source_nonoverlapping_windows_and_touching_boundaries_pass(self):
        for start in (0, 1480, 1720, 2400, 3480):
            with self.subTest(start=start):
                self.check(self.manifest(ORIGINAL_PILOT_SOURCE, start=start))

    def test_same_source_overlap_rejected(self):
        for start in (1480.001, 1590, 1600, 1650, 1719.999):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.check(self.manifest(ORIGINAL_PILOT_SOURCE, start=start))

    def test_main_test_train_and_other_intersection_rejected(self):
        samples = (
            next(row for row in self.rows if row["Formal Split"] == "Main Test"),
            next(row for row in self.rows if row["Formal Split"] == "Train"),
            next(row for row in self.rows if row["Formal Split"] == "Validation" and row["Intersection"] != "Bellevue_150th_Newport"),
        )
        for row in samples:
            with self.subTest(row=row["Source Video Filename"]), self.assertRaises(ValueError):
                self.check(self.manifest(row["Source Video Filename"]))

    def test_forging_main_test_row_to_validation_is_rejected(self):
        row = next(row for row in self.rows if row["Formal Split"] == "Main Test")
        manifest = self.manifest(row["Source Video Filename"])
        manifest["source_split"]["Formal Split"] = "Validation"
        with self.assertRaises(ValueError):
            self.check(manifest)

    def test_csv_tampering_rejected_even_with_recomputed_supplied_hash(self):
        for recompute in (False, True):
            manifest = self.manifest()
            scope = manifest["transfer_scope"]
            scope["split_manifest_csv_text"] += "\n"
            if recompute:
                scope["split_manifest_sha256"] = hashlib.sha256(scope["split_manifest_csv_text"].encode("utf-8")).hexdigest()
            with self.subTest(recompute=recompute), self.assertRaises(ValueError):
                self.check(manifest)

    def test_source_row_requires_exact_equality(self):
        for key, value in (("Notes", "rewritten"), ("Approval Status", "Pending"),
                           ("Source Video Filename", "invented_validation.mp4")):
            manifest = self.manifest()
            manifest["source_split"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check(manifest)

    def test_manifest_source_path_must_match_csv_source_filename(self):
        for value in (None, True, "/wrong/other.mp4", "/wrong/" + ORIGINAL_PILOT_SOURCE):
            manifest = self.manifest()
            manifest["source_video"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(manifest)

    def test_transfer_declaration_requires_explicit_true_and_known_schema(self):
        for key, value in (("schema_version", True), ("schema_version", 2), ("kind", "any_video"),
                           ("selected_before_inference", False), ("selected_before_inference", 1),
                           ("no_rule_tuning", False), ("no_rule_tuning", "true")):
            manifest = self.manifest()
            manifest["transfer_scope"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.check(manifest)
        manifest = self.manifest()
        manifest["transfer_scope"] = None
        with self.assertRaises(ValueError):
            self.check(manifest)

    def test_invalid_times_and_wrong_duration_rejected(self):
        for start, end in ((True, 121), (0, False), ("0", 120), (-1, 119), (0, 119.99),
                           (float("nan"), 120), (0, float("inf")), (3500, 3620)):
            manifest = self.manifest()
            manifest["clip_start_seconds"], manifest["clip_end_seconds"] = start, end
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.check(manifest)

    def test_clip_fingerprint_and_video_geometry_remain_bound(self):
        for key, value in (("clip_sha256", "0" * 64), ("frame_count", 3599), ("width", 1920),
                           ("height", 1080), ("fps", 29.97), ("fps", "30"), ("fps", float("nan"))):
            manifest = self.manifest()
            manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check(manifest)


if __name__ == "__main__":
    unittest.main()
