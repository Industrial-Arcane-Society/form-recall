# -*- coding: utf-8 -*-
r"""把项目整理成"可上传 GitHub 私密仓库"的协作包，放到桌面。

原则（为什么要筛，不能整个目录直接传）：
    · GitHub 单文件硬上限 100 MB、仓库建议 < 1 GB；本项目里
      `_wheels/`（513 MB 的 OCP 轮子）、`kb/real_brep*/`（120+ MB 实测网格）
      属于**可重建的数据**，不该进版本库；
    · 代码、文档、报告、提示词、对比图（22 MB）才是要协作的东西；
    · 大件一律给"一条命令重建"的脚本，README 里写清楚。
"""
from __future__ import annotations

import io
import os
import shutil
import sys

SRC = r"C:\Users\25643\Desktop\ai自动建模插件"
DST = r"C:\Users\25643\Desktop\AI建模助手_协作包"

#: 整目录复制（代码与文档）
DIRS = [
    ("ai_modeler", "ai_modeler"),          # 插件源码
    ("tools", "tools"),                    # 工具/基准/渲染/比对
    ("catia_plugin", "catia_plugin"),      # CATIA 宏与插件
    ("docs", "docs"),                      # 设计文档
    ("AI训练提示词", "datasets/训练提示词"),
    ("assets", "assets"),
]
#: 单个/少量文件复制（顶层文档、脚本）
FILES = [
    ("操作说明_CATIA连接版.md", "docs/操作说明_CATIA连接版.md"),
    ("README.md", "README_upstream.md"),
]
#: 报告目录：md/docx 全要（体量小）
REPORTS_EXT = (".md", ".docx", ".png", ".json", ".csv")
#: kb 里只带"小而关键"的索引与判据，网格另给重建脚本
KB_KEEP = [
    "bolt_library.json", "egt_library.json", "standard_real_library.json",
    "purchased_library.json", "model_geometry.json", "real_geometry.json",
    "real_features.json", "detail_fidelity.json", "real_geometry_bench.json",
    "photo_similarity.json", "reshoot_index.json", "photo_view.json",
    "real_brep_index.json", "real_brep_coarse_index.json", "selflearn_state.json",
    "fixture_gap.json", "catia_catalogs.json",
]


def copytree(s, d, skip=()):
    os.makedirs(d, exist_ok=True)
    n = 0
    for dp, dns, fns in os.walk(s):
        dns[:] = [x for x in dns if x not in ("__pycache__", ".git", ".pytest_cache")]
        rel = os.path.relpath(dp, s)
        for fn in fns:
            if fn.endswith((".pyc", ".pyo")) or fn in skip:
                continue
            sp = os.path.join(dp, fn)
            sub = "" if rel == "." else rel
            dd = os.path.join(d, sub)
            os.makedirs(dd, exist_ok=True)
            shutil.copy2(sp, os.path.join(dd, fn))
            n += 1
    return n


def main():
    if os.path.exists(DST):
        print("目标已存在，先清空：%s" % DST)
        shutil.rmtree(DST, ignore_errors=True)
    os.makedirs(DST, exist_ok=True)
    total = 0
    for s, d in DIRS:
        sp = os.path.join(SRC, s)
        if not os.path.isdir(sp):
            print("跳过（不存在）:", s)
            continue
        n = copytree(sp, os.path.join(DST, d))
        total += n
        print("  %-28s → %-28s %4d 个文件" % (s, d, n))
    for s, d in FILES:
        sp = os.path.join(SRC, s)
        if os.path.exists(sp):
            dp = os.path.join(DST, d)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
            total += 1
    # reports
    rs = os.path.join(SRC, "reports")
    if os.path.isdir(rs):
        n = 0
        for dp, _dn, fns in os.walk(rs):
            for fn in fns:
                if not fn.lower().endswith(REPORTS_EXT):
                    continue
                rel = os.path.relpath(dp, rs)
                dd = os.path.join(DST, "reports", "" if rel == "." else rel)
                os.makedirs(dd, exist_ok=True)
                shutil.copy2(os.path.join(dp, fn), os.path.join(dd, fn))
                n += 1
        total += n
        print("  %-28s → %-28s %4d 个文件" % ("reports", "reports", n))
    # kb 精选
    kb = os.path.join(SRC, "kb")
    kb_dst = os.path.join(DST, "kb")
    os.makedirs(kb_dst, exist_ok=True)
    n = 0
    for fn in KB_KEEP:
        sp = os.path.join(kb, fn)
        if os.path.exists(sp):
            shutil.copy2(sp, os.path.join(kb_dst, fn))
            n += 1
    total += n
    print("  %-28s → %-28s %4d 个文件" % ("kb（精选索引/判据）", "kb", n))
    # 跑图对比：零件比对图（最近几轮）
    cmp_src = os.path.join(SRC, "零件比对图")
    if os.path.isdir(cmp_src):
        n = copytree(cmp_src, os.path.join(DST, "reports", "跑图对比", "零件比对图"))
        total += n
        print("  %-28s → %-28s %4d 个文件" % ("零件比对图", "reports/跑图对比", n))
    # 真实照片样本：每族抽 1 组（含背景帧），其余靠脚本重建
    ph = os.path.join(SRC, "零件真实图片_带背景")
    if os.path.isdir(ph):
        picked = 0
        for fam in sorted(os.listdir(ph)):
            fp = os.path.join(ph, fam)
            if not os.path.isdir(fp):
                continue
            pngs = sorted(f for f in os.listdir(fp) if f.endswith(".png") and "_bg" not in f)
            if not pngs:
                continue
            base = pngs[0]
            d = os.path.join(DST, "examples", "真实照片样本", fam)
            os.makedirs(d, exist_ok=True)
            shutil.copy2(os.path.join(fp, base), os.path.join(d, base))
            bg = base[:-4] + "_bg.png"
            if os.path.exists(os.path.join(fp, bg)):
                shutil.copy2(os.path.join(fp, bg), os.path.join(d, bg))
            picked += 2
        total += picked
        print("  %-28s → %-28s %4d 个文件（每族 1 组）" % ("零件真实图片_带背景", "examples", picked))
    # AI 渲染样本（掩码 + 图）
    rn = os.path.join(SRC, "零件AI建模图片")
    if os.path.isdir(rn):
        picked = 0
        for fam in sorted(os.listdir(rn)):
            fp = os.path.join(rn, fam)
            if not os.path.isdir(fp):
                continue
            pngs = sorted(f for f in os.listdir(fp) if f.endswith("_mask.png"))
            if not pngs:
                continue
            m = pngs[0]
            d = os.path.join(DST, "examples", "AI建模渲染样本", fam)
            os.makedirs(d, exist_ok=True)
            shutil.copy2(os.path.join(fp, m), os.path.join(d, m))
            img = m.replace("_mask.png", ".png")
            if os.path.exists(os.path.join(fp, img)):
                shutil.copy2(os.path.join(fp, img), os.path.join(d, img))
            picked += 1
        total += picked
        print("  %-28s → %-28s %4d 个文件（每族 1 个）" % ("零件AI建模图片", "examples", picked))
    sz = sum(os.path.getsize(os.path.join(dp, f))
             for dp, _dn, fns in os.walk(DST) for f in fns)
    print("\n合计 %d 个文件，%.1f MB" % (total, sz / 1e6))
    print("→ %s" % DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
