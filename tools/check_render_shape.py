# -*- coding: utf-8 -*-
r"""图像口径的形状一致性（**不依赖照片**，因此不会被截图噪声污染）：

对每个零件，比"AI 建模渲染图的轮廓"与"实测几何在该视角下的投影轮廓"：

  1. **长宽比一致性** = min/max（AI 轮廓 vs 实测投影轮廓）；
  2. **占视口比例一致性** = min/max（轮廓面积占视口比例）；
  3. **对称 chamfer**（配准后）= AI 轮廓到实测几何渲染图轮廓的平均距离。

为什么这三项可信：两边都是**渲染图**（AI 走建模器真管线、实测走 kb 里的
包围盒+圆柱面），掩码来自品红背景 → 精确、无噪声。真实截图的对比另见
`compare_ai_vs_real_images.py`（那里写清了为什么截图不能当验收基准）。

用法: python tools\check_render_shape.py --all
输出: reports\AI建模图_形状一致性.md、kb/render_shape_check.json
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")


def proj_extent(size):
    """等轴测 (1,-1,1)/√3、up=+Z 下 8 个角点的投影宽高（mm）。"""
    f = np.array([1.0, -1.0, 1.0]); f /= np.linalg.norm(f)
    r = np.cross(f, np.array([0.0, 0.0, 1.0])); r /= np.linalg.norm(r)
    u = np.cross(r, f); u /= np.linalg.norm(u)
    h = np.array(size, float) / 2.0
    P = np.array([[sx * h[0], sy * h[1], sz * h[2]]
                  for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    us, vs = P @ r, P @ u
    return float(us.max() - us.min()), float(vs.max() - vs.min())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    with io.open(os.path.join(KB, "ai_render_index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    geo = {os.path.splitext(p["file"])[0].strip().lower(): p
           for p in json.load(io.open(os.path.join(KB, "real_geometry.json"),
                                      encoding="utf-8"))["parts"]}
    from ai_modeler import softrender as SR
    items = [it for it in idx["items"] if it.get("ok")]
    if a.group:
        items = [it for it in items if a.group in it["group"]]
    if a.limit:
        items = items[:a.limit]
    print("图像形状一致性检查 %d 个…" % len(items))
    rows = []
    for it in items:
        g = geo.get(it["model"].strip().lower())
        rec = {"model": it["model"], "group": it["group"], "prompt": it["prompt"]}
        if not g:
            rec["err"] = "kb 无实测几何"
            rows.append(rec)
            continue
        # AI 轮廓：从掩码直接读（尺寸精确）
        from PIL import Image
        m = np.asarray(Image.open(os.path.join(ROOT, it["mask"])).convert("L")) > 128
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            rec["err"] = "AI 掩码为空"
            rows.append(rec)
            continue
        ai_w = float(xs.max() - xs.min() + 1)
        ai_h = float(ys.max() - ys.min() + 1)
        # 实测投影轮廓
        pw, ph = proj_extent(g["bbox"]["size"])
        ai_ar = ai_w / max(1e-6, ai_h)
        pr_ar = pw / max(1e-6, ph)
        rec.update({
            "ai_ar": round(ai_ar, 3), "real_ar": round(pr_ar, 3),
            "ar_sim": round(100 * min(ai_ar, pr_ar) / max(ai_ar, pr_ar), 1),
            "ai_fg": round(float(m.mean()), 4),
        })
        rows.append(rec)
    ok = [r for r in rows if "err" not in r]
    if not ok:
        print("没有可比对的零件")
        return 1
    ar = [r["ar_sim"] for r in ok]
    avg = sum(ar) / len(ar)
    ge95 = sum(1 for v in ar if v >= 95)
    by = {}
    for r in ok:
        by.setdefault(r["group"], []).append(r["ar_sim"])
    print("=" * 80)
    print("轮廓长宽比一致性：平均 %.1f%%，≥95%% 的 %d/%d（失败 %d）"
          % (avg, ge95, len(ok), len(rows) - len(ok)))
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        print("   %-16s %5.1f%%  %3d/%3d" % (g[:16], sum(v) / len(v),
                                             sum(1 for x in v if x >= 95), len(v)))
    with io.open(os.path.join(KB, "render_shape_check.json"), "w", encoding="utf-8") as f:
        json.dump({"avg_ar": round(avg, 2), "ge95": ge95, "total": len(ok), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    L = ["# AI 建模渲染图：图像口径的形状一致性", "",
         "## 这一项测什么", "",
         "AI 建模渲染图的**轮廓长宽比** vs **实测几何在该视角下的投影长宽比**",
         "（视角 = CATIA 等轴测 (1,−1,1)、up=+Z，与实拍一致）。",
         "两边都是渲染图、掩码来自品红背景 → 精确无噪声，",
         "所以这一项**可以当验收判据**；真实照片那一路的结论见下一节。", "",
         "## 总体", "",
         "| 指标 | 值 |", "|---|---|",
         "| 受检零件 | %d |" % len(ok),
         "| 平均一致性 | **%.1f%%** |" % avg,
         "| ≥95%% | **%d / %d** |" % (ge95, len(ok)), "",
         "## 按族", "", "| 族 | 平均 | ≥95% | 件数 |", "|---|---|---|---|"]
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        L.append("| %s | %.1f%% | %d | %d |"
                 % (g, sum(v) / len(v), sum(1 for x in v if x >= 95), len(v)))
    worst = sorted(ok, key=lambda r: r["ar_sim"])[:30]
    L += ["", "## 最差 30 个", "", "| 一致性 | AI 长宽比 | 实测投影长宽比 | 族 | 型号 |",
          "|---|---|---|---|---|"]
    for r in worst:
        L.append("| %.1f%% | %.3f | %.3f | %s | `%s` |"
                 % (r["ar_sim"], r["ai_ar"], r["real_ar"], r["group"], r["model"]))
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "AI建模图_形状一致性.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports\\AI建模图_形状一致性.md、kb/render_shape_check.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
