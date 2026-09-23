# -*- coding: utf-8 -*-
r"""可行性验证：把 N 个孔作为**一个多边形汤**一次布尔减掉，是否既正确又快。

背景：EGT 长型材真实有 500+ 个 ⌀11 孔（30mm 等距）。逐个布尔减是 O(n²)+
（实测 64 个孔 9s），根本做不完。若"一次减一锅"成立，289 个型材就能真复刻。

判据：
  · 正确性：一次减的体积 vs 逐个减的体积，相对差 < 0.5%；
  · 速度：记录两者耗时。
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM   # noqa: E402
from ai_modeler import kernel as K      # noqa: E402


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


def cyl_polys(cx, cy, d, height=200.0, seg=16):
    return K.extrude_profile(
        [(cx + d / 2.0 * __import__("math").cos(2 * 3.141592653589793 * i / seg),
          cy + d / 2.0 * __import__("math").sin(2 * 3.141592653589793 * i / seg))
         for i in range(seg)],
        height, (0.0, 0.0, -height / 2.0))


def main():
    L, W, H = 4000.0, 80.0, 80.0
    box = K.make_box(L, W, H)
    for n in (16, 32, 64, 128):
        # 沿长度方向每 30mm 一个 ⌀11 孔（与 EGT 真实孔阵一致）
        pts = [(-L / 2.0 + 60.0 + 30.0 * i, -20.0) for i in range(n)]
        t0 = time.time()
        soup = []
        for (x, y) in pts:
            soup.extend(cyl_polys(x, y, 11.0, height=H * 2))
        t_soup = time.time() - t0
        t0 = time.time()
        try:
            once = K.csg_subtract(list(box), soup)
            t_once = time.time() - t0
            v_once = volume(once)
        except Exception as e:
            once, t_once, v_once = None, None, None
            print("  一次减法异常: %s" % e)
        t0 = time.time()
        body = list(box)
        for (x, y) in pts:
            body = K.csg_subtract(body, cyl_polys(x, y, 11.0, height=H * 2))
        t_seq = time.time() - t0
        v_seq = volume(body)
        ok = (v_once is not None
              and abs(v_once - v_seq) <= 0.005 * max(v_seq, 1.0))
        print("孔 %4d：一次 %.2fs（%.1f mm³, %d 面） | 逐个 %6.2fs（%.1f mm³, %d 面） | %s"
              % (n, t_once or -1, v_once or -1, len(once or []),
                 t_seq, v_seq, len(body),
                 "✅ 一致" if ok else "❌ 不一致"))


if __name__ == "__main__":
    main()
