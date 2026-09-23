# -*- coding: utf-8 -*-
r"""
内六角螺钉几何自检：确认建出来的**真的是内六角圆柱头螺钉**，不是个圆柱。

为什么单独做这个：`零件参数\螺钉\MyBoltLibrary.xlsx` 里 110 行全是
"内六角螺栓-带弹垫"（M5/M6/M8/M10/M12）。标准件的"真实样子"由
ISO 4762 / GB/T 70.1 定义，所以可以**逐项量出来核对**：

  ① 头部是**圆柱**（外径 = dk），不是六棱柱；
  ② 头端面有**内六角孔**：在头部高度上做水平切片，量孔的外接圆直径 = 2s/√3；
  ③ 杆径 = d；总高 = L + k（头在下、杆在上）。
  ④ 内六角孔的**深度**约为 t（在头表面往下 t 处，孔应仍然存在；再深就没有了）。

用项目自带的 meshslice 切片量，不靠"看着像"。

用法：python tools\check_bolt_geometry.py
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import kernel as K          # noqa: E402
from ai_modeler import meshslice as MS      # noqa: E402
from ai_modeler import std_bolt as SB       # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def ring_outer_diameter(polys, z):
    """在高度 z 处做水平切片，返回最外圈半径（取所有交点到轴的距离）。"""
    pts = MS.slice_horizontal(polys, z)
    if not pts:
        return None
    return max(math.hypot(x, y) for (x, y) in pts)


def socket_width(polys, z, head_d):
    """量内六角孔在该高度上的**对角宽度**（内轮廓的外接圆直径）。

    做法：水平切片上，属于内孔轮廓的点到轴的距离远小于 head_d/2；
    取这些点里最大的距离 ×2 = 内孔外接圆直径。
    """
    pts = MS.slice_horizontal(polys, z)
    if not pts:
        return None
    inner = [math.hypot(x, y) for (x, y) in pts
             if math.hypot(x, y) < head_d / 2.0 * 0.9]
    if not inner:
        return None
    return 2.0 * max(inner)


def measure_socket_depth(polys, head_d, head_h, tol=0.02):
    """二分法量出内六角孔的**实际深度**（头表面 z=0 往下）。

    不靠"在某个猜测高度找孔"—— 早先我在 z=4.5 处找 M5 的孔（深只有 2.5），
    当然是实心，于是误判成"孔深不对"。改为直接量边界。
    """
    lo, hi = 0.0, float(head_h)
    if socket_width(polys, max(0.02, lo + 0.02), head_d) is None:
        return None                      # 头表面附近就没有孔
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if socket_width(polys, mid, head_d) is not None:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return lo


def main():
    print("内六角圆柱头螺钉几何自检（量出来，不靠目视）")
    specs = ["M5", "M6", "M8", "M10", "M12"]
    for sp in specs:
        s = SB.spec_of(sp, 30)
        print("\n=== %s ===" % SB.summary(s))
        d, dk, k, hs, ht = (s["thread_d"], s["head_d"], s["head_h"],
                            s["hex_s"], s["hex_t"])
        polys = K.make_socket_head_bolt(d, 30.0, dk, k, hs, ht, (0, 0, 0), 0, 48)
        bb = K.bbox(polys)
        sx, sy, sz = bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]

        chk(abs(sx - dk) < 0.15 and abs(sy - dk) < 0.15,
            "头部是**圆柱**：外径 %.2f×%.2f（期望 ⌀%g）" % (sx, sy, dk))
        chk(abs(sz - (30.0 + k)) < 0.15,
            "总高 %.2f（期望 杆30 + 头%g = %g）" % (sz, k, 30.0 + k))

        # 头部中截面：应能量到内六角孔，且外接圆直径 = 2s/√3
        z_mid = k / 4.0
        ow = socket_width(polys, z_mid, dk)
        want = 2.0 * hs / math.sqrt(3.0)
        chk(ow is not None, "头部截面量到内六角孔轮廓")
        if ow is not None:
            chk(abs(ow - want) < 0.25,
                "内六角对角宽度 %.2f（期望 2s/√3 = %.2f，s=%g）" % (ow, want, hs))

        # 孔深：**二分法量出来**，与标准值 t 比
        depth = measure_socket_depth(polys, dk, k)
        chk(depth is not None, "头部确实开了内六角孔（不是实心圆柱头）")
        if depth is not None:
            chk(abs(depth - ht) < 0.15,
                "内六角孔深 %.2f（期望 t=%g）" % (depth, ht))
            # 再确认：孔底往下应是实心
            chk(socket_width(polys, min(k - 0.05, depth + 0.5), dk) is None,
                "孔底往下为实心（%.2f 处已无孔）" % min(k - 0.05, depth + 0.5))

        # 杆：头部以上量半径，应 = d/2
        r_shank = ring_outer_diameter(polys, k + 10.0)
        chk(r_shank is not None and abs(2 * r_shank - d) < 0.1,
            "杆径 %.2f（期望 %g）" % ((2 * r_shank) if r_shank else -1, d))

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
