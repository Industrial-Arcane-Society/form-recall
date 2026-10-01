# -*- coding: utf-8 -*-
r"""决定性试验：把 N 个孔合成**一个多边形汤**、只做**一次**布尔减，行不行？

背景（实测数据）：
  · 逐个减：16 孔 0.25s 稳 / 48 孔 3.9s 稳 / 96 孔 36.6s 稳 / **192 孔体积出错**；
  · EGT 长型材真实有 545 个 ⌀11 孔 → 逐个减不可行。
若"一锅切"既正确又快，长型材就能真复刻。

判据：以**逐个减在稳定区（n=48）**的结果为基准，比一锅切的体积；
再单测 n=192/384 的一锅切是否仍然正确、耗时多少。

用法: python tools\probe_soup_cut.py
"""
from __future__ import annotations

import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM      # noqa: E402
from ai_modeler import kernel as K         # noqa: E402


def volume(body):
    tot = 0.0
    for poly in body:
        vs = [v[0] if isinstance(v, (tuple, list)) and len(v) == 2 else v
              for v in (getattr(poly, "vertices", None) or [])]
        if len(vs) < 3:
            continue
        a = vs[0]
        for i in range(1, len(vs) - 1):
            b, c = vs[i], vs[i + 1]
            tot += (a[0] * (b[1] * c[2] - b[2] * c[1])
                    - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
    return abs(tot)


L, W, H = 4000.0, 80.0, 80.0


def box_doc():
    doc = DM.Document()
    doc.add(DM.Feature('box', {'length': L, 'width': W, 'height': H,
                               'corner': 0.0, 'corner_mode': 'fillet',
                               'center': (0, 0, H / 2.0), 'base': False,
                               'axis': 'Z'}, DM.ADD))
    return doc


def seq(n, seg=16):
    doc = box_doc()
    for i in range(n):
        doc.add(DM.Feature('hole', {'diameter': 11.0, 'through': True,
                                    'x': -L / 2.0 + 60.0 + 30.0 * i,
                                    'y': 0.0, 'axis': 'Z',
                                    'segments': seg}, DM.CUT))
    t0 = time.time()
    b = doc.build(deadline=None)
    return volume(b), time.time() - t0, len(b)


def soup(n, seg=16):
    """一个汤：把 n 个圆柱的多边形直接拼起来，一次减。"""
    # 与 _build_holes 的切割体一致：沿 Z 通长，上下各多 10mm
    prof_h = H + 20.0
    polys = []
    for i in range(n):
        x = -L / 2.0 + 60.0 + 30.0 * i
        pts = [(x + 5.5 * math.cos(2 * math.pi * k / seg),
                5.5 * math.sin(2 * math.pi * k / seg)) for k in range(seg)]
        polys.extend(K.extrude_profile(pts, prof_h, (0.0, 0.0, -10.0)))
    body = list(K.make_box(L, W, H))
    t0 = time.time()
    out = K.csg_subtract(body, polys)
    return volume(out), time.time() - t0, len(out)


def main():
    print("%5s %-26s %-26s %s" % ("孔数", "逐个减", "一锅切", "结论"))
    for n in (16, 48, 96, 192, 384):
        vs, ts, fs = seq(n)
        v1, t1, f1 = soup(n)
        want = L * W * H - n * math.pi * 5.5 ** 2 * H
        e_s = abs(vs - want) / want * 100
        e_1 = abs(v1 - want) / want * 100
        note = "一致" if abs(vs - v1) < 0.005 * want else "**不一致**"
        print("%5d  %10.0f mm³ %5.2fs   %10.0f mm³ %5.2fs   逐个误差%.2f%% 一锅误差%.2f%% %s"
              % (n, vs, ts, v1, t1, e_s, e_1, note))


if __name__ == "__main__":
    main()
