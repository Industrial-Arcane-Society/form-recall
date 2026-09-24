# -*- coding: utf-8 -*-
r"""打协作包 zip —— 用 Python 的 zipfile，而不是 PowerShell 的 Compress-Archive。

为什么不用 `Compress-Archive`（实测踩到）：
    它在这个 Windows 上把条目名写成**反斜杠**（`ai_modeler\app.py`），
    而 ZIP 规范要求正斜杠。Windows 的 Expand-Archive 能容忍，
    但队友在 macOS / Linux 上解压会得到一个"名字里带反斜杠的平铺文件堆" ——
    目录结构全丢。用 zipfile 生成就是规范的 `ai_modeler/app.py`，跨平台都对。

顺带做三件 Compress-Archive 不会做的事：
    1. **排除 `.git`**（git 对象库有几十 MB，且不该跟着包走）；
    2. 打完包**回读校验**：关键文件必须在、条目名必须是正斜杠、条目数要对得上；
    3. 打印体积与最大文件，提醒 GitHub 的 100 MB 单文件上限。

用法:
    python tools\make_share_zip.py                     # 默认源/目标
    python tools\make_share_zip.py --src <目录> --out <zip>
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_SRC = r"C:\Users\25643\Desktop\AI建模助手_协作包"
DEF_OUT = r"C:\Users\25643\Desktop\AI建模助手_协作包.zip"
#: 目录名匹配（这些名字在哪儿都是临时产物）
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache", "_selftest_out", "_wheels"}
#: 相对包根的路径匹配（大数据/客户资料；与 .gitignore 的数据策略一致）
#: ⚠ 必须写成"从包根算起的路径"，不能只写目录名：
#:   包里有 `reports/跑图对比/零件比对图/`（121 张判据对比图，**是要带上的**），
#:   只按名字匹配会把它一起排除掉 —— 实测踩过，zip 从 27 MB 掉到 5 MB。
SKIP_PATHS = {"kb/real_brep", "kb/real_brep_coarse", "零件参数",
              "零件真实图片", "零件真实图片_带背景", "零件AI建模图片", "零件比对图"}
SKIP_EXT = {".pyc", ".pyo"}
#: 打完包必须能在 zip 里找到（正斜杠写法，跨平台一致）
MUST = [".gitignore", ".gitattributes", ".github/workflows/acceptance.yml",
        ".github/pull_request_template.md",
        "ai_modeler/catia_export.py", "ai_modeler/realsolid.py",
        "kb/README_数据说明.md", "kb/auto_parts.json", "kb/real_features.json",
        "docs/更新协作包到私密仓库.md", "scripts/05_打包协作包.ps1",
        "tools/check_share_pack.py"]


def collect(src):
    out = []
    skipped = {}
    for dp, dns, fns in os.walk(src):
        keep = []
        for d in dns:
            full = os.path.join(dp, d)
            rel_d = os.path.relpath(full, src).replace("\\", "/")
            if d in SKIP_NAMES or rel_d in SKIP_PATHS:
                n = sum(len(f) for _, _, f in os.walk(full))
                skipped[rel_d] = n
            else:
                keep.append(d)
        dns[:] = keep
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            p = os.path.join(dp, fn)
            out.append((p, os.path.relpath(p, src).replace("\\", "/")))
    for d, n in sorted(skipped.items()):
        print("  已排除目录 %s（%d 个文件，属大数据/临时产物）" % (d, n))
    return sorted(out, key=lambda t: t[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEF_SRC)
    ap.add_argument("--out", default=DEF_OUT)
    a = ap.parse_args()
    src, out = os.path.abspath(a.src), os.path.abspath(a.out)
    if not os.path.isdir(src):
        print("协作包不存在：%s" % src)
        return 1
    files = collect(src)
    if os.path.exists(out):
        os.remove(out)
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in files:
            z.write(p, rel)
            total += os.path.getsize(p)
    mx = max((os.path.getsize(p) for p, _ in files), default=0)
    size = os.path.getsize(out)
    print("已打包 %d 个文件（原始 %.1f MB）→ %s（%.1f MB）"
          % (len(files), total / 1048576.0, out, size / 1048576.0))
    print("最大单文件 %.2f MB（GitHub 上限 100 MB）" % (mx / 1048576.0))

    # 回读校验
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        bad_sep = [n for n in names if "\\" in n]
        lack = [m for m in MUST if m not in names]
        git = [n for n in names if n.startswith(".git/")]
    print("条目数 %d（应为 %d）" % (len(names), len(files)))
    problems = []
    if len(names) != len(files):
        problems.append("条目数与文件数不一致")
    if bad_sep:
        problems.append("有 %d 个条目用了反斜杠（跨平台解压会丢目录）：%s" % (len(bad_sep), bad_sep[:3]))
    if lack:
        problems.append("缺关键文件：%s" % lack)
    if git:
        problems.append("zip 里混进了 .git（%d 条）" % len(git))
    if problems:
        for x in problems:
            print("**问题：%s**" % x)
        return 1
    print("抽查通过：条目全是正斜杠、关键文件齐全、没有 .git。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
