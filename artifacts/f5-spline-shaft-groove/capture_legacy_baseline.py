"""Capture one old Python Quick spline-shaft-with-groove raw mesh baseline.

Run from the repository root with the legacy source on PYTHONPATH:
  $env:PYTHONPATH='src/legacy'; python artifacts/f5-spline-shaft-groove/capture_legacy_baseline.py

This records the old Parser -> Document.build() mesh only. It does not call
Document.build_brep(), Rust Quick, Cargo, or CATIA.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path


PROMPT = "花键轴 小径20 大径25 长度40 齿数8 键槽宽5"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def main() -> None:
    result = Parser().parse(PROMPT)
    document = result.doc
    # Build through the historical mesh path. This deliberately does not use
    # the separate B-Rep builder and does not imply Rust parity.
    mesh = document.build()
    stats = document.stats()

    stl_path = HERE / "legacy_document_build_raw.stl"
    export_stl(mesh, str(stl_path), document.name, binary=True)
    report = {
        "evidence_kind": "old_python_quick_raw_mesh_baseline",
        "oracle_scope": "Parser.parse -> Document.build -> binary STL",
        "explicitly_not_used": ["Document.build_brep", "Rust Quick", "Cargo", "CATIA"],
        "prompt": PROMPT,
        "prompt_sha256_utf8": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "parser_result": {
            "class": type(result).__name__,
            "ok": result.ok,
            "recognized": result.recognized,
            "message": result.message,
            "steps": result.steps,
            "warnings": result.warnings,
            "questions": result.questions,
            "assumed": result.assumed,
        },
        "document": {
            "name": document.name,
            "source_prompt": document.source_prompt,
            "features": [json_value(vars(feature)) for feature in document.features],
        },
        "raw_mesh": {
            "path": stl_path.name,
            "format": "binary STL from legacy core.export.exporters.export_stl",
            "sha256": sha256(stl_path),
            "bytes": stl_path.stat().st_size,
            "stats": json_value(stats),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "limitations": [
            "This is the legacy Quick tessellated mesh output, not a B-Rep/CATIA result.",
            "The old parser applies its generic pocket defaults to the keyway phrase: length 20, width 5, depth 5 mm; it does not infer an engineering keyway location or fit.",
            "This baseline does not establish Rust parity, manufacturing suitability, or continuous Hausdorff distance.",
        ],
    }
    report_path = HERE / "legacy_raw_baseline.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path.relative_to(REPO)),
        "stl": str(stl_path.relative_to(REPO)),
        "stl_sha256": report["raw_mesh"]["sha256"],
        "stats": report["raw_mesh"]["stats"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
