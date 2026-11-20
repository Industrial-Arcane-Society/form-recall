"""Focused Rust/OCCT to legacy OCP acceptance for CADPlan worm."""
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

BOUNDS_TOLERANCE_MM = 1e-6
VOLUME_TOLERANCE_MM3 = 1e-5


def main():
    base = json.loads((TAURI / "../../../core/cad-engine/examples/fixtures/native_geometry_plan.json").read_text(encoding="utf-8"))
    executable = TAURI / "target/debug/examples/native_plan_geometry_probe.exe"
    if not executable.is_file():
        raise FileNotFoundError("Build native_plan_geometry_probe with cargo build --offline --example native_plan_geometry_probe")
    manifest = Path(os.environ.get("AIMODELER_OCCT_MANIFEST", TAURI / "target/f4-occt/runtime-manifest.json"))
    if not manifest.is_file():
        raise FileNotFoundError("Set AIMODELER_OCCT_MANIFEST to a read-only staged OCCT runtime manifest.")
    output = TAURI / "target/f4-occt/worm-parity"
    output.mkdir(parents=True, exist_ok=True)
    standard = {
        "radius": 10.0, "height": 60.0, "thread_r": 12.5, "wire_r": 2.5,
        "height2": 50.0, "turns": 3.75,
        "center": [0.0, 0.0, -30.0], "center2": [0.0, 0.0, -25.0],
        "segments": 48, "seg_per_turn": 16, "wire_seg": 8,
    }

    def make_plan(parameters, unit="mm"):
        plan = copy.deepcopy(base)
        plan["name"] = "Deterministic native worm acceptance fixture"
        plan["source_prompt"] = "Explicit fixture values; not an image estimate."
        plan["units"]["length"] = unit
        plan["features"] = [{
            "id": "worm-fixture", "name": "worm-fixture", "kind": "worm", "operation": "add",
            "parameters": copy.deepcopy(parameters),
            "sources": {key: {"type": "user", "note": "deterministic acceptance fixture"}
                        for key in parameters},
        }]
        validation = validate_plan(plan)
        if not validation["valid"]:
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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", timeout=60,
        )
        return result, report_path

    positives = [
        ("legacy-tangent-mm", standard, "mm"),
        ("translated-coaxial-mm", {**standard, "center": [12.0, -7.0, 5.0],
                                    "center2": [12.0, -7.0, 10.0]}, "mm"),
        ("legacy-tangent-cm", {**standard, "radius": 1.0, "height": 6.0,
                               "thread_r": 1.25, "wire_r": 0.25, "height2": 5.0,
                               "center": [0.0, 0.0, -3.0],
                               "center2": [0.0, 0.0, -2.5]}, "cm"),
    ]
    comparisons = []
    for name, parameters, unit in positives:
        plan = make_plan(parameters, unit)
        result, report_path = run_native(name, plan)
        if result.returncode:
            raise RuntimeError(f"{name}: {result.stderr[:2000]}")
        native = json.loads(report_path.read_text(encoding="utf-8"))
        legacy_shape, legacy_report = occ_kernel.build_document(plan_to_document(plan))
        legacy = occ_kernel.inspect_shape(legacy_shape, check_self_intersection=False)
        if (native["state"] != "built" or native["valid"] is not True
                or native["closed_solid_count"] != 1 or legacy["brep_valid"] is not True
                or legacy["solid_count"] != 1 or legacy["closed"] is not True
                or legacy_report["state"] != "passed"):
            raise AssertionError((name, native, legacy, legacy_report.get("state")))
        bounds_error = max(abs(float(a) - float(b))
                           for a, b in zip(native["bounds_mm"], legacy["bbox_mm"]))
        volume_error = abs(native["volume_mm3"] - legacy["volume_mm3"])
        details = native["feature_results"][0]["support_details"]
        if bounds_error > BOUNDS_TOLERANCE_MM or volume_error > VOLUME_TOLERANCE_MM3:
            raise AssertionError((name, bounds_error, volume_error, native["bounds_mm"], legacy["bbox_mm"]))
        if (native["feature_results"][0]["geometry_support"] != "approximate"
                or details["occt_validation"]["solid_count"] != 1
                or details["occt_validation"]["closed_solid_count"] != 1
                or not any("facet-BREP" in warning for warning in details["warnings"])):
            raise AssertionError((name, details))
        comparisons.append({"case": name, "bounds_error_mm": bounds_error,
                            "volume_error_mm3": volume_error, "solid_count": 1,
                            "support": "approximate"})

    negatives = [
        ("non-z-axis", {**standard, "axis": "X"}, "全局 Z 轴"),
        ("off-axis-center2", {**standard, "center2": [0.01, 0.0, -25.0]}, "同一全局 Z 轴"),
        ("radial-gap", {**standard, "thread_r": 14.0}, "径向间隙"),
        ("buried-thread-crown", {**standard, "thread_r": 8.0, "wire_r": 1.0}, "完全覆盖"),
        ("no-axial-overlap", {**standard, "center2": [0.0, 0.0, 40.0]}, "Z 方向没有重叠"),
    ]
    rejected = []
    for name, parameters, expected in negatives:
        result, report_path = run_native(name, make_plan(parameters))
        if result.returncode == 0 or expected not in result.stderr or report_path.exists():
            raise AssertionError((name, result.returncode, result.stderr, report_path.exists()))
        rejected.append({"case": name, "stage": "preflight"})

    # Eight shaft facets leave only disconnected contacts at analytic tangency.
    # OCCT returns a valid compound of two closed solids; the builder must fail closed.
    result, report_path = run_native("tangent-compound-rejected",
                                     make_plan({**standard, "segments": 8}))
    expected = "必须是单一有效闭合实体；当前 valid=1, solids=2, closed=2"
    if result.returncode == 0 or expected not in result.stderr or report_path.exists():
        raise AssertionError(("tangent-compound-rejected", result.returncode, result.stderr, report_path.exists()))
    rejected.append({"case": "tangent-compound-rejected", "stage": "after_real_occt_fuse",
                     "occt_valid": True, "solid_count": 2, "closed_solid_count": 2})

    summary = {
        "scope": "native_rust_occt_worm_to_legacy_document_ocp_geometry_parity",
        "positive_cases": comparisons, "negative_cases": rejected,
        "bounds_tolerance_mm": BOUNDS_TOLERANCE_MM, "volume_tolerance_mm3": VOLUME_TOLERANCE_MM3,
        "single_body_contract": "successful builds are exactly one valid closed solid",
        "approximation_boundary": "48-sided shaft and sampled helix facet-BREP; CATIA exports only smooth shaft",
        "occt_manifest": str(manifest), "outputs": str(output),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
