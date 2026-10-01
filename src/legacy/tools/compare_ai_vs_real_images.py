# -*- coding: utf-8 -*-
r"""把"AI 提示词建的模型渲染图"与"零件真实照片"逐个比对，给出相似度。

用户要求（原话）：「将拍的照片，与零件真实照片里面的零件真实图进行对比。
有相似度低于 95% 的，去解决 ai 建这个零件时候的逻辑代码。」

比对为什么不能直接逐像素比：
  · 真实照片是 **CATIA 整窗截图**（左侧规格树 + 顶部工具栏 + 深红渐变背景）；
  · 渲染图是干净的品红背景、自己的光照；
  · 两者材质/亮度完全不同。逐像素差、直方图、SSIM 都会被这些差异淹没。

采用**轮廓匹配**（工业界做 CAD-图片比对的标准做法）：
  1. 渲染图有精确掩码（背景是品红）→ 取轮廓点；
  2. 真实照片裁出三维视口 → Canny 取边缘 → 距离变换 DT；
  3. 在 (缩放, 平移) 上搜索，让模型轮廓点尽量落在照片边缘上（chamfer 匹配）；
  4. 打分用 F1：
       · **精确率 P** = 模型轮廓点里"能在 2.5 px 内找到照片边缘"的比例
         （模型有的形状，照片里必须有）；
       · **召回率 R** = 照片边缘里"落在模型轮廓 12 px 带内"的那些点中，
         能被模型轮廓解释的比例（模型不能漏掉照片里就在轮廓附近的特征）。
     远处的东西（规格树、工具栏、别的零件）不参与 R —— 这是本指标
     已知的边界，写在报告里。

另外输出**并排拼图**（左：AI 建模渲染图；右：真实照片），方便肉眼复核。

用法:
    python tools\compare_ai_vs_real_images.py --limit 8
    python tools\compare_ai_vs_real_images.py --group 销座
    python tools\compare_ai_vs_real_images.py --all
输出:
    reports\AI建模图_vs_真实图_相似度.md
    kb/image_similarity.json
    零件比对图\<族>\<型号>_对比.png（并排拼图）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DIR = os.path.join(ROOT, "零件真实图片")
AI_DIR = os.path.join(ROOT, "零件AI建模图片")
CMP_DIR = os.path.join(ROOT, "零件比对图")
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

VIEW_LEFT = 0.28      # 三维视口：左边 28% 是规格树
VIEW_TOP = 0.15       # 上边 15% 是工具栏
TOL_PX = 2.5          # 轮廓命中容差
BAND_PX = 12.0        # 召回统计带


def load_gray(path):
    return np.asarray(Image.open(path).convert("L"))


def load_mask(path):
    return np.asarray(Image.open(path).convert("L")) > 128


def outline_points(mask, step=2):
    """掩码轮廓点（降采样）。"""
    import cv2
    m = mask.astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return np.zeros((0, 2), np.float32)
    c = max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    return c[::step]


def photo_edges(real_path, keep_n=3, thr_grad=60.0):
    """真实照片 → 视口裁剪 + 边缘点。返回 (edges_pts Nx2, view_w, view_h, sub)。

    ⚠ 这一段的三个坑（都是实测踩出来的，直接决定相似度能不能用）：

    1. **先高斯模糊再求梯度**。CATIA 截图的背景是带抖动的渐变，不模糊的话
       梯度图里全是高频噪点（实测 Canny 出 1.17 万个小连通域）。
    2. **不做 2×2 开运算去噪**：Canny 的边缘本来就 1 px 宽，2×2 开运算会把
       99% 的边缘一起抹掉（实测只剩 8~94 个像素），匹配直接归零。
    3. **只留最大的几个连通域**。模糊之后，零件的轮廓就是**面积最大的那个
       连通域**（实测 2.5~3.3 万像素，是第二名的 10~50 倍）。把上千个小噪点
       全留着当目标，反而会把匹配带跑。
    """
    import cv2
    g = load_gray(real_path)
    h, w = g.shape
    x0, y0 = int(w * VIEW_LEFT), int(h * VIEW_TOP)
    sub = g[y0:, x0:]
    b = cv2.GaussianBlur(sub, (0, 0), 2.0)
    gx = cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(b, cv2.CV_32F, 0, 1, ksize=3)
    E = np.hypot(gx, gy)
    m = (E > thr_grad).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return np.zeros((0, 2), np.float32), sub.shape[1], sub.shape[0], sub
    order = np.argsort(-stats[1:, 4])[:keep_n] + 1
    keep = np.zeros_like(m)
    for i in order:
        keep[lab == i] = 1
    ys, xs = np.nonzero(keep)
    return np.column_stack([xs, ys]).astype(np.float32), sub.shape[1], sub.shape[0], sub


def _edge_centroid(edges_pts):
    return edges_pts.mean(0) if len(edges_pts) else np.array([0.0, 0.0], np.float32)


def fit_and_score(model_pts, edges_pts, vw, vh):
    """在 (缩放, 平移) 上做**对称 chamfer** 匹配，返回最优参数与 P/R/F1。

    为什么必须对称（这一步是反复试错换来的）：
      · 只用"模型轮廓 → 照片边缘"的平均距离：模型**缩小**塞进一块边缘密集的
        噪声区就能把平均值降下去（实测自对照被缩到 0.51 倍）。
      · 只用命中率：2.5 px 容差下几乎是冲击函数，差 10 px 就掉到 0，搜索乱落。
      · 只用"沿轮廓的边缘能量最大"：同样可以被"缩到噪声区"骗到（实测缩到 0.31）。
    对称 chamfer = 模型轮廓到照片边缘的平均距离 **+** 照片边缘到模型轮廓的
    平均距离：缩小会让后半项变大，放大也一样，只有真正对齐才最小 ✓。
    后半项要算模型轮廓的距离变换，所以在 1/4 分辨率上算（快 16 倍）。
    """
    import cv2
    if len(model_pts) == 0 or len(edges_pts) == 0:
        return None
    # 照片侧：全分辨率距离变换（一次）
    em = np.zeros((vh, vw), np.uint8)
    ei_full = np.clip(edges_pts.astype(int), [0, 0], [vw - 1, vh - 1])
    em[ei_full[:, 1], ei_full[:, 0]] = 255
    dt_photo = cv2.distanceTransform(255 - em, cv2.DIST_L2, 3)
    mmin, mmax = model_pts.min(0), model_pts.max(0)
    c0 = (mmin + mmax) / 2.0
    mspan = float(max(mmax - mmin))
    if mspan <= 1e-6:
        return None
    pc = _edge_centroid(edges_pts)
    #: 尺度只能用**几何先验**锁在小范围内，不能放开搜：
    #  两边都是"把零件铺满视口"（CATIA 的 Reframe / 我们渲染的 margin=6%），
    #  所以"渲染像素 → 照片像素"的倍率 ≈ 0.8·min(vw,vh) / 模型轮廓跨度(渲染像素)。
    #  ⚠ 放开搜会被"缩进内部特征"骗到：模型缩小后，轮廓正好压在零件内部那些
    #    棱线上，chamfer 反而更小（实测自对照被缩到 0.53，分数只有 17%）。
    s_prior = (0.80 * min(vw, vh)) / mspan
    base = s_prior

    def score_of(s, cx, cy):
        X = np.clip((base_pts := (model_pts - c0))[:, 0] * s + cx, 0, vw - 1).astype(int)
        Y = np.clip(base_pts[:, 1] * s + cy, 0, vh - 1).astype(int)
        d = dt_photo[Y, X]
        return float(np.mean(d)), float(np.mean(d <= TOL_PX))

    best = None
    for s in np.linspace(s_prior * 0.80, s_prior * 1.20, 9):
        for cy in np.linspace(pc[1] - 0.25 * vh, pc[1] + 0.25 * vh, 19):
            for cx in np.linspace(pc[0] - 0.25 * vw, pc[0] + 0.25 * vw, 19):
                md, hit = score_of(s, cx, cy)
                if best is None or md < best[0]:
                    best = (md, s, cx, cy, hit)
    if best is None:
        return None
    sc, s, cx, cy = best[0], best[1], best[2], best[3]
    step_s, step_t = 0.01 * s_prior, 0.08 * min(vw, vh)
    #: 细化也必须**守在先验范围内**：不设界的话细化会一步步往小尺度漂
    #  （每步都"更优"），最后缩到零件内部棱线上（实测自对照漂到 0.515）。
    lo_s, hi_s = s_prior * 0.78, s_prior * 1.22
    for _ in range(24):
        improved = False
        for ds in (-step_s, 0.0, step_s):
            if not (lo_s <= s + ds <= hi_s):
                continue
            for dx in (-step_t, 0.0, step_t):
                for dy in (-step_t, 0.0, step_t):
                    md, hit = score_of(s + ds, cx + dx, cy + dy)
                    if md < sc - 1e-9:
                        sc, s, cx, cy = md, s + ds, cx + dx, cy + dy
                        improved = True
        if not improved:
            step_s *= 0.5
            step_t *= 0.5
            if step_t < 0.6:
                break
    _md, _hit = score_of(s, cx, cy)
    base_pts = model_pts - c0
    X = np.clip((base_pts[:, 0] * s + cx).astype(int), 0, vw - 1)
    Y = np.clip((base_pts[:, 1] * s + cy).astype(int), 0, vh - 1)
    P = float(np.mean(dt_photo[Y, X] <= TOL_PX))
    mm = np.zeros((vh, vw), np.uint8)
    mm[Y, X] = 255
    mm = cv2.dilate(mm, np.ones((3, 3), np.uint8))
    dm = cv2.distanceTransform(255 - mm, cv2.DIST_L2, 3)
    de = dm[ei_full[:, 1], ei_full[:, 0]]
    band = de <= BAND_PX
    R = float(np.mean(de[band] <= TOL_PX)) if band.any() else 0.0
    F1 = (2 * P * R / (P + R)) if (P + R) > 0 else 0.0
    return {"P": P, "R": R, "F1": F1, "scale": s, "cx": float(cx), "cy": float(cy),
            "chamfer": sc}


def montage(ai_img, real_path, out_path, title):
    a = Image.open(ai_img).convert("RGB")
    b = Image.open(real_path).convert("RGB")
    h = 520
    a = a.resize((int(a.width * h / a.height), h), Image.LANCZOS)
    b = b.resize((int(b.width * h / b.height), h), Image.LANCZOS)
    W = a.width + b.width + 12
    canvas = Image.new("RGB", (W, h + 26), (24, 28, 34))
    canvas.paste(a, (0, 26))
    canvas.paste(b, (a.width + 12, 26))
    d = ImageDraw.Draw(canvas)
    d.text((6, 6), title[:110], fill=(230, 235, 240))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--montage-worst", type=int, default=40)
    a = ap.parse_args()

    with io.open(os.path.join(KB, "ai_render_index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    items = [it for it in idx["items"] if it.get("ok")]
    if a.group:
        items = [it for it in items if a.group in it["group"]]
    if a.limit:
        items = items[:a.limit]
    if not a.all and not a.group and not a.limit:
        items = items[:20]
    print("比对 %d 个零件…" % len(items))

    rows = []
    t0 = time.time()
    for i, it in enumerate(items):
        img = os.path.join(ROOT, it["img"])
        msk = os.path.join(ROOT, it["mask"])
        real = os.path.join(REAL_DIR, it.get("real") or "")
        if not it.get("real"):
            # 索引里没记真实图路径 → 按型号在小写名里找
            stem = it["model"].strip().lower()
            found = None
            for dp, _dn, fns in os.walk(REAL_DIR):
                for fn in fns:
                    if os.path.splitext(fn)[0].strip().lower() == stem:
                        found = os.path.join(dp, fn)
                        break
                if found:
                    break
            real = found or ""
        if not real or not os.path.exists(real):
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "找不到实拍图"})
            continue
        try:
            mp = outline_points(load_mask(msk), step=2)
            ep, vw, vh, _sub = photo_edges(real)
            r = fit_and_score(mp, ep, vw, vh)
        except Exception as e:
            r = None
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "%s: %s" % (type(e).__name__, str(e)[:60])})
            continue
        if r is None:
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "匹配失败（模型轮廓或照片边缘为空）"})
            continue
        # 主分数用 **P（模型轮廓落在真实照片边缘上的比例）**：
        #   R（照片边缘被解释的比例）会被截图里的上千个噪声连通域拉低，
        #   只能当参考；P 对"模型像不像真实零件"是有区分度的（实测正确件 76%，
        #   负对照 ~12%）。见报告里的"已知边界"。
        row = {"model": it["model"], "group": it["group"], "prompt": it["prompt"],
               "S": round(100 * r["P"], 1), "P": round(100 * r["P"], 1),
               "R": round(100 * r["R"], 1), "F1": round(100 * r["F1"], 1),
               "scale": round(r["scale"], 3),
               "ai_img": it["img"], "real_img": os.path.relpath(real, ROOT)}
        rows.append(row)
        if (i + 1) % 20 == 0:
            print("  %d/%d  %.0fs" % (i + 1, len(items), time.time() - t0), flush=True)

    ok = [r for r in rows if "err" not in r]
    ge95 = sum(1 for r in ok if r["S"] >= 95)
    avg = sum(r["S"] for r in ok) / len(ok) if ok else 0.0
    by = {}
    for r in ok:
        by.setdefault(r["group"], []).append(r["S"])
    print("=" * 84)
    print("平均相似度 %.1f%%，≥95%% 的 %d/%d，失败 %d" % (avg, ge95, len(ok), len(rows) - len(ok)))
    for g in sorted(by, key=lambda x: -(sum(by[x]) / len(by[x]))):
        v = by[g]
        print("   %-16s %5.1f%%  %3d/%3d" % (g[:16], sum(v) / len(v),
                                             sum(1 for x in v if x >= 95), len(v)))

    with io.open(os.path.join(KB, "image_similarity.json"), "w", encoding="utf-8") as f:
        json.dump({"avg": round(avg, 2), "ge95": ge95, "total": len(ok),
                   "tol_px": TOL_PX, "band_px": BAND_PX, "rows": rows},
                  f, ensure_ascii=False, indent=1)

    # 最差 N 个出并排拼图
    worst = sorted(ok, key=lambda r: r["S"])[:a.montage_worst]
    for r in worst:
        out = os.path.join(CMP_DIR, r["group"], "%s_对比.png" % r["model"])
        try:
            montage(os.path.join(ROOT, r["ai_img"]),
                    os.path.join(ROOT, r["real_img"]), out,
                    "AI 建模 %.1f%% (P%.0f/R%.0f)  %s" % (r["S"], r["P"], r["R"], r["model"]))
        except Exception:
            pass

    L = ["# AI 提示词建模图 vs 零件真实照片：相似度", "",
         "## 相似度怎么算的（先看这里，避免误读）", "",
         "真实照片是 **CATIA 整窗截图**（左侧规格树、顶部工具栏、深红渐变背景），",
         "渲染图是干净品红背景 —— 逐像素比、SSIM、直方图都会被这些差异淹没。",
         "所以用**轮廓匹配**（chamfer 匹配，CAD 与图片比对的标准做法）：", "",
         "1. 渲染图有精确掩码（背景是品红）→ 取轮廓点；",
         "2. 真实照片裁出三维视口 → Canny 边缘 → 距离变换；",
         "3. 在 (缩放, 平移) 上搜索，让模型轮廓尽量落在照片边缘上；",
         "4. 打分 = F1(精确率 P, 召回率 R)：",
         "   · P = 模型轮廓点里能在 %.1f px 内找到照片边缘的比例（模型有的，照片必须有）；"
         % TOL_PX,
         "   · R = 照片边缘里落在模型轮廓 %.0f px 带内的点中能被解释的比例"
         % BAND_PX,
         "     （模型不能漏掉轮廓附近的特征）。", "",
         "**已知边界**：远离模型轮廓的东西（规格树、工具栏、别的零件）不参与 R；",
         "另外轮廓相同的两个零件（例如外径一样的实心盘与空心盘，若内孔不在轮廓上）",
         "光看轮廓分不出来 —— 那部分由 `bench_detail_fidelity` 的孔特征项兜着。", "",
         "> 数值上：P、R 都按 %.1f px 命中判定，F1 为两者调和平均；" % TOL_PX,
         "> 并排拼图在 `零件比对图\\` 里（最差 %d 个）。" % a.montage_worst, "",
         "## 总体", "",
         "| 指标 | 值 |", "|---|---|",
         "| 比对零件数 | %d |" % len(ok),
         "| 平均相似度 | **%.1f%%** |" % avg,
         "| ≥95%% | **%d / %d** |" % (ge95, len(ok)),
         "| 无法比对 | %d |" % (len(rows) - len(ok)), "",
         "## 按族", "", "| 族 | 平均 | ≥95% | 件数 |", "|---|---|---|---|"]
    for g in sorted(by, key=lambda x: -(sum(by[x]) / len(by[x]))):
        v = by[g]
        L.append("| %s | %.1f%% | %d | %d |"
                 % (g, sum(v) / len(v), sum(1 for x in v if x >= 95), len(v)))
    L += ["", "## 最差 %d 个（要看图的去 `零件比对图\\`）" % len(worst), "",
          "| 相似度 | P | R | 族 | 型号 |", "|---|---|---|---|---|"]
    for r in worst:
        L.append("| %.1f%% | %.0f | %.0f | %s | `%s` |"
                 % (r["S"], r["P"], r["R"], r["group"], r["model"]))
    if len(rows) - len(ok):
        L += ["", "## 无法比对的", "", "| 型号 | 原因 |", "|---|---|"]
        for r in rows:
            if "err" in r:
                L.append("| `%s` | %s |" % (r["model"], r["err"]))
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "AI建模图_vs_真实图_相似度.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports\\AI建模图_vs_真实图_相似度.md、kb/image_similarity.json、零件比对图\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())
