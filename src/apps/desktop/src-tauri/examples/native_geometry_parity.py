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
        if name == "recess-nine-holes":
            measured = native.get("hole_instances", [])
            if (native.get("hole_measurements_verified") is not True
                    or len(measured) != 9
                    or any(row.get("status") != "verified_open"
                           or row.get("final_measurement", {}).get("accepted") is not True
                           or len(row.get("final_measurement", {}).get("actual_radius_measurements", [])) == 0
                           or abs(row.get("final_measurement", {}).get("actual_radius_measurements", [0])[0]
                                  - row.get("diameter_mm", 0) / 2) > 1e-6
                           for row in measured)):
                raise AssertionError(("final-nine-hole-metrology",
                    native.get("hole_measurements_verified"),
                    [(row.get("feature_id"), row.get("instance_index"), row.get("status"),
                      row.get("final_measurement", {}).get("actual_radius_measurements")) for row in measured]))
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

    # Additional blind-hole checks do not change the established 44 positive
    # parity cases above. The first plan leaves only the recessed local span;
    # the second asks for more depth than that span, and the third samples an
    # already-through-cut centerline with no pre-cut material.
    blind_local = copy.deepcopy(base)
    blind_local["features"] = blind_local["features"][:2]
    blind_parameters = dict(diameter=8, axis="Y", pos="point", point=[0, 0, 60],
                            through=False, depth=5)
    blind_local["features"].append({"id": "blind-local-positive", "kind": "hole",
        "operation": "cut", "parameters": blind_parameters,
        "sources": {key: {"type": "user"} for key in blind_parameters
                    if key in ("diameter", "point", "depth")}})
    deep_local = copy.deepcopy(blind_local)
    deep_local["features"][-1]["id"] = "blind-local-too-deep"
    deep_local["features"][-1]["parameters"]["depth"] = 33
    deep_local["features"][-1]["sources"]["depth"] = {"type": "user"}
    negatives.append(("blind-depth-exceeds-local-span", deep_local, "局部正向材料厚度"))

    no_material = copy.deepcopy(base)
    no_material_parameters = dict(diameter=8, axis="Y", pos="point", point=[0, 0, 60],
                                  through=False, depth=5)
    no_material["features"].append({"id": "blind-no-local-material", "kind": "hole",
        "operation": "cut", "parameters": no_material_parameters,
        "sources": {key: {"type": "user"} for key in no_material_parameters
                    if key in ("diameter", "point", "depth")}})
    negatives.append(("blind-centerline-no-precut-material", no_material,
                      "没有前置材料区间"))
    for name, plan, expected in negatives:
        source, report_path = output / f"negative-{name}.json", output / f"negative-{name}.report.json"
        if report_path.exists():
            report_path.unlink()
        source.write_text(json.dumps(plan), encoding="utf-8")
        result = subprocess.run([str(executable), str(source), str(manifest), str(report_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode == 0 or expected not in result.stderr or report_path.exists():
            raise AssertionError((name, result.returncode, result.stderr, "report_exists", report_path.exists()))

    blind_source = output / "blind-local-positive.json"
    blind_report_path = output / "blind-local-positive.report.json"
    blind_source.write_text(json.dumps(blind_local), encoding="utf-8")
    result = subprocess.run([str(executable), str(blind_source), str(manifest), str(blind_report_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise RuntimeError(f"blind-local-positive: {result.stderr[:1500]}")
    blind_report = json.loads(blind_report_path.read_text(encoding="utf-8"))
    blind_hole = blind_report["sampled_hole_evidence"][-1]
    blind_evidence = blind_hole["sampled_evidence"]
    local_span = blind_report["requested_holes"][-1]["local_positive_material_span_mm"]
    if (blind_hole["status"] != "verified_by_sampled_rays"
            or blind_evidence["expected_through"] is not False
            or blind_evidence["depth_matches_sampled_center_change"] is not True
            or abs(blind_evidence["center_removed_material_mm"] - 5) > 1e-4
            or local_span is None or local_span[1] - local_span[0] <= 5):
        raise AssertionError(("blind-local-positive", blind_evidence, local_span))

    edge_partial = copy.deepcopy(base)
    edge_partial["features"] = edge_partial["features"][:1]
    edge_parameters = dict(diameter=10, axis="Z", pos="point", point=[48, 0, 60], through=True)
    edge_partial["features"].append({"id": "near-edge-hole", "kind": "hole",
        "operation": "cut", "parameters": edge_parameters,
        "sources": {key: {"type": "user"} for key in ("diameter", "point")}})
    edge_source = output / "near-edge-partial-evidence.json"
    edge_report_path = output / "near-edge-partial-evidence.report.json"
    if edge_report_path.exists():
        edge_report_path.unlink()
    edge_source.write_text(json.dumps(edge_partial), encoding="utf-8")
    result = subprocess.run([str(executable), str(edge_source), str(manifest), str(edge_report_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise RuntimeError(f"near-edge-partial-evidence unexpectedly blocked geometry: {result.stderr[:1500]}")
    edge_report = json.loads(edge_report_path.read_text(encoding="utf-8"))
    edge_evidence = edge_report["sampled_hole_evidence"][-1]
    if (edge_report["state"] != "built"
            or edge_report["hole_measurements_verified"] is not False
            or edge_evidence["status"] not in ("partial", "unverified")
            or not edge_evidence["sampled_evidence"]["reason_codes"]):
        raise AssertionError(("near-edge-partial-evidence", edge_evidence))

    # A later operation enlarges one previously valid hole. The final B-Rep
    # must invalidate the old diameter measurement rather than reusing the
    # successful intermediate revision.
    widened = copy.deepcopy(base)
    widened_hole = copy.deepcopy(widened["features"][2])
    widened_hole["id"] = "hole-1-1-widened"
    widened_hole["parameters"]["diameter"] = 14
    widened_hole["sources"]["diameter"] = {"type": "user", "note": "final B-Rep invalidation probe"}
    widened["features"].append(widened_hole)
    widened_source = output / "later-feature-widens-bore.json"
    widened_report_path = output / "later-feature-widens-bore.report.json"
    widened_source.write_text(json.dumps(widened), encoding="utf-8")
    result = subprocess.run([str(executable), str(widened_source), str(manifest), str(widened_report_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise RuntimeError(f"later-feature-widens-bore unexpectedly blocked geometry: {result.stderr[:1500]}")
    widened_report = json.loads(widened_report_path.read_text(encoding="utf-8"))
    widened_rows = [row for row in widened_report["hole_instances"] if row["feature_id"] in ("hole-1-1", "hole-1-1-widened")]
    if (widened_report["hole_measurements_verified"] is not False
            or not any(row["feature_id"] == "hole-1-1" and row["status"] == "candidate_unverified" for row in widened_rows)
            or not any(row["feature_id"] == "hole-1-1-widened" and row["status"] == "candidate_unverified"
                       and row.get("final_measurement", {}).get("actual_radius_measurements") == [7.0]
                       for row in widened_rows)):
        raise AssertionError(("later-feature-widens-bore-not-remeasured",
            [(row.get("feature_id"), row.get("status"),
              row.get("final_measurement", {}).get("actual_radius_measurements")) for row in widened_rows]))
    summary = dict(scope="CADPlan_geometry_parameter_parity_not_Agent_image_acceptance",
                   cases=records, passed=len(records), bounds_tolerance_mm=1e-6,
                   volume_tolerance_mm3=1e-5,
                   negative_cases_rejected=len(negatives),
                   blind_local_span_positive=True,
                   near_edge_partial_did_not_block=True,
                   final_brep_holes_measured=True,
                   later_feature_invalidates_stale_bore_measurement=True,
                   python_ocp_version=python_version, rust_occt_version="7.9.3")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}))


if __name__ == "__main__":
    main()
