import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from ai_modeler.core import exporters
from ai_modeler.sdk import cadplan


OUT = Path(__file__).resolve().parent


def estimate(evidence, note="按图比例估算，不是制造标注。"):
    return {"type": "image_estimate", "evidence": evidence, "note": note}


def derived(evidence):
    return {"type": "derived", "evidence": evidence}


features = [
    {
        "id": "outer-envelope", "kind": "box", "operation": "add",
        "name": "连续外壳主体",
        "note": "按正视图估计宽高，按等轴图估计深度；先建连续厚实体。",
        "parameters": {"length": 322, "width": 122, "height": 340,
                       "center": [0, 0, 170]},
        "sources": {
            "length": estimate("正视图外轮廓约 322 px，按 340 mm 高度归一后估算 322 mm。"),
            "width": estimate("等轴图可见厚度约为高度的 0.36，取 122 mm。"),
            "height": estimate("正视图总高作为名义尺度，取 340 mm。"),
            "center": derived("X/Y 居中，底面置于 Z=0。"),
        },
    },
    {
        "id": "center-recess", "kind": "box", "operation": "cut",
        "name": "中央贯通高度的后退槽",
        "note": "正面中段后退约 20 mm；切除体超出前表面和上下边各 1 mm，避免布尔共面。",
        "parameters": {"length": 68, "width": 21, "height": 342,
                       "center": [0, 51.5, 170]},
        "sources": {
            "length": estimate("正视图中央区域约为总宽的 68/322。"),
            "width": estimate("可见等轴图前后退台估为 20 mm，另加 1 mm 布尔越界。",
                              "有效槽深估 20 mm；参数含 1 mm 越界。"),
            "height": estimate("槽沿主体全高贯通上下边，参数含上下各 1 mm 越界。"),
            "center": derived("主体前表面 Y=61；切除范围 Y=41..62，形成 Y=41 的后退面。"),
        },
    },
]

for panel, x in (("left", -97.5), ("center", 0), ("right", 97.5)):
    front_y = 41 if panel == "center" else 61
    for row, z, diameter in (("top", 276, 46), ("middle", 170, 31), ("bottom", 64, 46)):
        features.append({
            "id": f"{panel}-hole-{row}", "kind": "hole", "operation": "cut",
            "name": f"{panel} {row} through hole",
            "parameters": {"diameter": diameter, "through": True, "pos": "point",
                           "point": [x, front_y, z], "axis": "Y"},
            "sources": {
                "diameter": estimate("正视图孔径与对应面板宽度的比例；上/下为大孔，中排为小孔。"),
                "point": estimate("按正视图孔心行列比例定位；中央孔从后退面 Y=41 起钻，外侧孔从前面 Y=61 起钻。"),
            },
        })

plan = {
    "schema_version": "1.0",
    "name": "Stepped block with nine through holes",
    "source_prompt": "依据用户同一工件的等轴测图与正视图复刻；无标注尺寸，全部名义尺寸是图像比例估值。",
    "units": {"length": "mm", "angle": "deg"},
    "coordinate_system": {"type": "global_cartesian", "handedness": "right",
                          "axes": ["X", "Y", "Z"], "origin": [0, 0, 0]},
    "features": features,
}

plan_path = OUT / "candidate.cadplan.json"
plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
validation = cadplan.validate_plan(plan)
if not validation.get("valid"):
    raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))

preview, doc, manifest, data, body = cadplan._preview_internal(plan)
if preview.get("state") != "previewed":
    raise SystemExit(json.dumps(preview, ensure_ascii=False, indent=2))

exporters.export_project(doc, str(OUT / "candidate.aimodel"), extra={
    "cadplan_schema_version": cadplan.CADPLAN_SCHEMA_VERSION,
    "cadplan": data,
    "feature_manifest": manifest,
})
exporters.export_stl(body, str(OUT / "candidate.stl"), doc.name)
iso = cadplan.preview_plan(plan, str(OUT / "candidate-iso.png"), view_set="iso")
views = cadplan.preview_plan(plan, str(OUT / "candidate-extended.png"), view_set="extended")

report = {
    "validation": validation,
    "preview": preview,
    "iso_preview": iso,
    "extended_preview": views,
    "catia_executed": False,
    "catia_invoked": False,
    "method": "single outer envelope + one subtractive full-height front recess + nine Y-axis through-holes",
}
(OUT / "candidate-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(OUT / "method-report.md").write_text("""# 结构化建模对照：连续厚实体加中央退台

- 输入：用户等轴测图和正视图。尺寸无标注，均为图像比例名义估值。
- 外形估值：322 × 122 × 340 mm；前视宽高比 0.947，厚高比 0.359。
- 主拓扑：先建完整外包络，再从正面切除宽约 68 mm、有效深约 20 mm 的中央槽；中心面因此低于左右前面，不是三面齐平。
- 孔位：左右前面和中央后退面各 3 个贯穿孔；行位距底面约 64、170、276 mm，孔径大-小-大约 46、31、46 mm，轴向 Y。
- 布尔切除体在前表面及上下各越界 1 mm，以避开内核对共面布尔的已知问题；模型有效槽深仍为 20 mm。
- 运行 CADPlan 本地校验、网格预览、AIMODEL 和 STL 导出；没有调用 CATIA。

详细尺寸、支持映射、网格检查与输出路径见 `candidate-report.json`。
""", encoding="utf-8")

print(json.dumps({
    "state": preview.get("state"),
    "valid": validation.get("valid"),
    "dimensions": preview.get("dimensions"),
    "hole_checks": (preview.get("local_geometry_checks") or {}).get("through_hole_mesh_checks"),
    "paths": [str(OUT / name) for name in (
        "candidate.cadplan.json", "candidate.aimodel", "candidate.stl",
        "candidate-iso.png", "candidate-extended.png", "candidate-report.json",
        "method-report.md")],
}, ensure_ascii=False, indent=2))
