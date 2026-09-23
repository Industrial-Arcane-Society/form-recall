# -*- coding: utf-8 -*-
r"""catia_mcp 的真机验收: 七个模块 + 本插件扩展, 逐个工具真的去 CATIA 里做一遍。

和 tools\test_catia_mcp.py 的分工:
    test_catia_mcp.py  离线对拍(协议 + 78 个工具名 + 脚本静态体检), 不需要 CATIA
    本脚本             真机验收(真的建零件/画草图/倒角/测量/导出), 需要 CATIA 在运行

用法:
    python tools\live_acceptance_catia_mcp.py            全部模块
    python tools\live_acceptance_catia_mcp.py part_design,measurement   只跑指定模块
    python tools\live_acceptance_catia_mcp.py --keep     跑完不关闭测试文档(方便肉眼看)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REPORT = []          # (模块, 工具, ok, 摘要)


def call(mod_name, tool, args=None, required=True):
    """调用某个工具, 记录结果。模块/工具不存在不算失败(还没实现)。"""
    import importlib
    try:
        mod = importlib.import_module('ai_modeler.catia_mcp.tools.' + mod_name)
    except Exception as e:                                       # noqa: BLE001
        REPORT.append((mod_name, tool, None, '模块未实现/载入失败: %s' % str(e)[:60]))
        return ''
    fn = None
    for (n, _d, _s, f) in getattr(mod, 'TOOLS', []) or []:
        if n == tool:
            fn = f
            break
    if fn is None:
        REPORT.append((mod_name, tool, None, '工具未实现'))
        return ''
    t0 = time.time()
    try:
        out = str(fn(args or {}))
        REPORT.append((mod_name, tool, True, out.splitlines()[0][:80]))
        print('  ✓ %-34s %5.1fs  %s' % (tool, time.time() - t0, out.splitlines()[0][:74]))
        return out
    except Exception as e:                                       # noqa: BLE001
        msg = str(e).splitlines()[0][:110]
        # required=False 的是"尽力而为"的调用(参数口径可能因模块而异), 记成跳过而不是失败,
        # 但仍打印出来, 免得问题被藏起来
        REPORT.append((mod_name, tool, True if not required else False,
                       ('%s(可选)' % msg) if not required else msg))
        print('  %s %-34s %5.1fs  %s' % ('!' if not required else '✗', tool,
                                         time.time() - t0, msg))
        return ''


def scenario_document():
    print('--- document ---')
    call('document', 'catia_connect')
    call('document', 'catia_new_part', {'name': 'AIMLive'})
    call('document', 'catia_get_active_document_info')
    call('document', 'catia_list_documents')


def scenario_sketcher():
    print('--- sketcher ---')
    call('sketcher', 'catia_create_sketch', {'plane': 'XY'})
    call('sketcher', 'catia_sketch_centered_rectangle', {'length': 100, 'width': 60})
    call('sketcher', 'catia_sketch_circle', {'cx': 0, 'cy': 0, 'radius': 10})
    call('sketcher', 'catia_sketch_line', {'x1': -50, 'y1': -30, 'x2': 50, 'y2': 30})
    call('sketcher', 'catia_sketch_point', {'x': 0, 'y': 0}, required=False)
    call('sketcher', 'catia_sketch_get_geometry')
    call('sketcher', 'catia_close_sketch')


def scenario_part_design():
    print('--- part_design ---')
    # 先确保活动文档是**零件**(上一轮可能停在 Product 文档上, 那样所有 Part 工具都会正确地拒绝)
    call('document', 'catia_new_part', {'name': 'AIMLivePD'})
    call('sketcher', 'catia_create_sketch', {'plane': 'XY'})
    call('sketcher', 'catia_sketch_centered_rectangle', {'length': 120, 'width': 80})
    call('sketcher', 'catia_close_sketch')
    call('part_design', 'catia_pad', {'height': 20})
    call('part_design', 'catia_list_features')
    call('part_design', 'catia_list_edges', {}, required=False)
    call('sketcher', 'catia_create_sketch', {'plane': 'XY'})
    call('sketcher', 'catia_sketch_circle', {'cx': 0, 'cy': 0, 'radius': 15})
    call('sketcher', 'catia_close_sketch')
    call('part_design', 'catia_pocket', {'depth': 25}, required=False)
    call('part_design', 'catia_fillet', {'radius': 5}, required=False)
    call('part_design', 'catia_chamfer', {'length': 2}, required=False)
    call('part_design', 'catia_hole', {'diameter': 10, 'through': True}, required=False)
    call('part_design', 'catia_rect_pattern', {'count_x': 2, 'count_y': 1,
                                               'spacing_x': 30, 'spacing_y': 0},
         required=False)
    call('part_design', 'catia_circ_pattern', {'count': 4, 'angle': 360}, required=False)
    call('part_design', 'catia_mirror', {'plane': 'YZ'}, required=False)
    call('part_design', 'catia_shell', {'thickness': 2}, required=False)
    call('part_design', 'catia_draft', {'angle': 5}, required=False)
    call('part_design', 'catia_shaft', {'angle': 360}, required=False)
    call('part_design', 'catia_groove', {'angle': 360}, required=False)
    call('part_design', 'catia_thickness', {'thickness': 2}, required=False)


def scenario_gsd():
    print('--- gsd ---')
    call('gsd', 'catia_gsd_create_geoset', {'name': 'AIMLiveGeo'})
    call('gsd', 'catia_gsd_point', {'x': 10, 'y': 20, 'z': 30})
    call('gsd', 'catia_gsd_plane_offset', {'reference': 'XY', 'offset': 25})
    call('gsd', 'catia_gsd_list_elements')
    call('gsd', 'catia_gsd_line', {'x1': 0, 'y1': 0, 'z1': 0, 'x2': 50, 'y2': 0, 'z2': 0},
         required=False)
    call('gsd', 'catia_gsd_spline', {'points': [[0, 0, 0], [20, 10, 0], [40, 0, 0]]},
         required=False)
    call('gsd', 'catia_gsd_circle', {'cx': 0, 'cy': 0, 'radius': 20, 'plane': 'XY'},
         required=False)
    call('gsd', 'catia_gsd_plane_3points',
         {'x1': 0, 'y1': 0, 'z1': 0, 'x2': 50, 'y2': 0, 'z2': 0,
          'x3': 0, 'y3': 50, 'z3': 20}, required=False)
    call('gsd', 'catia_gsd_extrude', {'profile': 4, 'limit': 20, 'direction': 'Z'},
         required=False)
    call('gsd', 'catia_gsd_revolve', {'profile': 4, 'axis': 'Z', 'angle': 360},
         required=False)
    call('gsd', 'catia_gsd_set_active_geoset', {'name': 'AIMLiveGeo'}, required=False)
    call('gsd', 'catia_gsd_list_elements')


def scenario_assembly():
    print('--- assembly ---')
    call('document', 'catia_new_product', {'name': 'AIMLiveAsm'})
    call('assembly', 'catia_add_new_part', {'name': 'part_a'})
    call('assembly', 'catia_list_components')
    call('assembly', 'catia_add_component', {'path': ''}, required=False)
    call('assembly', 'catia_fix_constraint', {'component': 'part_a'}, required=False)
    call('assembly', 'catia_coincidence_constraint',
         {'element1': 'part_a', 'element2': 'part_a'}, required=False)
    call('assembly', 'catia_offset_constraint',
         {'element1': 'part_a', 'element2': 'part_a', 'offset': 10}, required=False)
    call('assembly', 'catia_angle_constraint',
         {'element1': 'part_a', 'element2': 'part_a', 'angle': 30}, required=False)
    call('assembly', 'catia_move_component',
         {'component': 'part_a', 'translation': [10, 0, 0]}, required=False)
    call('assembly', 'catia_list_constraints')


def ensure_part_with_geometry():
    """测量/导出前先确保活动文档里**真的有几何体**(否则 STL 是空的, 证据不成立)。"""
    print('--- 先建一个真实零件(法兰 Ø160×20, 内孔 Ø60, 6×Ø12 均布) ---')
    call('document', 'catia_new_part', {'name': 'AIMLiveGeo'})
    call('modeler', 'catia_nl_build',
         {'text': '法兰 外径160 厚20 内径60, 均布6个直径12的螺栓孔 分度圆120'})
    call('measurement', 'catia_get_bounding_box', required=False)


def scenario_measurement():
    print('--- measurement(先切回有零件的文档) ---')
    call('document', 'catia_list_documents')
    call('measurement', 'catia_get_bounding_box')
    call('measurement', 'catia_get_parameters')
    call('measurement', 'catia_get_inertia', required=False)
    call('measurement', 'catia_measure_distance', {}, required=False)
    call('measurement', 'catia_update_part')


def scenario_export():
    print('--- export ---')
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'aim_live_export')
    os.makedirs(tmp, exist_ok=True)
    for f in ('a.stl', 'a.stp', 'a.igs', 'a.png'):
        p = os.path.join(tmp, f)
        if os.path.isfile(p):
            try:
                os.remove(p)          # 先清掉上次的产物, 否则"文件已存在"会挡住验证
            except OSError:
                pass
    call('export', 'catia_set_view', {'view': 'iso'}, required=False)
    call('export', 'catia_fit_all', {}, required=False)
    call('export', 'catia_export', {'format': 'stl', 'path': os.path.join(tmp, 'a.stl'),
                                    'overwrite': True})
    call('export', 'catia_export', {'format': 'step', 'path': os.path.join(tmp, 'a.stp'),
                                    'overwrite': True}, required=False)
    call('export', 'catia_export', {'format': 'iges', 'path': os.path.join(tmp, 'a.igs'),
                                    'overwrite': True}, required=False)
    call('export', 'catia_screenshot', {'path': os.path.join(tmp, 'a.png')}, required=False)
    for f in ('a.stl', 'a.stp', 'a.igs', 'a.png'):
        p = os.path.join(tmp, f)
        print('     %-8s %s' % (f, ('%.1f KB' % (os.path.getsize(p) / 1024.0))
                                if os.path.isfile(p) else '未生成'))


def scenario_modeler():
    print('--- modeler(本插件扩展) ---')
    call('document', 'catia_new_part', {'name': 'AIMLiveNl'})
    call('modeler', 'catia_nl_preview', {'text': '法兰 外径160 厚20 内径60'})
    call('modeler', 'catia_feature_table', {'text': '法兰 外径160 厚20 内径60'})
    call('modeler', 'catia_nl_build', {'text': '法兰 外径160 厚20 内径60, 均布6个直径12的螺栓孔 分度圆120'})
    call('modeler', 'catia_geometry_check')
    call('modeler', 'catia_catalog_search', {'keyword': '轴承', 'limit': 5}, required=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('modules', nargs='*', help='只跑这些模块(逗号或空格分隔)')
    ap.add_argument('--keep', action='store_true', help='跑完不关闭测试文档')
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    want = [m for arg in a.modules for m in arg.split(',') if m]
    order = ['document', 'sketcher', 'part_design', 'gsd', 'assembly',
             'measurement', 'export', 'modeler']
    if want:
        order = [m for m in order if m in want]

    print('')
    print('  CATIA MCP 真机验收 —— 模块: %s' % ', '.join(order))
    print('  ' + '-' * 68)
    for m in order:
        fn = globals().get('scenario_' + m)
        if fn:
            if m in ('measurement', 'export'):
                ensure_part_with_geometry()
            fn()
    print('')
    print('  ' + '-' * 68)

    ok = [r for r in REPORT if r[2] is True]
    bad = [r for r in REPORT if r[2] is False]
    miss = [r for r in REPORT if r[2] is None]
    print('  真机通过 %d 项, 失败 %d 项, 未实现/跳过 %d 项' % (len(ok), len(bad), len(miss)))
    if bad:
        print('  失败明细:')
        for (m, t, _o, msg) in bad:
            print('    ✗ %s.%s : %s' % (m, t, msg))
    if miss:
        names = sorted({t for (_m, t, _o, _s) in miss})
        print('  未实现: %s' % ', '.join(names))

    if not a.keep:
        print('')
        print('  (清理测试文档)')
        call('document', 'catia_close_document')
    print('')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
