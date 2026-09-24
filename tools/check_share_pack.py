# -*- coding: utf-8 -*-
r"""检查"协作包"是不是漏了东西（队友实测踩到：`.gitignore` 误伤把源码排除了）。

为什么必须专门查一次：
    我在 `.gitignore` 里写了 `catia_*.py`（本意是忽略临时生成的 CATIA 脚本），
    结果它同时匹配了正儿八经的源码 `ai_modeler/catia_export.py` ——
    **git 会静默忽略它**，仓库里那个文件根本不存在，安装脚本一跑就报
    "ModuleNotFoundError: ai_modeler.catia_export"。

检查口径：
  1. 逐文件比对"原项目"与"协作包"，把缺失文件分成两类：
     · **有意排除**（大数据/缓存/临时产物，见 .gitignore 的数据策略）；
     · **意外漏掉**（源码、脚本、配置、文档）→ 必须为 0，否则退出码非 0；
  2. 单独校验：所有 `ai_modeler/**.py`、`tools/**.py`、`*.ps1`、`*.vbs`、`*.CATScript`
     必须在包里；
  3. 顺手扫一遍 `.gitignore` 的每条规则，看它会不会误伤包内已有文件
     （用 `fnmatch` 近似 git 的行为，并列出"被规则命中但其实是源码"的文件）。

用法: python tools\check_share_pack.py
输出: reports\协作包完整性检查.md
"""
from __future__ import annotations

import fnmatch
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = r"C:\Users\25643\Desktop\AI建模助手_协作包"
REPORTS = os.path.join(ROOT, "reports")

#: 已知的**路径映射**：原项目路径 → 包内路径（不算漏）
PATH_MAP = {
    "AI训练提示词": "datasets/训练提示词",
    "零件比对图": "reports/跑图对比",
    "操作说明_CATIA连接版.md": "docs/操作说明_CATIA连接版.md",
    "README_upstream.md": "README_upstream.md",
}
#: 明确不进包的本机/临时文件（列出来，避免误判"漏了"）
NOT_PACKED = {
    "config.json", "cache_online.json", "_mig_dbg.json", "_target_urls.json",
    "ai建模插件.doc", "AI建模训练提示词_使用说明.docx",
    "零件比对图.zip",                    # 图片压缩包（数据，见 kb/README_数据说明.md）
    "handbook_grid.jsonl", "handbook_target.jsonl",   # 手册爬取原件 41.8+10.5 MB
    "papers_cn_cache.json", "ai_render_index.json",   # 缓存 / 自动生成的索引
}

#: 有意排除的（与 README 第三节的数据策略一致）
#: EXCLUDE_DIRS 按"目录名"匹配（这些名字在哪儿都是产物）；
#: EXCLUDE_TOP 按"从项目根算起的路径"匹配 —— 必须这样区分：
#:   包里有 `reports/跑图对比/零件比对图/`（121 张判据对比图，是要带上的），
#:   只按名字匹配会把"漏了这 121 张"误判成"有意排除"，等于放过了真的漏。
EXCLUDE_DIRS = {"_wheels", "__pycache__", ".git", "_selftest_out", ".pytest_cache",
                "share_pack_files"}      # 打包暂存区：内容已按最终路径进包
EXCLUDE_TOP = {"kb/real_brep", "kb/real_brep_coarse", "零件参数",
               "零件真实图片", "零件真实图片_带背景", "零件AI建模图片", "零件比对图"}
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".tmp"}
#: 临时文件名前缀（`_refill_pack.py`、`_regression_log.txt` 这类一次性产物）
EXCLUDE_PREFIX = "_"

#: 必须存在的关键文件（少一个就是打包事故）
MUST = [
    "ai_modeler/catia_export.py", "ai_modeler/kernel.py", "ai_modeler/docmodel.py",
    "ai_modeler/partlib.py", "ai_modeler/realparts.py", "ai_modeler/reallib.py",
    "ai_modeler/realholes.py", "ai_modeler/realsolid.py", "ai_modeler/directmesh.py",
    "ai_modeler/occ_kernel.py", "ai_modeler/nlparser.py", "ai_modeler/app.py",
    "ai_modeler/selftest.py", "ai_modeler/__init__.py", "ai_modeler/__main__.py",
    "catia_plugin/AI自动建模助手.CATScript",
    "tools/bench_detail_fidelity.py", "tools/compare_ai_vs_photos.py",
    "tools/render_ai_masks.py", "tools/build_real_brep.py",
    "tools/check_share_pack.py",
    "README.md", "CONTRIBUTING.md", "LICENSE", ".gitignore",
    "scripts/01_安装依赖.ps1", "scripts/04_验收基准.ps1",
    "scripts/05_打包协作包.ps1", "docs/更新协作包到私密仓库.md",
    ".gitattributes", ".github/pull_request_template.md",
    ".github/workflows/acceptance.yml",
    # 运行时依赖的知识库（缺了会 ModuleNotFoundError 或 FileNotFoundError）
    "kb/auto_parts.json", "kb/bolt_std.json", "kb/bearing_std.json",
    "kb/pulley_std.json", "kb/sprocket_std.json", "kb/pin_real_std.json",
    "kb/intent_corrections.json", "kb/real_features.json",
    "kb/real_brep_index.json", "kb/catia_catalogs.json",
    "kb/README_数据说明.md",
]


def rel_files(base):
    out = set()
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in ("__pycache__", ".git", ".pytest_cache")]
        for fn in fns:
            out.add(os.path.relpath(os.path.join(dp, fn), base).replace("\\", "/"))
    return out


def excluded(rel):
    parts = rel.split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    for ex in EXCLUDE_TOP:                 # 只看"从根算起"的路径，避免误伤同名子目录
        if rel == ex or rel.startswith(ex + "/"):
            return True
    if os.path.basename(rel).startswith(EXCLUDE_PREFIX):
        return True            # 一次性临时脚本/日志
    return os.path.splitext(rel)[1].lower() in EXCLUDE_EXT


def main():
    src = rel_files(ROOT)
    pak = rel_files(PACK) if os.path.isdir(PACK) else set()
    if not pak:
        print("协作包不存在：%s" % PACK)
        return 1
    # 包内 basename 索引：打包时会**重新归档**（顶层报告 → docs/历史报告/、
    # 启动脚本 → 启动脚本/、说明 → docs/），所以"改了位置"不等于"漏了"，
    # 只有"整包都找不到同名文件"才算漏。
    pak_names = {}
    for r in pak:
        pak_names.setdefault(os.path.basename(r), []).append(r)
    # 1) 缺失分类
    missing_intended, missing_bad, regrouped = [], [], []
    for r in sorted(src - pak):
        mapped = None
        for a, b in PATH_MAP.items():
            if r == a or r.startswith(a + "/"):
                mapped = b + r[len(a):]
                break
        in_pack = (mapped in pak) if mapped else False
        if excluded(r) or os.path.basename(r) in NOT_PACKED:
            missing_intended.append(r)
        elif in_pack:
            missing_intended.append(r)
        elif os.path.basename(r) in pak_names:
            regrouped.append((r, pak_names[os.path.basename(r)][0]))
        else:
            missing_bad.append(r)
    # 2) 关键文件
    lack = [m for m in MUST if m not in pak]
    # 3) .gitignore 误伤扫描
    rules = []
    gi = os.path.join(PACK, ".gitignore")
    if os.path.exists(gi):
        for line in io.open(gi, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rules.append(line)
    hurt = []
    for r in sorted(pak):
        base = os.path.basename(r)
        for rule in rules:
            if rule.startswith("!"):
                continue                # 否定规则是"放行"，不是"命中"
            pat = rule
            if pat.endswith("/"):
                pat = pat.rstrip("/")
                if r.startswith(pat + "/") or ("/" + pat + "/") in ("/" + r):
                    hurt.append((r, rule))
                    break
            elif fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(r, pat):
                hurt.append((r, rule))
                break
    # 4) 脚本编码检查（这一条比缺文件更隐蔽）
    #    · .ps1：Windows PowerShell 5.1 不认无 BOM 的 UTF-8 → 中文乱码 + **语法解析失败**。
    #      实测 02/03/04 三个"一键脚本"全中招，队友双击就是一闪而过。
    #    · .bat / .vbs：cmd 与 WSH 按 ANSI(GBK) 读，UTF-8 的中文会读坏（含中文路径直接跑不动）。
    enc_bad = []
    for r in sorted(pak):
        low = r.lower()
        p = os.path.join(PACK, r.replace("/", os.sep))
        try:
            with open(p, "rb") as f:
                head = f.read(4)
        except OSError:
            continue
        if low.endswith(".ps1"):
            if head[:3] != b"\xef\xbb\xbf":
                enc_bad.append((r, "缺 UTF-8 BOM（PS 5.1 会解析失败）"))
        elif low.endswith((".bat", ".cmd", ".vbs")):
            if head[:3] == b"\xef\xbb\xbf" or head[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue            # BOM / UTF-16 都不算错
            try:
                io.open(p, encoding="gbk").read()
            except (UnicodeDecodeError, OSError):
                enc_bad.append((r, "不是 GBK(ANSI)（cmd/WSH 会读成乱码）"))
    ok = not missing_bad and not lack and not enc_bad
    print("原项目文件 %d 个，协作包 %d 个" % (len(src), len(pak)))
    print("有意排除：%d 个（大数据/缓存/重新归档）" % len(missing_intended))
    print("  其中重新归档（内容在包里，只是换了目录）：%d 个" % len(regrouped))
    print("**意外漏掉：%d 个**" % len(missing_bad))
    for m in missing_bad[:25]:
        print("   [漏] %s" % m)
    print("关键文件缺失：%d 个" % len(lack))
    for m in lack:
        print("   [缺] %s" % m)
    print("被 .gitignore 规则命中的包内文件：%d 个" % len(hurt))
    for r, rule in hurt[:15]:
        print("   [命中] %-46s ← 规则 %s" % (r, rule))
    print("脚本编码不对：%d 个" % len(enc_bad))
    for r, why in enc_bad[:15]:
        print("   [编码] %-46s %s" % (r, why))
    L = ["# 协作包完整性检查", "",
         "| 指标 | 值 |", "|---|---|",
         "| 原项目文件 | %d |" % len(src),
         "| 协作包文件 | %d |" % len(pak),
         "| 有意排除（大数据/缓存/重新归档） | %d |" % len(missing_intended),
         "| **意外漏掉** | **%d** |" % len(missing_bad),
         "| 关键文件缺失 | %d |" % len(lack),
         "| 被 .gitignore 命中的包内文件 | %d |" % len(hurt),
         "| 脚本编码不对（.ps1 缺 BOM / .bat.vbs 非 GBK） | %d |" % len(enc_bad), ""]
    if regrouped:
        L += ["## 重新归档（内容在包里，路径变了）", "",
              "| 原路径 | 包内路径 |", "|---|---|"]
        L += ["| `%s` | `%s` |" % (a, b) for a, b in regrouped[:200]]
    if missing_bad:
        L += ["", "## 意外漏掉的文件（必须补）", ""] + ["- `%s`" % x for x in missing_bad[:200]]
    if lack:
        L += ["", "## 关键文件缺失", ""] + ["- `%s`" % x for x in lack]
    if hurt:
        L += ["", "## .gitignore 误伤（git 会静默忽略这些文件）", "",
              "| 文件 | 命中的规则 |", "|---|---|"]
        L += ["| `%s` | `%s` |" % (r, rule) for r, rule in hurt[:200]]
    if enc_bad:
        L += ["", "## 脚本编码不对（队友那里会跑不起来）", "",
              "| 文件 | 问题 |", "|---|---|"]
        L += ["| `%s` | %s |" % (r, why) for r, why in enc_bad[:200]]
        L += ["", "修法：`python tools\\fix_script_encoding.py --dir <该仓库> --check` 先看，去掉 `--check` 即修。"]
    if ok and not hurt:
        L += ["", "**结论：包是完整的，且 .gitignore 不会误伤包内文件。**"]
    if ok and not hurt and not enc_bad:
        L += ["", "脚本编码检查：`.ps1` 全部带 UTF-8 BOM（Windows PowerShell 5.1 可解析），"
                  "`.bat`/`.vbs` 全部是 GBK(ANSI)（cmd / WSH 可读）。"]
    os.makedirs(REPORTS, exist_ok=True)
    # newline="\n"：`.gitattributes` 规定 `*.md eol=lf`，报告也用 LF 写，
    # 免得每次 git add 都刷一行 "CRLF will be replaced by LF" 的警告（实测踩过）。
    with io.open(os.path.join(REPORTS, "协作包完整性检查.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("\n→ reports\\协作包完整性检查.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
