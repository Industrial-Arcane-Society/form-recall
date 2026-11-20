"""Development-only CLI comparison with the retained Python OCCT baseline.

This does not drive an Agent, inject image estimates, or provide runtime fallback.
"""
import copy
import importlib.metadata
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
TAURI = ROOT / "src/apps/desktop/src-tauri"
sys.path.insert(0, str(ROOT / "src/legacy"))
from ai_modeler.core.geometry import occ_kernel
from ai_modeler.sdk.cadplan import plan_to_document
from ai_modeler.parts import std_pulley


def main():
    base = json.loads((TAURI / "../../../core/cad-engine/examples/fixtures/native_geometry_plan.json").read_text(encoding="utf-8"))
    output = TAURI / "target/f4-occt/plan-parity"
    output.mkdir(parents=True, exist_ok=True)
    executable = TAURI / "target/debug/examples/native_plan_geometry_probe.exe"
    manifest = Path(os.environ.get(
        "AIMODELER_OCCT_MANIFEST",
        TAURI / "target/f4-occt/runtime-manifest.json",
    ))
    cases = [("recess-nine-holes", base)]

    def add(name, kind, parameters, units=None):
        plan = copy.deepcopy(base)
        plan["features"] = [{"id": "f1", "kind": kind, "operation": "add", "parameters": parameters,
            "sources": {key: {"type": "user"} for key, value in parameters.items()
                        if isinstance(value, (int, float, list)) and not isinstance(value, bool)}}]
        if units:
            plan["units"] = units
        cases.append((name, plan))

    add("gear-native-approximation", "gear", dict(teeth=20, module=2, height=10,
        center=[0, 0, 0], axis="Z", base=False))
    add("helix-z-baseline", "helix", dict(radius=8, wire_r=1, height=20, turns=3))
    add("helix-y-translated", "helix", dict(radius=10, wire_r=1, height=20, turns=4,
        axis="Y", center=[12, -7, 5]))
    add("helix-x-tapered", "helix", dict(radius=10, wire_r=1, height=20, turns=4,
        axis="X", center=[2, 3, 4], taper=0.15, taper_start=0.25))
    add("helix-cm-unit-scaling", "helix", dict(radius=1, wire_r=0.1, height=2, turns=4,
        axis="Y", center=[1.2, -0.7, 0.5]), dict(length="cm", angle="deg"))
    add("helical-gear-legacy-straight", "helical_gear", dict(teeth=20, module=2, height=10,
        helix_deg=18, bore_d=0, center=[0, 0, 0], segments=20))
    add("helical-gear-angle-ignored", "helical_gear", dict(teeth=20, module=2, height=10,
        helix_deg=42, bore_d=0, center=[0, 0, 0], segments=20))
    add("helical-gear-explicit-bore", "helical_gear", dict(teeth=20, module=2, height=10,
        helix_deg=18, bore_d=8, center=[10, -5, 7], segments=20))
    add("socket-bolt-visible-thread", "socket_head_bolt", dict(shank_d=8, thread_pitch=1.25,
        length=20, head_d=13, head_h=8, hex_s=6, hex_t=4, center=[0, 0, 0], segments=48))
    add("piston-one-groove-with-x-pin", "piston",
        dict(diameter=20, height=12, pin_d=1, grooves=1, center=[0, 0, 0]))
    add("piston-two-grooves-and-x-pin", "piston",
        dict(diameter=20, height=12, pin_d=4, grooves=2, center=[2, -3, 10]))
    add("piston-cm-unit-scaling", "piston",
        dict(diameter=2, height=1.2, pin_d=0.4, grooves=2, center=[0.2, -0.3, 1]),
        dict(length="cm", angle="deg"))
    add("camshaft-origin", "camshaft", dict(length=80, journal_d=12, base_d=20,
        lift=4, lobes=4, lobe_deg=0, center=[0, 0, 0]))
    add("camshaft-translated-phased", "camshaft", dict(length=80, journal_d=12, base_d=20,
        lift=4, lobes=4, lobe_deg=17, center=[12, -7, 5]))
    add("camshaft-cm-unit-scaling", "camshaft", dict(length=8, journal_d=1.2, base_d=2,
        lift=0.4, lobes=4, lobe_deg=0.296705972839036, center=[1.2, -0.7, 0.5]),
        dict(length="cm", angle="rad"))
    add("crankshaft-b30-four-throw", "crankshaft", dict(length=320, main_d=50, pin_d=42,
        stroke=86, throws=4, center=[0, 0, 0]))
    add("crankshaft-translated-three-throw", "crankshaft", dict(length=180, main_d=36, pin_d=28,
        stroke=54, throws=3, center=[12, -7, 5]))
    add("crankshaft-cm-unit-scaling", "crankshaft", dict(length=32, main_d=5, pin_d=4.2,
        stroke=8.6, throws=4, center=[1.2, -0.7, 0.5]),
        dict(length="cm", angle="rad"))
    add("bevel-gear-origin-default-bore", "bevel_gear", dict(teeth=20, module=2,
        height=12, cone_deg=45, bore_d=0, center=[0, 0, 0], segments=12))
    add("bevel-gear-translated-explicit-bore-angle-clamp", "bevel_gear", dict(teeth=24,
        module=1.5, height=18, cone_deg=90, bore_d=16, center=[12, -7, 5], segments=8))
    add("vbelt-pulley-standard-a-translated", "vbelt_pulley", dict(pitch_d=80,
        grooves=3, height=55, groove_type="A", bore_d=12, outer_d=85.5,
        center=[3, -4, 5]))
    add("vbelt-pulley-standard-y", "vbelt_pulley", dict(pitch_d=50,
        grooves=2, height=25, groove_type="Y", bore_d=8, outer_d=53.2,
        center=[0, 0, 0]))
    add("vbelt-pulley-standard-b", "vbelt_pulley", dict(pitch_d=150,
        grooves=3, height=70, groove_type="B", bore_d=20, outer_d=157,
        center=[12, -7, 5]))
    add("vbelt-pulley-standard-b-large-mm", "vbelt_pulley", dict(pitch_d=240,
        grooves=3, height=700, groove_type="B", bore_d=20, outer_d=247,
        center=[120, -70, 50]))
    add("vbelt-pulley-standard-b-large-cm", "vbelt_pulley", dict(pitch_d=24,
        grooves=3, height=70, groove_type="B", bore_d=2, outer_d=24.7,
        center=[12, -7, 5]), dict(length="cm", angle="rad"))
    add("bevel-gear-cm-rad-scaling", "bevel_gear", dict(teeth=20, module=0.2,
        height=1.2, cone_deg=0.7853981633974483, bore_d=0,
        center=[0, 0, 0], segments=12), dict(length="cm", angle="rad"))
    add("timing-pulley-xl-default-bore", "timing_pulley",
        dict(pitch_d=80, teeth=50, height=10, tooth_type="XL", bore_d=0,
             center=[0, 0, 0]))
    add("timing-pulley-t5-translated-explicit-bore", "timing_pulley",
        dict(pitch_d=60, teeth=60, height=14, tooth_type="T5", bore_d=12,
             center=[12, -7, 5], segments=32))
    add("timing-pulley-mxl-cm-unit-scaling", "timing_pulley",
        dict(pitch_d=10, teeth=40, height=1.2, tooth_type="MXL", bore_d=2.5,
             center=[1.2, -0.7, 0.5], segments=48),
        dict(length="cm", angle="deg"))
    add("spoked-wheel-z-origin", "spoked_wheel",
        dict(outer_r=30, rim_w=4, hub_r=8, height=8, spokes=6, spoke_w=3,
             center=[0, 0, 0]))
    add("spoked-wheel-z-translated", "spoked_wheel",
        dict(outer_r=20, rim_w=4, hub_r=6, height=10, spokes=5, spoke_w=3,
             center=[12, -7, 5]))
    add("spoked-wheel-cm-unit-scaling", "spoked_wheel",
        dict(outer_r=2, rim_w=0.4, hub_r=0.6, height=1, spokes=5, spoke_w=0.3,
             center=[1.2, -0.7, 0.5]),
        dict(length="cm", angle="deg"))
    add("conrod-origin", "conrod",
        dict(length=80, big_d=30, small_d=18, big_bore=20, small_bore=10,
             shank_w=10, thick=8, center=[0, 0, 0]))
    add("conrod-translated-center", "conrod",
        dict(length=80, big_d=30, small_d=18, big_bore=20, small_bore=10,
             shank_w=10, thick=8, center=[20, -11, 7]))
    add("conrod-cm-unit-scaling", "conrod",
        dict(length=8, big_d=3, small_d=1.8, big_bore=2, small_bore=1,
             shank_w=1, thick=0.8, center=[2, -1.1, 0.7]),
        dict(length="cm", angle="deg"))
    add("cross-joint-origin", "cross_joint",
        dict(span=40, journal_d=12, center=[0, 0, 0], segments=24))
    add("cross-joint-translated", "cross_joint",
        dict(span=40, journal_d=12, center=[12, -7, 5], segments=24))
    add("cross-joint-cm-unit-scaling", "cross_joint",
        dict(span=4, journal_d=1.2, center=[1.2, -0.7, 0.5], segments=24),
        dict(length="cm", angle="deg"))
    add("scroll-disc-legacy-oracle", "scroll_disc",
        dict(outer_d=60, height=8, wall_t=2, turns=2.5))
    add("scroll-disc-translated", "scroll_disc",
        dict(outer_d=60, height=8, wall_t=2, turns=2.5, center=[12, -7, 5]))
    add("scroll-disc-cm-unit-scaling", "scroll_disc",
        dict(outer_d=6, height=0.8, wall_t=0.2, turns=2.5, center=[1.2, -0.7, 0.5]),
        dict(length="cm", angle="deg"))

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

    def user_sources(parameters):
        return {key: {"type": "user"} for key, value in parameters.items()
                if isinstance(value, (int, float, list)) and not isinstance(value, bool)}

    def add_pattern_case(name, features, parameters, source_id, operation):
        features.append({"id": "pattern", "kind": "pattern", "operation": operation,
            "parameters": parameters, "sources": user_sources(parameters),
            "relations": [{"type": "pattern_source", "feature_id": source_id}]})
        pattern_plan = copy.deepcopy(base)
        pattern_plan["features"] = features
        cases.append((name, pattern_plan))

    seed_box_parameters = dict(length=2, width=2, height=2, center=[0, 0, 0], base=False)
    seed_box = {"id": "seed-box", "kind": "box", "operation": "add",
        "parameters": seed_box_parameters, "sources": user_sources(seed_box_parameters)}
    add_pattern_case("pattern-linear-overlap", [seed_box],
        dict(mode="linear", count=3, step=[1.5, 0, 0]), "seed-box", "add")
    add_pattern_case("pattern-linear-y", [seed_box],
        dict(mode="linear", count=3, step=[0, 1.5, 0]), "seed-box", "add")
    add_pattern_case("pattern-linear-z", [seed_box],
        dict(mode="linear", count=3, step=[0, 0, 1.5]), "seed-box", "add")

    def stock_and_hole(hole_axis):
        stock_parameters = dict(length=20, width=20, height=20, center=[0, 0, 0], base=False)
        hole_parameters = dict(diameter=2, axis=hole_axis, pos="point", point=[3, 0, 0], through=True)
        return [
            {"id": "stock", "kind": "box", "operation": "add", "parameters": stock_parameters,
             "sources": user_sources(stock_parameters)},
            {"id": "hole-seed", "kind": "hole", "operation": "cut", "parameters": hole_parameters,
             "sources": user_sources(hole_parameters)},
        ]

    add_pattern_case("pattern-circular-four-through-holes", stock_and_hole("Z"),
        dict(mode="circular", count=4, axis="Z", angle=360, full=True, center=[0, 0, 0]),
        "hole-seed", "cut")
    seed_cylinder_parameters = dict(radius=1, height=20, axis="Y", center=[0, 0, 0], base=False)
    seed_cylinder = {"id": "seed-cylinder", "kind": "cylinder", "operation": "add",
        "parameters": seed_cylinder_parameters, "sources": user_sources(seed_cylinder_parameters)}
    add_pattern_case("pattern-circular-rotated-tool", [seed_cylinder],
        dict(mode="circular", count=2, axis="Z", angle=45, full=False, center=[0, 0, 0]),
        "seed-cylinder", "add")
    for pattern_axis, source_axis in (("X", "Z"), ("Y", "X")):
        cylinder_parameters = dict(radius=1, height=20, axis=source_axis,
                                   center=[0, 0, 0], base=False)
        cylinder = {"id": "seed-cylinder", "kind": "cylinder", "operation": "add",
            "parameters": cylinder_parameters, "sources": user_sources(cylinder_parameters)}
        add_pattern_case(f"pattern-circular-{pattern_axis.lower()}-axis",
            [cylinder], dict(mode="circular", count=2, axis=pattern_axis,
            angle=45, full=False, center=[0, 0, 0]), "seed-cylinder", "add")
    add_pattern_case("pattern-circular-cut-no-op", stock_and_hole("Z"),
        dict(mode="circular", count=3, axis="Z", angle=0, full=False, center=[0, 0, 0]),
        "hole-seed", "cut")

    expected_patterns = {
        "pattern-linear-overlap": ([-1, -1, -1, 4, 1, 1], 20.0),
        "pattern-linear-y": ([-1, -1, -1, 1, 4, 1], 20.0),
        "pattern-linear-z": ([-1, -1, -1, 1, 1, 4], 20.0),
        "pattern-circular-x-axis": ([-1, -7.778174593, -10, 1, 7.778174593, 10], None),
        "pattern-circular-y-axis": ([-10, -1, -7.778174593, 10, 1, 7.778174593], None),
        "pattern-circular-four-through-holes": ([-10, -10, -10, 10, 10, 10], 8000 - 80 * 3.141592653589793),
        "pattern-circular-rotated-tool": ([-7.778174593, -10, -1, 7.778174593, 10, 1], None),
        "pattern-circular-cut-no-op": ([-10, -10, -10, 10, 10, 10], 8000 - 20 * 3.141592653589793),
    }
    expected_pistons = {
        "piston-one-groove-with-x-pin": ([-10, -10, -6, 10, 10, 6], None),
        "piston-two-grooves-and-x-pin": ([-8, -13, 4, 12, 7, 16], None),
        "piston-cm-unit-scaling": ([-8, -13, 4, 12, 7, 16], None),
    }
    expected_wheels = {
        "spoked-wheel-z-origin": ([-30, -30, -4, 30, 30, 4], [0, 0, 0]),
        "spoked-wheel-z-translated": ([-8, -27, 0, 32, 13, 10], [12, -7, 5]),
        "spoked-wheel-cm-unit-scaling": ([-8, -27, 0, 32, 13, 10], [12, -7, 5]),
    }
    expected_conrods = {
        "conrod-origin": ([-55, -15, -4, 49, 15, 4], [0, 0, 0], 13425.635002984112),
        "conrod-translated-center": ([-35, -26, 3, 69, 4, 11], [20, -11, 7], 13425.635002984112),
        "conrod-cm-unit-scaling": ([-35, -26, 3, 69, 4, 11], [20, -11, 7], 13425.635002984112),
    }
    expected_cross_joint_bounds = {
        "cross-joint-origin": [-20, -20, -6, 20, 20, 6],
        "cross-joint-translated": [-8, -27, -1, 32, 13, 11],
        "cross-joint-cm-unit-scaling": [-8, -27, -1, 32, 13, 11],
    }
    expected_scroll_disc_bounds = {
        "scroll-disc-legacy-oracle": [-30, -30, -4, 30, 30, 4],
        "scroll-disc-translated": [-18, -37, 1, 42, 23, 9],
        "scroll-disc-cm-unit-scaling": [-18, -37, 1, 42, 23, 9],
    }
    expected_helix_bounds = {
        "helix-z-baseline": [-9, -8.774351209636414, -1, 9, 8.774351209636414, 21],
        "helix-y-translated": [1.2757929659999374, -8, -6, 22.724207034000063, 14, 16],
        "helix-x-tapered": [1, -8, -6.72420703400006, 23, 14, 14.724207034000061],
        "helix-cm-unit-scaling": [1.2757929659999374, -8, -6, 22.724207034000063, 14, 16],
    }
    helix_native_volumes = {}
    cross_joint_native_volumes = {}
    camshaft_native_volumes = {}
    crankshaft_native_volumes = {}
    timing_pulley_native_volumes = {}
    helical_native_results = {}
    vbelt_native_volumes = {}
    scroll_disc_native_volumes = {}
    scroll_disc_native_areas = {}

    records = []
    for name, plan in cases:
        source, report_path = output / f"{name}.json", output / f"{name}.report.json"
        source.write_text(json.dumps(plan), encoding="utf-8")
        result = subprocess.run([str(executable), str(source), str(manifest), str(report_path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", timeout=120)
        if result.returncode:
            raise RuntimeError(f"{name}: {result.stderr[:1500]}")
        native = json.loads(report_path.read_text(encoding="utf-8"))
        if name in expected_patterns:
            expected_bounds, expected_volume = expected_patterns[name]
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_bounds)) > 1e-6
                    or (expected_volume is not None
                        and abs(native["volume_mm3"] - expected_volume) > 1e-5)):
                raise AssertionError((name, "unexpected-pattern-geometry", native["bounds_mm"],
                                      native["volume_mm3"], expected_bounds, expected_volume))
        if name in expected_pistons:
            expected_bounds, expected_volume = expected_pistons[name]
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_bounds)) > 1e-6
                    or (expected_volume is not None
                        and abs(native["volume_mm3"] - expected_volume) > 1e-5)):
                raise AssertionError((name, "unexpected-piston-geometry", native["bounds_mm"],
                                      native["volume_mm3"], expected_bounds, expected_volume))
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (support.get("geometry_support") != "exact"
                    or details.get("geometry_support") != "exact"
                    or details.get("grooves_built") != (1 if name == "piston-one-groove-with-x-pin" else 2)):
                raise AssertionError((name, "piston-support-not-exact", support))
            bore = details.get("pin_bore", {})
            expected_center = [0, 0, 0] if name == "piston-one-groove-with-x-pin" else [2, -3, 10]
            expected_diameter = 1.0 if name == "piston-one-groove-with-x-pin" else 4.0
            if (bore.get("axis") != "X"
                    or bore.get("centerline_mm") != expected_center
                    or abs(bore.get("diameter_mm", 0) - expected_diameter) > 1e-9):
                raise AssertionError((name, "piston-pin-bore-contract", bore))
        if name in expected_wheels:
            expected_bounds, expected_center = expected_wheels[name]
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_bounds)) > 1e-6
                    or support.get("geometry_support") != "exact"
                    or details.get("geometry_support") != "exact"
                    or details.get("axis") != "Z"
                    or any(abs(a - b) > 1e-9 for a, b in zip(details.get("center_mm", []), expected_center))):
                raise AssertionError((name, "spoked-wheel-geometry-contract", native["bounds_mm"], support))
        if name in expected_conrods:
            expected_bounds, expected_center, expected_volume = expected_conrods[name]
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_bounds)) > 1e-6
                    or abs(native["volume_mm3"] - expected_volume) > 1e-5
                    or support.get("geometry_support") != "approximate"
                    or details.get("geometry_support") != "approximate"
                    or details.get("builder") != "analytic_annular_ends_with_external_tangent_web"
                    or any(abs(a - b) > 1e-9 for a, b in zip(details.get("center_mm", []), expected_center))):
                raise AssertionError((name, "conrod-geometry-contract", native["bounds_mm"],
                                      native["volume_mm3"], support))
        if name in expected_cross_joint_bounds:
            expected_bounds = expected_cross_joint_bounds[name]
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            expected_samples = {
                "x_journal": [-20.0, 20.0],
                "y_journal": [-20.0, 20.0],
                "z_hub": [-5.4, 5.4],
            }
            sample_rows = {row.get("label"): row for row in details.get("material_samples", [])}
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_bounds)) > 1e-6
                    or native.get("valid") is not True
                    or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("geometry_support") != "approximate"
                    or details.get("builder") != "analytic_hub_box_with_four_fused_cylindrical_journals"
                    or details.get("baseline_comparison", "").find("volume difference is reported") < 0
                    or set(sample_rows) != set(expected_samples)
                    or any(sample_rows[label].get("passed") is not True
                           or sample_rows[label].get("material_intervals_mm") != [interval]
                           for label, interval in expected_samples.items())):
                raise AssertionError((name, "cross-joint-geometry-contract", native["bounds_mm"], support))
            cross_joint_native_volumes[name] = native["volume_mm3"]
        if name in expected_scroll_disc_bounds:
            shape, baseline_report = occ_kernel.build_document(plan_to_document(plan))
            bounds_error = max(abs(a - b) for a, b in zip(occ_kernel._bbox(shape), native["bounds_mm"]))
            volume_error = abs(occ_kernel.volume(shape) - native["volume_mm3"])
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            baseline_area = occ_kernel.surface_area(shape)
            area_estimate = details.get("surface_area_estimate_mm2")
            area_relative_error = (abs(area_estimate - baseline_area) / max(baseline_area, 1e-12)
                                   if isinstance(area_estimate, (int, float)) else math.inf)
            if (max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_scroll_disc_bounds[name])) > 1e-6
                    or native.get("valid") is not True
                    or native.get("closed_solid_count") != 1
                    or baseline_report.get("geometry_state") != "passed"
                    or baseline_report.get("brep_valid") is not True
                    or baseline_report.get("solid_count") != 1
                    or baseline_report.get("closed") is not True
                    or support.get("geometry_support") != "approximate"
                    or details.get("builder") != "legacy_prismatic_archimedean_spiral_and_backplate"
                    or details.get("axis") != "Z"
                    or details.get("surface_area_estimate_method", {}).get("relative_comparison_tolerance") != 0.006
                    or bounds_error > 1e-6 or volume_error > 1e-5
                    or area_relative_error > 0.006):
                raise AssertionError((name, "scroll-disc-brep-or-approximation-boundary",
                                      native, baseline_report, bounds_error, volume_error,
                                      area_estimate, baseline_area, area_relative_error))
            scroll_disc_native_volumes[name] = native["volume_mm3"]
            scroll_disc_native_areas[name] = area_estimate
        if name in expected_helix_bounds:
            shape, baseline_report = occ_kernel.build_document(plan_to_document(plan))
            baseline_bounds = occ_kernel._bbox(shape)
            baseline_volume = occ_kernel.volume(shape)
            bounds_error = max(abs(a - b) for a, b in zip(baseline_bounds, native["bounds_mm"]))
            volume_error = abs(baseline_volume - native["volume_mm3"])
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (bounds_error > 1e-6
                    or volume_error > 1e-5
                    or max(abs(a - b) for a, b in zip(native["bounds_mm"], expected_helix_bounds[name])) > 1e-6
                    or native.get("valid") is not True
                    or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("geometry_support") != "approximate"
                    or details.get("builder") != "legacy_oriented_triangle_sweep_sewn_to_occt_facet_brep"
                    or details.get("occt_validation", {}).get("valid") is not True
                    or details.get("occt_validation", {}).get("solid_count") != 1
                    or details.get("occt_validation", {}).get("closed_solid_count") != 1):
                raise AssertionError((name, "helix-legacy-parity", {
                    "native_bounds": native["bounds_mm"], "baseline_bounds": baseline_bounds,
                    "bounds_error_mm": bounds_error, "native_volume_mm3": native["volume_mm3"],
                    "baseline_volume_mm3": baseline_volume, "volume_error_mm3": volume_error,
                    "support": support,
                }))
            helix_native_volumes[name] = native["volume_mm3"]
        if name.startswith("vbelt-pulley-"):
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (native.get("valid") is not True or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("geometry_support") != "approximate"
                    or details.get("builder") != "legacy_standard_vbelt_axial_profile_analytic_revolve"
                    or details.get("axis") != "Z"):
                raise AssertionError((name, "vbelt-pulley-native-contract", native, support))
            vbelt_native_volumes[name] = native["volume_mm3"]
        if name == "pattern-circular-four-through-holes":
            holes = native.get("hole_instances", [])
            if (len(holes) != 4 or native.get("hole_measurements_verified") is not True
                    or any(row.get("status") != "verified_open" for row in holes)):
                raise AssertionError((name, "four-patterned-holes-not-measured", holes))
        if name == "pattern-circular-cut-no-op":
            result = native["feature_results"][-1]
            if result.get("state") != "no_op" or len(native.get("hole_instances", [])) != 1:
                raise AssertionError((name, "no-op-pattern-not-reported", result,
                                      native.get("hole_instances")))
        if name in ("gear-native-approximation", "helical-gear-legacy-straight",
                    "helical-gear-angle-ignored", "helical-gear-explicit-bore", "socket-bolt-visible-thread"):
            support = native["feature_results"][0]
            if support.get("geometry_support") != "approximate" or not support.get("support_details", {}).get("warnings"):
                raise AssertionError(("native-standard-feature-support-not-labeled", name, support))
            if name.startswith("helical-gear-"):
                details = support["support_details"]
                if (details.get("builder") != "legacy_straight_trapezoidal_gear_prism"
                        or details.get("helix_deg_applied") != 0.0
                        or native.get("valid") is not True
                        or native.get("closed_solid_count") != 1):
                    raise AssertionError((name, "helical-gear-contract", native, support))
                helical_native_results[name] = (native["bounds_mm"], native["volume_mm3"])
            if name == "socket-bolt-visible-thread" and support["support_details"].get("thread", {}).get("visible_geometry") is not True:
                raise AssertionError(("native-bolt-thread-not-visible", support["support_details"]))
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
        shape, baseline_report = occ_kernel.build_document(plan_to_document(plan))
        bounds_error = max(abs(a - b) for a, b in zip(occ_kernel._bbox(shape), native["bounds_mm"]))
        volume_error = abs(occ_kernel.volume(shape) - native["volume_mm3"])
        if name.startswith("timing-pulley-"):
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            parameters = plan["features"][0]["parameters"]
            scale = 10.0 if plan.get("units", {}).get("length") == "cm" else 1.0
            pitch_d = parameters["pitch_d"] * scale
            height = parameters["height"] * scale
            bore_d = parameters.get("bore_d", 0.0) * scale
            info = std_pulley.timing_pitch(parameters["tooth_type"])
            pitch_radius = max(2.0, pitch_d / 2.0)
            root_radius = pitch_radius - info["tooth_h"] * 0.55
            inner_radius = max(1.0, bore_d / 2.0) if bore_d else max(2.0, root_radius * 0.35)
            segments = max(3, int(parameters.get("segments", 64)))
            analytic_blank_delta = (math.pi - segments / 2.0 * math.sin(2.0 * math.pi / segments)) * (
                root_radius * root_radius - inner_radius * inner_radius) * height
            if (bounds_error > 1e-6 or native.get("valid") is not True
                    or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("builder") != "legacy_trapezoidal_teeth_with_analytic_annular_blank"
                    or details.get("teeth_built") != parameters["teeth"]
                    or volume_error <= 0
                    or volume_error > analytic_blank_delta * 1.02
                    or volume_error / max(occ_kernel.volume(shape), 1e-12) > 0.01):
                raise AssertionError((name, "timing-pulley-faceted-blank-boundary", bounds_error,
                                      volume_error, analytic_blank_delta, native, baseline_report))
            timing_pulley_native_volumes[name] = native["volume_mm3"]
        if name.startswith("helical-gear-"):
            if (native.get("valid") is not True or native.get("closed_solid_count") != 1
                    or baseline_report.get("geometry_state") != "passed"
                    or baseline_report.get("brep_valid") is not True
                    or baseline_report.get("solid_count") != 1
                    or baseline_report.get("closed") is not True
                    or bounds_error > 1e-6):
                raise AssertionError((name, "validity-or-bounds", native, baseline_report, bounds_error))
        if name.startswith("camshaft-"):
            if (bounds_error > 1e-6 or baseline_report.get("geometry_state") != "passed"
                    or baseline_report.get("brep_valid") is not True
                    or baseline_report.get("solid_count") != 1
                    or baseline_report.get("closed") is not True):
                raise AssertionError((name, "camshaft-bounds-or-legacy-brep", bounds_error,
                                      baseline_report.get("geometry_state"), baseline_report.get("brep_valid"),
                                      baseline_report.get("solid_count"), baseline_report.get("closed")))
            camshaft_native_volumes[name] = native["volume_mm3"]
        if name.startswith("crankshaft-"):
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (native.get("valid") is not True or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("builder") != "analytic_journals_with_rectangular_webs"):
                raise AssertionError((name, "crankshaft-native-brep-contract", support))
            crankshaft_native_volumes[name] = native["volume_mm3"]
        if name.startswith("bevel-gear-"):
            support = native["feature_results"][0]
            details = support.get("support_details", {})
            if (native.get("valid") is not True or native.get("closed_solid_count") != 1
                    or support.get("geometry_support") != "approximate"
                    or details.get("geometry_support") != "approximate"
                    or details.get("builder") != "legacy_tapered_trapezoidal_gear_faceted_brep"
                    or details.get("triangles_built", 0) <= 0
                    or details.get("segments_built", 0) < 4):
                raise AssertionError((name, "bevel-gear-native-brep-contract", support))
        if name in expected_cross_joint_bounds:
            if (bounds_error > 1e-6
                    or baseline_report.get("geometry_state") != "passed"
                    or baseline_report.get("brep_valid") is not True
                    or baseline_report.get("solid_count") != 1
                    or baseline_report.get("closed") is not True):
                raise AssertionError((name, "cross-joint-bounds-or-legacy-brep", bounds_error,
                                      baseline_report.get("geometry_state"), baseline_report.get("brep_valid"),
                                      baseline_report.get("solid_count"), baseline_report.get("closed")))
        elif not name.startswith(("camshaft-", "timing-pulley-", "scroll-disc-")) and (bounds_error > 1e-6 or volume_error > 1e-5):
            raise AssertionError((name, bounds_error, volume_error))
        records.append(dict(case=name, passed=True, bounds_max_error_mm=bounds_error,
                            volume_error_mm3=volume_error,
                            volume_comparison=("reported_only_faceted_legacy_vs_analytic_native"
                                               if name in expected_cross_joint_bounds or name.startswith(("camshaft-", "timing-pulley-"))
                                               else "parity_checked"),
                            legacy_volume_mm3=(occ_kernel.volume(shape)
                                               if name in expected_cross_joint_bounds or name.startswith(("camshaft-", "timing-pulley-", "crankshaft-", "helical-gear-", "bevel-gear-")) else None),
                            native_volume_mm3=(native["volume_mm3"]
                                               if name in expected_cross_joint_bounds or name.startswith(("camshaft-", "timing-pulley-", "crankshaft-", "helical-gear-", "bevel-gear-")) else None),
                            volume_relative_difference=(volume_error / max(abs(occ_kernel.volume(shape)), 1e-12)
                                                        if name in expected_cross_joint_bounds or name.startswith(("camshaft-", "timing-pulley-", "crankshaft-", "helical-gear-", "bevel-gear-")) else None)))
    cross_joint_origin_volume = cross_joint_native_volumes["cross-joint-origin"]
    for name in ("cross-joint-translated", "cross-joint-cm-unit-scaling"):
        if abs(cross_joint_native_volumes[name] - cross_joint_origin_volume) > 1e-6:
            raise AssertionError(("cross-joint-transform-volume-invariance", name,
                                  cross_joint_native_volumes[name], cross_joint_origin_volume))
    camshaft_origin_volume = camshaft_native_volumes["camshaft-origin"]
    for name in ("camshaft-translated-phased", "camshaft-cm-unit-scaling"):
        if abs(camshaft_native_volumes[name] - camshaft_origin_volume) > 1e-5:
            raise AssertionError(("camshaft-transform-volume-invariance", name,
                                  camshaft_native_volumes[name], camshaft_origin_volume))
    crankshaft_origin_volume = crankshaft_native_volumes["crankshaft-b30-four-throw"]
    if abs(crankshaft_native_volumes["crankshaft-cm-unit-scaling"] - crankshaft_origin_volume) > 1e-5:
        raise AssertionError(("crankshaft-unit-volume-invariance",
                              crankshaft_native_volumes["crankshaft-cm-unit-scaling"],
                              crankshaft_origin_volume))
    if (helical_native_results["helical-gear-legacy-straight"] !=
            helical_native_results["helical-gear-angle-ignored"]):
        raise AssertionError(("legacy-helical-gear-angle-must-remain-ignored",
                              helical_native_results["helical-gear-legacy-straight"],
                              helical_native_results["helical-gear-angle-ignored"]))
    bevel_gear_origin_volume = next(row["native_volume_mm3"] for row in records
                                    if row["case"] == "bevel-gear-origin-default-bore")
    bevel_gear_cm_volume = next(row["native_volume_mm3"] for row in records
                                if row["case"] == "bevel-gear-cm-rad-scaling")
    if abs(bevel_gear_cm_volume - bevel_gear_origin_volume) > 1e-5:
        raise AssertionError(("bevel-gear-unit-volume-invariance", bevel_gear_cm_volume,
                              bevel_gear_origin_volume))
    if abs(vbelt_native_volumes["vbelt-pulley-standard-b-large-mm"]
           - vbelt_native_volumes["vbelt-pulley-standard-b-large-cm"]) > 1e-5:
        raise AssertionError(("vbelt-pulley-cm-volume-invariance", vbelt_native_volumes))
    scroll_disc_oracle_volume = scroll_disc_native_volumes["scroll-disc-legacy-oracle"]
    scroll_disc_oracle_area = scroll_disc_native_areas["scroll-disc-legacy-oracle"]
    for name in ("scroll-disc-translated", "scroll-disc-cm-unit-scaling"):
        if (abs(scroll_disc_native_volumes[name] - scroll_disc_oracle_volume) > 1e-5
                or abs(scroll_disc_native_areas[name] - scroll_disc_oracle_area) > 1e-5):
            raise AssertionError(("scroll-disc-transform-unit-invariance", name,
                                  scroll_disc_native_volumes, scroll_disc_native_areas))
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
    missing_bolt_size = copy.deepcopy(cases[[name for name, _ in cases].index("socket-bolt-visible-thread")][1])
    missing_bolt_size["features"][0]["parameters"].pop("head_d")
    missing_bolt_size["features"][0]["sources"].pop("head_d")
    negatives.append(("bolt-missing-head-d", missing_bolt_size, "校验"))
    helical_case = copy.deepcopy(cases[[name for name, _ in cases].index("helical-gear-legacy-straight")][1])
    helical_bad_module = copy.deepcopy(helical_case)
    helical_bad_module["features"][0]["parameters"]["module"] = 0
    negatives.append(("helical-gear-zero-module", helical_bad_module, "module"))
    helical_bad_bore = copy.deepcopy(helical_case)
    helical_bad_bore["features"][0]["parameters"]["bore_d"] = -1
    helical_bad_bore["features"][0]["sources"]["bore_d"] = {"type": "user"}
    negatives.append(("helical-gear-negative-bore", helical_bad_bore, "bore_d"))
    over_limit_pattern = copy.deepcopy(cases[[name for name, _ in cases].index("pattern-linear-overlap")][1])
    over_limit_pattern["features"][-1]["parameters"]["count"] = 10_001
    over_limit_pattern["features"][-1]["sources"]["count"] = {"type": "user"}
    negatives.append(("pattern-over-resource-limit", over_limit_pattern, "OCCT 单特征上限"))
    disjoint_pattern = copy.deepcopy(cases[[name for name, _ in cases].index("pattern-linear-overlap")][1])
    disjoint_pattern["features"][-1]["parameters"]["step"] = [4, 0, 0]
    disjoint_pattern["features"][-1]["sources"]["step"] = {"type": "user"}
    negatives.append(("pattern-disjoint-solids", disjoint_pattern, "未生成单一有效封闭实体"))

    piston_grooves = copy.deepcopy(cases[[name for name, _ in cases].index("piston-two-grooves-and-x-pin")][1])
    piston_grooves["features"][0]["parameters"]["grooves"] = 257
    piston_grooves["features"][0]["sources"]["grooves"] = {"type": "user"}
    negatives.append(("piston-groove-resource-limit", piston_grooves, "资源上限"))
    piston_small = copy.deepcopy(cases[[name for name, _ in cases].index("piston-one-groove-with-x-pin")][1])
    piston_small["features"][0]["parameters"]["diameter"] = 1.5
    piston_small["features"][0]["sources"]["diameter"] = {"type": "user"}
    negatives.append(("piston-invalid-inner-radius", piston_small, "直径过小"))
    piston_overlapping_grooves = copy.deepcopy(cases[[name for name, _ in cases].index("piston-two-grooves-and-x-pin")][1])
    piston_overlapping_grooves["features"][0]["parameters"]["grooves"] = 7
    piston_overlapping_grooves["features"][0]["sources"]["grooves"] = {"type": "user"}
    negatives.append(("piston-overlapping-grooves", piston_overlapping_grooves, "会自交"))
    camshaft_case = cases[[name for name, _ in cases].index("camshaft-origin")][1]
    for key, value, message in (("length", 1, "length"), ("base_d", 1, "base_d"), ("lobes", 513, "512")):
        invalid = copy.deepcopy(camshaft_case)
        invalid["features"][0]["parameters"][key] = value
        invalid["features"][0]["sources"][key] = {"type": "user"}
        negatives.append((f"camshaft-invalid-{key}", invalid, message))
    crankshaft_case = cases[[name for name, _ in cases].index("crankshaft-b30-four-throw")][1]
    for key, value, message in (("main_d", 0, "main_d"), ("stroke", -1, "stroke"),
                                ("throws", 129, "128")):
        invalid = copy.deepcopy(crankshaft_case)
        invalid["features"][0]["parameters"][key] = value
        invalid["features"][0]["sources"][key] = {"type": "user"}
        negatives.append((f"crankshaft-invalid-{key}", invalid, message))

    bevel_gear_case = cases[[name for name, _ in cases].index("bevel-gear-origin-default-bore")][1]
    for key, value, message in (("module", 0, "必须大于 0"), ("height", -1, "必须大于 0"),
                                ("bore_d", -1, "不能小于 0"),
                                ("teeth", 5, "不足以生成有效轮廓"),
                                ("segments", 2, "不足以生成有效轮廓")):
        invalid = copy.deepcopy(bevel_gear_case)
        invalid["features"][0]["parameters"][key] = value
        invalid["features"][0]["sources"][key] = {"type": "user"}
        negatives.append((f"bevel-gear-invalid-{key}", invalid, message))
    bevel_gear_wide_bore = copy.deepcopy(bevel_gear_case)
    bevel_gear_wide_bore["features"][0]["parameters"].update(teeth=6, module=2, bore_d=9)
    bevel_gear_wide_bore["features"][0]["sources"].update({
        "teeth":{"type":"user"}, "module":{"type":"user"}, "bore_d":{"type":"user"}})
    negatives.append(("bevel-gear-bore-crosses-tooth-root", bevel_gear_wide_bore, "中心孔半径"))
    bevel_gear_over_budget = copy.deepcopy(bevel_gear_case)
    bevel_gear_over_budget["features"][0]["parameters"]["teeth"] = 100_000
    bevel_gear_over_budget["features"][0]["sources"]["teeth"] = {"type":"user"}
    negatives.append(("bevel-gear-over-triangle-budget", bevel_gear_over_budget, "超过原生上限"))
    bevel_gear_missing_angle = copy.deepcopy(bevel_gear_case)
    bevel_gear_missing_angle["features"][0]["parameters"].pop("cone_deg")
    bevel_gear_missing_angle["features"][0]["sources"].pop("cone_deg")
    negatives.append(("bevel-gear-missing-cone-angle", bevel_gear_missing_angle, "校验"))

    timing_case = cases[[name for name, _ in cases].index("timing-pulley-xl-default-bore")][1]
    timing_missing = copy.deepcopy(timing_case)
    timing_missing["features"][0]["parameters"].pop("tooth_type")
    timing_missing["features"][0]["sources"].pop("tooth_type", None)
    negatives.append(("timing-pulley-missing-tooth-type", timing_missing, "校验"))
    for key, value, message in (("teeth", 513, "6..=512"), ("bore_d", -1, "不能小于 0"),
                                ("bore_d", 79, "齿根半径"), ("segments", 513, "3..=512")):
        invalid = copy.deepcopy(timing_case)
        invalid["features"][0]["parameters"][key] = value
        invalid["features"][0]["sources"][key] = {"type": "user"}
        negatives.append((f"timing-pulley-invalid-{key}-{value}", invalid, message))
    timing_overlap = copy.deepcopy(timing_case)
    timing_overlap["features"][0]["parameters"].update(pitch_d=20, teeth=50)
    timing_overlap["features"][0]["sources"].update({
        "pitch_d": {"type": "user"}, "teeth": {"type": "user"}})
    negatives.append(("timing-pulley-overlapping-tooth-tips", timing_overlap, "齿顶圆切向齿距"))
    timing_no_overlap = copy.deepcopy(timing_case)
    timing_no_overlap["features"][0]["parameters"]["tooth_type"] = "8M"
    timing_no_overlap["features"][0]["sources"]["tooth_type"] = {"type": "user"}
    negatives.append(("timing-pulley-no-tooth-blank-overlap", timing_no_overlap, "没有材料重叠"))

    vbelt_case = cases[[name for name, _ in cases].index("vbelt-pulley-standard-a-translated")][1]
    vbelt_narrow = copy.deepcopy(vbelt_case)
    vbelt_narrow["features"][0]["parameters"]["height"] = 40
    negatives.append(("vbelt-pulley-width-below-minimum", vbelt_narrow, "最小轮宽"))
    vbelt_bore = copy.deepcopy(vbelt_case)
    vbelt_bore["features"][0]["parameters"]["bore_d"] = 80
    negatives.append(("vbelt-pulley-oversized-bore", vbelt_bore, "不会静默缩小"))
    vbelt_spokes = copy.deepcopy(vbelt_case)
    vbelt_spokes["features"][0]["parameters"].update(
        pitch_d=300, grooves=3, height=140, groove_type="C", bore_d=8, outer_d=309.6)
    negatives.append(("vbelt-pulley-spoke-variant", vbelt_spokes, "辐条"))
    vbelt_axis = copy.deepcopy(vbelt_case)
    vbelt_axis["features"][0]["parameters"]["axis"] = "X"
    negatives.append(("vbelt-pulley-non-z-axis", vbelt_axis, "unknown_parameter"))

    wheel_case_by_name = {name: plan for name, plan in cases}
    wheel_non_z = copy.deepcopy(wheel_case_by_name["spoked-wheel-z-origin"])
    wheel_non_z["features"][0]["parameters"]["axis"] = "X"
    negatives.append(("spoked-wheel-non-z-axis", wheel_non_z, "该特征不支持此参数"))
    wheel_bad_count = copy.deepcopy(wheel_case_by_name["spoked-wheel-z-origin"])
    wheel_bad_count["features"][0]["parameters"]["spokes"] = 1
    wheel_bad_count["features"][0]["sources"]["spokes"] = {"type": "user"}
    negatives.append(("spoked-wheel-too-few-spokes", wheel_bad_count, "至少需要两根"))
    wheel_bad_profile = copy.deepcopy(wheel_case_by_name["spoked-wheel-z-origin"])
    wheel_bad_profile["features"][0]["parameters"]["rim_w"] = 23
    wheel_bad_profile["features"][0]["sources"]["rim_w"] = {"type": "user"}
    negatives.append(("spoked-wheel-invalid-rim-hub", wheel_bad_profile, "轮缘内半径"))

    conrod_missing = copy.deepcopy(wheel_case_by_name["conrod-origin"])
    conrod_missing["features"][0]["parameters"].pop("small_bore")
    conrod_missing["features"][0]["sources"].pop("small_bore")
    negatives.append(("conrod-missing-required-small-bore", conrod_missing, "small_bore"))
    conrod_bad_bore = copy.deepcopy(wheel_case_by_name["conrod-origin"])
    conrod_bad_bore["features"][0]["parameters"]["big_bore"] = 30
    conrod_bad_bore["features"][0]["sources"]["big_bore"] = {"type": "user"}
    negatives.append(("conrod-bore-not-inside-head", conrod_bad_bore, "孔径必须小于"))
    conrod_short = copy.deepcopy(wheel_case_by_name["conrod-origin"])
    conrod_short["features"][0]["parameters"]["length"] = 5
    conrod_short["features"][0]["sources"]["length"] = {"type": "user"}
    negatives.append(("conrod-short-external-tangent", conrod_short, "外公切"))
    cross_joint_missing = copy.deepcopy(wheel_case_by_name["cross-joint-origin"])
    cross_joint_missing["features"][0]["parameters"].pop("journal_d")
    cross_joint_missing["features"][0]["sources"].pop("journal_d")
    negatives.append(("cross-joint-missing-journal-d", cross_joint_missing, "journal_d"))
    cross_joint_nonpositive = copy.deepcopy(wheel_case_by_name["cross-joint-origin"])
    cross_joint_nonpositive["features"][0]["parameters"]["span"] = 0
    cross_joint_nonpositive["features"][0]["sources"]["span"] = {"type": "user"}
    negatives.append(("cross-joint-nonpositive-span", cross_joint_nonpositive, "必须大于 0"))

    scroll_disc_case = cases[[name for name, _ in cases].index("scroll-disc-legacy-oracle")][1]
    for key, value, message in (("outer_d", 0, "outer_d"), ("height", 0, "height"),
                                ("wall_t", -1, "wall_t"), ("turns", 0, "turns"),
                                ("segments", 4097, "segments")):
        invalid = copy.deepcopy(scroll_disc_case)
        invalid["features"][0]["parameters"][key] = value
        invalid["features"][0]["sources"][key] = {"type": "user"}
        negatives.append((f"scroll-disc-invalid-{key}-{value}", invalid, message))
    scroll_disc_overlap = copy.deepcopy(scroll_disc_case)
    scroll_disc_overlap["features"][0]["parameters"]["wall_t"] = 8
    scroll_disc_overlap["features"][0]["sources"]["wall_t"] = {"type": "user"}
    negatives.append(("scroll-disc-self-intersecting-turns", scroll_disc_overlap, "自交或相切"))
    scroll_disc_disjoint_fusion = copy.deepcopy(scroll_disc_case)
    stock_parameters = dict(length=10, width=10, height=10, center=[500, 500, 500], base=False)
    scroll_disc_disjoint_fusion["features"].insert(0, {
        "id": "disjoint-stock", "kind": "box", "operation": "add",
        "parameters": stock_parameters, "sources": user_sources(stock_parameters)})
    negatives.append(("scroll-disc-disjoint-fusion", scroll_disc_disjoint_fusion,
                      "未生成单一有效封闭实体"))

    # The first plan leaves only the recessed local span;
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
                   timing_pulley_positive_cases=len(timing_pulley_native_volumes),
                   vbelt_pulley_positive_cases=len(vbelt_native_volumes),
                   scroll_disc_positive_cases=len(scroll_disc_native_volumes),
                   helix_positive_cases=len(helix_native_volumes),
                   helix_bounds_comparison="legacy OCP; <=1e-6 mm",
                   helix_volume_comparison="legacy OCP; <=1e-5 mm3",
                   scroll_disc_area_comparison="OCCT tessellation at 0.08 mm / 0.35 rad; <=0.6% relative to legacy exact B-Rep area",
                   scroll_disc_volume_comparison="same sampled spiral and analytic backplate; <=1e-5 mm3",
                   timing_pulley_faceted_blank_volume_boundary="analytic-minus-segmented-annulus; each positive case checked against segment formula and <=1% legacy relative delta",
                   python_ocp_version=python_version, rust_occt_version="7.9.3")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}))


if __name__ == "__main__":
    main()
