# -*- coding: utf-8 -*-
r"""
U1 两段式不变量 —— 全库自检。

把 U1 的两条硬性要求变成每次都能跑的断言：
    阶段 A（规划）：**禁止**数字字面量，数值必须是引用；
    阶段 B（构建）：**只认 ParamTable**，追不到出处的数值 = 违规。

本脚本做三件事：
  1. 在**评测题库全量**上跑 `check_parse()`，统计两个不变量的通过率；
  2. **负向对照**：故意构造违反的输入，确认检查**真的会报**（否则就是空检查）；
  3. 逐条列出仍然追不到出处的参数（如果有），不藏。

用法：python tools\check_twostage.py [EVAL_N]
"""
from __future__ import annotations

import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import twostage as TS      # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def load_cases(limit=200):
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import evaluate as EV
        return EV.load_cases(limit=limit)
    except Exception as e:
        print("  载入题库失败: %s" % e)
        return []


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 160
    print("U1 两段式不变量 —— 全库自检（EVAL_N=%d）" % n)

    # ------------------------------------------------------------ 负向对照
    print("\n[0] 负向对照：检查必须真的会报（否则是空检查）")
    bad_plan = {"plan": [{"kind": "box", "op": "add",
                          "params": {"length": 120, "width": 80}}]}
    ok_b, bad_b = TS.check_no_bare_numbers(bad_plan)
    chk(not ok_b, "含裸数字的 plan 被判违规（%d 处）" % len(bad_b))
    chk(any("length" in s for s in bad_b), "指到了 length")

    class _F(object):
        def __init__(self, kind, params, prov):
            self.kind, self.params, self.provenance = kind, params, prov

    class _D(object):
        def __init__(self, feats):
            self.features = feats

    bad_doc = _D([_F("cylinder", {"radius": 30.0, "segments": 48}, {})])
    ok_c, bad_c = TS.check_feature_numbers(bad_doc)
    chk(not ok_c, "有数值但无出处的特征被判违规")
    chk(any("radius" in s for s in bad_c), "指到了 radius")
    chk(not any("segments" in s for s in bad_c),
        "几何开关 segments 不被误报（它是结构，不是尺寸）")
    good_doc = _D([_F("cylinder", {"radius": 30.0}, {"radius": "explicit"})])
    chk(TS.check_feature_numbers(good_doc)[0], "有出处的特征通过")

    # ------------------------------------------------------------ 全库
    print("\n[1] 题库全量检查")
    cases = load_cases(n)
    if not cases:
        chk(False, "没有载入到任何用例")
    else:
        n_ok_c = n_ok_p = 0
        untrace = Counter()
        unres = Counter()
        for c in cases:
            text = c.get("text") or c.get("p") or ""
            try:
                rep = TS.check_parse(text)
            except Exception as e:
                untrace["<解析异常: %s>" % str(e)[:40]] += 1
                continue
            if rep["construct_traceable"]:
                n_ok_c += 1
            for u in rep["untraceable"]:
                untrace[u] += 1
            if rep["plan_clean"]:
                n_ok_p += 1
            for u in rep["unresolved"]:
                unres[u] += 1
        total = len(cases)
        print("  用例数: %d" % total)
        print("  构建期（数值可追溯到出处）: %d/%d = %.1f%%"
              % (n_ok_c, total, 100.0 * n_ok_c / total))
        print("  规划期（结构表达无裸数字）: %d/%d = %.1f%%"
              % (n_ok_p, total, 100.0 * n_ok_p / total))
        if untrace:
            print("  仍追不到出处的参数（按出现次数）:")
            for k, v in untrace.most_common(12):
                print("     %-34s %d" % (k, v))
        chk(n_ok_p == total, "规划期不变量在全部用例上成立")
        # 构建期允许有少量存量（如实报告），但不能是"大面积"
        chk(n_ok_c >= int(total * 0.95),
            "构建期可追溯率 >= 95%%（实得 %.1f%%）"
            % (100.0 * n_ok_c / total))

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
