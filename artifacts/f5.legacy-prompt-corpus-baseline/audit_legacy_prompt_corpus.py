#!/usr/bin/env python3
"""Read-only Parser.parse coverage audit for the deduplicated training prompt corpus."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "src/legacy/datasets/训练提示词/全部提示词.jsonl"
F589 = ROOT / "src/apps/desktop/src-tauri/tests/fixtures/quick_legacy_catalog_audit_after_f5_90.json"
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402


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

    for index, (prompt, source_rows) in enumerate(by_prompt.items(), 1):
        meta = source_rows[0]
        category = str(meta.get("category") or "<missing>")
        category_counts[category] += 1
        try:
            # Parser is stateful: reusing one instance accumulates prior
            # Document.features and corrupts per-prompt counts.
            parsed = Parser().parse(prompt)
            features = getattr(getattr(parsed, "doc", None), "features", []) or []
            ok = bool(getattr(parsed, "ok", False))
            message = str(getattr(parsed, "message", "") or "")
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
            failures.append({
                "id": str(meta.get("id", "")),
                "category": category,
                "group": str(meta.get("group", "")),
                "variant": str(meta.get("variant", "")),
                "parser_ok": False,
                "feature_count": 0,
                "signature": "<exception>",
                "message": f"{type(exc).__name__}: {exc}"[:240],
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
