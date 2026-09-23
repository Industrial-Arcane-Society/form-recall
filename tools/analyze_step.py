# -*- coding: utf-8 -*-
r"""
从真实夹具的 STEP（AP203）里提取几何特征 —— 给我的建模插件建"真实件基准"。

为什么做这个：`预设计文件` 里的 CATIA 原生格式（.CATPart/.CATProduct/.cgr）读不了，
但 `_REF` 下有一批夹具零件的 **.stp**（STEP 是 ISO-10303-21 文本格式，能解析）。
于是可以从真实夹具零件上量出**可复刻的特征清单**，作为插件的能力基准。

本脚本**只用标准库**，做四件事：
  1. 解析装配结构（PRODUCT / NEXT_ASSEMBLY_USAGE_OCCURRENCE）——这个零件由几个子件组成；
  2. 统计实体与面：MANIFOLD_SOLID_BREP、ADVANCED_FACE，按曲面类型分类
     （PLANE 平面 / CYLINDRICAL_SURFACE 圆柱面 / …）；
  3. **认出孔**：每个圆柱面的半径 + 轴线方向 -> 聚成孔，给出孔径与轴向；
  4. 包围盒（从所有 CARTESIAN_POINT 算，注意包含绝对坐标偏移）。

用法：python tools\analyze_step.py <file.stp> [...]
      python tools\analyze_step.py --all    # 扫 _REF 下全部 stp
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

REF_DIR = (r"C:\Users\25643\Desktop\AI自动画图项目设计数据及视频-20260402-V01"
           r"\预设计文件\2A_3S6_U_SI_006L_JG01\2A_3S6_U_SI_006L_JG01_REF")

_RE_ENTITY = re.compile(r"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*;\s*$")
_RE_CART = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)")
_RE_CYL = re.compile(
    r"CYLINDRICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*(-?[\d.Ee+]+)\s*\)")
_RE_CIRCLE = re.compile(r"CIRCLE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*(-?[\d.Ee+]+)\s*\)")


def _nums(s):
    out = []
    for tok in s.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def parse_step(path):
    """解析 STEP，返回一份结构化的几何概要。"""
    with io.open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    lines = text.splitlines()

    ents = {}                       # id -> (type, args)
    products = []                   # 装配里的子件名
    ent_type_count = Counter()
    for ln in lines:
        m = _RE_ENTITY.match(ln.strip())
        if not m:
            continue
        eid, etype, args = int(m.group(1)), m.group(2), m.group(3)
        ents[eid] = (etype, args)
        ent_type_count[etype] += 1
        if etype == "PRODUCT":
            pm = re.match(r"\s*'([^']*)'", args)
            if pm:
                products.append(pm.group(1).strip())

    # ---- 圆柱面：半径 + 轴线方向（用于认孔） ----
    axis_dir = {}
    for eid, (t, a) in ents.items():
        if t == "DIRECTION":
            v = _nums(a)
            if len(v) == 3:
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                axis_dir[eid] = (v[0] / n, v[1] / n, v[2] / n)

    cylinders = []
    for eid, (t, a) in ents.items():
        if t != "CYLINDRICAL_SURFACE":
            continue
        m = re.search(r"#(\d+)\s*,\s*(-?[\d.Ee+]+)\s*\)\s*$", a)
        if not m:
            continue
        axid, r = int(m.group(1)), float(m.group(2))
        cylinders.append({"radius": r, "dir": axis_dir.get(axid)})

    hole_radii = Counter(round(c["radius"], 4) for c in cylinders)

    # ---- 所有点 -> 包围盒 ----
    pts = []
    for m in _RE_CART.finditer(text):
        v = _nums(m.group(1))
        if len(v) == 3:
            pts.append(v)
    bbox = None
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        bbox = {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)],
                "z": [min(zs), max(zs)],
                "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]}

    faces = ent_type_count.get("ADVANCED_FACE", 0)
    surfs = {k: v for k, v in ent_type_count.items()
             if k.endswith("_SURFACE") or k in ("PLANE", "TOROIDAL_SURFACE")}

    return {
        "file": os.path.basename(path),
        "bytes": os.path.getsize(path),
        "lines": len(lines),
        "schema": (re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text) or
                   [None, "?"])[1],
        "writer": (re.search(r"FILE_DESCRIPTION\s*\(\s*\(\s*'([^']+)'", text) or
                   [None, "?"])[1],
        "products": products,
        "n_solids": ent_type_count.get("MANIFOLD_SOLID_BREP", 0),
        "n_faces": faces,
        "n_edge_curves": ent_type_count.get("EDGE_CURVE", 0),
        "n_circles": ent_type_count.get("CIRCLE", 0),
        "n_lines": ent_type_count.get("LINE", 0),
        "surfaces": surfs,
        "n_cylinders": len(cylinders),
        "hole_diameters": {("%.3f" % (2 * r)): n
                           for r, n in sorted(hole_radii.items())},
        "bbox": bbox,
        "top_entities": ent_type_count.most_common(14),
    }


def collect_all():
    out = []
    for dirpath, _dn, fns in os.walk(REF_DIR):
        for fn in fns:
            if fn.lower().endswith(".stp") or fn.lower().endswith(".step"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def short(p):
    """把长路径压成可读的短标签。"""
    b = os.path.basename(p)
    parent = os.path.basename(os.path.dirname(p))
    return "%s/%s" % (parent, b)


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0] == "--all":
        files = collect_all()
    else:
        files = args
    if not files:
        print("没找到 .stp 文件")
        return 1

    print("分析 %d 个 STEP 文件\n" % len(files))
    results = []
    for p in files:
        try:
            r = parse_step(p)
        except Exception as e:
            print("!! %s 解析失败: %s" % (short(p), e))
            continue
        r["path"] = p
        r["label"] = short(p)
        results.append(r)
        bb = r["bbox"]
        size = bb["size"] if bb else (0, 0, 0)
        print("=" * 74)
        print("%s" % r["label"])
        print("  子件数 %d | 实体 %d | 面 %d | 圆柱面 %d | 圆 %d"
              % (len(r["products"]), r["n_solids"], r["n_faces"],
                 r["n_cylinders"], r["n_circles"]))
        print("  外形尺寸 %.1f × %.1f × %.1f mm" % tuple(size))
        if r["bbox"]:
            print("  坐标原点偏移 x=%.1f y=%.1f z=%.1f（绝对坐标，非原点）"
                  % (r["bbox"]["x"][0], r["bbox"]["y"][0], r["bbox"]["z"][0]))
        if r["hole_diameters"]:
            hs = sorted(((float(k), v) for k, v in r["hole_diameters"].items()),
                        reverse=True)[:10]
            print("  主要孔径（直径 mm × 面数）: %s"
                  % "、".join("⌀%g×%d" % (d, n) for d, n in hs))
        if r["products"]:
            print("  子件: %s" % "、".join(r["products"][:6]))

    os.makedirs(KB, exist_ok=True)
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(KB, "fixture_stp_profile.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"n_files": len(results), "parts": results}, f,
                  ensure_ascii=False, indent=1)
    print("\n明细已写入 kb/fixture_stp_profile.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
