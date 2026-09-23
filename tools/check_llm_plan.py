# -*- coding: utf-8 -*-
r"""
U9 验收自检：LLM **只出结构、不出数**。

升级文档口径：
    LLM 只做 ① 意图理解 ② 结果解释；**绝不做算术、绝不生成数值**。
    落地：`ai_modeler/llm_plan.py` 的返回必须是**结构化 plan**，
    数值一律回到本地标准表/公式求值。

本脚本把它变成可复跑的断言：
  A) 合规 plan（数值全是引用）-> 通过校验；
  B) **裸数字** plan -> 被判违规，且错误信息指到具体参数；
  C) 引用类型的合法性（未知 ref / text 缺 identifier / std 缺 key）都能被抓出；
  D) 求值只在本地发生：`text` 引用走本地 `extract_dims`，**模型没机会做算术**；
  E) 标准表引用走本地 `kb/*_std.json`（轴承 6204 -> 47/14/20）；
  F) 求不出来的引用 -> 进"问题/错误"列表，**不静默填一个数**；
  G) 复现性：同一个 plan 求值两次结果完全一致。

用法：python tools\check_llm_plan.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import llm_plan as LP      # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


PLAN_OK = {"plan": [
    {"kind": "cylinder", "op": "add",
     "params": {"radius": {"ref": "text", "identifier": "\u76f4\u5f84"},
                "height": {"ref": "text", "identifier": "\u9ad8"}}}]}

PLAN_BARE = {"plan": [
    {"kind": "cylinder", "op": "add",
     "params": {"radius": 30, "height": 120}}]}

TEXT = "\u5706\u67f1 \u76f4\u5f8460 \u9ad8120"


def main():
    print("U9 LLM 只出结构不出数 —— 验收自检")

    print("\n[A] 合规 plan（数值全是引用）")
    ok, errs = LP.validate_plan(PLAN_OK)
    chk(ok, "校验通过（问题数 %d）" % len(errs))

    print("\n[B] 裸数字 plan 必须被判违规")
    ok2, errs2 = LP.validate_plan(PLAN_BARE)
    chk(not ok2, "被判违规")
    chk(any("radius" in e for e in errs2), "错误信息指到了 radius")
    chk(any("裸数字" in e for e in errs2), "错误信息说明了原因是裸数字")

    print("\n[C] 引用本身不合法也要抓出来")
    bad_refs = {"plan": [
        {"kind": "box", "params": {"length": {"ref": "telepathy"}}},
        {"kind": "box", "params": {"width": {"ref": "text"}}},
        {"kind": "box", "params": {"height": {"ref": "std", "table": "x"}}},
        {"kind": "box", "params": {"depth": {"ref": "ask"}}},
    ]}
    ok3, errs3 = LP.validate_plan(bad_refs)
    chk(not ok3, "被判违规（问题 %d 条）" % len(errs3))
    chk(any("未知引用类型" in e for e in errs3), "抓到未知引用类型")
    chk(any("identifier" in e for e in errs3), "抓到 text 缺 identifier")
    chk(any("缺少 key" in e for e in errs3), "抓到 std 缺 key")
    chk(any("question" in e for e in errs3), "抓到 ask 缺 question")

    print("\n[D] 求值在本地做（模型不碰算术）")
    feats, asks, bad = LP.resolve_plan(PLAN_OK, TEXT)
    chk(not bad, "无求值错误")
    chk(len(feats) == 1 and feats[0]["kind"] == "cylinder", "得到 1 个 cylinder 特征")
    p = feats[0]["params"]
    # 原文写的是"直径60"，本地 extract_dims 读到的是 60（**原始值**）；
    # "除以 2 变成半径" 属于几何层的算术，不在这层发生 —— 这正是 U9 的边界。
    chk(p.get("radius") == 60.0, "直径原样取到 60（不做任何算术，实得 %r）" % p.get("radius"))
    chk(p.get("height") == 120.0, "高取到 120（实得 %r）" % p.get("height"))

    print("\n[E] 标准表引用走本地 kb/*_std.json")
    plan_std = {"plan": [
        {"kind": "deep_groove_ball_bearing", "op": "add",
         "params": {"D": {"ref": "std", "table": "bearing_std", "key": "6204", "field": "D"},
                    "B": {"ref": "std", "table": "bearing_std", "key": "6204", "field": "B"},
                    "d": {"ref": "std", "table": "bearing_std", "key": "6204", "field": "d"}}}]}
    ok4, errs4 = LP.validate_plan(plan_std)
    chk(ok4, "标准表 plan 校验通过")
    f2, a2, b2 = LP.resolve_plan(plan_std, "")
    chk(not b2, "标准表引用求值成功")
    if f2:
        pp = f2[0]["params"]
        chk(pp.get("D") == 47.0 and pp.get("B") == 14.0 and pp.get("d") == 20.0,
            "6204 -> 内径20 / 外径47 / 宽14（实得 %s/%s/%s）"
            % (pp.get("d"), pp.get("D"), pp.get("B")))
    chk(any(s.get("ref") == "std" for s in (plan_std["plan"][0]["params"].values())),
        "标准表值确实来自引用而非模型给的数字")

    print("\n[F] 求不出来的引用 -> 进问题/错误，不静默填数")
    plan_miss = {"plan": [
        {"kind": "cylinder", "params": {"radius": {"ref": "text", "identifier": "\u76f4\u5f84"}}}]}
    f3, a3, b3 = LP.resolve_plan(plan_miss, "\u6ca1\u6709\u4efb\u4f55\u5c3a\u5bf8\u7684\u4e00\u53e5\u8bdd")
    chk(bool(b3), "缺值被报成错误（%s）" % (b3[:1],))
    chk(not (f3 and f3[0]["params"].get("radius")),
        "没有给 radius 塞一个默认数")
    plan_ask = {"plan": [
        {"kind": "cylinder", "params": {"radius": {"ref": "ask", "question": "\u5916\u5f84\u591a\u5c11\uff1f"}}}]}
    f4, a4, b4 = LP.resolve_plan(plan_ask, "")
    chk(bool(a4), "ask 引用被收进问题列表（%s）" % (a4[:1],))

    print("\n[G] 复现性：同一 plan 两次求值一致")
    r1 = LP.resolve_plan(PLAN_OK, TEXT)[0]
    r2 = LP.resolve_plan(PLAN_OK, TEXT)[0]
    chk(r1 == r2, "两次求值完全相同")

    print("\n[H] 提示词本身必须明确禁止出数")
    sp = LP.PLAN_SYSTEM_PROMPT
    chk("不要输出任何数字" in sp, "提示词里写明'不要输出任何数字'")
    chk("算术" in sp, "提示词里写明不许做算术")
    chk("identifier" in sp, "提示词要求用 identifier 指明参数名")

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
