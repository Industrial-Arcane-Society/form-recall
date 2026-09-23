# -*- coding: utf-8 -*-
r"""
从真实 STEP 提取定位销的**真实特征表**，并解码 KMS-M01 的 P 码。

用户要求"先让 AI 明白是什么、现实长什么样"。定位销是唯一能**完整解码**的一族：
22 个零件、每个只有 1 个实体 26 个面，几何简单且规律明确。

从真实几何里能读出的量：
  · 全部圆柱面直径（含每个的轴向范围 -> 判断是"定位段"还是"台阶段"）
  · 真实总长、锥度（用圆锥面判断有没有导向锥）
  · P 码 / A 码 与实测尺寸的对应关系

用法：python tools\extract_pin_real.py
输出：kb/pin_std.json、reports\定位销真实尺寸表.md
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
BASE = r"C:\Users\25643\AppData\Local\Temp\catia_step_out"
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

RE_ENT = re.compile(r"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*;?\s*$")
RE_XYZ = re.compile(r"\(\s*(-?[\d.Ee+-]+)\s*,\s*(-?[\d.Ee+-]+)\s*,\s*(-?[\d.Ee+-]+)\s*\)")


def parse_pin(path):
    """解析一个定位销 STEP，返回真实特征。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    pts = {}
    dirs = {}
    cyl = []          # (radius, axis_entity)
    cones = 0
    planes = 0
    faces = 0
    solids = 0
    product = None
    for line in text.splitlines():
        m = RE_ENT.match(line.strip())
        if not m:
            continue
        eid, et, args = int(m.group(1)), m.group(2), m.group(3)
        if et == "CARTESIAN_POINT":
            m2 = RE_XYZ.search(args)
            if m2:
                pts[eid] = tuple(float(m2.group(i)) for i in (1, 2, 3))
        elif et == "DIRECTION":
            m2 = RE_XYZ.search(args)
            if m2:
                dirs[eid] = tuple(float(m2.group(i)) for i in (1, 2, 3))
        elif et == "CYLINDRICAL_SURFACE":
            m2 = re.search(r"#(\d+)\s*,\s*(-?[\d.Ee+-]+)", args)
            if m2:
                cyl.append((float(m2.group(2)), int(m2.group(1))))
        elif et == "CONICAL_SURFACE":
            cones += 1
        elif et == "PLANE":
            planes += 1
        elif et == "ADVANCED_FACE":
            faces += 1
        elif et in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS"):
            solids += 1
        elif et == "PRODUCT" and product is None:
            m2 = re.match(r"\s*'([^']*)'", args)
            if m2:
                product = m2.group(1)
    allpts = list(pts.values())
    bbox = None
    if allpts:
        xs = [p[0] for p in allpts]
        ys = [p[1] for p in allpts]
        zs = [p[2] for p in allpts]
        bbox = {"min": [min(xs), min(ys), min(zs)],
                "max": [max(xs), max(ys), max(zs)],
                "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]}
    return {"file": os.path.basename(path), "product": product,
            "solids": solids, "faces": faces, "planes": planes,
            "cones": cones,
            "cylinders": sorted({round(2 * r, 4) for r, _ in cyl}, reverse=True),
            "cyl_count": len(cyl), "bbox": bbox}


def main():
    d = os.path.join(BASE, "标准件", "定位销")
    if not os.path.isdir(d):
        print("找不到定位销目录:", d)
        return 1
    files = sorted(fn for fn in os.listdir(d) if fn.lower().endswith(".stp"))
    print("定位销 %d 个\n" % len(files))
    rows = []
    for fn in files:
        r = parse_pin(os.path.join(d, fn))
        r["file"] = fn
        rows.append(r)

    # ---- P 码 -> 实测直径 ----
    print("=" * 92)
    print("%-24s %-8s %-6s %s" % ("文件", "实体/面", "锥面", "圆柱直径（降序，×次数）"))
    print("=" * 92)
    for r in rows:
        ds = "、".join("%g" % x for x in r["cylinders"])
        print("%-24s %d/%-5d %-6d %s" % (r["file"], r["solids"], r["faces"],
                                         r["cones"], ds))

    # P 码解析：P<nnn> 与哪个直径相关
    print("\n【P 码解码尝试】P070 / P082 这类编码 vs 实测直径")
    print("%-8s %-10s %-10s %s" % ("P码", "P值/100", "实测最大圆柱", "实测最小圆柱"))
    tab = {}
    for r in rows:
        m = re.match(r"KMS-M01-(?:P|D)(\d+)-A(\d+)", r["file"])
        if not m or not r["cylinders"]:
            continue
        code, a = int(m.group(1)), int(m.group(2))
        big = r["cylinders"][0]
        small = r["cylinders"][-1]
        tab.setdefault(code, {"big": big, "small": small, "a": {}})
        tab[code]["a"][a] = r["bbox"]["size"][2] if r["bbox"] else None
        print("%-8s %-10.2f %-10g %g" % ("P%03d" % code, code / 100.0, big, small))

    # ---- A 码 -> 长度 ----
    print("\n【A 码 vs 实测总长】")
    print("%-8s %-11s %-11s %-11s %s" % ("P码", "A15", "A20", "A30", "A20-A15"))
    md = ["# 定位销真实尺寸表（KMS-M01，从 CATIA 导出的 STEP 量出）", "",
          "数据来源：`%s\\标准件\\定位销` 的 %d 个 STEP。" % (BASE, len(rows)),
          "**每个零件都是 1 个实体 / 26 个面**，几何规律明确。", "",
          "| 文件 | 实体/面 | 锥面 | 圆柱直径(mm) | 总长(mm) |", "|---|---|---|---|---|"]
    for r in rows:
        ds = "、".join("%g" % x for x in r["cylinders"])
        L = r["bbox"]["size"][2] if r["bbox"] else 0
        md.append("| %s | %d/%d | %d | %s | %.3f |"
                  % (r["file"], r["solids"], r["faces"], r["cones"], ds, L))
    md += ["", "## P 码 vs 实测直径", "", "| P码 | P值/100 | 实测最大圆柱 | 实测最小圆柱 |",
           "|---|---|---|---|"]
    for code in sorted(tab):
        md.append("| P%03d | %.2f | %g | %g |"
                  % (code, code / 100.0, tab[code]["big"], tab[code]["small"]))
    md += ["", "## A 码 vs 实测总长", "", "| P码 | A15 | A20 | A30 | 增量(A20-A15) |",
           "|---|---|---|---|---|"]
    for code in sorted(tab):
        a = tab[code]["a"]
        inc = ("%+.3f" % (a[20] - a[15])) if (15 in a and 20 in a and a[15] and a[20]) else "—"
        print("%-8s %-11s %-11s %-11s %s"
              % ("P%03d" % code, a.get(15, "—"), a.get(20, "—"), a.get(30, "—"), inc))
        md.append("| P%03d | %s | %s | %s | %s |"
                  % (code, a.get(15, "—"), a.get(20, "—"), a.get(30, "—"), inc))

    os.makedirs(KB, exist_ok=True)
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(KB, "pin_std.json"), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.join(BASE, "标准件/定位销"),
                   "count": len(rows), "pins": rows},
                  f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(REPORTS, "定位销真实尺寸表.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n已写入 kb/pin_std.json、reports/定位销真实尺寸表.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
