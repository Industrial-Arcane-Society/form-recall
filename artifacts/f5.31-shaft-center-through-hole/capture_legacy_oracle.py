#!/usr/bin/env python3
"""Capture the old Python Quick oracle for a shaft with one center through-hole.

This script deliberately refuses to overwrite a non-empty output directory.
Run from the repository root with PYTHONPATH=src/legacy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROMPT = "轴 直径20 长度100 通孔6"
WELD_TOLERANCE_MM = 1e-5
BREP_LINEAR_DEFLECTION_MM = 0.08
BREP_ANGULAR_DEFLECTION_RAD = 0.35

sys.path.insert(0, str(ROOT / "src" / "legacy"))
sys.path.insert(0, str(ROOT))

from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from ai_modeler.core import nlparser  # noqa: E402
from ai_modeler.core.export import exporters  # noqa: E402
from ai_modeler.core.geometry import occ_kernel as K  # noqa: E402
from tools.compare_mesh_surfaces import read_binary_stl, topology_diagnostics  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    try:
        stored_path = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        stored_path = str(path.resolve())
    return {
        "path": stored_path,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent,
        help="empty directory for the oracle package (default: this script's directory)",
    )
    args = argument_parser.parse_args()
    output_dir = args.output_dir.resolve()
    existing_outputs = list(output_dir.iterdir()) if output_dir.exists() else []
    if existing_outputs:
        raise SystemExit(
            f"Refusing to overwrite non-empty oracle directory: {output_dir}. "
            "Choose a new output directory before capturing again."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = nlparser.Parser().parse(PROMPT)
    if not parsed.ok:
        raise RuntimeError(f"legacy Python Quick parse failed: {parsed.message}")
    doc = parsed.doc

    raw_path = output_dir / "legacy_document_build_raw.stl"
    raw_polygons = doc.build()
    exporters.export_stl(raw_polygons, str(raw_path), doc.name, binary=True)
    raw_stats = doc.stats()
    raw_topology = topology_diagnostics(read_binary_stl(raw_path), WELD_TOLERANCE_MM)

    brep_shape = doc.build_brep(tolerance=1e-5, max_mesh_faces=120000)
    brep_report = doc.brep_report
    if brep_shape.IsNull() or not brep_report or not brep_report.get("brep_valid"):
        raise RuntimeError(f"legacy Document.build_brep failed validity check: {brep_report}")

    brep_path = output_dir / "legacy_document_build_brep.brep"
    if not K.write_brep(brep_shape, str(brep_path)):
        raise RuntimeError("legacy BREP serializer returned false")

    K._triangulated(brep_shape, BREP_LINEAR_DEFLECTION_MM, BREP_ANGULAR_DEFLECTION_RAD)
    brep_stl_path = output_dir / "legacy_document_build_brep_mesh.stl"
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(brep_shape, str(brep_stl_path)):
        raise RuntimeError("legacy BREP tessellated STL serializer returned false")
    brep_topology = topology_diagnostics(read_binary_stl(brep_stl_path), WELD_TOLERANCE_MM)

    features = []
    for order, feature in enumerate(doc.features):
        features.append({
            "order": order,
            "kind": feature.kind,
            "operation": feature.op,
            "name": feature.name,
            "parameters": feature.params,
            "provenance": feature.provenance,
        })

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None

    rust_static_observation = {
        "status": "source_only_not_executed",
        "repository_revision": revision,
        "file": "src/apps/desktop/src-tauri/src/native_quick_parser.rs",
        "entry_symbol": "parse_legacy_shaft",
        "entry_line_observed": 3186,
        "issue": "legacy_shaft_modifier_not_migrated",
        "issue_line_observed": 3231,
        "observation": "The legacy shaft parser branch rejects hole/through-hole modifiers before producing a plan.",
    }

    files = {
        "capture_script": file_record(Path(__file__).resolve()),
        "legacy_raw_stl": file_record(raw_path),
        "legacy_brep": file_record(brep_path),
        "legacy_brep_tessellated_stl": file_record(brep_stl_path),
    }
    report = {
        "schema_version": "1.0",
        "captured_by": Path(__file__).name,
        "input": PROMPT,
        "legacy_pipeline": "Parser().parse -> Document.build raw STL; separately Document.build_brep -> BREP + tessellated STL",
        "runtime": {
            "python": platform.python_version(),
            "occt": brep_report.get("kernel"),
            "cwd": str(ROOT),
        },
        "parser": {
            "ok": parsed.ok,
            "recognized": parsed.recognized,
            "message": parsed.message,
            "warnings": parsed.warnings,
            "assumed": parsed.assumed,
            "questions": parsed.questions,
            "summary": parsed.summary(),
        },
        "ordered_features": features,
        "source_limits": {
            "shaft": "Old cylinder radius/height provenance marks both values explicit.",
            "hole": "Old generic hole feature provenance is empty; although the prompt states a through-hole, the old feature does not record an explicit/derived source marker.",
        },
        "document_build_raw": {
            "stats": raw_stats,
            "topology_weld_tolerance_mm": WELD_TOLERANCE_MM,
            "topology": raw_topology,
            "interpretation": "Raw mesh diagnostics are separate from the valid B-Rep solid report and are not used as an authoritative solid-volume claim.",
        },
        "document_build_brep": {
            "report": brep_report,
            "valid": bool(brep_report.get("brep_valid")),
            "solid_count": brep_report.get("solid_count"),
            "closed": brep_report.get("closed"),
            "hole_measurements": brep_report.get("hole_instances", []),
            "tessellation": {
                "linear_deflection_mm": BREP_LINEAR_DEFLECTION_MM,
                "angular_deflection_rad": BREP_ANGULAR_DEFLECTION_RAD,
                "relative": False,
            },
            "tessellated_stl_topology_weld_tolerance_mm": WELD_TOLERANCE_MM,
            "tessellated_stl_topology": brep_topology,
        },
        "rust_static_observation": rust_static_observation,
        "files": files,
    }
    report_path = output_dir / "legacy_oracle_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "files": files}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
