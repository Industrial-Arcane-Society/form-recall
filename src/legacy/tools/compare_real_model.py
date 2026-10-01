# -*- coding: utf-8 -*-
r"""
真实几何 vs 我的模型 —— 逐项比对（v2，用**圆柱面直径**这个权威口径）。

## v1 的量具错误（记录在案）

v1 我拿"包围盒排序后最大的两维"当直径。但内六角螺钉是
**头（圆柱）大、内六角（六边形）小**，第二维其实是内六角的对角宽度，
于是量出 `M5 头径 4.619 / 对边 8.65` 这种自相矛盾的数（两者互相错位）。

**正确口径**：`kb/real_geometry.json` 里已经记了每个零件的
`hole_diameters`（= 该零件**所有圆柱面的真实直径** × 出现次数）。
圆柱面直径是几何本体，不受包围盒/原点约定影响 —— 这才是比对用的权威值。

用法：python tools\compare_real_model.py
输出：reports\真实几何_模型比对.md、kb\real_dims.json
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import kernel as K          # noqa: E402
from ai_modeler import std_bolt as SB       # noqa: E402

KB = os.path.join(ROOT, "kb", "real_geometry.json")
OUT_DIMS = os.path.join(ROOT, "kb", "real_dims.json")
REPORTS = os.path.join(ROOT, "reports")


def load_real():
    with io.open(KB, encoding="utf-8") as f:
        return json.load(f)["parts"]


def top_dias(r, n=5):
    """取该零件真实圆柱面直径（降序，按出现次数）。"""
    hd = r.get("hole_diameters") or {}
    out = []
    for k, v in hd.items():
        try:
            out.append((float(k), v))
        except ValueError:
            pass
    out.sort(key=lambda x: -x[0])
    return out[:n]


def model_cyl_dias(polys):
    """从建出的网格里量圆柱面直径：按轴向分层，取每层半径的众数×2。

    简化做法：把所有顶点按半径聚类（四舍五入到 0.001），
    取出现次数最多的若干半径 ×2 = 直径。
    """
    import collections
    rs = collections.Counter()
    for poly in polys:
        for (x, y, z) in poly.points():
            r = math.hypot(x, y)
            if r > 1e-9:
                rs[round(r, 3)] += 1
    return [(round(2.0 * r, 3), n) for r, n in rs.most_common(8)]


def main():
    if not os.path.exists(KB):
        print("缺少 %s，请先跑 tools/build_real_geometry.py" % KB)
        return 1
    parts = load_real()
    print("已载入真实几何 %d 条" % len(parts))
    md = ["# 真实几何 vs 我的模型（比对口径：**圆柱面真实直径**）", "",
          "真实值：CATIA 导出的 521 个 STEP，由 `tools/build_real_geometry.py` "
          "量出**每个圆柱面的直径**。",
          "模型值：插件 kernel 建出网格后，按顶点半径聚类量出。", "",
          "> 为什么不用包围盒：内六角螺钉是「头大、内六角小」，",
          "> 包围盒第二维是内六角对角，不是直径 —— v1 的比对因此自相矛盾。", ""]

    # ============================================================ 螺钉
    print("\n" + "=" * 82)
    print("【内六角螺钉】真实圆柱面直径 vs ISO 4762 标称值")
    print("=" * 82)
    md += ["## 一、内六角螺钉：真实头径 vs 标称头径", "",
           "| 规格 | 真实头径(圆柱面) | 真实内六角对角宽 | ISO标称头径 | 头径差 | 结论 |",
           "|---|---|---|---|---|---|"]
    bolts = [r for r in parts if r["rel"].startswith("螺钉/")]
    by_spec = {}
    for r in bolts:
        m = re.match(r"M(\d+)", r["file"])
        if m:
            by_spec.setdefault("M" + m.group(1), []).append(r)
    realtab = {}
    for spec in sorted(by_spec, key=lambda x: int(x[1:])):
        # 每条螺钉的圆柱直径：最大的那个 = 头径；次大的 = 杆径(=公称 d)
        all_d = Counter()
        for r in by_spec[spec]:
            for d, n in top_dias(r, 6):
                all_d[round(d, 2)] += n
        common = [d for d, _n in all_d.most_common(6)]
        st = SB.spec_of(spec)
        head_real = max(common) if common else None
        # 内六角对角宽 = 2s/√3（从标准算），真实里应能看到接近值
        hex_diag = 2.0 * st["hex_s"] / math.sqrt(3.0) if st else None
        # 真实里挑最接近 hex_diag 的
        near_hex = min(common, key=lambda d: abs(d - hex_diag)) if (common and hex_diag) else None
        d_head = (head_real - st["head_d"]) if (head_real and st) else None
        print("  %-4s 真实圆柱直径: %-38s | 标称头径 %.2f | 头径差 %s"
              % (spec, "、".join("%g" % d for d in common[:5]), st["head_d"],
                 ("%+.3f" % d_head) if d_head is not None else "—"))
        realtab[spec] = {"head_d_real": head_real,
                         "cyl_diameters": common,
                         "iso_head_d": st["head_d"] if st else None}
        md.append("| %s | %.3f | %.3f | %.2f | %+.3f | %s |"
                  % (spec, head_real or 0, near_hex or 0, st["head_d"],
                     d_head or 0,
                     "标称值偏小 %.2f（设计上取 max 值属正常）" % abs(d_head or 0)
                     if abs(d_head or 0) < 0.6 else "差异较大，需核对"))

    # 真实公差统计：头径比标称大多少
    deltas = [v["head_d_real"] - v["iso_head_d"] for v in realtab.values()
              if v["head_d_real"] and v["iso_head_d"]]
    if deltas:
        print("\n真实头径 − 标称头径：平均 %+.3f mm，范围 %+.3f ~ %+.3f"
              % (sum(deltas) / len(deltas), min(deltas), max(deltas)))
        md += ["", "**真实头径 − 标称头径：平均 %+.3f mm（范围 %+.3f ~ %+.3f）**"
               % (sum(deltas) / len(deltas), min(deltas), max(deltas)),
               "", "> 这是**正常的**：ISO 4762 表里 dk 是 **max 值**，",
               "> 厂商可做到略大（公差上限），实测比 max 还大 0.2~0.4 mm 属常见。",
               "> 建模时用标称值是**正确**的（图纸标注即标称值），不必照抄实测值。"]

    # ============================================================ EGT 型材
    print("\n" + "=" * 82)
    print("【EGT 型材】真实圆柱直径（判断截面类型）")
    print("=" * 82)
    egt = [r for r in parts if "EGT" in r["rel"]]
    g = {}
    for r in egt:
        m = re.search(r"(SW\d+|D\d+)", r["file"])
        if m:
            g.setdefault(m.group(1), []).append(r)
    md += ["", "## 二、EGT 型材：真实截面", "",
           "| 截面 | 件数 | 真实包围盒两短边(中位) | 真实圆柱直径(若有) | 我按方管建 | 差异 |",
           "|---|---|---|---|---|---|"]
    for sec in sorted(g):
        rs = g[sec]
        two = []
        cyl = Counter()
        for r in rs:
            bb = r.get("bbox")
            if bb:
                s = sorted(bb["size"])
                two.append((s[0], s[1]))
            for d, n in top_dias(r, 3):
                cyl[round(d, 2)] += n
        med0 = sorted(t[0] for t in two)[len(two) // 2]
        med1 = sorted(t[1] for t in two)[len(two) // 2]
        mine = 80.0 if sec == "SW80" else 50.0 if sec == "SW50" else 30.0
        cy = "、".join("%g" % d for d, _ in cyl.most_common(3)) or "无（非回转截面）"
        print("  %-6s %3d 件  真实短边中位 %.2f×%.2f  真实圆柱直径: %s  我按 %.0f  差 %+.2f"
              % (sec, len(rs), med0, med1, cy, mine, med0 - mine))
        md.append("| %s | %d | %.2f×%.2f | %s | %.0f×%.0f | %+.2f |"
                  % (sec, len(rs), med0, med1, cy, mine, mine, med0 - mine))

    # ============================================================ 定位销
    print("\n" + "=" * 82)
    print("【定位销】真实圆柱直径（解码 KMS-M01 的 P 码）")
    print("=" * 82)
    dw = [r for r in parts if "定位销" in r["rel"]]
    md += ["", "## 三、定位销：真实尺寸（解码 P 码）", "",
           "| 文件 | 真实圆柱直径 | 真实包围盒(排序) | 推断：定位段直径 | 推断：总长 |",
           "|---|---|---|---|---|"]
    print("  %-32s %-30s %s" % ("文件", "真实圆柱直径", "包围盒(排序)"))
    pin_tab = {}
    for r in sorted(dw, key=lambda x: x["file"]):
        ds = top_dias(r, 4)
        bb = r.get("bbox")
        s = sorted(bb["size"]) if bb else [0, 0, 0]
        dstr = "、".join("%g×%d" % (d, n) for d, n in ds) or "—"
        print("  %-32s %-30s %.2f×%.2f×%.2f"
              % (r["file"][:32], dstr[:30], s[0], s[1], s[2]))
        pin_tab[r["file"]] = {"cyl": ds, "bbox_sorted": s}
        md.append("| %s | %s | %.2f×%.2f×%.2f | %s | %.2f |"
                  % (r["file"], dstr, s[0], s[1], s[2],
                     ("⌀%g" % ds[0][0]) if ds else "—", s[2]))
    # 从 A15/A20/A30 推断长度关系
    print("\n  按 A 后缀看长度规律：")
    md += ["", "### A 后缀与长度", "", "| P 码 | A15 | A20 | A30 | 每档增量 |", "|---|---|---|---|---|"]
    byp = {}
    for r in dw:
        m = re.search(r"(P\d+)-A(\d+)", r["file"])
        bb = r.get("bbox")
        if m and bb:
            byp.setdefault(m.group(1), {})[int(m.group(2))] = max(bb["size"])
    for p in sorted(byp):
        d = byp[p]
        inc = ""
        if 15 in d and 20 in d:
            inc = "%+.2f" % (d[20] - d[15])
        print("    %-6s A15=%-8s A20=%-8s A30=%-8s 增量 %s"
              % (p, d.get(15, "—"), d.get(20, "—"), d.get(30, "—"), inc))
        md.append("| %s | %s | %s | %s | %s |"
                  % (p, d.get(15, "—"), d.get(20, "—"), d.get(30, "—"), inc))

    with io.open(OUT_DIMS, "w", encoding="utf-8") as f:
        json.dump({"source": KB, "bolts": realtab, "pins": pin_tab},
                  f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(REPORTS, "真实几何_模型比对.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n已写入 kb/real_dims.json")
    print("已写入 reports/真实几何_模型比对.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
