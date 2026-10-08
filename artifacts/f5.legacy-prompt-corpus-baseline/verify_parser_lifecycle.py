#!/usr/bin/env python3
"""Verify legacy Quick parser fresh-document and explicit append lifecycles."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core import nlparser  # noqa: E402

rows = [
    json.loads(line)
    for line in (ROOT / "src/legacy/datasets/训练提示词/全部提示词.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
first, second = rows[0], rows[1]
first_result = nlparser.parse(first["prompt"])
first_count = len(first_result.doc.features)
second_fresh_result = nlparser.parse(second["prompt"])
second_fresh_count = len(second_fresh_result.doc.features)
second_append_result = nlparser.parse(second["prompt"], first_result.doc)
appended_count = len(second_append_result.doc.features)

result = {
    "schema_version": 1,
    "probe_scope": "two-row lifecycle diagnostic only; not mixed into independent-prompt Parser.parse counts",
    "rows": [{"id": row["id"], "category": row.get("category"), "group": row.get("group")} for row in (first, second)],
    "independent_mode": {
        "entry": "ai_modeler.core.nlparser.parse(text) -> Parser().parse(text, doc=None)",
        "first_ok": bool(first_result.ok),
        "first_feature_count": first_count,
        "second_fresh_ok": bool(second_fresh_result.ok),
        "second_fresh_feature_count": second_fresh_count,
    },
    "append_mode": {
        "entry": "ui/bridge.py model.quick_build with new_model=false -> NLP.parse(text, self.doc)",
        "second_ok": bool(second_append_result.ok),
        "document_feature_count_before_second_call": first_count,
        "document_feature_count_after_second_call": appended_count,
        "features_added_by_second_call": appended_count - first_count,
        "note": "The module helper creates a fresh Parser per call, but passing the existing Document intentionally preserves/appends prior document features.",
    },
    "source_refs": {
        "parser_class": "src/legacy/ai_modeler/core/parsing/nlparser.py:668-680",
        "module_helper": "src/legacy/ai_modeler/core/parsing/nlparser.py:1886-1887",
        "quick_build_lifecycle": "src/legacy/ai_modeler/ui/bridge.py:334-341",
        "f589_harness": "tools/audit_quick_legacy_samples.py:40-43",
    },
    "raw_prompt_text_persisted": False,
}
out = Path(__file__).resolve().parent / "parser_lifecycle_probe.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
