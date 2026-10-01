# -*- coding: utf-8 -*-
r"""离线自检: 新曲轴(哑铃形曲柄臂)的网格体积/包围盒 vs 解析值。

用法: python tools\probe_kernel_crank.py
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler.core.geometry import kernel as K        # noqa: E402


def mesh_volume(polys):
    v = 0.0
    for a, b, c, _t in K.polygons_to_triangles(polys):
        v += (a[0] * (b[1] * c[2] - b[2] * c[1])
              - a[1] * (b[0] * c[2] - b[2] * c[0])
              + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return v


def shoelace(pts):
    s = 0.0
    for i in range(len(pts)):
        u0, v0 = pts[i]
        u1, v1 = pts[(i + 1) % len(pts)]
        s += u0 * v1 - u1 * v0
    return abs(s) / 2.0


def main():
    d, dp, S, n = 50.0, 42.0, 86.0, 6
    L = 700.0
    polys = K.make_crankshaft(d, dp, S, n, total_len=L, segments=32)
    vs = [q for poly in polys for q in poly.points()]
    bx = (min(p[0] for p in vs), max(p[0] for p in vs))
    by = (min(p[1] for p in vs), max(p[1] for p in vs))
    bz = (min(p[2] for p in vs), max(p[2] for p in vs))
    print('polygons=%d  vertices=%d' % (len(polys), len(vs)))
    print('X %.3f..%.3f (%.3f)' % (bx[0], bx[1], bx[1] - bx[0]))
    print('Y %.3f..%.3f (%.3f)' % (by[0], by[1], by[1] - by[0]))
    print('Z %.3f..%.3f (%.3f)' % (bz[0], bz[1], bz[1] - bz[0]))
    print('size 报告值 crankshaft_size =', K.crankshaft_size(d, dp, S, n, L))

    # 解析体积: 主轴颈 + 连杆颈 + 曲柄臂(凸包面积 x 臂宽)
    spacing = L / n
    web_w = min(max(2.0, d * 0.28), spacing * 0.25)
    pin_len = spacing * 0.42
    main_len = L - 2.0 * n * web_w - n * pin_len
    g = K.crank_cheek_geometry(d / 2.0, dp / 2.0, S / 2.0, 64)
    a_hull = shoelace(g['outline'])
    v_main = math.pi * (d / 2.0) ** 2 * main_len
    v_pin = math.pi * (dp / 2.0) ** 2 * pin_len * n
    v_web = a_hull * web_w * 2.0 * n
    print('解析: 主轴颈 %.1f + 连杆颈 %.1f + 曲柄臂 %.1f = %.1f mm^3'
          % (v_main, v_pin, v_web, v_main + v_pin + v_web))
    print('网格体积 = %.1f mm^3   (臂凸包面积 %.2f mm^2)'
          % (mesh_volume(polys), a_hull))
    return 0


if __name__ == '__main__':
    sys.exit(main())
