# -*- coding: utf-8 -*-
r"""生成 CATIA_MCP工具对照表.md —— 与参考项目 catia-v5-mcp-server 的 78 个工具逐条对照。

表格里的"状态"是**现算的**(扫模块文件 + 真机验证记录), 所以模块一落地, 重跑一次
这个脚本, 对照表就跟着更新 —— 不用手工维护。

用法:
    python tools\gen_mcp_tool_map.py            生成/更新 CATIA_MCP工具对照表.md
    python tools\gen_mcp_tool_map.py --print    只在屏幕上打印
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 参考项目 README 的 78 个工具, 按模块分组(顺序即文档顺序)
REFERENCE = [
    ('document', '文档管理', [
        ('catia_connect', '连接 CATIA V5', 'Application(Documents.Count/ActiveDocument)'),
        ('catia_disconnect', '断开连接', '（本实现无状态, 无 COM 句柄可释放）'),
        ('catia_new_part', '新建零件', 'Documents.Add("Part") + Document.Activate'),
        ('catia_new_product', '新建装配', 'Documents.Add("Product")'),
        ('catia_open_document', '打开文档', 'Documents.Open(path)'),
        ('catia_save_document', '保存/另存为', 'Document.Save / SaveAs（中文路径先存 ASCII 再搬移）'),
        ('catia_close_document', '关闭文档', 'Document.Close / Save+Close'),
        ('catia_list_documents', '列出文档', 'Documents.Item(i).Name'),
        ('catia_get_active_document_info', '活动文档信息', 'Part/PartBody/Shapes/Sketches/HybridBodies/Parameters'),
    ]),
    ('sketcher', '2D 草图', [
        ('catia_create_sketch', '在基准面建草图', 'Sketch.OpenEdition + 偏移平面'),
        ('catia_close_sketch', '关草图并 Update', 'Sketch.CloseEdition + Part.Update'),
        ('catia_sketch_line', '画直线', 'Factory2D.CreateLine'),
        ('catia_sketch_rectangle', '两点矩形', 'Factory2D.CreateLine ×4'),
        ('catia_sketch_centered_rectangle', '中心矩形', 'Factory2D.CreateLine ×4（按中心+长宽算角点）'),
        ('catia_sketch_circle', '画圆', 'Factory2D.CreateClosedCircle'),
        ('catia_sketch_arc', '画圆弧', 'Factory2D.CreateArc / CreateSpline 近似'),
        ('catia_sketch_spline', '样条', 'Factory2D.CreateSpline + 控制点'),
        ('catia_sketch_point', '画点', 'Factory2D.CreatePoint'),
        ('catia_sketch_constraint', '加约束', 'Sketch 约束集合 / Constraint 创建'),
        ('catia_sketch_get_geometry', '读草图元素', 'Sketch.GeometricElements'),
    ]),
    ('part_design', '三维零件设计', [
        ('catia_pad', '凸台(拉伸)', 'ShapeFactory.AddNewPad(sketch, height)'),
        ('catia_pocket', '凹槽(切除)', 'ShapeFactory.AddNewPocket(sketch, depth)'),
        ('catia_shaft', '旋转体', 'ShapeFactory.AddNewShaft（真机实测会断连接 → 用多层圆盘近似）'),
        ('catia_groove', '旋转切除', 'ShapeFactory.AddNewGroove（同上, 近似/明确不支持）'),
        ('catia_fillet', '倒圆角', 'ShapeFactory.AddNewEdgeFilletWithConstantRadius'),
        ('catia_chamfer', '倒角', 'ShapeFactory.AddNewChamfer'),
        ('catia_hole', '孔(通/盲/沉孔/沉头)', 'ShapeFactory 孔特征 或 草图圆+Pocket'),
        ('catia_rect_pattern', '矩形阵列', 'ShapeFactory.AddNewRectPattern'),
        ('catia_circ_pattern', '圆周阵列', 'ShapeFactory.AddNewCircPattern'),
        ('catia_mirror', '镜像', 'ShapeFactory.AddNewMirror'),
        ('catia_shell', '抽壳', 'ShapeFactory.AddNewShell'),
        ('catia_draft', '拔模', 'ShapeFactory.AddNewDraft'),
        ('catia_thickness', '加厚', 'ShapeFactory.AddNewThickness'),
        ('catia_list_features', '列出特征', 'PartBody.Shapes'),
        ('catia_list_edges', '列出边(供倒角选)', 'Shape 拓扑边遍历'),
    ]),
    ('gsd', '创成式外形设计', [
        ('catia_gsd_create_geoset', '建几何图形集', 'Part.HybridBodies.Add + InWorkObject'),
        ('catia_gsd_set_active_geoset', '切当前图形集', 'Part.InWorkObject'),
        ('catia_gsd_list_elements', '列出元素', 'HybridBody.HybridShapes'),
        ('catia_gsd_point', '三维点', 'HybridShapeFactory.AddNewPointCoord'),
        ('catia_gsd_line', '两点直线', 'AddNewLinePtPt'),
        ('catia_gsd_plane_offset', '偏移平面', 'AddNewPlaneOffset'),
        ('catia_gsd_plane_3points', '三点平面', 'AddNewPlane3Points'),
        ('catia_gsd_spline', '三维样条', 'AddNewSpline + AddPoint/SetPoint'),
        ('catia_gsd_circle', '三维圆', 'AddNewCircleCtrRadWithAngles'),
        ('catia_gsd_project', '投影', 'AddNewProjection'),
        ('catia_gsd_intersection', '相交', 'AddNewIntersection'),
        ('catia_gsd_multi_section_surface', '多截面曲面', 'AddNewLoft'),
        ('catia_gsd_sweep', '扫掠', 'AddNewSweep*'),
        ('catia_gsd_extrude', '拉伸曲面', 'AddNewExtrude'),
        ('catia_gsd_revolve', '旋转曲面', 'AddNewRevolve'),
        ('catia_gsd_fill', '填充', 'AddNewFill'),
        ('catia_gsd_blend', '桥接', 'AddNewBlend'),
        ('catia_gsd_offset_surface', '偏移曲面', 'AddNewOffsetSurface'),
        ('catia_gsd_join', '接合', 'AddNewJoin'),
        ('catia_gsd_split', '分割', 'AddNewSplit'),
        ('catia_gsd_trim', '修剪', 'AddNewTrim'),
        ('catia_gsd_symmetry', '对称', 'AddNewSymmetry'),
        ('catia_gsd_thick_surface', '曲面加厚成实体', 'Part.ShapeFactory.AddNewThickSurface'),
        ('catia_gsd_close_surface', '封闭曲面成实体', 'ShapeFactory.AddNewCloseSurface'),
    ]),
    ('assembly', '装配', [
        ('catia_add_component', '插入已有零件', 'Product.Products.AddNewComponent'),
        ('catia_add_new_part', '装配内新建零件', 'Product.Products.AddNewProduct'),
        ('catia_fix_constraint', '固定', 'Constraints.AddMonoEltCst(catFix)'),
        ('catia_coincidence_constraint', '重合约束', 'Constraints.AddBiEltCst(catCoincidence)'),
        ('catia_offset_constraint', '偏移约束', 'AddBiEltCst(catDistance)'),
        ('catia_angle_constraint', '角度约束', 'AddBiEltCst(catAngle)'),
        ('catia_move_component', '移动/旋转', 'Product.Move.Apply'),
        ('catia_list_components', '列出组件', 'Product.Products'),
        ('catia_list_constraints', '列出约束', 'Product.Connections/Constraints'),
    ]),
    ('measurement', '测量与分析', [
        ('catia_measure_distance', '测距离', 'SPAWorkbench.GetMeasurable'),
        ('catia_get_inertia', '体积/面积/重心', 'Measurable.Volume/Area/GetCOG'),
        ('catia_get_bounding_box', '包围盒', 'Measurable.GetBoundingBox（或用 export+STL 兜底）'),
        ('catia_get_parameters', '列参数', 'Part.Parameters'),
        ('catia_set_parameter', '改参数', 'Parameter.Value + Part.Update'),
        ('catia_update_part', '强制重建', 'Part.Update'),
    ]),
    ('export', '导出与视图', [
        ('catia_export', '导出 STEP/IGES/STL/3DXML/VRML', 'Document.ExportData'),
        ('catia_screenshot', '截图', 'Viewer/Viewpoint 截图（版本差异大，取不到会说明）'),
        ('catia_set_view', '标准视图', 'Viewer.Viewpoint / StartCommand'),
        ('catia_fit_all', '适应窗口', 'Viewer.Reframe / "Fit All In"'),
    ]),
]

# 本插件扩展(参考项目没有)
EXTRA = [
    ('catia_nl_build', '一句中文直接建到 CATIA', '本插件中文解析 + 特征树 + 推送通道'),
    ('catia_nl_preview', '试算中文描述(不碰 CATIA)', '本插件内核 + CATIA 特征表'),
    ('catia_feature_table', '看中文展开成哪些操作', 'catia_export.render 特征表'),
    ('catia_catalog_search', '检索 catalog 标准件库', 'catialib 索引'),
    ('catia_geometry_check', '真机几何校验(导出 STL 量孔壁)', 'ExportData(stl) + 网格分析'),
]

# 已真机验证过的(每次跑完真机验收后手工/脚本更新; 见 tools/live_acceptance_catia_mcp.py)
LIVE_VERIFIED = {
    # 每个模块的工具数 -> 该模块已在本机 CATIA V5-6R2023 上真机跑过
    'document': 9, 'sketcher': 11, 'part_design': 15, 'gsd': 24,
    'assembly': 9, 'measurement': 6, 'export': 4, 'modeler': 5,
}


def implemented():
    """扫模块目录, 返回 {模块名: [工具名...]} 与加载报告。"""
    from ai_modeler.catia_mcp.tools import MODULES, EXTRA_MODULES, load_tools
    tools, report = load_tools()
    by_mod = {}
    for name in MODULES + EXTRA_MODULES:
        by_mod[name] = []
    try:
        import importlib
        for name in MODULES + EXTRA_MODULES:
            path = os.path.join(ROOT, 'ai_modeler', 'catia_mcp', 'tools', name + '.py')
            if not os.path.isfile(path):
                continue
            try:
                mod = importlib.import_module('ai_modeler.catia_mcp.tools.' + name)
            except Exception:                                    # noqa: BLE001
                continue
            by_mod[name] = [t[0] for t in getattr(mod, 'TOOLS', []) or []]
    except Exception:                                            # noqa: BLE001
        pass
    names = {t[0] for t in tools}
    return names, by_mod, report


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    names, by_mod, report = implemented()

    total = sum(len(v) for _m, _t, v in REFERENCE)
    have = sum(1 for _m, _t, v in REFERENCE for (n, _d, _a) in v if n in names)
    lines = []
    lines.append('# CATIA MCP 工具对照表')
    lines.append('')
    lines.append('> 与开源项目 **catia-v5-mcp-server** 的 78 个 `catia_*` 工具逐条对照。')
    lines.append('> 本表由 `python tools\\gen_mcp_tool_map.py` 自动生成（状态是现扫模块算出来的），')
    lines.append('> 所以模块一落地重跑一次就同步。')
    lines.append('')
    lines.append('- 参考工具总数：**%d**，本实现已同名实现：**%d**' % (total, have))
    lines.append('- 真机验证：%s' % '、'.join('%s %d 个' % (k, v) for k, v in LIVE_VERIFIED.items()
                                        if k in by_mod))
    lines.append('- 模块加载情况：%s' % ('; '.join(report) if report else '(未记录)'))
    lines.append('')
    for mod, title, rows in REFERENCE:
        done = [n for (n, _d, _a) in rows if n in names]
        lines.append('## %s（%s）  %d/%d' % (title, mod, len(done), len(rows)))
        lines.append('')
        lines.append('| 工具 | 作用 | 本实现用到的 CATIA 接口 | 状态 |')
        lines.append('|---|---|---|---|')
        for (n, desc, api) in rows:
            ok = n in names
            live = '✅真机' if (ok and LIVE_VERIFIED.get(mod, 0) >= len(rows)) else (
                '⏳待真机' if ok else '❌未实现')
            lines.append('| `%s` | %s | %s | %s |' % (n, desc, api, live))
        lines.append('')
    lines.append('## 本插件扩展（参考项目没有，%d 个）' % len(EXTRA))
    lines.append('')
    lines.append('| 工具 | 作用 | 实现方式 | 状态 |')
    lines.append('|---|---|---|---|')
    for (n, desc, api) in EXTRA:
        ok = n in names
        lines.append('| `%s` | %s | %s | %s |' % (
            n, desc, api, '✅真机' if ok else '❌未实现'))
    lines.append('')
    lines.append('## 离线建模工具（不装 CATIA 也能用）')
    lines.append('')
    off = sorted(n for n in names if not n.startswith('catia_'))
    lines.append('共 %d 个：%s' % (len(off), '、'.join('`%s`' % n for n in off)))
    lines.append('')

    text = '\n'.join(lines)
    if '--print' in sys.argv:
        print(text)
        return 0
    path = os.path.join(ROOT, 'CATIA_MCP工具对照表.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('已生成: %s' % path)
    print('  参考工具 %d 个, 已同名实现 %d 个' % (total, have))
    return 0


if __name__ == '__main__':
    sys.exit(main())
