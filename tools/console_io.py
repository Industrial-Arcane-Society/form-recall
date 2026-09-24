# -*- coding: utf-8 -*-
r"""控制台输出统一成 UTF-8 + 行缓冲 + 可同时写日志 —— 避免"黑屏什么都看不到"。

为什么要有这个模块（两次真实事故换来的）：

  1. **编码**：输出被重定向/管道时 Python 会退回系统区域编码（简中 = GBK），
     `⇄`(U+21C4)、`✓`(U+2713) 这类 GBK 没有的字符会让 `print` 直接抛
     `UnicodeEncodeError` 把程序崩掉。
  2. **缓冲（黑屏事故）**：非交互场景下 stdout 是**块缓冲**的，
     几百字节的输出会一直躺在缓冲区里不显示 —— 用户双击打开看到的就是**纯黑窗口**，
     以为程序死了。这里强制 `line_buffering=True`（每行都出去）+ `write_through=True`。
  3. **日志**：我们看不到用户的窗口（截图也可能晚一步），所以除了打印，
     还把同样的内容写到日志文件；出问题时直接看日志就知道卡在哪一行。

用法：
    import console_io
    console_io.setup(log_file=r"...\_sync_assistant.log")
"""
from __future__ import annotations

import io
import os
import sys
import time


class Tee:
    """把写入同时送到"原流"和"日志文件"，并且每次都 flush。"""

    def __init__(self, stream, fp):
        self._s = stream
        self._f = fp

    def write(self, data):
        try:
            self._s.write(data)
            self._s.flush()
        except Exception:
            pass
        try:
            self._f.write(data)
            self._f.flush()
        except Exception:
            pass
        return len(data) if data else 0

    def flush(self):
        for x in (self._s, self._f):
            try:
                x.flush()
            except Exception:
                pass

    # 让 print 的其它属性（encoding 等）透传
    def __getattr__(self, name):
        return getattr(self._s, name)


def setup(log_file: str | None = None, keep_old_log: bool = False) -> str | None:
    """返回实际的日志路径（没写日志就返回 None）。"""
    # 1) 让控制台能显示 UTF-8（直接 python 运行时控制台可能还是 936）
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
    except Exception:
        pass

    # 2) 编码 + 行缓冲 + 直写（黑屏就是这里没设对）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True, write_through=True)
        except Exception:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    # 3) 日志（带时间戳表头，方便对上"用户说的那次"）
    if not log_file:
        return None
    try:
        mode = "a" if keep_old_log else "w"
        fp = io.open(log_file, mode, encoding="utf-8", newline="\n")
        fp.write("\n===== %s  同步助手启动 =====\n"
                 % time.strftime("%Y-%m-%d %H:%M:%S"))
        fp.flush()
        sys.stdout = Tee(sys.stdout, fp)
        sys.stderr = Tee(sys.stderr, fp)
        return log_file
    except Exception:
        return None
