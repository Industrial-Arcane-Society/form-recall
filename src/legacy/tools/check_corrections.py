# -*- coding: utf-8 -*-
r"""
U4 意图纠错层 —— 验收自检。

验收口径（升级文档 U4 原文）：
    **纠正 3 次以内的表述，重复出现时不再需要纠正（命中率 100%）。**

本脚本把它变成可复跑的四组断言：
  A) 纠正落盘：`record()` 之后 `kb/intent_corrections.json` 里确实有这条；
  B) **同一句**再解析 -> 纠正生效；
  C) **同类但数字不同**的句子 -> 纠正同样生效（这是"泛化"的关键，
     照原文存键的实现会在这里失败）；
  D) **不同类别**的句子 -> **不受影响**（纠正不能污染别的零件）；
  E) 用户可在界面上看到"已套用你的意图纠正"（不静默改值）。

用法：python tools\check_corrections.py
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import corrections as C      # noqa: E402
from ai_modeler import nlparser as NLP       # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


T_SAME = "\u591a\u69fd\u5e26\u8f6e \u5185\u5f8425 \u5916\u5f84100 \u5bbd\u5ea630 \u69fd\u65703 A\u578b"
T_LIKE = "\u591a\u69fd\u5e26\u8f6e \u5185\u5f8430 \u5916\u5f84120 \u5bbd\u5ea640 \u69fd\u65704 A\u578b"
T_OTHER = "\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8450 \u9f7f\u657020 XL\u578b"


def groove_type(text):
    r = NLP.parse(text)
    for f in r.doc.features:
        if f.kind == "vbelt_pulley":
            return f.params.get("groove_type")
    return None


def timing_type(text):
    r = NLP.parse(text)
    for f in r.doc.features:
        if f.kind == "timing_pulley":
            return f.params.get("tooth_type")
    return None


def main():
    print("U4 意图纠错层自检")
    C.reload()
    backup = json.dumps(C._load(), ensure_ascii=False) if C.rules() else None
    try:
        C.clear()

        print("\n[A] 纠正前：基线")
        chk(groove_type(T_SAME) == "A", "原句槽型读作 A（实得 %s）" % groove_type(T_SAME))
        chk(groove_type(T_LIKE) == "A", "同类句槽型读作 A（实得 %s）" % groove_type(T_LIKE))

        print("\n[B] 记录一条纠正（A -> B）")
        rule = C.record("\u591a\u69fd\u5e26\u8f6e", "t", "B", source_text=T_SAME,
                        note="\u7528\u6237\u6539\u6210 B \u578b")
        chk(bool(rule.get("skeleton")), "纠正带可泛化骨架：%r" % rule.get("skeleton"))
        chk(os.path.exists(C.PATH), "已落盘 %s" % os.path.basename(C.PATH))
        with io.open(C.PATH, encoding="utf-8") as f:
            db = json.load(f)
        chk(len(db.get("rules") or []) == 1, "文件里有 1 条规则")

        print("\n[C] 同一句：纠正生效")
        chk(groove_type(T_SAME) == "B", "同句槽型 -> B（实得 %s）" % groove_type(T_SAME))

        print("\n[D] 同类但数字不同：纠正也生效（泛化）")
        got = groove_type(T_LIKE)
        chk(got == "B", "同类句槽型 -> B（实得 %s）" % got)

        print("\n[E] 不同类别：不受影响（不污染）")
        chk(timing_type(T_OTHER) == "XL",
            "同步带轮齿型仍是 XL（实得 %s）" % timing_type(T_OTHER))

        print("\n[F] 用户可见：提示里说明套用了纠正")
        r = NLP.parse(T_LIKE)
        hits = [s for s in r.steps if "\u610f\u56fe\u7ea0\u6b63" in s]
        warns = [w for w in r.warnings if "\u610f\u56fe\u7ea0\u6b63" in w]
        chk(bool(hits or warns), "解析结果里出现了「意图纠正」提示")

        print("\n[G] 命中计数（用于'重复出现不再需要纠正'的统计）")
        chk(C.bump(T_SAME, "\u591a\u69fd\u5e26\u8f6e") >= 1, "命中计数递增")

    finally:
        C.clear()
        if backup:
            with io.open(C.PATH, "w", encoding="utf-8") as f:
                f.write(backup)
        C.reload()

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
