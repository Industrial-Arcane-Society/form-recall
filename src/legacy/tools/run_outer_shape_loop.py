# -*- coding: utf-8 -*-
r"""外轮廓闭环：建库 → 重渲染 → 图像比对 → 出"低于 95% 的清单 + 失败归因"。

一条命令跑完整个迭代（用户要求：导出完成后自动对比、低于 95% 的重构优化）。

用法:
    python tools\run_outer_shape_loop.py                # 全流程（STEP 源自动探测）
    python tools\run_outer_shape_loop.py --skip-brep    # 库已建好时跳过第一步
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")
#: CATIA 导出的 STEP 落在**用户真实 TEMP**下（本会话的 TEMP 被沙箱重定向过，别搞混）
STEP_CANDIDATES = [
    r"C:\Users\25643\AppData\Local\Temp\catia_step_out",
    os.path.join(os.environ.get("TEMP", "."), "catia_step_out"),
]


def run(cmd, **kw):
    print("→ %s" % " ".join(cmd), flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=ROOT, **kw)
    print("   用时 %.0fs，退出码 %s" % (time.time() - t0, p.returncode), flush=True)
    return p.returncode


def find_steps():
    for d in STEP_CANDIDATES:
        if os.path.isdir(d):
            n = sum(1 for dp, _dn, fns in os.walk(d) for f in fns
                    if f.lower().endswith((".stp", ".step")))
            if n:
                return d, n
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-brep", action="store_true")
    ap.add_argument("--skip-render", action="store_true")
    a = ap.parse_args()
    src, n = find_steps()
    print("STEP 源：%s（%d 个文件）" % (src, n))
    if not a.skip_brep:
        if not src:
            print("没找到 STEP —— 先在 CATIA 里跑『启动脚本/导出真实几何_双击运行.vbs』")
            return 1
        run([sys.executable, r"tools\build_real_brep.py", "--src", src, "--tol", "0.3"])
    # 库统计
    try:
        with io.open(os.path.join(KB, "real_brep_index.json"), encoding="utf-8") as f:
            d = json.load(f)
        print("实测 B-Rep 库：ok=%d fail=%d（公差 %.2f mm）" % (d["ok"], d["fail"], d["tol"]))
    except Exception as e:
        print("读库索引失败：%s" % e)
    if not a.skip_render:
        run([sys.executable, r"tools\render_ai_parts.py", "--all"])
    run([sys.executable, r"tools\compare_ai_vs_photos.py", "--all", "--montage", "40"])
    # 归因
    try:
        with io.open(os.path.join(KB, "photo_similarity.json"), encoding="utf-8") as f:
            ps = json.load(f)
    except Exception as e:
        print("读比对结果失败：%s" % e)
        return 1
    ok = [r for r in ps["rows"] if "err" not in r]
    low = [r for r in ok if r["S"] < 95]
    print("\n" + "=" * 84)
    print("图像相似度：平均 %.1f%%，≥95%% %d/%d，<95%% 共 %d 个"
          % (ps["avg"], ps["ge95"], ps["total"], len(low)))
    by = {}
    for r in ok:
        by.setdefault(r["group"], []).append(r)
    print("\n按族（<95% 占比高的排前面）：")
    for g in sorted(by, key=lambda x: sum(1 for r in by[x] if r["S"] < 95) / len(by[x]),
                    reverse=True):
        v = by[g]
        nlo = sum(1 for r in v if r["S"] < 95)
        print("   %-24s 平均 %5.1f%%   <95%% %3d/%3d" %
              (g[:24], sum(r["S"] for r in v) / len(v), nlo, len(v)))
    # 失败归因：IoU 低=形状不对；IoU 高但 chamfer 大=边缘不贴合；两者都低=位置/尺度
    print("\n最差 30 个（附归因）：")
    for r in sorted(low, key=lambda x: x["S"])[:30]:
        iou = r["iou"] / 100.0
        ch = r["chamfer"]
        if iou < 0.55:
            why = "形状差异大（外轮廓/型腔没对）"
        elif ch > 20:
            why = "边缘不贴合（局部特征/圆角差异）"
        else:
            why = "整体接近，细节待修"
        print("   %5.1f%%  IoU %4.1f%%  chamfer %5.2f  %-14s %-22s %s"
              % (r["S"], r["iou"], ch, r["group"][:14], r["model"][:22], why))
    with io.open(os.path.join(REPORTS, "外轮廓闭环_低于95清单.md"), "w",
                 encoding="utf-8") as f:
        f.write("# 外轮廓闭环：图像相似度 <95% 清单与归因\n\n")
        f.write("相似度口径：0.5·IoU + 0.5·(1 − 归一化对称 chamfer)；"
                "AI 侧掩码来自渲染（品红背景），真实侧来自重拍双帧差分。\n\n")
        f.write("## 总体\n\n| 指标 | 值 |\n|---|---|\n")
        f.write("| 平均相似度 | %.1f%% |\n| ≥95%% | %d / %d |\n| <95%% | %d |\n\n"
                % (ps["avg"], ps["ge95"], ps["total"], len(low)))
        f.write("## 按族\n\n| 族 | 平均 | <95% | 件数 |\n|---|---|---|---|\n")
        for g in sorted(by, key=lambda x: sum(1 for r in by[x] if r["S"] < 95) / len(by[x]),
                        reverse=True):
            v = by[g]
            f.write("| %s | %.1f%% | %d | %d |\n"
                    % (g, sum(r["S"] for r in v) / len(v),
                       sum(1 for r in v if r["S"] < 95), len(v)))
        f.write("\n## <95% 明细（前 120）\n\n| 相似度 | IoU | chamfer | 族 | 型号 | 归因 |\n"
                "|---|---|---|---|---|---|\n")
        for r in sorted(low, key=lambda x: x["S"])[:120]:
            iou = r["iou"] / 100.0
            why = ("形状差异大" if iou < 0.55 else
                   ("边缘不贴合" if r["chamfer"] > 20 else "细节待修"))
            f.write("| %.1f%% | %.1f%% | %.2f | %s | `%s` | %s |\n"
                    % (r["S"], r["iou"], r["chamfer"], r["group"], r["model"], why))
    print("\n→ reports\\外轮廓闭环_低于95清单.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
