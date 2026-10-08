#!/usr/bin/env python3
"""Read-only Parser.parse coverage audit for the deduplicated training prompt corpus."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "src/legacy/datasets/训练提示词/全部提示词.jsonl"
F589 = ROOT / "src/apps/desktop/src-tauri/tests/fixtures/quick_legacy_catalog_audit_after_f5_90.json"
OUT = Path(__file__).resolve().parent
RESULTS_JSONL = OUT / "legacy_parser_results.jsonl.gz"
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402


def json_safe(value: Any) -> Any:
    """Convert legacy parser values to deterministic JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def feature_snapshot(feature: Any) -> dict[str, Any]:
    """Preserve Feature.to_dict fields, with an explicit stable contract."""
    serialized = feature.to_dict() if callable(getattr(feature, "to_dict", None)) else {}
    result = {
        "kind": getattr(feature, "kind", None),
        "op": getattr(feature, "op", None),
        "params": getattr(feature, "params", {}),
        "name": getattr(feature, "name", None),
        "feature_id": getattr(feature, "feature_id", None),
        "relations": getattr(feature, "relations", []),
        "enabled": getattr(feature, "enabled", None),
        "provenance": getattr(feature, "provenance", {}),
        "warnings": getattr(feature, "warnings", []),
    }
    result.update(serialized)
    result["warnings"] = getattr(feature, "warnings", result.get("warnings", []))
    return json_safe(result)


def main() -> int:
    source_bytes = DATASET.read_bytes()
    raw_lines = source_bytes.decode("utf-8-sig").splitlines()
    rows: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise TypeError("JSONL row is not an object")
            rows.append(item)
        except Exception as exc:
            malformed.append({"line": line_number, "error": f"{type(exc).__name__}: {exc}"})
    prompt_rows = [row for row in rows if isinstance(row.get("prompt"), str) and row["prompt"].strip()]
    by_prompt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prompt_rows:
        by_prompt[row["prompt"]].append(row)

    matrix = json.loads(F589.read_text(encoding="utf-8"))
    catalog_rows = matrix.get("rows", [])
    catalog_prompts = {row.get("input") for row in catalog_rows if isinstance(row.get("input"), str)}
    feature_type_prompt_counts: Counter[str] = Counter()
    feature_type_instance_counts: Counter[str] = Counter()
    combination_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    success_category_counts: Counter[str] = Counter()
    category_signatures: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []
    per_prompt: dict[str, dict[str, Any]] = {}
    all_feature_counts: list[int] = []
    largest_feature_prompts: list[dict[str, Any]] = []
    ok_count = nonempty_count = ok_empty_count = false_with_features = exception_count = 0
    started = time.monotonic()
    result_fd, result_temp_name = tempfile.mkstemp(prefix="legacy_parser_results.", suffix=".gz.tmp", dir=OUT)
    result_rows = 0
    raw_result_file = os.fdopen(result_fd, "wb")
    compressed_result_file = gzip.GzipFile(filename="", mode="wb", fileobj=raw_result_file, mtime=0)
    result_file = io.TextIOWrapper(compressed_result_file, encoding="utf-8", newline="\n", write_through=True)

    try:
        for index, (prompt, source_rows) in enumerate(by_prompt.items(), 1):
            meta = source_rows[0]
            result_written = False
            category = str(meta.get("category") or "<missing>")
            category_counts[category] += 1
            try:
                # Parser is stateful: reusing one instance accumulates prior
                # Document.features and corrupts per-prompt counts.
                parsed = Parser().parse(prompt)
                features = getattr(getattr(parsed, "doc", None), "features", []) or []
                ok = bool(getattr(parsed, "ok", False))
                message = str(getattr(parsed, "message", "") or "")
                snapshot = {
                    "schema_version": 1,
                    "dataset_id": str(meta.get("id", "")),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "parse_ok": ok,
                    "feature_count": len(features),
                    "features": [feature_snapshot(feature) for feature in features],
                    "warnings": json_safe(getattr(parsed, "warnings", [])),
                    "steps": json_safe(getattr(parsed, "steps", [])),
                    "recognized": json_safe(getattr(parsed, "recognized", None)),
                    "message": message,
                    "questions": json_safe(getattr(parsed, "questions", [])),
                    "assumed": json_safe(getattr(parsed, "assumed", [])),
                    "used_templates": json_safe(getattr(parsed, "used_templates", [])),
                }
                result_file.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                result_rows += 1
                result_written = True
                signature_counts: Counter[str] = Counter()
                signature_hash = hashlib.sha256()
                sequence_prefix: list[str] = []
                sequence_suffix: deque[str] = deque(maxlen=4)
                unique_kinds: set[str] = set()
                for feature in features:
                    kind = str(getattr(feature, "kind", "<missing>"))
                    operation = str(getattr(feature, "op", "<missing>"))
                    token = f"{kind}:{operation}"
                    signature_counts[token] += 1
                    unique_kinds.add(kind)
                    if len(sequence_prefix) < 6:
                        sequence_prefix.append(token)
                    sequence_suffix.append(token)
                    signature_hash.update(token.encode("utf-8"))
                    signature_hash.update(b"\0")
                    feature_type_instance_counts[kind] += 1
                feature_count = len(features)
                composition = ",".join(f"{token}x{count}" for token, count in sorted(signature_counts.items())) or "none"
                prefix = ">".join(sequence_prefix) or "none"
                suffix = ">".join(sequence_suffix) or "none"
                signature = f"n={feature_count};mix={composition};first={prefix};last={suffix};seq={signature_hash.hexdigest()[:16]}"
                has_features = bool(features)
                all_feature_counts.append(feature_count)
                ok_count += int(ok)
                nonempty_count += int(has_features)
                ok_empty_count += int(ok and not has_features)
                false_with_features += int((not ok) and has_features)
                combination_counts[signature] += 1
                category_signatures[category][signature] += 1
                if has_features:
                    success_category_counts[category] += 1
                    feature_type_prompt_counts.update(unique_kinds)
                if not ok or not has_features:
                    failures.append({
                        "id": str(meta.get("id", "")),
                        "category": category,
                        "group": str(meta.get("group", "")),
                        "variant": str(meta.get("variant", "")),
                        "parser_ok": ok,
                        "feature_count": feature_count,
                        "signature": signature,
                        "message": message[:240],
                    })
                per_prompt[prompt] = {
                    "category": category,
                    "group": str(meta.get("group", "")),
                    "variant": str(meta.get("variant", "")),
                    "parser_ok": ok,
                    "feature_count": feature_count,
                    "signature": signature,
                }
                largest_feature_prompts.append({
                    "id": str(meta.get("id", "")),
                    "category": category,
                    "group": str(meta.get("group", "")),
                    "variant": str(meta.get("variant", "")),
                    "feature_count": feature_count,
                    "composition": composition,
                    "sequence_sha256": signature_hash.hexdigest(),
                })
            except Exception as exc:
                exception_count += 1
                error_message = f"{type(exc).__name__}: {exc}"
                snapshot = {
                    "schema_version": 1,
                    "dataset_id": str(meta.get("id", "")),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "parse_ok": False,
                    "feature_count": 0,
                    "features": [],
                    "warnings": [],
                    "steps": [],
                    "recognized": None,
                    "message": error_message,
                    "questions": [],
                    "assumed": [],
                    "used_templates": [],
                    "exception": error_message,
                }
                if not result_written:
                    result_file.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                    result_rows += 1
                failures.append({
                    "id": str(meta.get("id", "")),
                    "category": category,
                    "group": str(meta.get("group", "")),
                    "variant": str(meta.get("variant", "")),
                    "parser_ok": False,
                    "feature_count": 0,
                    "signature": "<exception>",
                    "message": error_message[:240],
                })
                per_prompt[prompt] = {
                    "category": category,
                    "group": str(meta.get("group", "")),
                    "variant": str(meta.get("variant", "")),
                    "parser_ok": False,
                    "feature_count": 0,
                    "signature": "<exception>",
                }
            if index % 100 == 0 or index == len(by_prompt):
                elapsed = time.monotonic() - started
                print(f"Parser.parse progress: {index}/{len(by_prompt)} unique prompts; {elapsed:.1f}s", flush=True)
        result_file.flush()
        result_file.detach()
        compressed_result_file.close()
        raw_result_file.flush()
        os.fsync(raw_result_file.fileno())
        raw_result_file.close()
        os.replace(result_temp_name, RESULTS_JSONL)
    except BaseException:
      try:
        result_file.close()
      except Exception:
        pass
      try:
        raw_result_file.close()
      except Exception:
        pass
      try:
        os.unlink(result_temp_name)
      except FileNotFoundError:
        pass
      raise

    dataset_prompts = set(by_prompt)
    in_catalog = dataset_prompts & catalog_prompts
    catalog_not_in_dataset = catalog_prompts - dataset_prompts
    dataset_not_in_catalog = dataset_prompts - catalog_prompts
    dataset_outside_success_by_category = Counter(
        per_prompt[prompt]["category"] for prompt in dataset_not_in_catalog if per_prompt[prompt]["feature_count"] > 0
    )
    catalog_category_missing = Counter(
        str(row.get("category") or "<missing>")
        for row in catalog_rows
        if row.get("input") in catalog_not_in_dataset
    )
    catalog_exact_success = sum(
        per_prompt[prompt]["feature_count"] > 0 and per_prompt[prompt]["parser_ok"]
        for prompt in in_catalog
    )
    sorted_feature_counts = sorted(all_feature_counts)
    p95_index = max(0, min(len(sorted_feature_counts) - 1, int(0.95 * len(sorted_feature_counts) + 0.999999) - 1))
    largest_feature_prompts.sort(key=lambda item: (-item["feature_count"], item["id"]))
    compressed_snapshot = RESULTS_JSONL.read_bytes()
    uncompressed_snapshot = gzip.decompress(compressed_snapshot)

    summary = {
        "schema_version": 1,
        "method": {
            "parser_entrypoint": "src/legacy/ai_modeler/core/parsing/nlparser.py::Parser.parse",
            "invocation": "create a fresh Parser for each distinct prompt, call Parser.parse(prompt), inspect ParseResult.ok and ParseResult.doc.features only",
            "parser_instance_policy": "fresh Parser instance per prompt; confirmed necessary because Parser reuses/accumulates Document.features when the instance is reused",
            "geometry_builds": 0,
            "document_stats_calls": 0,
            "deduplication": "exact Unicode prompt string; duplicate rows counted once for parser statistics",
            "raw_prompt_text_persisted": False,
            "detailed_result_snapshot": "one JSONL record per exact unique prompt, in input order; prompt itself omitted; fields include dataset_id, UTF-8 prompt_sha256, ParseResult fields, and full ordered Feature.to_dict-compatible data",
            "current_f589_comparison": "exact prompt string set comparison against rows[].input in the saved F5.89 66-row audit fixture",
            "parser_success_definition": "ParseResult.ok is true and doc.features is non-empty; counts are also reported separately to expose false/empty combinations",
        },
        "input": {
            "path": "src/legacy/datasets/训练提示词/全部提示词.jsonl",
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "byte_length": len(source_bytes),
            "physical_lines": len(raw_lines),
            "nonblank_jsonl_rows": len(rows),
            "malformed_rows": malformed,
            "rows_with_nonblank_prompt": len(prompt_rows),
            "unique_exact_prompts": len(by_prompt),
            "duplicate_prompt_rows_removed": len(prompt_rows) - len(by_prompt),
            "missing_or_blank_prompt_rows": len(rows) - len(prompt_rows),
        },
        "feature_snapshot": {
            "path": RESULTS_JSONL.name,
            "schema_version": 1,
            "rows": result_rows,
            "sha256": hashlib.sha256(compressed_snapshot).hexdigest(),
            "byte_length": len(compressed_snapshot),
            "compression": {
                "format": "gzip",
                "mtime": 0,
                "uncompressed_sha256": hashlib.sha256(uncompressed_snapshot).hexdigest(),
                "uncompressed_byte_length": len(uncompressed_snapshot),
                "compressed_sha256": hashlib.sha256(compressed_snapshot).hexdigest(),
                "compressed_byte_length": len(compressed_snapshot),
                "size_reduction_percent": round((1 - len(compressed_snapshot) / len(uncompressed_snapshot)) * 100, 2) if uncompressed_snapshot else 0,
            },
            "raw_prompt_text_persisted": False,
            "row_fields": ["schema_version", "dataset_id", "prompt_sha256", "parse_ok", "feature_count", "features", "warnings", "steps", "recognized", "message", "questions", "assumed", "used_templates"],
            "feature_fields": ["kind", "op", "params", "name", "feature_id", "relations", "enabled", "provenance", "warnings", "note"],
        },
        "parser_counts": {
            "parse_ok": ok_count,
            "feature_producing": nonempty_count,
            "successful_feature_producing": sum(v["parser_ok"] and v["feature_count"] > 0 for v in per_prompt.values()),
            "parse_ok_without_features": ok_empty_count,
            "parse_not_ok_with_features": false_with_features,
            "exceptions": exception_count,
            "total_feature_instances": sum(feature_type_instance_counts.values()),
            "feature_count_per_prompt": {
                "minimum": min(sorted_feature_counts, default=0),
                "median": (sorted_feature_counts[(len(sorted_feature_counts) - 1) // 2] + sorted_feature_counts[len(sorted_feature_counts) // 2]) / 2 if sorted_feature_counts else 0,
                "p95_nearest_rank": sorted_feature_counts[p95_index] if sorted_feature_counts else 0,
                "maximum": max(sorted_feature_counts, default=0),
            },
        },
        "feature_type_groups": {
            "prompts_by_kind": dict(sorted(feature_type_prompt_counts.items())),
            "feature_instances_by_kind": dict(sorted(feature_type_instance_counts.items())),
        },
        "ordered_feature_combinations": dict(sorted(combination_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        "largest_feature_prompts_without_prompt_text": largest_feature_prompts[:10],
        "category_groups": {
            category: {
                "unique_prompts": count,
                "feature_producing_prompts": success_category_counts[category],
                "ordered_combinations": dict(sorted(category_signatures[category].items(), key=lambda pair: (-pair[1], pair[0]))),
            }
            for category, count in sorted(category_counts.items())
        },
        "f589_fixed_catalog_difference": {
            "catalog_rows": len(catalog_rows),
            "catalog_unique_inputs": len(catalog_prompts),
            "exactly_present_in_training_corpus": len(in_catalog),
            "present_and_old_parser_feature_success": catalog_exact_success,
            "catalog_inputs_absent_from_training_corpus": len(catalog_not_in_dataset),
            "absent_catalog_categories": dict(sorted(catalog_category_missing.items())),
            "training_prompts_outside_fixed_catalog": len(dataset_not_in_catalog),
            "outside_catalog_old_parser_feature_success": sum(per_prompt[p]["parser_ok"] and per_prompt[p]["feature_count"] > 0 for p in dataset_not_in_catalog),
            "outside_catalog_feature_success_by_category": dict(sorted(dataset_outside_success_by_category.items())),
        },
        "failed_or_empty_items_without_prompt_text": failures,
        "malformed_jsonl_items": malformed,
        "run_seconds": round(time.monotonic() - started, 3),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
