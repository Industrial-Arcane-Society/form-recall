# -*- coding: utf-8 -*-
r"""控制台输出统一成 UTF-8 —— 一行调用，避免中文/符号把脚本搞崩。

为什么需要（实测踩到）：
    1. `.bat` 里 `chcp 65001` 对**已经是 UTF-8 的 Python**没问题，但反过来：
       当输出被**重定向/管道**（比如菜单程序去调用子脚本收集输出）时，
       Python 会退回**系统区域编码（简中 = GBK）**，于是：
         · 正常中文能编出来（GBK 有），但
         · `⇄`(U+21C4)、`✓`(U+2713)、`✗`(U+2717) 这类符号 **GBK 没有** →
           `UnicodeEncodeError` 直接崩，而且是崩在打印状态那一行，看着莫名其妙。
    2. 直接双击 `python tools\sync_menu.py`（不走 .bat）时控制台还是 936，
       UTF-8 输出会乱码 —— 所以这里顺手把控制台代码页也切成 65001。

做法：
    * `SetConsoleOutputCP(65001)`：直接运行时也让控制台能显示 UTF-8；
    * `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`：
      无论输出到控制台还是管道，都按 UTF-8 编码；万一真有编不出的字符，
      用 replace 顶掉字符而不是崩掉整个程序（宁可少一个符号，不能中断同步）。
"""
from __future__ import annotations

import sys


def setup() -> None:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
