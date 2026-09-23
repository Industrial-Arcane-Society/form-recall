# -*- coding: utf-8 -*-
r"""图像相似度：AI 提示词建模渲染图 vs **实测几何渲染图**（干净、可复核）。

真实照片（CATIA 整窗截图）为什么不能当基准，见 `render_real_reference.py` 的
注释：那些截图的边缘里有上千个噪声连通域，任何轮廓都有 ~85% 的点能落在
"某条边缘"上 —— 实测真配对 85.3% / 交叉配对 84.1%，**区分度只有 1.2 个点**，
这种指标不能用来验收。要拿截图当基准必须白底重拍。

本脚本用**实测几何**（包围盒 + 每个圆柱面，都来自 STEP 实测）渲染出"参考图"，
与 AI 建模渲染图比轮廓：

    · 两张图都是**同一个渲染器、同一个视角（CATIA 等轴测）、同一留白**，
      掩码来自品红背景 → 精确、无噪声、无需分割；
    · 先在 (缩放 ±8%, 平移) 上做对称 chamfer 配准（正常情况下就是恒等变换）；
    · 相似度 = 0.5·IoU + 0.5·(1 − 平均对称 chamfer / 掩码对角线)。
      两项都落在 [0,1]，权重各半：IoU 管"面积对不对"，chamfer 管"边缘贴不贴"。

用法:
    python tools\compare_renders.py --all
    python tools\compare_renders.py --group 销座
输出:
    reports\AI建模图_vs_实测几何图_相似度.md
    kb\render_similarity.json
    零件比对图\<族>\<型号>_对比.png   （左：AI 建模；右：实测几何参考）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")
CMP = os.path.join(ROOT, "零件比对图")


def load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def mask_of(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("L")) > 128


def outline(m, step=2):
    import cv2
    cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return np.zeros((0, 2), np.float32)
    c = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    return c[::step]


def compare(ma, mb):
    """返回 (相似度%, IoU%, chamfer_px, 位姿)。ma=AI 掩码, mb=参考掩码。"""
    import cv2
    pa, pb = outline(ma), outline(mb)
    if len(pa) == 0 or len(pb) == 0:
        return None
    h, w = ma.shape
    ea = np.zeros((h, w), np.uint8)
    ea[np.clip(pa[:, 1].astype(int), 0, h - 1), np.clip(pa[:, 0].astype(int), 0, w - 1)] = 255
    dt_a = cv2.distanceTransform(255 - ea, cv2.DIST_L2, 3)
    eb = np.zeros((h, w), np.uint8)
    eb[np.clip(pb[:, 1].astype(int), 0, h - 1), np.clip(pb[:, 0].astype(int), 0, w - 1)] = 255
    dt_b = cv2.distanceTransform(255 - eb, cv2.DIST_L2, 3)

    ca = pa.mean(0)
    cb = pb.mean(0)
    diag = math.hypot(w, h)

    def ev(s, dx, dy):
        X = np.clip((pb[:, 0] - cb[0]) * s + ca[0] + dx, 0, w - 1).astype(int)
        Y = np.clip((pb[:, 1] - cb[1]) * s + ca[1] + dy, 0, h - 1).astype(int)
        d_ab = float(dt_a[Y, X].mean())
        Xa = np.clip((pa[:, 0] - ca[0] - dx) / s + cb[0], 0, w - 1).astype(int)
        Ya = np.clip((pa[:, 1] - ca[1] - dy) / s + cb[1], 0, h - 1).astype(int)
        d_ba = float(dt_b[Ya, Xa].mean())
        return d_ab + d_ba

    best = None
    for s in np.linspace(0.92, 1.08, 9):
        for dy in np.linspace(-14, 14, 15):
            for dx in np.linspace(-14, 14, 15):
                v = ev(s, dx, dy)
                if best is None or v < best[0]:
                    best = (v, s, dx, dy)
    v, s, dx, dy = best
    for _ in range(20):
        improved = False
        for ds in (-0.01, 0, 0.01):
            for ddx in (-2, 0, 2):
                for ddy in (-2, 0, 2):
                    vv = ev(s + ds, dx + ddx, dy + ddy)
                    if vv < v - 1e-9:
                        v, s, dx, dy = vv, s + ds, dx + ddx, dy + ddy
                        improved = True
        if not improved:
            break
    # 配准后的 IoU 与 chamfer
    M = np.float32([[s, 0, ca[0] + dx - s * cb[0]], [0, s, ca[1] + dy - s * cb[1]]])
    warp = cv2.warpAffine(ma.astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    inter = np.logical_and(warp, mb).sum()
    union = np.logical_or(warp, mb).sum()
    iou = inter / float(union) if union else 0.0
    ch = v / 2.0
    sim = 0.5 * iou + 0.5 * max(0.0, 1.0 - ch / (0.12 * diag))
    return 100 * sim, 100 * iou, ch, (round(s, 4), round(float(dx), 1), round(float(dy), 1))


def montage(ai_img, ref_img, out_path, title):
    from PIL import Image, ImageDraw
    a = Image.open(ai_img).convert("RGB")
    b = Image.open(ref_img).convert("RGB")
    h = 460
    a = a.resize((int(a.width * h / a.height), h), Image.LANCZOS)
    b = b.resize((int(b.width * h / b.height), h), Image.LANCZOS)
    canvas = Image.new("RGB", (a.width + b.width + 12, h + 26), (24, 28, 34))
    canvas.paste(a, (0, 26))
    canvas.paste(b, (a.width + 12, 26))
    ImageDraw.Draw(canvas).text((6, 6), title[:120], fill=(230, 235, 240))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--montage", type=int, default=0, help="给最差 N 个出并排拼图")
    a = ap.parse_args()
    ai = load("ai_render_index.json")
    rf = {it["model"]: it for it in load("real_render_index.json")["items"]}
    items = [it for it in ai["items"] if it.get("ok") and rf.get(it["model"], {}).get("ok")]
    if a.group:
        items = [it for it in items if a.group in it["group"]]
    if a.limit:
        items = items[:a.limit]
    print("比对 %d 个零件（AI 渲染图 vs 实测几何渲染图）…" % len(items))
    rows = []
    t0 = time.time()
    for i, it in enumerate(items):
        r = rf[it["model"]]
        try:
            ma = mask_of(os.path.join(ROOT, it["mask"]))
            mb = mask_of(os.path.join(ROOT, r["mask"]))
            res = compare(ma, mb)
        except Exception as e:
            res = None
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "%s: %s" % (type(e).__name__, str(e)[:50])})
            continue
        if res is None:
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "掩码为空"})
            continue
        sim, iou, ch, pose = res
        rows.append({"model": it["model"], "group": it["group"], "prompt": it["prompt"],
                     "S": round(sim, 1), "iou": round(iou, 1), "chamfer": round(ch, 2),
                     "pose": pose, "ai_img": it["img"], "ref_img": r["img"]})
        if (i + 1) % 50 == 0:
            print("  %d/%d  %.0fs" % (i + 1, len(items), time.time() - t0), flush=True)
    ok = [r for r in rows if "err" not in r]
    avg = sum(r["S"] for r in ok) / len(ok) if ok else 0.0
    ge95 = sum(1 for r in ok if r["S"] >= 95)
    by = {}
    for r in ok:
        by.setdefault(r["group"], []).append(r["S"])
    print("=" * 84)
    print("平均相似度 %.1f%%，≥95%% 的 %d/%d，失败 %d" % (avg, ge95, len(ok), len(rows) - len(ok)))
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        print("   %-16s %5.1f%%  %3d/%3d" % (g[:16], sum(v) / len(v),
                                             sum(1 for x in v if x >= 95), len(v)))
    with io.open(os.path.join(KB, "render_similarity.json"), "w", encoding="utf-8") as f:
        json.dump({"avg": round(avg, 2), "ge95": ge95, "total": len(ok), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    if a.montage:
        for r in sorted(ok, key=lambda x: x["S"])[:a.montage]:
            montage(os.path.join(ROOT, r["ai_img"]), os.path.join(ROOT, r["ref_img"]),
                    os.path.join(CMP, r["group"], "%s_对比.png" % r["model"]),
                    "相似度 %.1f%% (IoU %.0f%%)  %s" % (r["S"], r["iou"], r["model"]))
    L = ["# AI 提示词建模图 vs 实测几何渲染图：相似度", "",
         "## 口径说明（先看这里）", "",
         "**没有拿 CATIA 截图当基准** —— 那些是整窗截图（深红渐变背景 + 规格树），",
         "边缘提取里混着上千个噪声连通域：实测「真配对 85.3% / 交叉配对 84.1%」，",
         "区分度只有 1.2 个百分点，**没有区分能力**，拿来当验收就是假通过。",
         "（要拿截图当基准必须白底重拍：`tools\\capture_real_images.py` 已支持改背景。）", "",
         "这里用**实测几何**（STEP 实测的包围盒 + 每个圆柱面的直径/轴线/位置）",
         "渲染出参考图，与 AI 建模渲染图比轮廓 —— 同一个渲染器、同一视角、",
         "同一留白，掩码来自品红背景（精确、无噪声）：", "",
         "```",
         "相似度 = 0.5·IoU + 0.5·(1 − 平均对称 chamfer / 0.12·对角线)",
         "```", "",
         "配准只用 ±8% 缩放 + ±14 px 平移（两边都是铺满视口的渲染图，正常就是恒等变换）。", "",
         "## 总体", "",
         "| 指标 | 值 |", "|---|---|",
         "| 比对零件数 | %d |" % len(ok),
         "| 平均相似度 | **%.1f%%** |" % avg,
         "| ≥95%% | **%d / %d** |" % (ge95, len(ok)),
         "| 无法比对 | %d |" % (len(rows) - len(ok)), "",
         "## 按族", "", "| 族 | 平均 | ≥95% | 件数 |", "|---|---|---|---|"]
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        L.append("| %s | %.1f%% | %d | %d |"
                 % (g, sum(v) / len(v), sum(1 for x in v if x >= 95), len(v)))
    worst = sorted(ok, key=lambda r: r["S"])[:40]
    L += ["", "## 最差 40 个（这些才是要改建模逻辑的）", "",
          "| 相似度 | IoU | chamfer(px) | 族 | 型号 |", "|---|---|---|---|---|"]
    for r in worst:
        L.append("| %.1f%% | %.1f%% | %.2f | %s | `%s` |"
                 % (r["S"], r["iou"], r["chamfer"], r["group"], r["model"]))
    if len(rows) - len(ok):
        L += ["", "## 无法比对", "", "| 型号 | 原因 |", "|---|---|"]
        for r in rows:
            if "err" in r:
                L.append("| `%s` | %s |" % (r["model"], r["err"]))
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "AI建模图_vs_实测几何图_相似度.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports\\AI建模图_vs_实测几何图_相似度.md、kb/render_similarity.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
