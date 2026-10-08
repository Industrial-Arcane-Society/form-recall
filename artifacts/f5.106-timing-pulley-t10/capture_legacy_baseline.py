"""Capture the old Quick T10 timing-pulley result and optional B-Rep oracle.

From repository root in PowerShell:
  $env:PYTHONPATH = 'src/legacy'
  python artifacts/f5.106-timing-pulley-t10/capture_legacy_baseline.py
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.geometry import occ_kernel  # noqa: E402
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402
from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402

PROMPT = "同步带轮 T10 节圆直径95.493 齿数30 宽度16 内孔10"
RAW_STL = HERE / "legacy_document_build_raw.stl"
BREP_FILE = HERE / "legacy_document_build_brep_failed_candidate.brep"
BREP_STL = HERE / "legacy_document_build_brep.stl"
REPORT = HERE / "legacy_t10_baseline.json"
WELD_TOLERANCE_MM = 1e-5


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


def file_record(path: Path, triangles=None):
    record = {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if triangles is not None:
        record["triangle_count"] = len(triangles)
        bounds = mesh_compare._mesh_summary(path, triangles, WELD_TOLERANCE_MM)["bounds_mm"]
        record["bbox_mm"] = bounds["minimum"] + bounds["maximum"]
        record["topology"] = mesh_compare.topology_diagnostics(triangles, WELD_TOLERANCE_MM)
    return record


def main() -> int:
    parsed = Parser().parse(PROMPT)
    if parsed.doc is None:
        raise RuntimeError("legacy Parser returned no Document")

    # Keep this raw mesh path independent from Document.build_brep().
    raw_mesh = parsed.doc.build()
    raw_stats = parsed.doc.stats()
    export_stl(raw_mesh, str(RAW_STL), "legacy_timing_pulley_T10_raw", binary=True)
    raw_triangles = mesh_compare.read_binary_stl(RAW_STL)

    brep_result = {"attempted": True, "path": "Document.build_brep()"}
    try:
        brep_shape = parsed.doc.build_brep()
        brep_report = parsed.doc.brep_report
        brep_result["report"] = json_value(brep_report)
        brep_topologically_valid_closed = bool(
            brep_report
            and brep_report.get("brep_valid") is True
            and brep_report.get("closed") is True
        )
        brep_result["valid_closed_single_solid"] = bool(
            brep_topologically_valid_closed
            and brep_report.get("solid_count") == 1
        )
        if brep_topologically_valid_closed:
            if not occ_kernel.write_brep(brep_shape, str(BREP_FILE)):
                raise RuntimeError("occ_kernel.write_brep returned false")
            brep_result["returned_shape_file"] = file_record(BREP_FILE)
        if brep_result["valid_closed_single_solid"]:
            brep_polys = occ_kernel.shape_to_polys(brep_shape, tol=0.08, max_faces=800000)
            export_stl(brep_polys, str(BREP_STL), "legacy_timing_pulley_T10_brep_oracle", binary=True)
            brep_triangles = mesh_compare.read_binary_stl(BREP_STL)
            brep_result["oracle_tessellation"] = {
                "linear_deflection_mm": 0.08,
                "angular_deflection_rad": 0.5,
                "triangle_count": len(brep_triangles),
                "stl": file_record(BREP_STL, brep_triangles),
            }
        else:
            brep_result["limitation"] = "The returned B-Rep compound is topologically valid/closed but contains multiple solids or failed geometry validation; saved only as a failed candidate and not used as an oracle."
    except Exception as exc:  # Preserve raw baseline even if the separate B-Rep route fails.
        brep_result["valid_closed_single_solid"] = False
        brep_result["error_type"] = type(exc).__name__
        brep_result["error"] = str(exc)

    report = {
        "evidence_kind": "f5_106_legacy_quick_timing_pulley_t10_baseline",
        "prompt": PROMPT,
        "oracle_scope": "old Python Parser.parse -> Document.build raw STL; separate optional Document.build_brep attempt",
        "legacy_sources": {
            "parser": "src/legacy/ai_modeler/core/parsing/nlparser.py::Parser.parse",
            "part_recipe": "src/legacy/ai_modeler/parts/library/partlib.py::_timing_pulley",
            "raw_builder": "src/legacy/ai_modeler/core/geometry/kernel.py::make_timing_pulley",
            "document_builder": "src/legacy/ai_modeler/core/model/docmodel.py::Document.build / Document.build_brep",
        },
        "parse_result": {
            "class": type(parsed).__name__,
            "ok": parsed.ok,
            "recognized": parsed.recognized,
            "message": parsed.message,
            "steps": json_value(parsed.steps),
            "warnings": json_value(parsed.warnings),
            "questions": json_value(parsed.questions),
            "assumed": json_value(parsed.assumed),
            "used_templates": json_value(parsed.used_templates),
        },
        "ordered_features": [json_value(vars(feature)) for feature in parsed.doc.features],
        "document": {
            "name": parsed.doc.name,
            "source_prompt": parsed.doc.source_prompt,
        },
        "raw_document_build": {
            "mesh_stats": json_value(raw_stats),
            "stl": file_record(RAW_STL, raw_triangles),
            "volume_semantics": "Document.stats volume is a raw mesh scalar; topology is reported separately and this value is not an authoritative B-Rep solid volume.",
        },
        "brep_attempt": brep_result,
        "rust_static_analysis": {
            "parser_status": "unsupported",
            "issue_code": "legacy_timing_pulley_type_not_supported_natively",
            "reason": "The parser recognizes T10 as a legacy type, then rejects it before parameter construction because Rust native timing pulley currently whitelists only MXL, XL, T2.5, T5, and 3M.",
            "parser_source": "src/apps/desktop/src-tauri/src/native_quick_parser.rs::parse_legacy_timing_pulley (supported_native_types / issue at lines 10372-10396)",
            "would_be_parameters_if_type_gate_were_removed": {
                "pitch_d": 95.493,
                "teeth": 30,
                "height": 16.0,
                "tooth_type": "T10",
                "bore_d": 10.0,
                "center": [0.0, 0.0, 0.0]
            },
            "native_preflight_direct": {
                "result": "reject",
                "reason": "T10 spec is pitch=10.0 mm, tooth_height=2.5 mm, tooth_tip=5.3 mm; generic preflight rejects when tooth_height*0.55 > 1.0, and T10 gives 1.375 mm.",
                "source": "src/apps/desktop/src-tauri/src/native_timing_pulley.rs::timing_type / preflight (T10 spec at line 30; overlap gate at line 78)",
                "consequence": "Even if the parser's type whitelist were expanded, the current native preflight rejects this T10 profile before size-dependent checks or construction."
            }
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "occt_kernel": (brep_result.get("report") or {}).get("kernel"),
        },
        "limitations": [
            "Raw Document.build mesh and optional Document.build_brep output are independent baselines and must not be conflated.",
            "This capture does not execute Rust, Cargo, CATIA, or desktop UI.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT.relative_to(REPO)),
        "raw_stl": file_record(RAW_STL, raw_triangles),
        "brep_valid_closed_single_solid": brep_result.get("valid_closed_single_solid"),
        "brep_candidate": brep_result.get("returned_shape_file"),
        "brep_error": brep_result.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
