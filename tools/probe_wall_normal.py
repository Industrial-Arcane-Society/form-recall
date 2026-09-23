# -*- coding: utf-8 -*-
r"""把孔壁法向的两种取法 × 是否强制绕向，四种组合一次量完，用实测定死。

背景：孔壁那个四边形，法向写"朝轴心"还是"背离轴心"、以及顶点绕向要不要跟法向
一致，我改来改去 —— 因为体积时而对时而错。这里不猜，直接四种组合都跑一遍，
看哪一种能让三个用例全部对上解析值（盒体、轴向孔、侧向孔）。

判据：体积与解析值的偏差 < 1%（圆柱离散会带来约 0.5%~1% 的偏差）。
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import directmesh as D      # noqa: E402
from ai_modeler import kernel as K          # noqa: E402


def volume(polys):
    tot = 0.0
    for poly in polys:
        vs = poly.points()
        a = vs[0]
        for i in range(1, len(vs) - 1):
            b, c = vs[i], vs[i + 1]
            tot += (a[0] * (b[1] * c[2] - b[2] * c[1])
                    - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(tot)


def poly_nofix(pts, normal, tag=0):
    """不强制绕向的版本（只设 plane/normal）。"""
    n = K.v_norm(normal)
    w = K.v_dot(n, pts[0])
    return K.Polygon([(p, n) for p in pts], K.Plane(n, w), tag)


def run(inward: bool, fix_winding: bool):
    old_poly = D._poly
    src = open(os.path.join(ROOT, "ai_modeler", "directmesh.py"),
               encoding="utf-8").read()
    # 通过猴子补丁切换两个开关
    def patched_radial(out, radial):
        s = sum(out[i] * radial[i] for i in range(3))
        return (s > 0) if inward else (s < 0)
    D._poly = D._poly if fix_winding else poly_nofix

    # inward=True 时把代码里的判断改成 >0 需要改源码；这里用等价手段：
    # 直接把生成出来的四边形按需要翻转
    cases = [
        ("实心盒", 80.0, 80.0, 200.0, "Y", [], 80 * 80 * 200.0),
        ("5 个轴向孔", 80.0, 80.0, 200.0, "Y",
         [{"d": 11.0, "axis": "Y", "u": 30.0 * (i % 3 - 1),
           "v": 30.0 * (i // 3 - 1), "through": True} for i in range(5)],
         80 * 80 * 200.0 - 5 * math.pi * 5.5 ** 2 * 200.0),
        ("1 个侧向孔", 80.0, 80.0, 120.0, "Y",
         [{"d": 11.0, "axis": "X", "u": 0.0, "v": 0.0, "through": True}],
         80 * 80 * 120.0 - math.pi * 5.5 ** 2 * 80.0),
    ]
    rows = []
    for (name, A, B, L, ax, holes, want) in cases:
        polys = D.extrude_with_holes(A, B, L, axis=ax, base=-L / 2.0,
                                     holes=holes, seg=24)
        if not inward:
            # 把四边形的存储法向整体反向（模拟"背离轴心"那一版）
            for p in polys:
                if len(p.points()) == 4:
                    p.flip()
        v = volume(polys)
        rows.append((name, v, want, abs(v - want) / want * 100.0))
    D._poly = old_poly
    return rows


def main():
    print("%-14s %-12s %-14s %s" % ("孔壁法向", "强制绕向", "用例", "偏差%"))
    best = None
    for inward in (True, False):
        for fix in (True, False):
            rows = run(inward, fix)
            worst = max(r[3] for r in rows)
            tag = "%s / %s" % ("朝轴心" if inward else "背离轴心",
                               "是" if fix else "否")
            for (name, v, want, e) in rows:
                print("%-14s %-12s %-14s %8.2f%%  (%.0f / %.0f)"
                      % (tag, "", name, e, v, want))
            if best is None or worst < best[0]:
                best = (worst, tag)
            print("   → 最差 %.2f%%" % worst)
            print()
    print("最佳组合：%s（最差偏差 %.2f%%）" % (best[1], best[0]))


if __name__ == "__main__":
    main()
