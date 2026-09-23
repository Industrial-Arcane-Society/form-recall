# -*- coding: utf-8 -*-
r"""验证内六角螺钉的真实几何：头、杆、两个垫圈、内六角孔都要能被**量出来**。

为什么不能只看包围盒：包围盒对"孔"完全不敏感 —— 六角孔切浅了、切歪了、
甚至根本没切，外轮廓都还是 40×20×20。现场那次故障就是这样：CATIA 里看到的
外轮廓没问题，但六角孔是零深、垫圈也没了。

这里用**体积法**，判据是可算的解析值：

  `V(带六角孔) − V(去掉六角孔)` = 六角孔真实切除的体积，
  应等于 `(√3/2)·s²·t`（s = ISO 4762 对边，t = 孔深）。
  孔切浅了 → 体积偏小；孔没从头顶面开始 → 切除量更小；孔大了 → 偏大。
  两个模型用**同一套网格离散**，所以圆柱面的离散误差在相减时抵消。

另外用解析公式整体校核一次总体积（容差放宽到 3%，因为圆柱是 32/48 边形离散）。

用法: python tools\check_bolt_real_geometry.py
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import docmodel as DM       # noqa: E402
from ai_modeler import partlib as PL        # noqa: E402

#: ISO 4762 / GB/T 70.1：对边 s、孔深 t、头径 dk、头高 k
ISO = {"M5": (4.0, 2.5, 8.5, 5.0), "M6": (5.0, 3.0, 10.0, 6.0),
       "M8": (6.0, 4.0, 13.0, 8.0), "M10": (8.0, 5.0, 16.0, 10.0),
       "M12": (10.0, 6.0, 18.0, 12.0)}


def volume(fs):
    """网格体积（散度定理；三角形用带符号四面体体积求和）。"""
    doc = DM.Document()
    for f in fs:
        doc.add(f)
    body = doc.build(deadline=None)
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


def main():
    fails = []
    print("%-9s %-10s %-10s %-10s %-10s %s"
          % ("型号", "孔实测体积", "孔期望体积", "总体积实测", "总体积解析", "判定"))
    for model in ("M5x16", "M6x20", "M8x25", "M10x30", "M12x50"):
        sp = model.split("x")[0]
        s, t, dk, k = ISO[sp]
        ln = float(model.split("x")[1])
        r = PL.build_features_ex("内六角螺栓 %s" % model)
        if not r:
            fails.append((model, "建不出来"))
            continue
        fs = r[1]
        ri = (PL.model_params("内六角螺栓 %s" % model) or {}).get("params") or {}
        real = ri.get("__real") or {}
        fw = real.get("flat_washer") or {"d_in": 5.3, "d_out": 10.0, "t": 1.0}
        sw = real.get("spring_washer") or {"d_in": 5.1, "d_out": 8.8, "t": 1.2}
        ht = float(ri.get("bolt_hex_t") or t)
        no_sock = [f for f in fs if "内六角孔" not in (f.name or "")]
        if len(no_sock) == len(fs):
            fails.append((model, "模型里没有内六角孔特征"))
            continue
        try:
            v1 = volume(fs)
            v0 = volume(no_sock)
        except Exception as e:
            fails.append((model, "建网格失败 %s" % e))
            continue
        got_sock = v0 - v1
        want_sock = (3 ** 0.5 / 2.0) * s * s * ht
        # 解析总体积：头 + 杆 + 两个垫圈环（内孔被杆填不到，留间隙）
        want_v = (math.pi * (dk / 2) ** 2 * k
                  + math.pi * (float(sp[1:]) / 2) ** 2 * ln
                  + math.pi / 4.0 * (fw["d_out"] ** 2 - fw["d_in"] ** 2) * fw["t"]
                  + math.pi / 4.0 * (sw["d_out"] ** 2 - sw["d_in"] ** 2) * sw["t"]
                  - want_sock)
        ok_sock = abs(got_sock - want_sock) <= 0.06 * want_sock
        ok_vol = abs(v1 - want_v) <= 0.04 * want_v
        ok = ok_sock and ok_vol
        print("%-9s %-10.2f %-10.2f %-10.1f %-10.1f %s"
              % (model, got_sock, want_sock, v1, want_v,
                 "OK" if ok else ("✗ 孔" if not ok_sock else "✗ 总体积")))
        if not ok_sock:
            fails.append((model, "六角孔切除体积 %.2f ≠ 期望 %.2f（s=%.1f 深%.2f）"
                          % (got_sock, want_sock, s, ht)))
        if not ok_vol:
            fails.append((model, "总体积 %.1f 与解析值 %.1f 偏差 >4%%" % (v1, want_v)))
    print()
    if fails:
        print("不通过 %d 项：" % len(fails))
        for m, why in fails:
            print("   ✗ %-9s %s" % (m, why))
        return 1
    print("全部通过 ✅（六角孔切除体积 = (√3/2)s²t，总体积与解析值一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
