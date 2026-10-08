#!/usr/bin/env python3
"""Compare the Rust Quick parse-only batch with the saved legacy parser corpus.

This script compares parser output only. It never builds or validates geometry.
Feature kind/operation signatures are deliberately compared raw; no equivalence
mapping is assumed by this first-pass audit.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "src/legacy/datasets/训练提示词/全部提示词.jsonl"
DEFAULT_LEGACY = ROOT / "artifacts/f5.legacy-prompt-corpus-baseline/legacy_parser_results.jsonl.gz"
DEFAULT_BASELINE_SUMMARY = ROOT / "artifacts/f5.legacy-prompt-corpus-baseline/summary.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_jsonl_bytes(data: bytes, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}: invalid JSON at line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label}: line {line_number} is not a JSON object")
        rows.append(value)
    return rows


def id_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    if "dataset_id" not in row or not isinstance(row.get("prompt_sha256"), str):
        raise ValueError("row is missing dataset_id or prompt_sha256")
    return id_key(row["dataset_id"]), row["prompt_sha256"]


def index_rows(rows: Iterable[dict[str, Any]], label: str) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = row_key(row)
        if key in indexed:
            raise ValueError(f"{label}: duplicate id+hash key {key}")
        indexed[key] = row
    return indexed


def old_success(row: dict[str, Any]) -> bool:
    return bool(row.get("parse_ok")) and int(row.get("feature_count", 0)) > 0


def rust_success(row: dict[str, Any]) -> bool:
    return row.get("status") == "ready" and int(row.get("feature_count", 0)) > 0


def legacy_signature(row: dict[str, Any]) -> list[list[Any]]:
    return [
        [feature.get("kind"), feature.get("op")]
        for feature in row.get("features", [])
    ]


def rust_signature(row: dict[str, Any]) -> list[list[Any]]:
    return [
        [feature.get("kind"), feature.get("operation")]
        for feature in row.get("signature", [])
    ]


def display(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate_groups(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, dict[str, int]] = {}
    for record in records:
        value = record.get(field)
        label = "<missing>" if value is None or value == "" else str(value)
        counts = groups.setdefault(
            label,
            {
                "rows": 0,
                "legacy_success": 0,
                "rust_success": 0,
                "status_mismatches": 0,
                "raw_signature_mismatches": 0,
            },
        )
        counts["rows"] += 1
        counts["legacy_success"] += int(record["legacy_success"])
        counts["rust_success"] += int(record["rust_success"])
        counts["status_mismatches"] += int(record["status_mismatch"])
        counts["raw_signature_mismatches"] += int(record["signature_mismatch"])
    return dict(sorted(groups.items(), key=lambda item: (-item[1]["status_mismatches"], -item[1]["rows"], item[0])))


def compare_rows(
    dataset_rows: list[dict[str, Any]],
    legacy_rows: list[dict[str, Any]],
    rust_rows: list[dict[str, Any]],
    source_sha256: str,
) -> dict[str, Any]:
    source_index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in dataset_rows:
        prompt = row.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("dataset row has a missing or blank prompt")
        key = (id_key(row.get("id")), sha256(prompt.encode("utf-8")))
        if key in source_index:
            raise ValueError(f"dataset has duplicate id+prompt hash key {key}")
        source_index[key] = row

    legacy_index = index_rows(legacy_rows, "legacy snapshot")
    rust_index = index_rows(rust_rows, "Rust output")
    source_keys = set(source_index)
    if set(legacy_index) != source_keys:
        raise ValueError(
            "legacy alignment differs from dataset: "
            f"missing={len(source_keys - set(legacy_index))}, "
            f"extra={len(set(legacy_index) - source_keys)}"
        )
    if set(rust_index) != source_keys:
        raise ValueError(
            "Rust alignment differs from dataset: "
            f"missing={len(source_keys - set(rust_index))}, "
            f"extra={len(set(rust_index) - source_keys)}"
        )
    for key, row in rust_index.items():
        if row.get("dataset_sha256") != source_sha256:
            raise ValueError(f"Rust output source hash differs at key {key}")

    records: list[dict[str, Any]] = []
    status_matrix: collections.Counter[tuple[str, str]] = collections.Counter()
    signature_groups: collections.Counter[tuple[str, str]] = collections.Counter()
    mismatches: list[dict[str, Any]] = []
    for key, source in source_index.items():
        old = legacy_index[key]
        rust = rust_index[key]
        old_ok = old_success(old)
        rust_ok = rust_success(rust)
        old_sig = legacy_signature(old)
        rust_sig = rust_signature(rust)
        status_mismatch = old_ok != rust_ok
        signature_mismatch = old_sig != rust_sig
        category = source.get("category")
        variant = source.get("variant")
        record = {
            "dataset_id": source.get("id"),
            "prompt_sha256": key[1],
            "category": category,
            "variant": variant,
            "legacy_success": old_ok,
            "legacy_feature_count": int(old.get("feature_count", 0)),
            "rust_success": rust_ok,
            "rust_status": rust.get("status"),
            "rust_feature_count": int(rust.get("feature_count", 0)),
            "status_mismatch": status_mismatch,
            "legacy_raw_signature": old_sig,
            "rust_raw_signature": rust_sig,
            "signature_mismatch": signature_mismatch,
        }
        records.append(record)
        status_matrix[("legacy_success" if old_ok else "legacy_rejected", "rust_ready" if rust_ok else "rust_not_ready") ] += 1
        if signature_mismatch:
            signature_groups[(display(old_sig), display(rust_sig))] += 1
        if status_mismatch or signature_mismatch:
            mismatches.append(record)

    matrix = {
        "legacy_success/rust_ready": status_matrix[("legacy_success", "rust_ready")],
        "legacy_success/rust_not_ready": status_matrix[("legacy_success", "rust_not_ready")],
        "legacy_rejected/rust_ready": status_matrix[("legacy_rejected", "rust_ready")],
        "legacy_rejected/rust_not_ready": status_matrix[("legacy_rejected", "rust_not_ready")],
    }
    status_mismatches = sum(v for (old_state, rust_state), v in status_matrix.items() if (old_state == "legacy_success") != (rust_state == "rust_ready"))
    raw_signature_mismatches = sum(1 for record in records if record["signature_mismatch"])
    return {
        "schema_version": 1,
        "comparison_scope": "parse_only; raw feature kind/operation signatures are compared without any semantic equivalence mapping",
        "row_count": len(records),
        "status_matrix": matrix,
        "status_mismatch_count": status_mismatches,
        "raw_signature_mismatch_count": raw_signature_mismatches,
        "by_category": aggregate_groups(records, "category"),
        "by_variant": aggregate_groups(records, "variant"),
        "top_raw_signature_pairs": [
            {"legacy_signature": json.loads(old), "rust_signature": json.loads(rust), "rows": count}
            for (old, rust), count in signature_groups.most_common(100)
        ],
        "mismatch_rows": mismatches,
    }


def make_report_md(report: dict[str, Any], hashes: dict[str, Any]) -> str:
    matrix = report["status_matrix"]
    lines = [
        "# Rust Quick parser corpus comparison",
        "",
        "Parse-only comparison between the saved legacy Python `Parser.parse` snapshot and Rust `native_quick_parser::parse`. No geometry builder, CADPlan validator, OCCT, or export is run.",
        "",
        "**Raw-signature warning:** `kind`/`op` strings are compared literally. A mismatch is a triage signal, not a semantic-equivalence verdict; no mapping has been approved in this first pass.",
        "",
        "## Integrity",
        "",
        f"- Dataset SHA-256: `{hashes['dataset_sha256']}` ({hashes['dataset_rows']} rows)",
        f"- Legacy snapshot SHA-256: `{hashes['legacy_snapshot_sha256']}` ({hashes['legacy_rows']} rows)",
        f"- Rust output source SHA-256: `{hashes['rust_dataset_sha256']}` ({hashes['rust_rows']} rows)",
        "",
        "## Status matrix",
        "",
        "| Legacy / Rust | Rows |",
        "|---|---:|",
        f"| Legacy accepted / Rust ready | {matrix['legacy_success/rust_ready']} |",
        f"| Legacy accepted / Rust not ready | {matrix['legacy_success/rust_not_ready']} |",
        f"| Legacy rejected / Rust ready | {matrix['legacy_rejected/rust_ready']} |",
        f"| Legacy rejected / Rust not ready | {matrix['legacy_rejected/rust_not_ready']} |",
        "",
        f"Status mismatches: **{report['status_mismatch_count']}**. Raw ordered signature mismatches: **{report['raw_signature_mismatch_count']}**.",
        "",
        "## Category summary",
        "",
        "| Category | Rows | Legacy success | Rust ready | Status mismatch | Raw signature mismatch |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for category, counts in report["by_category"].items():
        lines.append(
            f"| {category} | {counts['rows']} | {counts['legacy_success']} | {counts['rust_success']} | {counts['status_mismatches']} | {counts['raw_signature_mismatches']} |"
        )
    lines.extend([
        "",
        "## Variant summary",
        "",
        "| Variant | Rows | Legacy success | Rust ready | Status mismatch | Raw signature mismatch |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for variant, counts in report["by_variant"].items():
        lines.append(
            f"| {variant} | {counts['rows']} | {counts['legacy_success']} | {counts['rust_success']} | {counts['status_mismatches']} | {counts['raw_signature_mismatches']} |"
        )
    lines.extend([
        "",
        "## Top raw signature pairs",
        "",
        "| Rows | Legacy `kind:op` sequence | Rust `kind:operation` sequence |",
        "|---:|---|---|",
    ])
    for pair in report["top_raw_signature_pairs"]:
        lines.append(
            f"| {pair['rows']} | `{display(pair['legacy_signature'])}` | `{display(pair['rust_signature'])}` |"
        )
    lines.extend([
        "",
        "Detailed mismatch rows (IDs, hashes, category/variant, status, counts, and signatures) are in `comparison.json`; raw prompt text is not copied to either report.",
        "",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--legacy", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--rust", type=Path, required=True, help="JSONL emitted by quick_parser_corpus")
    parser.add_argument("--baseline-summary", type=Path, default=DEFAULT_BASELINE_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset_bytes = args.dataset.read_bytes()
    dataset_sha = sha256(dataset_bytes)
    dataset_rows = load_jsonl_bytes(dataset_bytes, "dataset")

    baseline = json.loads(args.baseline_summary.read_text(encoding="utf-8"))
    expected_dataset_sha = baseline["input"]["sha256"]
    expected_legacy_sha = baseline["feature_snapshot"]["sha256"]
    if dataset_sha != expected_dataset_sha:
        raise ValueError(f"dataset SHA-256 differs from saved baseline: {dataset_sha}")
    legacy_bytes = args.legacy.read_bytes()
    legacy_sha = sha256(legacy_bytes)
    if legacy_sha != expected_legacy_sha:
        raise ValueError(f"legacy snapshot SHA-256 differs from saved baseline: {legacy_sha}")
    legacy_data = gzip.decompress(legacy_bytes)
    legacy_rows = load_jsonl_bytes(legacy_data, "legacy snapshot")
    rust_bytes = args.rust.read_bytes()
    rust_rows = load_jsonl_bytes(rust_bytes, "Rust output")
    rust_hashes = {row.get("dataset_sha256") for row in rust_rows}
    if len(rust_hashes) != 1:
        raise ValueError(f"Rust output contains {len(rust_hashes)} distinct dataset hashes")

    report = compare_rows(dataset_rows, legacy_rows, rust_rows, dataset_sha)
    hashes = {
        "dataset_sha256": dataset_sha,
        "dataset_rows": len(dataset_rows),
        "legacy_snapshot_sha256": legacy_sha,
        "legacy_rows": len(legacy_rows),
        "rust_dataset_sha256": next(iter(rust_hashes)),
        "rust_rows": len(rust_rows),
    }
    report["integrity"] = hashes
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "comparison.md").write_text(make_report_md(report, hashes), encoding="utf-8")
    print(
        f"Compared {report['row_count']} rows: {report['status_mismatch_count']} status mismatches, "
        f"{report['raw_signature_mismatch_count']} raw signature mismatches."
    )
    print(f"Reports: {args.output_dir / 'comparison.md'} and {args.output_dir / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, TypeError, ValueError, gzip.BadGzipFile) as exc:
        print(f"compare_parser_corpus: {exc}", file=sys.stderr)
        raise SystemExit(2)
