# -*- coding: utf-8 -*-
r"""几何真机校验: 把模型建到 CATIA 里, 再导出 STL 量出真实几何。

为什么需要它:
    有些问题**不会报错**。最典型的一次: CATIA 里"凸台朝 +Z 长、凹槽朝 -Z 切"，
    导出器把切除草图放在材料下方 -> 每个孔都在空切。日志全是 ok、操作数也对，
    但模型上根本没有孔。光看"成功/失败"是抓不到的, 必须量几何。

    第二类坑是**判据本身错**(我踩过, 记在这里免得再犯):
      · "离轴最近顶点 ≈ 孔半径" —— 凹面(三角转子等)的端面三角化会在孔内部留下
        内部点, 于是"最近"可以远小于孔半径, 明明切了却判成没切。
      · "半径按原点量" —— 零件的绘制中心不一定在原点。三角转子的绘制中心是它的
        包围盒中心 (0, 5.32), 孔在 (0, 5.32) 处; 按原点量半径当然量不到孔壁,
        于是又一次把"切好的孔"判成"没切"。
    所以现在:**用导出器自己的切除记录里 (cx, cy, r) 当基准**, 逐个切除验证
    "存在一圈半径≈r、绕 (cx,cy)、且沿 z 有实际高度的孔壁顶点"。

做法:
    1. 用给定描述建模并推送到 CATIA
    2. 让 CATIA 把该零件导出成 STL (ASCII)
    3. 在 Python 里分析网格: 包围盒 + 每个圆形切除的孔壁是否存在
    4. 有凹槽/矩形切除时, 顺带核对材料包围盒

用法:
    python tools\check_catia_solid.py                        默认查齿轮中心孔
    python tools\check_catia_solid.py "法兰 外径160 厚20 内径60"
    python tools\check_catia_solid.py "…" --zrange 0 20
    python tools\check_catia_solid.py "…" --tol 0.8          放宽孔壁半径容差

参数:
    --zrange A B  期望网格 z 范围 (用于确认模型高度/位置)
    --tol V       孔壁半径容差, 默认 0.6 mm
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from ai_modeler import catia, catia_export as CE, nlparser  # noqa: E402

DEFAULT = '齿轮 模数3 齿数24 厚20, 中心孔直径20'


def export_active_stl(path):
    vbs = os.path.join(tempfile.gettempdir(), 'aim_export_stl.vbs')
    src = [
        'Dim C, d',
        'On Error Resume Next',
        'Set C = GetObject(, "CATIA.Application")',
        'If Err.Number <> 0 Then WScript.Echo "NO_CATIA": WScript.Quit 2',
        'Set d = C.ActiveDocument',
        'If Err.Number <> 0 Then WScript.Echo "NO_DOC": WScript.Quit 3',
        'd.ExportData "%s", "stl"' % path,
        'If Err.Number <> 0 Then WScript.Echo "EXPORT_FAILED " & Err.Description: WScript.Quit 4',
        'WScript.Echo "OK " & d.Name',
    ]
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write('\r\n'.join(src))
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, timeout=300)
    try:
        os.remove(vbs)
    except OSError:
        pass
    return p.stdout.decode('gbk', 'replace').strip()


def mesh_vertices(path):
    raw = open(path, 'rb').read()
    txt = raw.decode('utf-8', 'replace')
    return [tuple(float(x) for x in m) for m in
            re.findall(r'vertex\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)\s+(-?[\d.eE+]+)', txt)]


def check_hole(vs, cx, cy, r, tol):
    """孔壁判据: 存在一圈绕 (cx,cy)、半径≈r、沿 z 有实际高度的顶点。

    只要真的切出圆柱面就一定有这种点; 反过来, 空切/切在别处就一定没有。
    """
    band = []
    for v in vs:
        if abs(math.hypot(v[0] - cx, v[1] - cy) - r) <= tol:
            band.append(v)
    bz = [v[2] for v in band]
    span = (max(bz) - min(bz)) if bz else 0.0
    return len(band), span


def main():
    argv = sys.argv[1:]
    skip = set()
    for opt, n in (('--tol', 1), ('--zrange', 2)):
        if opt in argv:
            i = argv.index(opt)
            skip.update(range(i, min(i + 1 + n, len(argv))))
    text = ' '.join(a for i, a in enumerate(argv) if i not in skip and not a.startswith('-'))
    text = text.strip() or DEFAULT
    want_z = None
    tol = 0.6
    if '--zrange' in argv:
        i = argv.index('--zrange')
        want_z = (float(argv[i + 1]), float(argv[i + 2]))
    if '--tol' in argv:
        tol = float(argv[argv.index('--tol') + 1])

    print('描述: %s' % text)
    ok, msg = catia.probe()
    if not ok:
        print('CATIA: 未检测到 -> %s' % msg)
        return 2

    res = nlparser.parse(text)
    if not res.ok:
        print('解析失败: %s' % res.message)
        return 3
    doc = res.doc
    doc.build()
    feats, _warns = CE.build_catia_features(doc)
    cuts = [e for e in feats if str(e['kind']).endswith('_cut')]
    holes = [e for e in cuts if e['kind'] == 'circle_cut']
    rects = [e for e in cuts if e['kind'] in ('rect_cut', 'poly_cut')]
    print('  切除记录: %s' %
          (', '.join('⌀%.1f@(%.1f,%.1f) z=%.1f 深%.1f' % (h['r'] * 2, h['cx'], h['cy'],
                                                        h['z'], h['depth']) for h in holes) or
           '(无圆形切除)'))

    ok2, msg2, _script = catia.push_to_catia(doc, save_catpart=None)
    for line in msg2.splitlines():
        print('  ' + line)
    if not ok2:
        return 4

    stl = os.path.join(tempfile.gettempdir(), 'aim_check_solid.stl')
    if os.path.exists(stl):
        os.remove(stl)
    out = export_active_stl(stl)
    print('  导出: %s' % out)
    if not os.path.isfile(stl):
        print('[失败] 没有拿到 STL')
        return 5

    vs = mesh_vertices(stl)
    if not vs:
        print('[失败] STL 里没有网格数据')
        return 6
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    print('  网格: %d 顶点   x %.1f~%.1f  y %.1f~%.1f  z %.2f~%.2f' %
          (len(vs), min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))

    bad = []
    if not cuts and '孔' in text:
        bad.append('描述里有"孔"但没有解析出任何切除, 检查解析')
    for h in holes:
        n, span = check_hole(vs, h['cx'], h['cy'], h['r'], tol)
        tag = '⌀%.1f@(%.1f,%.1f)' % (h['r'] * 2, h['cx'], h['cy'])
        print('  孔壁 %-22s 顶点 %3d 个, z 跨度 %.1f mm (期望切深 %.1f)' %
              (tag, n, span, h['depth']))
        if n < 8 or span < 1.0:
            bad.append('孔 %s 找不到孔壁(顶点 %d, z 跨度 %.1f) -> 该切除没切到材料'
                       % (tag, n, span))
        elif h['depth'] > 2.0 and span < min(h['depth'], max(zs) - min(zs)) * 0.5:
            bad.append('孔 %s 孔壁 z 跨度只有 %.1f mm, 预期约 %.1f mm -> 切深不对'
                       % (tag, span, min(h['depth'], max(zs) - min(zs))))
    for rc in rects:
        # 凹槽/键槽的判据: 槽底(切除草图平面 -Z 方向 depth 处)应该真的存在,
        # 即"凹槽footprint 内、z≈槽底"的顶点. 空切则这些点一个都没有.
        floor_z = rc['z'] - rc['depth']
        half_l = (rc.get('l') or 0) / 2.0 + 0.7
        half_w = (rc.get('w') or 0) / 2.0 + 0.7
        prof = rc.get('profile') or []
        if prof:
            # 异形(带圆角/多边形)槽: 用顶点云落在槽底高度来判
            inrect = [v for v in vs if abs(v[2] - floor_z) <= 0.7]
        else:
            inrect = [v for v in vs if abs(v[2] - floor_z) <= 0.7
                      and abs(v[0] - rc['cx']) <= half_l and abs(v[1] - rc['cy']) <= half_w]
        print('  槽底 %-22s 顶点 %3d 个 (槽底 z=%.1f)'
              % (rc.get('name') or rc['kind'], len(inrect), floor_z))
        if len(inrect) < 4:
            bad.append('凹槽「%s」在 z=%.1f 处找不到槽底顶点(%d 个) -> 没切进去'
                       % (rc.get('name') or rc['kind'], floor_z, len(inrect)))
    if want_z is not None and (abs(min(zs) - want_z[0]) > 1.0 or abs(max(zs) - want_z[1]) > 1.0):
        bad.append('z 范围 %.2f~%.2f, 期望 %.2f~%.2f' % (min(zs), max(zs), want_z[0], want_z[1]))

    print('-' * 60)
    if bad:
        for b in bad:
            print('[失败] ' + b)
        return 1
    print('[通过] 几何与预期一致')
    return 0


if __name__ == '__main__':
    sys.exit(main())
