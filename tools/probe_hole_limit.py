# -*- coding: utf-8 -*-
r"""测建模器管线（不是裸内核）在**多孔**下的稳定性与耗时上限。

为什么必须用管线测：裸内核 `csg_subtract` 连减时实测会在 64 孔左右把实体切没
（体积归零/减半），而建模器管线里孔是由 `_build_holes` 生成的（切割体比材料
上下各多 10mm、24 段），两者行为不同。上限要按**真正在用的那条路径**定。

用法: python tools\probe_hole_limit.py
"""
from __future__ import annotations

import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM      # noqa: E402


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


def run(n, L=4000.0, pitch=30.0, d=11.0, seg=16, z_axis=True):
    doc = DM.Document()
    doc.add(DM.Feature('box', {'length': L, 'width': 80.0, 'height': 80.0,
                               'corner': 0.0, 'corner_mode': 'fillet',
                               'center': (0, 0, 40.0), 'base': False,
                               'axis': 'Z'}, DM.ADD))
    for i in range(n):
        x = -L / 2.0 + 60.0 + pitch * i
        doc.add(DM.Feature('hole', {'diameter': d, 'through': True,
                                    'x': x, 'y': 0.0, 'axis': 'Z',
                                    'segments': seg}, DM.CUT))
    t0 = time.time()
    body = doc.build(deadline=None)
    dt = time.time() - t0
    v = volume(body)
    want = L * 80.0 * 80.0 - n * math.pi * (d / 2.0) ** 2 * 80.0
    err = abs(v - want) / want * 100.0
    return dt, v, want, err, len(body)


def main():
    print("%5s %8s %14s %14s %8s %7s  %s"
          % ("孔数", "耗时s", "体积", "期望体积", "误差%", "面数", "判定"))
    for n in (16, 48, 96, 192, 384):
        try:
            dt, v, want, err, nf = run(n)
        except Exception as e:
            print("%5d 异常: %s" % (n, e))
            continue
        ok = err < 1.0
        print("%5d %8.2f %14.0f %14.0f %8.2f %7d  %s"
              % (n, dt, v, want, err, nf, "✅ 稳定" if ok else "❌ 体积不对"))
        if not ok or dt > 120:
            print("  → 到此为止（超过稳定边界或太慢）")
            break


if __name__ == "__main__":
    main()
