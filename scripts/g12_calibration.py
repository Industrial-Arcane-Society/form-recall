#!/usr/bin/env python3
"""Validate and evaluate manually annotated G12 proportion calibration data.

This tool intentionally does not create labels. An annotation record must be
human-reviewed, bound to immutable image bytes, and assigned to an object-level
split before it can contribute to calibration or holdout metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


def _fail(message: str) -> None:
    raise ValueError(message)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ratio(box: list[float]) -> float:
    if not isinstance(box, list) or len(box) != 4:
        _fail("bbox_norm must contain [x_min, y_min, x_max, y_max]")
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in box):
        _fail("bbox_norm coordinates must be finite numbers")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        _fail("bbox_norm must be a non-empty box within [0,1]")
    return (x1 - x0) / (y1 - y0)


def validate_dataset(dataset: dict[str, Any], base_dir: Path, require_data: bool = True) -> list[dict[str, Any]]:
    if dataset.get("schema_version") != "g12-calibration-v1":
        _fail("schema_version must be g12-calibration-v1")
    protocol = dataset.get("labeling_protocol")
    if not isinstance(protocol, str) or not protocol.strip() or "[HUMAN:" in protocol:
        _fail("labeling_protocol must name the frozen human annotation rules")
    pairs = dataset.get("pairs")
    if not isinstance(pairs, list):
        _fail("pairs must be an array")
    if require_data and not pairs:
        _fail("no human-labeled pairs: calibration and holdout counts are both zero")
    seen_pairs: set[str] = set()
    object_splits: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        where = f"pairs[{index}]"
        if not isinstance(pair, dict):
            _fail(f"{where} must be an object")
        object_id = pair.get("object_id")
        split = pair.get("split")
        if not isinstance(object_id, str) or not object_id.strip():
            _fail(f"{where}.object_id is required")
        if split not in ("calibration", "holdout"):
            _fail(f"{where}.split must be calibration or holdout")
        if object_id in object_splits and object_splits[object_id] != split:
            _fail(f"object {object_id!r} occurs in both calibration and holdout; split by object")
        object_splits[object_id] = split
        pair_id = pair.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id.strip() or pair_id in seen_pairs:
            _fail(f"{where}.pair_id must be a unique non-empty string")
        seen_pairs.add(pair_id)
        if pair.get("adjudication_status") != "adjudicated":
            _fail(f"{where}.adjudication_status must be adjudicated")
        pair_label = pair.get("pair_label")
        if pair_label not in ("acceptable", "unacceptable", "indeterminate"):
            _fail(f"{where}.pair_label must be adjudicated acceptable, unacceptable, or indeterminate")
        criterion = pair.get("criterion_version")
        reviewers = pair.get("reviewers")
        if not isinstance(criterion, str) or not criterion.strip():
            _fail(f"{where}.criterion_version is required")
        if (not isinstance(reviewers, list) or any(not isinstance(x, str) or not x.strip() for x in reviewers)
                or len(set(reviewers)) < 2):
            _fail(f"{where}.reviewers must name at least two independent reviewers")
        sides = {}
        for side in ("reference", "preview"):
            item = pair.get(side)
            if not isinstance(item, dict):
                _fail(f"{where}.{side} is required")
            rel = item.get("path")
            expected = item.get("sha256")
            if not isinstance(rel, str) or not rel.strip() or not isinstance(expected, str) or len(expected) != 64:
                _fail(f"{where}.{side} requires path and SHA-256")
            image_path = (base_dir / rel).resolve()
            if not image_path.is_file():
                _fail(f"{where}.{side} image missing: {rel}")
            actual = _sha(image_path)
            if actual.lower() != expected.lower():
                _fail(f"{where}.{side} SHA-256 mismatch: {rel}")
            annotation = item.get("annotation")
            if not isinstance(annotation, dict) or annotation.get("method") not in ("manual_bbox", "manual_mask"):
                _fail(f"{where}.{side}.annotation must use manual_bbox or manual_mask")
            annotator = annotation.get("annotator")
            evidence = annotation.get("evidence_note")
            if not isinstance(annotator, str) or not annotator.strip() or not isinstance(evidence, str) or not evidence.strip():
                _fail(f"{where}.{side}.annotation requires annotator and evidence_note")
            if annotation["method"] != "manual_bbox":
                _fail(f"{where}.{side}: this evaluator currently supports manual_bbox only")
            ratio = _ratio(annotation.get("bbox_norm"))
            sides[side] = {"ratio": ratio, "image_sha256": actual}
        view = pair.get("view")
        if view not in ("front", "right", "top", "back", "left", "bottom", "iso"):
            _fail(f"{where}.view must be a supported canonical view")
        if pair.get("projection") not in ("orthographic", "perspective"):
            _fail(f"{where}.projection must identify the compared projection")
        measurement = pair.get("gate_measurement")
        if not isinstance(measurement, dict) or measurement.get("metric") != "projected_body_width_over_height":
            _fail(f"{where}.gate_measurement.metric must be projected_body_width_over_height")
        if not isinstance(measurement.get("gate_revision"), str) or len(measurement["gate_revision"]) < 7:
            _fail(f"{where}.gate_measurement.gate_revision must identify the frozen gate code revision")
        interval = measurement.get("reference_interval"), measurement.get("preview_interval")
        for label, bounds in zip(("reference_interval", "preview_interval"), interval):
            if (not isinstance(bounds, list) or len(bounds) != 2 or
                    any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in bounds) or
                    bounds[0] <= 0 or bounds[1] < bounds[0]):
                _fail(f"{where}.gate_measurement.{label} must be [positive_low, high]")
        rows.append({"pair_id": pair_id, "object_id": object_id, "split": split,
                     "view": view, "projection": pair["projection"],
                     "pair_label": pair_label,
                     "reference_truth_ratio": sides["reference"]["ratio"],
                     "preview_truth_ratio": sides["preview"]["ratio"],
                     "reference_interval": interval[0], "preview_interval": interval[1]})
    return rows


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_split: dict[str, list[dict[str, Any]]] = {"calibration": [], "holdout": []}
    for row in rows:
        ref_interval, preview_interval = row["reference_interval"], row["preview_interval"]
        gate_overlaps = not (ref_interval[1] < preview_interval[0] or preview_interval[1] < ref_interval[0])
        prediction = "acceptable" if gate_overlaps else "unacceptable"
        row_result = {"pair_id": row["pair_id"], "object_id": row["object_id"],
                      "pair_label": row["pair_label"], "gate_prediction": prediction,
                      "human_ratio_abs_error": abs(row["reference_truth_ratio"] - row["preview_truth_ratio"]),
                      "correct": None if row["pair_label"] == "indeterminate" else prediction == row["pair_label"]}
        by_split[row["split"]].append(row_result)
    object_counts = {split: len({row["object_id"] for row in rows if row["split"] == split}) for split in by_split}
    pair_counts = {split: len(by_split[split]) for split in by_split}
    def accuracy(split: str) -> float | None:
        scored = [row["correct"] for row in by_split[split] if row["correct"] is not None]
        return sum(scored) / len(scored) if scored else None
    return {"status": "evaluated" if pair_counts["calibration"] and pair_counts["holdout"] else "insufficient_data",
            "pair_counts": pair_counts, "object_counts": object_counts,
            "calibration_accuracy": accuracy("calibration"), "holdout_accuracy": accuracy("holdout"),
            "holdout": by_split["holdout"],
            "note": "Scores the frozen gate output against adjudicated human labels. It does not tune the 8% margin; calibration requires a separately reviewed margin-selection protocol before an untouched object-held-out evaluation."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--allow-empty-template", action="store_true", help="validate an empty label template without claiming calibration")
    parser.add_argument("--evaluate", action="store_true", help="emit deterministic calibration/holdout metrics")
    args = parser.parse_args(argv)
    try:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        rows = validate_dataset(dataset, args.dataset.parent, require_data=not args.allow_empty_template)
        if args.evaluate:
            print(json.dumps(evaluate(rows), indent=2, sort_keys=True))
        else:
            print(json.dumps({"status": "valid_template" if not rows else "valid_labeled_data",
                              "pair_count": len(rows), "human_labeled_pair_count": len(rows)}, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"G12 calibration data invalid: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
