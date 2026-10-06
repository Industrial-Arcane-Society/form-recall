import hashlib
import tempfile
import unittest
from pathlib import Path

from g12_calibration import evaluate, validate_dataset


def _pair(base: Path, object_id: str, split: str, pair_id: str = "p1") -> dict:
    result = {}
    for name in ("reference", "preview"):
        path = base / f"{pair_id}-{name}.png"
        path.write_bytes((pair_id + name).encode())
        result[name] = {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "annotation": {"method": "manual_bbox", "annotator": f"reviewer-{name}",
                           "evidence_note": "manually traced outer object envelope", "bbox_norm": [0.2, 0.1, 0.8, 0.9]},
        }
    return {"pair_id": pair_id, "object_id": object_id, "split": split, "view": "front", "projection": "orthographic", "pair_label": "acceptable",
            "criterion_version": "silhouette-v1", "reviewers": ["reviewer-a", "reviewer-b"],
            "adjudication_status": "adjudicated", "reference": result["reference"], "preview": result["preview"],
            "gate_measurement": {"metric": "projected_body_width_over_height", "gate_revision": "abcdef123456", "reference_interval": [0.7, 0.8], "preview_interval": [0.7, 0.8]}}


class CalibrationDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def dataset(self, pairs):
        return {"schema_version": "g12-calibration-v1", "labeling_protocol": "silhouette-v1 frozen human rules", "pairs": pairs}

    def test_empty_template_is_not_accepted_as_labeled_data(self):
        with self.assertRaisesRegex(ValueError, "zero"):
            validate_dataset(self.dataset([]), self.base)
        self.assertEqual([], validate_dataset(self.dataset([]), self.base, require_data=False))

    def test_hash_mismatch_rejects_import(self):
        pair = _pair(self.base, "part-a", "calibration")
        pair["reference"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate_dataset(self.dataset([pair]), self.base)

    def test_object_cannot_leak_across_splits(self):
        pairs = [_pair(self.base, "part-a", "calibration", "p1"), _pair(self.base, "part-a", "holdout", "p2")]
        with self.assertRaisesRegex(ValueError, "split by object"):
            validate_dataset(self.dataset(pairs), self.base)

    def test_evaluator_reports_distinct_object_heldout_counts(self):
        pairs = [_pair(self.base, "part-a", "calibration", "p1"), _pair(self.base, "part-b", "holdout", "p2")]
        rows = validate_dataset(self.dataset(pairs), self.base)
        report = evaluate(rows)
        self.assertEqual("evaluated", report["status"])
        self.assertEqual({"calibration": 1, "holdout": 1}, report["object_counts"])
        self.assertEqual(1.0, report["holdout_accuracy"])


if __name__ == "__main__":
    unittest.main()
