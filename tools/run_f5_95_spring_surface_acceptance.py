#!/usr/bin/env python3
"""Rebuild the F5.95 legacy/Python and Rust headless spring geometry evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "legacy"))

from ai_modeler.core import nlparser  # noqa: E402
from ai_modeler.core.export import exporters  # noqa: E402
from ai_modeler.core.geometry import occ_kernel as K  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from tools.compare_mesh_surfaces import compare_three  # noqa: E402


CASES = (
    ("cylindrical", "螺旋弹簧 外径20 线径2 高度30 圈数8"),
    ("conical", "锥形弹簧 外径24 线径2 高度40 圈数6"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    try:
        stored_path = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        stored_path = str(path.resolve())
    return {"path": stored_path, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _legacy_oracle(prompt: str, case_dir: Path, output_root: Path,
                   linear_deflection_mm: float, angular_deflection_rad: float) -> dict[str, Any]:
    parsed = nlparser.parse(prompt)
    if not parsed.ok:
        raise RuntimeError(f"legacy Python Quick parse failed: {parsed.message}")
    doc = parsed.doc
    raw_polygons = doc.build()
    raw_path = case_dir / "legacy_document_build_raw.stl"
    exporters.export_stl(raw_polygons, str(raw_path), doc.name, binary=True)
    raw_stats = doc.stats()

    shape = doc.build_brep(tolerance=1e-5, max_mesh_faces=120000)
    brep_report = doc.brep_report
    if shape.IsNull() or not brep_report or not brep_report.get("brep_valid"):
        raise RuntimeError(f"legacy Document.build_brep did not return a valid BREP: {brep_report}")
    brep_path = case_dir / "legacy_document_build_brep.brep"
    if not K.write_brep(shape, str(brep_path)):
        raise RuntimeError("legacy BREP serializer returned false")
    K._triangulated(shape, linear_deflection_mm, angular_deflection_rad)
    brep_stl_path = case_dir / "legacy_document_build_brep_mesh.stl"
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(brep_stl_path)):
        raise RuntimeError("legacy BREP tessellated STL serializer returned false")

    return {
        "input": prompt,
        "parser": {"ok": parsed.ok, "summary": parsed.summary(), "warnings": parsed.warnings,
                   "assumed": parsed.assumed, "questions": parsed.questions},
        "features": [{"kind": feature.kind, "operation": feature.op, "name": feature.name,
                      "parameters": feature.params} for feature in doc.features],
        "raw_document_build_stats": raw_stats,
        "brep_report": brep_report,
        "brep_tessellation": {"linear_deflection_mm": linear_deflection_mm,
                              "angular_deflection_rad": angular_deflection_rad, "relative": False},
        "files": {
            "legacy_raw_stl": _file_record(raw_path, output_root),
            "legacy_brep": _file_record(brep_path, output_root),
            "legacy_brep_tessellated_stl": _file_record(brep_stl_path, output_root),
        },
        "raw_stl_path": raw_path,
        "brep_stl_path": brep_stl_path,
    }


def _rust_headless(prompt: str, case_name: str, case_dir: Path, output_root: Path,
                   headless_exe: Path, resource_root: Path, occt_manifest: Path) -> dict[str, Any]:
    scratch = case_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["AIMODELER_OCCT_MANIFEST"] = str(occt_manifest.resolve())
    command = [str(headless_exe.resolve()), str(resource_root.resolve()), str(scratch.resolve()), prompt]
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    stdout_path = case_dir / "headless_stdout.json"
    stderr_path = case_dir / "headless_stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Rust headless smoke failed for {case_name} ({completed.returncode}): {completed.stderr}")
    envelope = json.loads(completed.stdout)
    document = envelope.get("document", {})
    if envelope.get("status") != "ok" or document.get("state") != "complete":
        raise RuntimeError(f"Rust headless smoke returned no completed model: {envelope}")
    generated_stl = Path(document["output_paths"]["stl"])
    if not generated_stl.is_file():
        raise RuntimeError(f"Rust reported a missing STL: {generated_stl}")
    rust_stl = case_dir / "rust_headless_quick.stl"
    shutil.copy2(generated_stl, rust_stl)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "headless_state": document.get("state"),
        "geometry_report": document.get("geometry_report"),
        "stats": document.get("stats"),
        "cadplan": document.get("cadplan"),
        "files": {
            "stdout": _file_record(stdout_path, output_root),
            "stderr": _file_record(stderr_path, output_root),
            "rust_headless_quick_stl": _file_record(rust_stl, output_root),
        },
        "rust_stl_path": rust_stl,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless-exe", type=Path, required=True, help="built ai-modeler-headless-smoke executable")
    parser.add_argument("--resource-root", type=Path, default=ROOT / "src" / "legacy")
    parser.add_argument("--occt-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts" / "f5.95")
    parser.add_argument("--surface-tolerance-mm", type=float, default=0.08)
    parser.add_argument("--weld-tolerance-mm", type=float, default=1e-5)
    args = parser.parse_args()
    for label, path in (("headless exe", args.headless_exe), ("resource root", args.resource_root),
                        ("OCCT manifest", args.occt_manifest)):
        if not path.exists():
            parser.error(f"{label} does not exist: {path}")
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    oracle_results: list[dict[str, Any]] = []
    for case_name, prompt_text in CASES:
        oracle_dir = output_root / "oracle" / case_name
        rust_dir = output_root / "rust" / case_name
        oracle_dir.mkdir(parents=True, exist_ok=True)
        rust_dir.mkdir(parents=True, exist_ok=True)
        legacy = _legacy_oracle(prompt_text, oracle_dir, output_root, 0.08, 0.35)
        oracle_results.append({"name": case_name, **{key: value for key, value in legacy.items() if not key.endswith("_path")}})
        rust = _rust_headless(prompt_text, case_name, rust_dir, output_root,
                              args.headless_exe, args.resource_root, args.occt_manifest)
        comparison = compare_three(
            oracle_dir / "legacy_document_build_raw.stl",
            oracle_dir / "legacy_document_build_brep_mesh.stl",
            rust_dir / "rust_headless_quick.stl",
            args.surface_tolerance_mm,
            args.weld_tolerance_mm,
            (64, 128, 256),
        )
        comparison["passed"] = all(row["surface_equivalence_passed"] for row in comparison["comparisons"].values())
        comparison_path = rust_dir / "surface_comparison.json"
        _write_json(comparison_path, comparison)
        results.append({
            "name": case_name,
            "input": prompt_text,
            "legacy": {key: value for key, value in legacy.items() if not key.endswith("_path")},
            "rust": {key: value for key, value in rust.items() if not key.endswith("_path")},
            "surface_comparison": _file_record(comparison_path, output_root),
            "surface_equivalence_passed": comparison["passed"],
        })

    report = {
        "schema_version": 1,
        "acceptance": "F5.95 legacy raw mesh, old Python BREP oracle and Rust Quick surface comparison",
        "coordinate_policy": "world coordinates in mm, no independent bbox normalization or registration",
        "surface_tolerance_mm": args.surface_tolerance_mm,
        "tolerance_basis": "0.08 mm is the linear deflection used by both legacy BREP and Rust Quick tessellation",
        "runtime": {"python": sys.version, "ocp": getattr(__import__("OCP"), "__version__", None),
                    "headless_exe": str(args.headless_exe.resolve()), "headless_exe_sha256": _sha256(args.headless_exe),
                    "resource_root": str(args.resource_root.resolve()), "occt_manifest": str(args.occt_manifest.resolve()),
                    "occt_manifest_sha256": _sha256(args.occt_manifest)},
        "cases": results,
        "surface_equivalence_passed": all(row["surface_equivalence_passed"] for row in results),
    }
    report_path = output_root / "acceptance_report.json"
    _write_json(output_root / "oracle" / "legacy_oracle_report.json", {
        "schema_version": 1,
        "source": "legacy Python Quick Parser.parse -> Document.build and Document.build_brep",
        "python": sys.version,
        "ocp": getattr(__import__("OCP"), "__version__", None),
        "cases": oracle_results,
    })
    _write_json(report_path, report)
    print(json.dumps({"report": str(report_path), "surface_equivalence_passed": report["surface_equivalence_passed"],
                      "cases": [{"name": item["name"], "passed": item["surface_equivalence_passed"]} for item in results]},
                     ensure_ascii=False, indent=2))
    return 0 if report["surface_equivalence_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
