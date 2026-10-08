"""Capture old Quick Document.build_brep() and a fixed OCCT tessellation.

Run from repository root:
  $env:PYTHONPATH='src/legacy'; python artifacts/f5-spline-shaft-groove/capture_legacy_brep_oracle.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(REPO / "src" / "legacy") not in sys.path:
    sys.path.insert(0, str(REPO / "src" / "legacy"))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.geometry import occ_kernel  # noqa: E402
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402

PROMPT = "花键轴 小径20 大径25 长度40 齿数8 键槽宽5"
STL = HERE / "legacy_document_build_brep.stl"
REPORT = HERE / "legacy_brep_oracle.json"


def main() -> None:
    parsed = Parser().parse(PROMPT)
    shape = parsed.doc.build_brep(tolerance=1e-5)
    report = parsed.doc.brep_report
    if not report or report.get("brep_valid") is not True or report.get("closed") is not True:
        raise RuntimeError(f"legacy B-Rep was not valid and closed: {report}")
    # Use the legacy OCCT mesh conversion with a fixed 0.08 mm linear deflection.
    polygons = occ_kernel.shape_to_polys(shape, tol=0.08, max_faces=800000)
    export_stl(polygons, str(STL), "legacy_spline_shaft_brep_oracle", binary=True)
    summary = {
        "evidence_kind": "legacy_python_quick_brep_oracle",
        "prompt": PROMPT,
        "oracle_scope": "Parser.parse -> Document.build_brep -> legacy shape_to_polys OCCT triangulation at deflection 0.08 mm / default angle 0.5 rad -> binary STL",
        "report": report,
        "tessellation": {"linear_deflection_mm": 0.08, "angular_deflection_rad": 0.5, "triangle_count": len(polygons)},
        "stl": {"path": STL.name, "bytes": STL.stat().st_size, "sha256": hashlib.sha256(STL.read_bytes()).hexdigest()},
        "limitations": [
            "This is a local legacy OCCT B-Rep result, not CATIA/UI validation.",
            "Tessellated surface comparisons are finite samples and not a continuous Hausdorff bound.",
            "The separate old Document.build raw STL remains open and its mesh volume is not used as a solid-volume oracle.",
        ],
    }
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
