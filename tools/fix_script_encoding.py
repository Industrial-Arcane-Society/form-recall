# -*- coding: utf-8 -*-
r"""把脚本改成"Windows 真能跑"的编码 —— 这一条比缺文件更隐蔽，也更常见。

为什么需要它（都是实测踩出来的，不是照本宣科）：

  * `.ps1`：Windows PowerShell 5.1（就是双击 / `powershell -File` 用的那个）
    **不认没有 BOM 的 UTF-8**，会按 GBK 读；中文一进去就乱码，紧接着**语法解析失败**。
    实测 `02_跑全流程.ps1` / `03_重建大数据.ps1` / `04_验收基准.ps1` 全部
    `Missing closing '}'`、`The string is missing the terminator` —— 队友双击就是一闪而过。
    → 正确做法：**UTF-8 with BOM**（PowerShell 7 也照读，向后兼容）。

  * `.bat`：cmd.exe 默认用 OEM 代码页（简中 = 936/GBK），读 UTF-8 的中文会乱码。
    → 正确做法：存成 **GBK(ANSI)**，并保证 CRLF 换行。

  * `.vbs`：Windows Script Host 同样按 ANSI(GBK) 读；带 BOM 的 UTF-16LE 也支持。
    → UTF-8 无 BOM 的中文会被读坏（路径含中文就直接跑不动）→ 转 **GBK**。

幂等：已经是对的就跳过；编码不兼容（比如含 GBK 打不出的字符）会报出来，不硬转。

用法:
    python tools\fix_script_encoding.py            # 修工作区
    python tools\fix_script_encoding.py --dir <目录>  # 修指定目录（如协作包）
    python tools\fix_script_encoding.py --check     # 只检查不改
"""
from __future__ import annotations

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOM = b"\xef\xbb\xbf"
SKIP_DIRS = {"__pycache__", ".git", "_wheels", ".cache", "artifacts", ".pytest_cache",
             # 数据/临时目录里的脚本不要动（多数是运行时生成的 UTF-16 脚本）
             "零件真实图片", "零件真实图片_带背景", "零件AI建模图片", "零件比对图",
             "零件参数", "kb", "reports"}


def sniff(raw: bytes) -> str:
    if raw[:3] == BOM:
        return "utf8bom"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf16"
    try:
        raw.decode("utf-8")
        return "utf8"
    except UnicodeDecodeError:
        return "gbk"


def fix_ps1(path, raw, check):
    enc = sniff(raw)
    if enc == "utf8bom":
        return "ok", "已经是 UTF-8 BOM"
    if enc == "utf16":
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8", errors="replace")
    if check:
        return "bad", "缺 BOM（PS 5.1 会解析失败）"
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(BOM + text.encode("utf-8"))
    return "fixed", "加上 UTF-8 BOM（PS 5.1 可解析）"


def fix_ansi(path, raw, check):
    """`.bat` / `.vbs` → GBK(ANSI)，CRLF。"""
    enc = sniff(raw)
    if enc == "gbk":
        return "ok", "已经是 GBK(ANSI)"
    if enc == "utf16":
        return "ok", "UTF-16（WSH 支持，不动）"
    text = raw.decode("utf-8")
    # 编码转换前先剥掉 UTF-8 BOM：BOM 本身是 GBK 打不出的字符，
    # 而 cmd 见到 BOM 会把第一行当成乱码命令（实测：'@echo off' 直接报错）。
    text = text.lstrip("\ufeff")
    # 纯 ASCII 的文件两种编码读出来都一样，不算问题（很多 .bat 只有英文命令）
    if all(ord(c) < 128 for c in text):
        return "ok", "纯 ASCII，UTF-8/GBK 都对"
    try:
        data = text.encode("gbk")
    except UnicodeEncodeError as e:
        bad = text[max(0, e.start - 10):e.start + 10]
        return "bad", "含 GBK 打不出的字符（%r），需手工处理" % bad
    if check:
        return "bad", "是 UTF-8，cmd/WSH 会读成乱码"
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(text.encode("gbk"))
    return "fixed", "转成 GBK(ANSI)"


def walk(base):
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in sorted(fns):
            yield os.path.join(dp, fn)


def main():
    import console_io
    console_io.setup()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=ROOT)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    base = os.path.abspath(a.dir)
    print("目录：%s%s" % (base, "（只检查）" if a.check else ""))
    counts = {"ok": 0, "fixed": 0, "bad": 0}
    for p in walk(base):
        low = p.lower()
        if low.endswith(".ps1"):
            fn = fix_ps1
        elif low.endswith((".bat", ".cmd", ".vbs")):
            fn = fix_ansi
        else:
            continue
        with open(p, "rb") as f:
            raw = f.read()
        try:
            st, why = fn(p, raw, a.check)
        except UnicodeDecodeError as e:
            st, why = "bad", "读不出来：%s" % e
        counts[st] += 1
        mark = {"ok": "  正常", "fixed": "  已修", "bad": "**问题"}[st]
        print("%s  %-46s %s" % (mark, os.path.relpath(p, base), why))
    print("\n正常 %d / 已修 %d / 有问题 %d" % (counts["ok"], counts["fixed"], counts["bad"]))
    return 1 if counts["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
