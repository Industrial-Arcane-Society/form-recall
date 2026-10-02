"""Development-only CLI comparison with the retained Python OCCT baseline.

This does not drive an Agent, inject image estimates, or provide runtime fallback.
"""
import copy
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TAURI = ROOT / "src/apps/desktop/src-tauri"
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core.geometry import occ_kernel
from ai_modeler.sdk.cadplan import plan_to_document


def main():
    base = json.loads((TAURI / "examples/fixtures/native_geometry_plan.json").read_text(encoding="utf-8"))
    output = TAURI / "target/f4-occt/plan-parity"
    output.mkdir(parents=True, exist_ok=True)
    executable = TAURI / "target/debug/examples/native_plan_geometry_probe.exe"
    manifest = TAURI / "target/f4-occt/runtime-manifest.json"
    cases = [("recess-nine-holes", base)]

    def add(name, kind, parameters, units=None):
        plan = copy.deepcopy(base)
        plan["features"] = [{"id": "f1", "kind": kind, "operation": "add", "parameters": parameters,
            "sources": {key: {"type": "user"} for key, value in parameters.items()
                        if isinstance(value, (int, float, list)) and not isinstance(value, bool)}}]
        if units:
            plan["units"] = units
        cases.append((name, plan))

    for axis in "XYZ":
        for mode in ("center", "base_true", "base_false"):
            parameters = dict(length=10, width=20, height=30, center=[10, 20, 30], axis=axis)
            if mode != "center":
                parameters["base"] = mode == "base_true"
            add(f"box-{axis}-{mode}", "box", parameters)
        for bottom in (True, False):
            add(f"cylinder-{axis}-{bottom}", "cylinder",
                dict(radius=5, height=10, axis=axis, center=[10, 20, 30], base=bottom))
            add(f"cone-{axis}-{bottom}", "cylinder",
                dict(radius=5, radius2=2, height=10, axis=axis, center=[10, 20, 30], base=bottom))
        add(f"prism-{axis}", "prism", dict(profile=[[0, 0], [12, 0], [10, 7], [0, 9]],
            height=13, axis=axis, center=[10, 20, 30]))
        for angle in (360, 180, -90):
            add(f"revolve-{axis}-{angle}", "revolve", dict(profile=[[2, -3], [8, -3], [6, 7], [2, 7]],
                angle=angle, axis=axis, center=[10, 20, 30]))
    add("prism-cm", "prism", dict(profile=[[0, 0], [1, 0], [1, 2], [0, 2]],
        height=3, center=[1, 2, 3]), dict(length="cm", angle="deg"))
    add("revolve-cm-rad", "revolve", dict(profile=[[0, 0], [1, 0], [1, 2], [0, 2]],
        angle=1.5707963267948966, center=[1, 2, 3]), dict(length="cm", angle="rad"))
    add("sphere-cm", "sphere", dict(radius=2, center=[1, 2, 3]), dict(length="cm", angle="deg"))
    for axis in "XYZ":
        for side in ("positive", "negative"):
            plan = copy.deepcopy(base)
            parameters = dict(length=100, width=100, height=100, center=[0, 0, 0])
            plan["features"] = [{"id": "stock", "kind": "box", "operation": "add", "parameters": parameters,
                "sources": {key: {"type": "user"} for key in parameters}}]
            parameters = dict(length=20, width=30, depth=5, x=10, y=20, axis=axis, side=side)
            plan["features"].append({"id": "pocket", "kind": "pocket", "operation": "cut", "parameters": parameters,
                "sources": {key: {"type": "user"} for key, value in parameters.items() if isinstance(value, (int, float))}})
            cases.append((f"pocket-{axis}-{side}", plan))
    plan = copy.deepcopy(base)
    plan["units"]["angle"] = "rad"
    plan["features"] = plan["features"][:1]
    parameters = dict(diameter=6, pos="pcd", pcd=20, count=4, phase=0.5, axis="Z", through=True)
    plan["features"].append({"id": "pcd", "kind": "hole", "operation": "cut", "parameters": parameters,
        "sources": {key: {"type": "user"} for key, value in parameters.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)}})
    cases.append(("pcd-radians", plan))

    records = []
    for name, plan in cases:
        source, report_path = output / f"{name}.json", output / f"{name}.report.json"
        source.write_text(json.dumps(plan), encoding="utf-8")
        result = subprocess.run([str(executable), str(source), str(manifest), str(report_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode:
            raise RuntimeError(f"{name}: {result.stderr[:1500]}")
        native = json.loads(report_path.read_text(encoding="utf-8"))
        shape, _ = occ_kernel.build_document(plan_to_document(plan))
        bounds_error = max(abs(a - b) for a, b in zip(occ_kernel._bbox(shape), native["bounds_mm"]))
        volume_error = abs(occ_kernel.volume(shape) - native["volume_mm3"])
        if bounds_error > 1e-6 or volume_error > 1e-5:
            raise AssertionError((name, bounds_error, volume_error))
        records.append(dict(case=name, passed=True, bounds_max_error_mm=bounds_error,
                            volume_error_mm3=volume_error))
    python_version = "unknown"
    for package in ("cadquery-ocp", "cadquery-ocp-novtk"):
        try:
            python_version = importlib.metadata.version(package)
            break
        except importlib.metadata.PackageNotFoundError:
            continue
    negatives = []
    rounded = copy.deepcopy(base)
    rounded["features"][0]["parameters"]["corner"] = 2
    rounded["features"][0]["sources"]["corner"] = {"type": "user"}
    negatives.append(("rounded-unimplemented", rounded, "corner"))
    missing = copy.deepcopy(base)
    missing["features"][0]["sources"] = {}
    negatives.append(("missing-source", missing, "校验"))
    outside = copy.deepcopy(base)
    outside["features"][2]["parameters"]["point"][0] = 300
    negatives.append(("outside-hole", outside, "没有实际减少材料"))
    first_cut = copy.deepcopy(base)
    first_cut["features"] = first_cut["features"][2:3]
    negatives.append(("first-cut", first_cut, "前置实体"))
    for name, plan, expected in negatives:
        source, report_path = output / f"negative-{name}.json", output / f"negative-{name}.report.json"
        source.write_text(json.dumps(plan), encoding="utf-8")
        result = subprocess.run([str(executable), str(source), str(manifest), str(report_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode == 0 or expected not in result.stderr or report_path.exists():
            raise AssertionError((name, result.returncode, result.stderr, "report_exists", report_path.exists()))
    summary = dict(scope="CADPlan_geometry_parameter_parity_not_Agent_image_acceptance",
                   cases=records, passed=len(records), bounds_tolerance_mm=1e-6,
                   volume_tolerance_mm3=1e-5,
                   negative_cases_rejected=len(negatives),
                   python_ocp_version=python_version, rust_occt_version="7.9.3")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}))


if __name__ == "__main__":
    main()
