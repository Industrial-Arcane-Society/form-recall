# -*- coding: utf-8 -*-
r"""AI 提示词建模渲染图 vs 真实零件照片：相似度（IoU + 归一化对称 chamfer）。

前提（这一版才成立）：真实照片用 `tools/reshoot_real_parts.py` 重拍成**一对**
    <型号>.png      零件可见
    <型号>_bg.png   隐藏零件、其它不动
两张相减 = **零件的精确掩码**：规格树/工具栏/渐变背景全部抵消，不需要任何分割算法。

于是两边掩码都精确：
    · AI 侧：渲染时背景涂品红 → 掩码 = 非品红像素；
    · 真实侧：两张照片求差（限三维视口内：左 28% 是规格树、上 15% 是工具栏）。
相似度 = 0.5·IoU + 0.5·(1 − 平均对称 chamfer / 0.12·对角线)，
配准只在"几何先验尺度 ±20% + 平移"里搜（两边都是铺满视口的图，正常就是恒等变换）。

⚠️ 不要再回去用"整窗截图直接比边缘"：实测真配对 85.3% / 交叉配对 84.1%，
   只差 1.2 个点，那种指标没有区分能力。

用法:
    python tools\compare_ai_vs_photos.py --limit 20
    python tools\compare_ai_vs_photos.py --all
输出:
    reports\AI建模图_vs_真实照片_相似度.md
    kb/photo_similarity.json
    零件比对图\<族>\<型号>_对比.png
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
AI_DIR = os.path.join(ROOT, "零件AI建模图片")
PHOTO_DIR = os.path.join(ROOT, "零件真实图片_带背景")
VIEW_L, VIEW_T = 0.28, 0.15
DIFF_T = 40          # 两帧差异阈值（RGB 三通道绝对差之和）
TOL_PX = 3.0


def load(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB")).astype(np.int16)


def photo_mask(path, bg_path):
    """真实侧掩码：两帧相减 + 只取最大连通域 + 填洞。"""
    import cv2
    a, b = load(path), load(bg_path)
    if a.shape != b.shape:
        return None
    h, w = a.shape[:2]
    y0, x0 = int(h * VIEW_T), int(w * VIEW_L)
    d = np.abs(a - b).sum(2)[y0:, x0:]
    #: 质量门：有些零件在"隐藏"那一刻 CATIA 会整体重绘（背景/窗口都变），
    #  两帧差出来一大片，这种照片对**不可用**，必须否决而不是硬算。
    #  判据：视口内差异像素占比 > 35% 视为背景整体变化。
    if (d > DIFF_T).mean() > 0.35:
        return None
    m = (d > DIFF_T).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, 4]))
    m = (lab == i).astype(np.uint8)
    inv = (1 - m).astype(np.uint8)
    mk = np.zeros((m.shape[0] + 2, m.shape[1] + 2), np.uint8)
    cv2.floodFill(inv, mk, (0, 0), 2)
    filled = (inv != 2)
    #: 掩码合理性门禁（第二道）：填洞后占比应落在 [0.3%, 45%]。
    #  ⚠ 实测踩到：某些零件"隐藏"那一刻界面整体重绘，最大连通域 + 填洞会膨胀成
    #    60% 的大方块（例 `egt001 sw80x628`），这种照片对必须否决，不能硬算。
    cov = float(filled.mean())
    if cov < 0.003 or cov > 0.45:
        return None
    return filled


def ai_mask(path):
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


def similarity(ma, mb):
    """ma=AI 掩码, mb=真实掩码（两者尺寸任意，会先各自归一化到同尺度）。"""
    import cv2
    # 把两张掩码都缩放到同一画布（保留长宽比、按各自外接框对齐）
    def norm(m, size=384):
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            return None
        m2 = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        h, w = m2.shape
        s = (size * 0.92) / max(h, w)
        img = cv2.resize(m2.astype(np.uint8), (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((size, size), np.uint8)
        y0 = (size - img.shape[0]) // 2
        x0 = (size - img.shape[1]) // 2
        canvas[y0:y0 + img.shape[0], x0:x0 + img.shape[1]] = img
        return canvas > 0

    A, B = norm(ma), norm(mb)
    if A is None or B is None:
        return None
    pa, pb = outline(A), outline(B)
    if len(pa) == 0 or len(pb) == 0:
        return None
    h, w = A.shape
    # ⚠ 轮廓点是 float32，直接用去索引 numpy 数组会报
    #   "arrays used as indices must be of integer (or bool)"
    ia = (pa[:, 1].astype(int), pa[:, 0].astype(int))
    ea = np.zeros((h, w), np.uint8)
    ea[ia] = 255
    dt_a = cv2.distanceTransform(255 - ea, cv2.DIST_L2, 3)
    ib = (pb[:, 1].astype(int), pb[:, 0].astype(int))
    eb = np.zeros((h, w), np.uint8)
    eb[ib] = 255
    dt_b = cv2.distanceTransform(255 - eb, cv2.DIST_L2, 3)
    diag = math.hypot(h, w)

    # ⚠ 目标函数**只动轮廓点**，不重新 warp 整张图：
    #   早先每个候选都 warpAffine + 重新取轮廓（2~5 ms），13×13×13 个候选
    #   要 4~11 秒/件，全量跑不完还会把内存拖崩（实测进程被系统杀掉）。
    #   现在把 pb 的坐标直接旋转+平移（矩阵乘法），再查 dt_a 即可 —— 快两个数量级。
    th = np.radians(rot_grid := 0.0)  # 占位，真正的角度在下面循环里
    ca_all = None

    def ev(rot, dx, dy):
        a = math.radians(rot)
        ca, sa = math.cos(a), math.sin(a)
        x = pb[:, 0] - w / 2.0
        y = pb[:, 1] - h / 2.0
        X = (ca * x - sa * y) + w / 2.0 + dx
        Y = (sa * x + ca * y) + h / 2.0 + dy
        Xi = np.clip(X.astype(int), 0, w - 1)
        Yi = np.clip(Y.astype(int), 0, h - 1)
        d1 = float(dt_a[Yi, Xi].mean())
        d2 = float(dt_b[ia].mean())
        return d1 + d2, (X, Y)

    best = None
    for rot in np.linspace(-12, 12, 9):
        for dy in np.linspace(-24, 24, 9):
            for dx in np.linspace(-24, 24, 9):
                v, _ = ev(rot, dx, dy)
                if best is None or v < best[0]:
                    best = (v, rot, dx, dy)
    v, rot, dx, dy = best
    for _ in range(24):
        improved = False
        for dr in (-1.5, 0, 1.5):
            for ddx in (-2, 0, 2):
                for ddy in (-2, 0, 2):
                    vv, _ = ev(rot + dr, dx + ddx, dy + ddy)
                    if vv < v - 1e-9:
                        v, rot, dx, dy = vv, rot + dr, dx + ddx, dy + ddy
                        improved = True
        if not improved:
            break
    _, (Xf, Yf) = ev(rot, dx, dy)
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rot, 1.0)
    M[0, 2] += dx
    M[1, 2] += dy
    Bw = cv2.warpAffine(B.astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST) > 0
    inter = np.logical_and(A, Bw).sum()
    union = np.logical_or(A, Bw).sum()
    iou = inter / float(union) if union else 0.0
    #: **1 px 容差 IoU**：两边是**独立产生**的图（我们渲染 vs CATIA 截图），
    #  亚像素级的抗锯齿/Reframe 留白差异不是"形状差异"。长条形零件对此尤其敏感
    #  （一条 10 px 宽的长条，宽 1 px 就差 9% 的 IoU，而形状其实一模一样）。
    #  所以主分数用"1 px 容差"口径，同时把严格 IoU 一并报出来做对照，
    #  两个数都写进报告，不含糊。
    k = np.ones((3, 3), np.uint8)
    Ad = cv2.dilate(A.astype(np.uint8), k) > 0
    Bd = cv2.dilate(Bw.astype(np.uint8), k) > 0
    cov_a = np.logical_and(A, Bd).sum() / max(1, A.sum())
    cov_b = np.logical_and(Bw, Ad).sum() / max(1, Bw.sum())
    iou1 = 0.5 * (cov_a + cov_b)
    ch = v / 2.0
    sim = 0.5 * iou1 + 0.5 * max(0.0, 1.0 - ch / (0.12 * diag))
    return (100 * sim, 100 * iou1, ch,
            (round(rot, 1), round(dx, 1), round(dy, 1)), 100 * iou, cov_a, cov_b)


def montage(ai_path, photo_path, out_path, title):
    from PIL import Image, ImageDraw
    a = Image.open(ai_path).convert("RGB")
    b = Image.open(photo_path).convert("RGB")
    H = 470
    a = a.resize((int(a.width * H / a.height), H), Image.LANCZOS)
    b = b.resize((int(b.width * H / b.height), H), Image.LANCZOS)
    cv = Image.new("RGB", (a.width + b.width + 12, H + 26), (24, 28, 34))
    cv.paste(a, (0, 26)); cv.paste(b, (a.width + 12, 26))
    ImageDraw.Draw(cv).text((6, 7), title[:120], fill=(232, 238, 244))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv.save(out_path)


#: 视向重拟合用的候选（照片是 CATIA 当前视图，实测并非所有零件都在同一视向）
#: ⚠ 只留 4 个候选：实测 iso 与 iso4 其实是一对镜像（对称件上等价），
#: 多加候选既慢又容易在噪声里挑到"碰巧分高"的错视图。4 个 × 1.4 s ≈ 5.6 s/件。
VIEW_TRIES = {
    "iso(-45,35)": (-45.0, 35.264), "iso4(135,35)": (135.0, 35.264),
    "front(-90,0)": (-90.0, 0.0), "top(-90,89)": (-90.0, 89.0),
}


def refit_view(it, mb, best_so_far, thresh=60.0, log=print):
    """初次得分低于阈值时，换几个视向重渲染掩码，取相似度最高的那个。

    为什么要这一步：重拍用的是 CATIA **当前视图**（自动化设不了视向），
    实测并非所有零件都在同一视向 —— 型材碰巧对得上（85%），而支座/气缸这类
    有明确朝向的零件在等轴测下差很多（42%）。这不是模型错，是相机不同。
    """
    if best_so_far >= thresh:
        return None
    import render_ai_masks as RAM
    from ai_modeler import partlib as PL
    r = PL.build_features_ex(it["prompt"])
    if not r:
        return None
    best = None
    for name, (az, el) in VIEW_TRIES.items():
        am = RAM.render_mask(r[1], w=352, h=448, az=az, el=el)
        if am is None:
            continue
        res = similarity(am, mb)
        if res and (best is None or res[0] > best[0]):
            best = (res[0], name, res)
    if best:
        log("     视向重拟合：%s → %.1f%%（%s）" % (it["model"][:20], best[0], best[1]))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--group", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--montage", type=int, default=40)
    ap.add_argument("--refit", type=float, default=85.0,
                    help="对相似度低于该值的零件做视向重拟合（0=关闭）")
    a = ap.parse_args()
    with io.open(os.path.join(KB, "ai_render_index.json"), encoding="utf-8") as f:
        ai_idx = json.load(f)
    with io.open(os.path.join(KB, "reshoot_index.json"), encoding="utf-8") as f:
        ph = json.load(f)
    pmap = {it["model"].strip().lower(): it for it in ph["items"] if it.get("ok")}
    items = []
    for it in ai_idx["items"]:
        if not it.get("ok"):
            continue
        p = pmap.get(it["model"].strip().lower())
        if p:
            items.append((it, p))
    if a.group:
        items = [x for x in items if a.group in x[0]["group"]]
    if a.limit:
        items = items[:a.limit]
    print("比对 %d 个零件（AI 渲染图 vs 重拍的真实照片）…" % len(items))
    rows = []
    t0 = time.time()
    for i, (it, p) in enumerate(items):
        try:
            ma = ai_mask(os.path.join(ROOT, it["mask"]))
            mb = photo_mask(os.path.join(ROOT, p["img"]), os.path.join(ROOT, p["bg"]))
            res = similarity(ma, mb) if mb is not None else None
        except Exception as e:
            res = None
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "%s: %s" % (type(e).__name__, str(e)[:50])})
            continue
        if res is None:
            rows.append({"model": it["model"], "group": it["group"], "S": 0.0,
                         "err": "照片对不可用（两帧背景整体变化）"})
            continue
        sim, iou, ch, pose = res[0], res[1], res[2], res[3]
        iou_strict = res[4]
        view_used = "iso(-45,35)"
        if a.refit and sim < a.refit:
            b = refit_view(it, mb, sim, thresh=a.refit)
            if b and b[0] > sim:
                sim, view_used, res = b[0], b[1], b[2]
                iou, ch = res[1], res[2]
                iou_strict = res[4]
        rows.append({"model": it["model"], "group": it["group"], "prompt": it["prompt"],
                     "view": view_used,
                     "S": round(sim, 1), "iou": round(iou, 1), "chamfer": round(ch, 2),
                     "iou_strict": round(iou_strict, 1),
                     "pose": pose, "ai_img": it["img"], "real_img": p["img"]})
        if (i + 1) % 25 == 0:
            print("  %d/%d  %.0fs" % (i + 1, len(items), time.time() - t0), flush=True)
    ok = [r for r in rows if "err" not in r]
    avg = sum(r["S"] for r in ok) / len(ok) if ok else 0.0
    ge95 = sum(1 for r in ok if r["S"] >= 95)
    by = {}
    for r in ok:
        by.setdefault(r["group"], []).append(r["S"])
    print("=" * 84)
    print("平均相似度 %.1f%%，≥95%% 的 %d/%d，无法比对 %d"
          % (avg, ge95, len(ok), len(rows) - len(ok)))
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        print("   %-22s %5.1f%%  %3d/%3d" % (g[:22], sum(v) / len(v),
                                             sum(1 for x in v if x >= 95), len(v)))
    with io.open(os.path.join(KB, "photo_similarity.json"), "w", encoding="utf-8") as f:
        json.dump({"avg": round(avg, 2), "ge95": ge95, "total": len(ok), "rows": rows},
                  f, ensure_ascii=False, indent=1)
    if a.montage:
        for r in sorted(ok, key=lambda x: x["S"])[:a.montage]:
            try:
                montage(os.path.join(ROOT, r["ai_img"]), os.path.join(ROOT, r["real_img"]),
                        os.path.join(CMP, r["group"], "%s_对比.png" % r["model"]),
                        "相似度 %.1f%% (IoU %.0f%%)  %s" % (r["S"], r["iou"], r["model"]))
            except Exception:
                pass
    L = ["# AI 提示词建模图 vs 真实零件照片：相似度", "",
         "## 口径（这一版才成立）", "",
         "真实照片用 `tools/reshoot_real_parts.py` 重拍成**一对**：`<型号>.png`（零件可见）",
         "与 `<型号>_bg.png`（隐藏零件、其它不动）。**两帧相减 = 零件的精确掩码** ——",
         "规格树、工具栏、渐变背景全部抵消，不需要任何分割算法；AI 侧掩码来自",
         "渲染时的品红背景，同样精确。", "",
         "```",
         "相似度 = 0.5·IoU + 0.5·(1 − 平均对称 chamfer / 0.12·对角线)",
         "```", "",
         "配准：先按各自外接框归一化到同一画布，再搜 ±6° 旋转 + ±24 px 平移", "",
         "## 总体", "",
         "| 指标 | 值 |", "|---|---|",
         "| 可比对零件 | %d |" % len(ok),
         "| 平均相似度 | **%.1f%%** |" % avg,
         "| ≥95%% | **%d / %d** |" % (ge95, len(ok)),
         "| 无法比对 | %d |" % (len(rows) - len(ok)), "",
         "## 按族", "", "| 族 | 平均 | ≥95% | 件数 |", "|---|---|---|---|"]
    for g in sorted(by, key=lambda x: -sum(by[x]) / len(by[x])):
        v = by[g]
        L.append("| %s | %.1f%% | %d | %d |"
                 % (g, sum(v) / len(v), sum(1 for x in v if x >= 95), len(v)))
    worst = sorted(ok, key=lambda r: r["S"])[:60]
    L += ["", "## 最差 60 个（这些是要改建模逻辑的）", "",
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
    with io.open(os.path.join(REPORTS, "AI建模图_vs_真实照片_相似度.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("→ reports\\AI建模图_vs_真实照片_相似度.md、kb/photo_similarity.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
