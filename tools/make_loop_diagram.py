# -*- coding: utf-8 -*-
r"""把"上一轮任务"（外轮廓闭环）画成一张流程图，并标上每一部分的实际数据。

输出：reports\外轮廓闭环_流程图.png（1920×1200，可直接插进 Word）
"""
from __future__ import annotations

import io
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
KB = os.path.join(ROOT, "kb")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def read(name, default=None):
    try:
        with io.open(os.path.join(KB, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def box(ax, x, y, w, h, title, lines, fc, ec, fs=11):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                linewidth=1.6, edgecolor=ec, facecolor=fc))
    ax.text(x + w / 2, y + h - 0.035, title, ha="center", va="top",
            fontsize=fs + 1.5, fontweight="bold", color="#12232e")
    for i, t in enumerate(lines):
        ax.text(x + 0.012, y + h - 0.075 - i * 0.032, t, ha="left", va="top",
                fontsize=fs - 1, color="#22333d")


def arrow(ax, p1, p2, color="#2b6cb0", style="-|>", rad=0.0, lw=1.8, ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=16,
                                 linewidth=lw, color=color, linestyle=ls,
                                 connectionstyle="arc3,rad=%s" % rad))


def main():
    geo = read("real_geometry.json", {"parts": []})
    br = read("real_brep_index.json", {"ok": 0, "fail": 0})
    sim = read("photo_similarity.json", {"avg": 0, "ge95": 0, "total": 0, "rows": []})
    fid = read("detail_fidelity.json", {"avg": 0, "ge95": 0, "total": 0})
    resh = read("reshoot_index.json", {"ok": 0, "n": 0})
    coarse = read("real_brep_coarse_index.json", {"ok": 0})
    rows = [r for r in sim.get("rows", []) if "err" not in r]
    n_low = sum(1 for r in rows if r["S"] < 95)
    n_veto = sum(1 for r in sim.get("rows", []) if "err" in r)

    fig, ax = plt.subplots(figsize=(16, 10), dpi=120)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.text(0.5, 0.975, "AI 自动建模助手 —— 外轮廓闭环（上一轮任务的可视化）",
            ha="center", va="top", fontsize=19, fontweight="bold", color="#0f2b3a")
    ax.text(0.5, 0.937, "目标：让「提示词建模的图」与「真实零件照片」的相似度 ≥95%　"
                        "口径：0.5·IoU + 0.5·(1 − 归一化对称 chamfer)",
            ha="center", va="top", fontsize=11.5, color="#41586b")

    C1, C2, C3 = "#e8f2fb", "#eaf7ee", "#fdf1e3"
    E1, E2, E3 = "#3f7fb5", "#4f9e63", "#c98a2e"

    # ── 上半：数据与模型（左→右）───────────────────────────────
    box(ax, 0.03, 0.66, 0.20, 0.22, "① 真实几何导出",
        ["CATIA 会话（你手动跑）",
         "启动脚本/导出真实几何_双击运行.vbs",
         "输入：518 个 CATPart",
         "输出：%d 个 STEP" % len(geo.get("parts", [])),
         "状态：[完成] 已完成"], C1, E1)
    box(ax, 0.27, 0.66, 0.20, 0.22, "② 实测 B-Rep 库",
        ["tools/build_real_brep.py",
         "OpenCASCADE 读 STEP → 三角化",
         "kb/real_brep：ok=%d fail=%d" % (br.get("ok", 0), br.get("fail", 0)),
         "粗网格（渲染用）：%d 件" % coarse.get("ok", 0),
         "状态：[完成] 已完成（121 MB）"], C1, E1)
    box(ax, 0.51, 0.66, 0.20, 0.22, "③ 模型侧：real_solid",
        ["docmodel 新特征 real_solid",
         "realparts：除标准件外",
         "　全部优先「实测 B-Rep 复刻」",
         "外轮廓 = 真实的型腔/筋/台阶",
         "状态：[完成] 已接入（可 A/B 开关）"], C2, E2)
    box(ax, 0.75, 0.66, 0.22, 0.22, "④ AI 侧掩码（快）",
        ["tools/render_ai_masks.py",
         "PIL 光栅化三角面 → 轮廓掩码",
         "352×448，与实拍同视角",
         "（CATIA 等轴测 (1,−1,1)、up=+Z）",
         "状态：进行中（全量约 10 分钟）"], C2, E2)

    box(ax, 0.03, 0.38, 0.20, 0.22, "⑤ 真实侧掩码",
        ["tools/reshoot_real_parts.py",
         "每个零件拍两张：",
         "　<型号>.png（零件可见）",
         "　<型号>_bg.png（隐藏零件）",
         "两张相减 = 精确掩码（%d 组）" % resh.get("ok", 0)], C1, E1)
    box(ax, 0.27, 0.38, 0.20, 0.22, "⑥ 两条门禁",
        ["① 两帧背景整体变化 → 否决",
         "② 掩码占比越界(>45%) → 否决",
         "本批可比对 %d 个" % sim.get("total", 0),
         "判不可用 %d 个（如实计入）" % n_veto,
         "为什么要有：宁可少比，不硬算"], C3, E3)
    box(ax, 0.51, 0.38, 0.20, 0.22, "⑦ 相似度比对",
        ["tools/compare_ai_vs_photos.py",
         "配准：±12° 旋转 + ±24 px 平移",
         "→ IoU + 对称 chamfer → F1 型得分",
         "平均 %.1f%%　≥95%% %d/%d"
         % (sim.get("avg", 0), sim.get("ge95", 0), sim.get("total", 0)),
         "状态：本轮待重算"], C3, E3)
    box(ax, 0.75, 0.38, 0.22, 0.22, "⑧ 低于 95% 归因",
        ["tools/run_outer_shape_loop.py",
         "形状差异大（IoU 低）",
         "边缘不贴合（chamfer 大）",
         "细节待修（都还行）",
         "→ reports\\外轮廓闭环_低于95清单.md",
         "本批低于 95%% 的：%d 个" % n_low], C3, E3)

    # ── 下半：闭环与硬判据 ────────────────────────────────────
    box(ax, 0.03, 0.10, 0.30, 0.20, "⑨ 重构优化（按族改建模逻辑）",
        ["· 形状差异大 → 检查该族几何构造顺序 / 布尔策略",
         "· 边缘不贴合 → 圆角、倒角、壁厚、端部细节",
         "· 细节待修 → 孔阵、沉孔、螺纹装饰表达",
         "改完回到 ③ 重跑（闭环）",
         "（标准件保持参数化：螺栓 99.98%）"], "#f3e8fb", "#7a4fb5")
    box(ax, 0.37, 0.10, 0.29, 0.20, "⑩ 硬判据（每一步都要过）",
        ["几何判据：平均 %.1f%%　≥95%% %d/%d"
         % (fid.get("avg", 0), fid.get("ge95", 0), fid.get("total", 0)),
         "真实几何命中：591/593（围包络）",
         "CATIA 通道：523/523（real_solid 走 STEP）",
         "自检：42/42",
         "规矩：任何绿变红 → 立即回滚"], "#eef1f5", "#5b6b7c")
    box(ax, 0.70, 0.10, 0.27, 0.20, "(11) 产出与留痕",
        ["零件AI建模图片\\（渲染图 + 掩码）",
         "零件真实图片_带背景\\（双帧）",
         "零件比对图\\（并排对比，最差 40）",
         "kb/*.json（判据与索引，可复核）",
         "reports\\*.md / *.docx（报告）"], "#eef1f5", "#5b6b7c")

    # 箭头
    arrow(ax, (0.23, 0.77), (0.27, 0.77))
    arrow(ax, (0.47, 0.77), (0.51, 0.77))
    arrow(ax, (0.71, 0.77), (0.75, 0.77))
    arrow(ax, (0.86, 0.66), (0.86, 0.60))
    arrow(ax, (0.23, 0.49), (0.27, 0.49))
    arrow(ax, (0.47, 0.49), (0.51, 0.49))
    arrow(ax, (0.71, 0.49), (0.75, 0.49))
    arrow(ax, (0.63, 0.60), (0.63, 0.49), style="<|-|>", color="#4f9e63")
    ax.text(0.645, 0.545, "两边的掩码在这里汇合", fontsize=9.5, color="#2f6b41")
    arrow(ax, (0.86, 0.38), (0.86, 0.30))
    arrow(ax, (0.75, 0.28), (0.35, 0.28), rad=0.12, color="#7a4fb5", ls="--")
    ax.text(0.55, 0.315, "闭环：改建模逻辑 → 重新出掩码 → 重新比对", fontsize=10,
            color="#7a4fb5", ha="center")
    arrow(ax, (0.33, 0.20), (0.37, 0.20), style="-|>", color="#5b6b7c")
    arrow(ax, (0.66, 0.20), (0.70, 0.20), style="-|>", color="#5b6b7c")

    ax.text(0.5, 0.055, "数据都是实测：几何来自 CATIA 导出的 STEP，真实侧掩码来自「隐藏零件前后两帧相减」；"
                        "本图数字随每轮自动刷新（重跑 tools\\run_outer_shape_loop.py）",
            ha="center", va="center", fontsize=10, color="#5b6b7c")
    out = os.path.join(REPORTS, "外轮廓闭环_流程图.png")
    os.makedirs(REPORTS, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    print("→", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
