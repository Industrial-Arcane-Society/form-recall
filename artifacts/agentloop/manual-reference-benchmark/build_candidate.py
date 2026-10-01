"""Build a hand-estimated CADPlan baseline for the supplied reference image.

This benchmark intentionally does not call the AgentLoop or any model provider.
It exercises only the repository's current CADPlan -> Document -> local mesh path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ai_modeler.core.export import exporters
from ai_modeler.sdk import cadplan

OUT = Path(__file__).resolve().parent
REFERENCE = Path(r"C:\Users\lycohana\.codex\attachments\0e0f3409-8877-4d84-a9d4-32a5bce2b544\image-1.png")


def estimate(evidence: str, note: str = "按参考图像比例估算的名义值，不是工程图或制造标注。"):
    return {"type": "image_estimate", "evidence": evidence, "note": note}


def derived(evidence: str):
    return {"type": "derived", "evidence": evidence}


# Front view is used for X/Z ratios. The reference has no dimension annotations.
# H=340 mm is a convenient nominal scale; the rest follows observed proportions.
width, depth, height = 330.0, 134.0, 340.0
left_width, middle_width, right_width = 130.0, 70.0, 130.0
recess_depth = 20.0
large_hole_diameter, small_hole_diameter = 48.0, 32.0
columns = (-100.0, 0.0, 100.0)
rows = (("upper", 275.0, large_hole_diameter),
        ("middle", 170.0, small_hole_diameter),
        ("lower", 65.0, large_hole_diameter))

features = [{
    "id": "outer-envelope",
    "kind": "box",
    "operation": "add",
    "name": "Continuous outer body",
    "note": "One full-height, full-depth body; image-derived nominal envelope.",
    "parameters": {"length": width, "width": depth, "height": height,
                    "center": [0.0, 0.0, height / 2.0]},
    "sources": {
        "length": estimate("Top-plane long projected edge ≈258 px and visible vertical edge ≈267 px; with H=340 mm, W≈329 mm, rounded to 330 mm."),
        "width": estimate("Top-plane short projected edge ≈105 px versus long edge ≈258 px; with the same approximate foreshortening, D≈340×105/267≈134 mm. Perspective makes this low-confidence."),
        "height": estimate("Nominal scale chosen for convenience because no physical dimension is annotated."),
        "center": derived("Center X/Y at the origin; bottom face is Z=0."),
    },
}, {
    "id": "center-front-recess",
    "kind": "box",
    "operation": "cut",
    "name": "Full-height central front recess",
    "note": "Subtract only the front 20 mm in the central 68 mm strip. The remaining rear wall is a solid recessed face, not a through-opening.",
    "parameters": {"length": middle_width, "width": recess_depth + 2.0,
                    "height": height + 2.0,
                    "center": [0.0, depth / 2.0 - recess_depth / 2.0 + 1.0,
                               height / 2.0]},
    "sources": {
        "length": estimate("The middle strip is estimated at about 21% of total width; assume approximate left-right symmetry for the side sections, yielding 130/70/130 mm."),
        "width": estimate("The visible isometric setback is estimated as 20 mm; 1 mm extra is only Boolean overshoot."),
        "height": estimate("The recess spans the full body height; 1 mm extra at each end is only Boolean overshoot."),
        "center": derived("Outer front face Y=61 mm; the cut extends from Y=41 to 62 mm, leaving the recessed face at Y=41 mm."),
    },
}]

# For axis=Y, CADPlan global points are converted by the SDK to the local in-plane
# coordinates (X,Z). The Y coordinate documents which visible surface is intended;
# the current local through-hole primitive cuts along the entire body depth.
for side, x in (("left", columns[0]), ("center", columns[1]), ("right", columns[2])):
    front_y = depth / 2.0 - (recess_depth if side == "center" else 0.0)
    for row_name, z, diameter in rows:
        size_name = "large" if diameter == large_hole_diameter else "small"
        features.append({
            "id": f"{side}-{row_name}-{size_name}-hole",
            "kind": "hole",
            "operation": "cut",
            "name": f"{side} {row_name} {size_name} through hole",
            "note": "Single explicitly placed bore; hole position and diameter estimated from the front-view pixel ratios.",
            "parameters": {"diameter": diameter, "through": True, "pos": "point",
                           "point": [x, front_y, z], "axis": "Y"},
            "sources": {
                "diameter": estimate("The top/bottom bores are visibly larger than the middle bore; their nominal ratio is set to 48:32=1.5."),
                "point": estimate("Column centers are approximately at 20%, 50%, 80% of front width; row centers at 19%, 50%, 81% of height. Y records the corresponding front/recessed face."),
            },
        })

plan = {
    "schema_version": "1.0",
    "name": "Three-segment recessed block with 9 holes",
    "source_prompt": "复刻 (manual benchmark from the supplied image only)",
    "units": {"length": "mm", "angle": "deg"},
    "coordinate_system": {"type": "global_cartesian", "handedness": "right",
                          "axes": ["X", "Y", "Z"], "origin": [0, 0, 0]},
    "features": features,
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "candidate.cadplan.json").write_text(
    json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

validation = cadplan.validate_plan(plan)
if not validation.get("valid"):
    raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))

preview, doc, manifest, data, body = cadplan._preview_internal(plan)
if preview.get("state") != "previewed" or not body:
    raise SystemExit(json.dumps(preview, ensure_ascii=False, indent=2))

exporters.export_project(doc, str(OUT / "candidate.aimodel"), extra={
    "cadplan_schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
    "cadplan": data,
    "feature_manifest": manifest,
})
exporters.export_stl(body, str(OUT / "candidate.stl"), doc.name)
views = cadplan.preview_plan(plan, str(OUT / "candidate-views.png"), view_set="extended")
front = cadplan.preview_plan(plan, str(OUT / "candidate-front.png"), view_set="front")
iso = cadplan.preview_plan(plan, str(OUT / "candidate-iso.png"), view_set="iso")

report = {
    "reference_image": str(REFERENCE),
    "reference_image_exists": REFERENCE.is_file(),
    "agentloop_used": False,
    "model_provider_used": False,
    "catia_invoked": False,
    "cadplan_validation": validation,
    "preview": preview,
    "views_preview": {"state": views.get("state"), "path": views.get("preview_image"),
                      "view_set": views.get("preview_image_view_set")},
    "front_preview": {"state": front.get("state"), "path": front.get("preview_image")},
    "iso_preview": {"state": iso.get("state"), "path": iso.get("preview_image")},
    "dimension_estimates_mm": {
        "overall": {"width_X": width, "depth_Y": depth, "height_Z": height},
        "segments_X": {"left": left_width, "middle": middle_width, "right": right_width},
        "middle_front_setback": recess_depth,
        "hole_centers_X": list(columns),
        "hole_centers_Z_from_bottom": [z for _, z, _ in rows],
        "hole_diameters_by_row_top_to_bottom": [large_hole_diameter, small_hole_diameter,
                                                large_hole_diameter],
        "status": "all values are image-derived nominal estimates; no manufacturing dimensions were visible",
    },
    "feature_tree": [{"index": i + 1, "id": f["id"], "operation": f["operation"],
                      "kind": f["kind"], "name": f["name"]}
                     for i, f in enumerate(features)],
    "known_interface_limits": [
        "The local hole primitive can place point holes on a global X/Z location for axis Y, but through=true always cuts across the current entire model bounding box; it cannot express a one-face start/stop extent for each panel.",
        "The box primitive plus subtractive box can represent the recessed center as a real solid back face; it cannot directly tag a face as a semantic panel or encode the three-panel interpretation beyond the feature tree and notes.",
        "The local preview and STL are kernel mesh results. They are not CATIA B-Rep evidence; CATIA was not run for this benchmark.",
    ],
    "manual_visual_qa": (
        "Inspected candidate-iso.png, candidate-front.png, and candidate-views.png against the supplied reference. "
        "The mesh shows a single continuous body with a full-height central front recess, three columns and three rows, "
        "and large-small-large diameters; orthographic front confirms the 3x3 layout, while the ISO and top views "
        "show the recessed center instead of a through-opening. The gross X/Z proportions, segment ratios, and hole "
        "ratios match the image-derived estimates. It is not a perfect rendered match: the local preview uses one "
        "uniform gray material, so it cannot reproduce the reference's colored panels and colored bore interiors; "
        "the ISO camera also differs from the source screenshot. This is a visual inspection of the local mesh preview, "
        "not CATIA B-Rep verification."
    ),
    "outputs": {name: str(OUT / name) for name in (
        "candidate.cadplan.json", "candidate.aimodel", "candidate.stl",
        "candidate-views.png", "candidate-front.png", "candidate-iso.png")},
}
(OUT / "candidate-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print(json.dumps({
    "validation_valid": validation.get("valid"),
    "preview_state": preview.get("state"),
    "dimensions": preview.get("dimensions"),
    "hole_checks": (preview.get("local_geometry_checks") or {}).get("through_hole_mesh_checks"),
    "views_state": views.get("state"),
    "paths": report["outputs"],
}, ensure_ascii=False, indent=2))
