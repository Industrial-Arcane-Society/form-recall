"""Compare Rust impeller builds with the retained Python Document/OCP oracle."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TAURI = ROOT / "src/apps/desktop/src-tauri"
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core.geometry import occ_kernel
from ai_modeler.sdk.cadplan import plan_to_document, validate_plan

BOUNDS_TOLERANCE_MM = 0.1
VOLUME_RELATIVE_TOLERANCE = 0.03


def main():
    base = json.loads(
        (TAURI / "examples/fixtures/native_geometry_plan.json").read_text(encoding="utf-8")
    )
    executable = TAURI / "target/debug/examples/native_plan_geometry_probe.exe"
    if not executable.is_file():
        raise FileNotFoundError(
            "Build native_plan_geometry_probe with cargo build --offline --example native_plan_geometry_probe"
        )
    manifest = Path(
        os.environ.get(
            "AIMODELER_OCCT_MANIFEST", TAURI / "target/f4-occt/runtime-manifest.json"
        )
    )
    if not manifest.is_file():
        raise FileNotFoundError("Set AIMODELER_OCCT_MANIFEST to a staged OCCT runtime manifest.")
    output = TAURI / "target/f4-occt/impeller-parity"
    output.mkdir(parents=True, exist_ok=True)

    standard = {
        "hub_d": 20.0,
        "tip_d": 60.0,
        "height": 15.0,
        "blades": 6,
        "blade_t": 2.0,
        "twist_deg": 25.0,
        "segments": 12,
    }

    def make_plan(parameters, *, unit="mm", angle="deg", allow_invalid=False):
        plan = copy.deepcopy(base)
        plan["name"] = "Native impeller OCP parity fixture"
        plan["source_prompt"] = "Explicit deterministic legacy geometry fixture."
        plan["units"] = {"length": unit, "angle": angle}
        plan["features"] = [
            {
                "id": "impeller-fixture",
                "name": "impeller-fixture",
                "kind": "impeller",
                "operation": "add",
                "parameters": copy.deepcopy(parameters),
                "sources": {
                    key: {"type": "user", "note": "deterministic parity fixture"}
                    for key in parameters
                },
            }
        ]
        validation = validate_plan(plan)
        if not validation["valid"] and not allow_invalid:
            raise AssertionError(("legacy CADPlan fixture invalid", validation))
        return plan

    def run_native(name, plan):
        plan_path = output / f"{name}.plan.json"
        report_path = output / f"{name}.report.json"
        if report_path.exists():
            report_path.unlink()
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = subprocess.run(
            [str(executable), str(plan_path), str(manifest), str(report_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        return result, report_path

    positives = [
        ("explicit-default-mm", standard, "mm", "deg"),
        (
            "translated-explicit-mm",
            {**standard, "center": [12.0, -7.0, 5.0]},
            "mm",
            "deg",
        ),
        (
            "explicit-cm",
            {
                **standard,
                "hub_d": 2.0,
                "tip_d": 6.0,
                "height": 1.5,
                "blade_t": 0.2,
                "center": [1.2, -0.7, 0.5],
            },
            "cm",
            "deg",
        ),
    ]
    comparisons = []
    for name, parameters, unit, angle in positives:
        plan = make_plan(parameters, unit=unit, angle=angle)
        result, report_path = run_native(name, plan)
        if result.returncode:
            raise RuntimeError(f"{name}: {result.stderr[:3000]}")
        native = json.loads(report_path.read_text(encoding="utf-8"))
        legacy_shape, legacy_report = occ_kernel.build_document(plan_to_document(plan))
        legacy = occ_kernel.inspect_shape(legacy_shape, check_self_intersection=False)
        if (
            native["state"] != "built"
            or native["valid"] is not True
            or native["closed_solid_count"] != 1
            or legacy["brep_valid"] is not True
            or legacy["solid_count"] != 1
            or legacy["closed"] is not True
            or legacy_report["state"] != "passed"
        ):
            raise AssertionError((name, native, legacy, legacy_report.get("state")))
        bounds_error = max(
            abs(float(a) - float(b))
            for a, b in zip(native["bounds_mm"], legacy["bbox_mm"])
        )
        volume_error = abs(native["volume_mm3"] - legacy["volume_mm3"])
        volume_relative_error = volume_error / legacy["volume_mm3"]
        details = native["feature_results"][0]["support_details"]
        if bounds_error > BOUNDS_TOLERANCE_MM or volume_relative_error > VOLUME_RELATIVE_TOLERANCE:
            raise AssertionError(
                (name, bounds_error, volume_relative_error, native["bounds_mm"], legacy["bbox_mm"])
            )
        if (
            native["feature_results"][0]["geometry_support"] != "approximate"
            or details["occt_validation"]["solid_count"] != 1
            or details["occt_validation"]["closed_solid_count"] != 1
            or not any("facet-BREP" in warning for warning in details["warnings"])
        ):
            raise AssertionError((name, details))
        comparisons.append(
            {
                "case": name,
                "bounds_error_mm": bounds_error,
                "volume_error_mm3": volume_error,
                "volume_relative_error": volume_relative_error,
                "solid_count": 1,
                "support": "approximate",
            }
        )

    negatives = [
        ("missing-twist", {key: value for key, value in standard.items() if key != "twist_deg"}, "twist_deg", "CADPlan_confirmation"),
        ("tip-size-that-legacy-would-expand", {**standard, "tip_d": 20.5}, "tip_d>=1.05*hub_d", "native_preflight"),
        ("zero-blade-thickness", {**standard, "blade_t": 0.0}, "blade_t", "CADPlan_validation"),
        ("too-many-blades", {**standard, "blades": 129}, "3..=128", "native_preflight"),
        ("too-many-sections", {**standard, "segments": 129}, "4..=128", "native_preflight"),
    ]
    rejected = []
    for name, parameters, expected, stage in negatives:
        plan = make_plan(
            parameters,
            allow_invalid=name in {"missing-twist", "zero-blade-thickness"},
        )
        result, report_path = run_native(name, plan)
        if result.returncode == 0 or expected not in result.stderr or report_path.exists():
            raise AssertionError((name, result.returncode, result.stderr, report_path.exists()))
        rejected.append({"case": name, "stage": stage})

    summary = {
        "scope": "native_rust_occt_impeller_to_legacy_document_ocp_geometry_parity",
        "positive_cases": comparisons,
        "negative_cases": rejected,
        "bounds_tolerance_mm": BOUNDS_TOLERANCE_MM,
        "volume_relative_tolerance": VOLUME_RELATIVE_TOLERANCE,
        "single_body_contract": "successful builds are exactly one valid closed OCCT solid",
        "approximation_boundary": "legacy OCP hub cone and analytic backplate with legacy twisted section placement; blade sides triangulated and sewn as facet-BREP; CATIA still maps hub/backplate only",
        "occt_manifest": str(manifest),
        "outputs": str(output),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
