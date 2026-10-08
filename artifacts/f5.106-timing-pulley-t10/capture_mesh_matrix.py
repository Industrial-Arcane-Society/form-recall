"""Capture additional old Quick T10 raw-mesh cases with Parser -> Document.build.

Run from repository root:
  $env:PYTHONPATH = 'src/legacy'
  python artifacts/f5.106-timing-pulley-t10/capture_mesh_matrix.py

The third case adjusts only the parsed feature center before Document.build so
that the old geometry constructor's non-origin translation is independently
captured; the legacy parser itself defaults center to the origin.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
for path in (LEGACY, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.parsing.nlparser import Parser  # noqa: E402
from tools import compare_mesh_surfaces as mesh_compare  # noqa: E402

CASES = [
    {
        "id": "d80-z24-w12-bore8-origin",
        "prompt": "同步带轮 T10 节圆直径80 齿数24 宽度12 内孔8",
        "center_override": [0.0, 0.0, 0.0],
    },
    {
        "id": "d127_324-z40-w25-bore18-origin",
        "prompt": "同步带轮 T10 节圆直径127.324 齿数40 宽度25 内孔18",
        "center_override": [0.0, 0.0, 0.0],
    },
    {
        "id": "baseline-nonzero-center",
        "prompt": "同步带轮 T10 节圆直径95.493 齿数30 宽度16 内孔10",
        "center_override": [12.0, -4.0, 7.0],
    },
]
WELD_TOLERANCE_MM = 1e-5


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    rows = []
    for case in CASES:
        parsed = Parser().parse(case["prompt"])
        if parsed.doc is None or len(parsed.doc.features) != 1:
            raise RuntimeError(f"old Parser did not return one feature: {case['id']}: {parsed.message}")
        feature = parsed.doc.features[0]
        if feature.kind != "timing_pulley" or feature.params.get("tooth_type") != "T10":
            raise RuntimeError(f"old Parser returned unexpected feature: {case['id']}: {feature.to_dict()}")
        parsed_center = list(feature.params.get("center", [0.0, 0.0, 0.0]))
        feature.params["center"] = case["center_override"]
        mesh = parsed.doc.build()
        output = HERE / f"legacy_raw_{case['id']}.stl"
        export_stl(mesh, str(output), f"legacy_t10_{case['id']}", binary=True)
        triangles = mesh_compare.read_binary_stl(output)
        summary = mesh_compare._mesh_summary(output, triangles, WELD_TOLERANCE_MM)
        rows.append(
            {
                "case": case["id"],
                "prompt": case["prompt"],
                "legacy_parser": {
                    "ok": parsed.ok,
                    "recognized": parsed.recognized,
                    "warnings": parsed.warnings,
                    "questions": parsed.questions,
                    "ordered_feature": feature.to_dict(),
                    "center_before_geometry_override": parsed_center,
                    "center_used_by_document_build": case["center_override"],
                    "center_override_reason": (
                        "legacy parser fixes center at global origin; this case isolates the old geometry constructor translation"
                        if parsed_center != case["center_override"]
                        else None
                    ),
                },
                "raw_document_build": {
                    "stl": output.name,
                    "bytes": output.stat().st_size,
                    "sha256": sha256(output),
                    "triangle_count": len(triangles),
                    "bbox_mm": summary["bounds_mm"]["minimum"] + summary["bounds_mm"]["maximum"],
                    "topology": mesh_compare.topology_diagnostics(triangles, WELD_TOLERANCE_MM),
                    "mesh_volume_semantics": "legacy raw triangle mesh diagnostic only, not a B-Rep volume",
                },
            }
        )
    result = {
        "evidence_kind": "f5_106_t10_legacy_raw_mesh_parameter_matrix",
        "oracle_scope": "Parser.parse -> mutate only center for the offset case -> Document.build -> raw STL",
        "legacy_source": "src/legacy/ai_modeler/core/geometry/kernel.py::make_timing_pulley",
        "weld_tolerance_mm": WELD_TOLERANCE_MM,
        "cases": rows,
        "limitations": [
            "The origin-centered cases use old Parser output unchanged. The nonzero-center case changes only Feature.params.center after parsing because this legacy parser always defaults the center to the global origin and exposes no position syntax.",
            "These are raw mesh geometry oracles; no B-Rep or CATIA validation is implied.",
        ],
    }
    report = HERE / "legacy_t10_mesh_matrix.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
