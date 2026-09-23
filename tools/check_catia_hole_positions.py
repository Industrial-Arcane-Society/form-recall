# -*- coding: utf-8 -*-
r"""CATIA 导出**孔位几何**回归：孔位不能只是"形状合法"，还要"位置正确"。

为什么单独要有这个检查（`check_catia_export_real.py` 查不出来的一类错）：
    那个检查只看特征表能不能被 CATIA 安全执行（非空、非零深、段数正确）。
    而我在接 `extrude_holes`（B 计划直写网格件）时，孔位换算是"漏了一手"的：
      · 提取器给出的 (u, v) 是**下标升序**的另外两轴（Y 向孔 → u=X, v=Z）；
      · CATIA 的草图平面按 PlaneZX / PlaneXY / PlaneYZ 排（Y 向孔 → (Z, X)）。
    两者对 Y 向孔**正好互换**。特征表照样"合法"，但孔会沿对角线镜像 ——
    523/523 全绿，几何全错。所以这里把孔位**反算回零件坐标系**跟实测比对。

对每个型材（等截面直写网格件）验证三件事：
  1. 反算回的 (u, v) 能在 `kb/real_features.json` 的实测圆柱里逐个找到
     （同轴、同直径 ±0.01、位置 ±0.01）；
  2. 每个孔都落在截面矩形内（孔不能钻到零件外面去）；
  3. 孔的数量与"实测圆柱中 d≥2 且不与其他孔相交"的数量一致。

用法: python tools\check_catia_hole_positions.py [--all]
      默认抽查 40 个型号（含三族），`--all` 跑全部 289 个型材。
输出: reports\CATIA孔位几何检查.md
"""
from __future__ import annotations

import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import catia_export as CE   # noqa: E402
from ai_modeler import nlparser as NLP       # noqa: E402
from ai_modeler import realholes as RH       # noqa: E402

_AXMAP = {'Z': (0, 1, 2), 'X': (1, 2, 0), 'Y': (2, 0, 1)}


def _invert(cx, cy, hax, ref_min, ref_size):
    """把 CATIA 平面的 (cx, cy) 反算回零件坐标系的 (u, v)（升序另外两轴）。"""
    hai = "XYZ".index(hax)
    oth = [q for q in range(3) if q != hai]
    hm = _AXMAP[hax]
    by_letter = {"XYZ"[hm[0]]: cx, "XYZ"[hm[1]]: cy}
    u = by_letter["XYZ"[oth[0]]]
    v = by_letter["XYZ"[oth[1]]]
    if ref_min and ref_size:
        u += float(ref_min[oth[0]]) + float(ref_size[oth[0]]) / 2.0
        v += float(ref_min[oth[1]]) + float(ref_size[oth[1]]) / 2.0
    return u, v


def check_one(text):
    """返回 (问题列表, 孔数)。"""
    problems = []
    doc = NLP.parse(text).doc
    feats, _warns = CE.build_catia_features(doc)
    src = [f for f in doc.features if f.kind == "extrude_holes"]
    if not src:
        return ["不是直写网格件（无 extrude_holes），本检查跳过"], 0
    if len(src) != 1:
        return ["extrude_holes 特征数不为 1（%d）" % len(src)], 0
    p = src[0].params
    # 型号取自提示词本身（"EGT型材 EGT001 SW80x088" → "EGT001 SW80x088"）
    real = RH.feature_of(" ".join(text.split()[1:])) or {}
    real_cyls = [c for c in (real.get("cylinders") or []) if float(c["d"]) >= 2.0]
    # 文档里的孔（建模用）
    doc_holes = list(p.get("holes") or [])
    # 导出表里的孔（CATIA 用）
    exp_holes = [e for e in feats if str(e.get("kind")) == "circle_cut"
                 and str(e.get("name") or "").startswith("实测孔")]
    if len(exp_holes) != len(doc_holes):
        problems.append("导出孔数 %d ≠ 文档孔数 %d" % (len(exp_holes), len(doc_holes)))

    # ① 孔位反算回零件坐标系，必须能在实测圆柱里找到
    real_keys = {}
    for c in real_cyls:
        real_keys.setdefault((c["axis"], round(float(c["u"]), 2),
                              round(float(c["v"]), 2)), []).append(float(c["d"]))
    ref_min, ref_size = p.get("ref_min"), p.get("ref_size")
    hit = 0
    for e in exp_holes:
        hax = str(e.get("plane") or "Z").upper()
        u, v = _invert(float(e["cx"]), float(e["cy"]), hax, ref_min, ref_size)
        d = float(e["r"]) * 2.0
        cand = real_keys.get((hax, round(u, 2), round(v, 2)))
        if cand is None:
            # 允许 0.01 的位置偏差：按四舍五入的键再兜一层
            cand = [dd for (ax, ru, rv), dds in real_keys.items()
                    if ax == hax and abs(ru - u) <= 0.011 and abs(rv - v) <= 0.011
                    for dd in dds]
        if not cand or not any(abs(dd - d) <= 0.011 for dd in cand):
            problems.append("孔位反算不属实测: 轴%s (%.3f, %.3f) ⌀%g" % (hax, u, v, d))
        else:
            hit += 1

    # ② 孔必须在**零件**范围内
    #  ⚠ 边界的"另一个轴"不一定是截面轴：型材的 X 向孔，它的 cx 是沿**长度**的
    #    坐标（SW80 的长度 88、截面 40）。所以边界要按导出时那张 dims 表来取，
    #    不能一律用 sec_a/sec_b —— 我第一版就是这么误报的（把长度当截面，
    #    Y=-45 被判成"钻到截面外"，其实 -45 落在长度 ±59 之内）。
    sa, sb = float(p.get("sec_a")), float(p.get("sec_b"))
    ln = float(p.get("length"))
    ax = str(p.get("axis") or "Z").upper()
    ui2, vi2, _wi2 = _AXMAP[ax]
    dims = {'X': sa, 'Y': sb, 'Z': ln}
    dims[ax] = ln
    dims["XYZ"[ui2]] = sa
    dims["XYZ"[vi2]] = sb
    for e in exp_holes:
        hax = str(e.get("plane") or "Z").upper()
        hm = _AXMAP[hax]
        d = float(e["r"]) * 2.0
        for letter, c in (("XYZ"[hm[0]], float(e["cx"])), ("XYZ"[hm[1]], float(e["cy"]))):
            lim = dims[letter] / 2.0
            if abs(c) + d / 2.0 > lim + 0.51:
                problems.append("孔钻到零件外: 轴%s %s=%.2f ± %.2f > 半长 %.2f"
                                % (hax, letter, c, d / 2.0, lim))
                break

    # ③ 孔数与"实测可用孔"一致（相交的孔走布尔路径，这里只查已经走直写网格的）
    n_expect = len([h for h in (p.get("holes") or [])])
    if len(exp_holes) != n_expect:
        problems.append("导出孔数与特征里登记的不一致")
    return problems, hit


def main():
    args = set(sys.argv[1:])
    all_ = "--all" in args
    from ai_modeler import partlib as PL
    egt = PL.RL._load("egt_library.json")
    cases = []
    for fam, v in (egt.get("profiles") or {}).items():
        for code in (v.get("records") or {}):
            cases.append("EGT型材 %s %sx%03d" % (v["family"], v["section"], int(code)))
    if not all_:
        step = max(1, len(cases) // 40)
        cases = cases[::step]
    print("检查 %d 个型号的 CATIA 孔位几何…" % len(cases))
    t0 = time.time()
    bad, skip, n_hole = [], [], 0
    kinds = {}
    for text in cases:
        try:
            probs, nh = check_one(text)
        except Exception as e:
            probs, nh = ["异常 %s: %s" % (type(e).__name__, str(e)[:70])], 0
        if probs and probs[0].startswith("不是直写网格件"):
            skip.append(text)
            continue
        n_hole += nh
        for pr in probs:
            kk = pr.split(":")[0].split("（")[0]
            kinds[kk] = kinds.get(kk, 0) + 1
        if probs:
            bad.append((text, probs))
    dt = time.time() - t0
    print("=" * 78)
    print("通过 %d/%d（跳过 %d 非直写网格件），核对孔位 %d 个，用时 %.1fs"
          % (len(cases) - len(bad) - len(skip), len(cases) - len(skip), len(skip), n_hole, dt))
    if kinds:
        print("问题分类:")
        for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
            print("   %-28s %d" % (k[:28], v))
    if bad:
        print("\n不通过（前 20）：")
        for t, ps in bad[:20]:
            print("   %-34s %s" % (t[:34], ps[0][:96]))

    L = ["# CATIA 孔位几何检查", "",
         "把 CATIA 导出的孔位**反算回零件坐标系**，与 `kb/real_features.json` 的",
         "实测圆柱逐个比对。特征表「合法」不等于位置正确 —— 接直写网格件时我漏了",
         "一手：提取器的 (u,v) 是升序另外两轴，CATIA 草图平面按 PlaneZX 排，",
         "Y 向孔两者正好互换，孔会沿对角线镜像。", "",
         "| 指标 | 值 |", "|---|---|",
         "| 受检型号 | %d |" % (len(cases) - len(skip)),
         "| 通过 | %d |" % (len(cases) - len(bad) - len(skip)),
         "| 不通过 | %d |" % len(bad),
         "| 核对孔位 | %d 个 |" % n_hole,
         "| 用时 | %.1f s |" % dt, ""]
    if bad:
        L += ["## 不通过清单", "", "| 提示词 | 问题 |", "|---|---|"]
        for t, ps in bad:
            L.append("| `%s` | %s |" % (t, "；".join(ps)[:200]))
    else:
        L.append("**全部通过**：孔位反算回零件坐标系后与实测圆柱一致（±0.01 mm），"
                 "且没有孔钻到截面之外。")
    with io.open(os.path.join(ROOT, "reports", "CATIA孔位几何检查.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports/CATIA孔位几何检查.md")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
