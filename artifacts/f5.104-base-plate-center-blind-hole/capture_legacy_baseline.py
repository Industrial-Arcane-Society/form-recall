"""Capture the old Python Quick baseline for one base-plate center blind hole.

From repository root, PowerShell:
  $env:PYTHONPATH = 'src/legacy'
  $env:PYTHONDONTWRITEBYTECODE = '1'
  python artifacts/f5.104-base-plate-center-blind-hole/capture_legacy_baseline.py

This script calls the historical Parser.parse -> Document.build path and also
attempts the separate Document.build_brep path for diagnostic comparison. It
never invokes Rust, Cargo, CATIA, or a desktop UI.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROMPT = "基板 长度100 宽度80 厚度10 中心盲孔直径12深5"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = REPO / "src" / "legacy"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from ai_modeler.core.export.exporters import export_stl  # noqa: E402
from ai_modeler.core.geometry import kernel as K  # noqa: E402
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


def raw_topology(polygons, weld_tolerance_mm: float = 1e-5):
    triangles = K.polygons_to_triangles(polygons)
    vertices = []
    edge_uses = defaultdict(list)
    keys = []

    def vertex_key(point):
        return tuple(round(float(axis) / weld_tolerance_mm) for axis in point)

    for tri_index, tri in enumerate(triangles):
        tri_keys = [vertex_key(point) for point in tri[:3]]
        keys.append(tri_keys)
        for point, key in zip(tri[:3], tri_keys):
            vertices.append(tuple(float(axis) for axis in point))
        for i in range(3):
            a, b = tri_keys[i], tri_keys[(i + 1) % 3]
            undirected = tuple(sorted((a, b)))
            direction = 1 if a <= b else -1
            edge_uses[undirected].append((tri_index, direction))

    boundary = [edge for edge, uses in edge_uses.items() if len(uses) == 1]
    nonmanifold = [edge for edge, uses in edge_uses.items() if len(uses) > 2]
    winding_conflicts = [edge for edge, uses in edge_uses.items()
                         if len(uses) == 2 and uses[0][1] == uses[1][1]]
    neighbors = [[] for _ in triangles]
    for uses in edge_uses.values():
        if len(uses) >= 2:
            ids = [use[0] for use in uses]
            for tri_id in ids:
                neighbors[tri_id].extend(other for other in ids if other != tri_id)
    seen = set()
    components = 0
    for start in range(len(triangles)):
        if start in seen:
            continue
        components += 1
        todo = [start]
        seen.add(start)
        while todo:
            current = todo.pop()
            for other in neighbors[current]:
                if other not in seen:
                    seen.add(other)
                    todo.append(other)
    return {
        "weld_tolerance_mm": weld_tolerance_mm,
        "triangle_count": len(triangles),
        "unique_welded_vertices": len({key for tri in keys for key in tri}),
        "undirected_edge_count": len(edge_uses),
        "boundary_edge_count": len(boundary),
        "nonmanifold_edge_count": len(nonmanifold),
        "winding_conflict_edge_count": len(winding_conflicts),
        "connected_triangle_components": components,
        "closed_by_welded_triangle_edge_incidence": bool(triangles) and not boundary and not nonmanifold,
        "method_limit": "Triangle vertices are quantized to the stated weld tolerance; this is a raw mesh diagnostic, not a B-Rep validity result.",
    }


def main() -> None:
    result = Parser().parse(PROMPT)
    document = result.doc
    polygons = document.build()
    stats = document.stats()
    triangles = K.polygons_to_triangles(polygons)

    stl_path = HERE / "legacy_document_build_raw.stl"
    export_stl(polygons, str(stl_path), document.name, binary=True)

    brep_result = {"attempted": True, "available": False}
    brep_path = HERE / "legacy_document_build_brep.brep"
    try:
        shape = document.build_brep()
        report = document.brep_report
        brep_result = {
            "attempted": True,
            "available": shape is not None,
            "report": json_value(report),
            "path": None,
            "sha256": None,
            "bytes": None,
        }
        if shape is not None:
            from ai_modeler.core.geometry import occ_kernel
            written = bool(occ_kernel.write_brep(shape, str(brep_path)))
            brep_result["written"] = written
            if written:
                brep_result.update({
                    "path": brep_path.name,
                    "sha256": sha256(brep_path),
                    "bytes": brep_path.stat().st_size,
                })
    except Exception as exc:  # Record the old backend's real failure without hiding it.
        brep_result = {
            "attempted": True,
            "available": False,
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "report": json_value(document.brep_report),
        }

    features = [json_value(vars(feature)) for feature in document.features]
    report = {
        "evidence_kind": "old_python_quick_base_plate_center_blind_hole_baseline",
        "oracle_scope": "legacy Parser.parse -> Document.build -> binary STL; separate Document.build_brep attempt",
        "prompt": PROMPT,
        "prompt_sha256_utf8": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "parser_result": {
            "class": type(result).__name__,
            "ok": result.ok,
            "recognized": result.recognized,
            "message": result.message,
            "steps": json_value(result.steps),
            "warnings": json_value(result.warnings),
            "questions": json_value(result.questions),
            "assumed": json_value(result.assumed),
        },
        "document": {
            "name": document.name,
            "source_prompt": document.source_prompt,
            "features": features,
            "feature_provenance_summary": [
                {"index": index, "kind": feature.get("kind"),
                 "provenance": json_value(feature.get("provenance"))}
                for index, feature in enumerate(features)
            ],
        },
        "raw_mesh": {
            "path": stl_path.name,
            "format": "binary STL exported from legacy Document.build polygons",
            "sha256": sha256(stl_path),
            "bytes": stl_path.stat().st_size,
            "stats": json_value(stats),
            "topology": raw_topology(polygons),
            "volume_semantics": "Document.stats().volume is a legacy polygon-mesh scalar only; it is not a B-Rep volume.",
        },
        "brep": brep_result,
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "limitations": [
            "Parser and raw mesh values above are runtime-observed from the old Python Quick entrypoint.",
            "Raw triangle edge closure is assessed by vertex welding at 1e-5 mm and is separate from OCCT B-Rep validity.",
            "Legacy mesh volume must not be presented as B-Rep volume.",
            "No Rust Quick, Cargo, CATIA, or desktop UI was invoked or validated.",
        ],
    }
    report_path = HERE / "legacy_raw_baseline.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path.relative_to(REPO)),
        "report_sha256": sha256(report_path),
        "stl": str(stl_path.relative_to(REPO)),
        "stl_sha256": report["raw_mesh"]["sha256"],
        "raw_stats": report["raw_mesh"]["stats"],
        "raw_topology": report["raw_mesh"]["topology"],
        "brep": report["brep"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
