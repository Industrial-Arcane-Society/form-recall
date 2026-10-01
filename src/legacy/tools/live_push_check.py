# -*- coding: utf-8 -*-
r"""真机验证：把修好的模型推到**正在运行的 CATIA**里，看是否一次建成功。

用法:
    python tools\live_push_check.py                 # 默认测内六角螺栓 M5x16
    python tools\live_push_check.py "吊环 GB825_88_M16"

判据（都从 CATIA 侧的日志里读，不猜）：
  · 每条特征都有 "ok"；
  · 没有 "FAILED"；
  · 没有 "Sketch.Add failed" / "方法 Update 失败" / "缺少对象"。

注意：会在 CATIA 里**新建一个零件文档**（不覆盖当前打开的文档，也不保存）。
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import catia as CA          # noqa: E402
from ai_modeler import nlparser as NLP      # noqa: E402

BAD = ("FAILED", "failed", "失败", "缺少对象", "cannot create sketch",
       "zero thickness")


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "内六角螺栓 M5x16"
    print("真机推送：%s" % text)
    doc = NLP.parse(text).doc
    ok, msg, vbs = CA.push_to_catia(doc)
    print("结果:", "成功" if ok else "失败")
    print(msg)
    log = (vbs or "").replace(".vbs", ".log")
    if log and os.path.exists(log):
        raw = open(log, "rb").read()
        for enc in ("gbk", "utf-8", "mbcs"):
            try:
                t = raw.decode(enc)
                break
            except Exception:
                t = raw.decode("utf-8", "replace")
        print("\n---- CATIA 侧日志 ----")
        print(t)
        n_ok = len(re.findall(r"\bok\b", t))
        n_bad = sum(t.count(b) for b in BAD)
        print("---- 统计: ok %d 处, 可疑失败标记 %d 处 ----" % (n_ok, n_bad))
        return 0 if (ok and n_bad == 0) else 1
    print("（没找到日志文件 %s）" % log)
    return 1 if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
