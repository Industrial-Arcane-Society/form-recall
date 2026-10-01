# -*- coding: utf-8 -*-
r"""
带轮/标准件 提示词识别 —— 多轮"学习"评测脚本。

这里的"学习"指的是**规则与标准知识库的迭代**（不是训练神经网络）：
  每一轮 = 跑一遍题库 → 统计命中率 → 定位失败 → 改规则/补标准数据 → 下一轮。
轮次结果全部落盘，可复现、可对比，因此能被验证"确实在变准"。

评测维度（每题都逐项判定，全对才算通过）：
  1) 是否识别成功（parser.ok）
  2) 主特征类型是否正确（vbelt_pulley / timing_pulley …）
  3) 关键参数是否正确（槽数/槽型/节距直径/内孔/齿数）
  4) 标准推导是否正确（V 带轮的外径 da = dd + 2·ha，槽数、槽型）
  5) 是否给出了"提示词 → 特征"对照说明
  6) 是否产生了不该有的多余特征（例如把带轮误建成圆柱+方槽）

输出：
  reports\pulley_learning_round<N>.json    每轮明细
  reports\带轮提示词学习报告.md             汇总报告（含逐轮命中率与失败原因）

运行: python tools\learn_pulley_prompts.py            # 跑全部轮次
      python tools\learn_pulley_prompts.py --rounds 1 # 只跑第 1 轮
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser, std_pulley  # noqa: E402

REPORTS = os.path.join(ROOT, "reports")
BANK = os.path.join(ROOT, "kb", "prompt_cases_pulley.json")

# --------------------------------------------------------------------------
# 题库第 1 批：V 带轮（GB/T 10412）。写法尽量覆盖真实用户的口语与符号混用。
# --------------------------------------------------------------------------
ROUND1 = [
    # --- 标准写法：零件名 + 参数名 ---
    dict(p="多槽带轮 内径25 外径100 宽度30 槽数3", kind="vbelt_pulley",
         want=dict(grooves=3, bore_d=25)),
    dict(p="V带轮 基准直径120 内径30 轮宽40 槽数4 A型", kind="vbelt_pulley",
         want=dict(grooves=4, groove_type="A", bore_d=30)),
    dict(p="皮带轮 节圆直径80 内径20 宽度20 槽数2 Z型", kind="vbelt_pulley",
         want=dict(grooves=2, groove_type="Z", bore_d=20)),
    dict(p="多楔带轮 直径200 孔径45 槽数5 B型", kind="vbelt_pulley",
         want=dict(grooves=5, groove_type="B", bore_d=45)),
    dict(p="窄V带轮 SPB 基准直径180 内径40 槽数5", kind="vbelt_pulley",
         want=dict(grooves=5, groove_type="B", bore_d=40)),
    # --- 符号式 ---
    dict(p="带轮 d25 D100 B30 n3 A型", kind="vbelt_pulley",
         want=dict(grooves=3, groove_type="A", bore_d=25)),
    dict(p="V带轮 Ø25 基准直径Ø100 宽30 槽数3 A", kind="vbelt_pulley",
         want=dict(grooves=3, groove_type="A")),
    dict(p="多槽带轮 孔径25mm 基准直径100mm 轮宽30mm 槽数3", kind="vbelt_pulley",
         want=dict(grooves=3, bore_d=25)),
    # --- 只说槽型/槽数，直径缺省 ---
    dict(p="3槽A型V带轮 内径25 宽30", kind="vbelt_pulley",
         want=dict(grooves=3, groove_type="A")),
    dict(p="双槽带轮 基准直径80 内径20", kind="vbelt_pulley", want=dict()),
    # --- 中文数字 / "个槽" ---
    dict(p="带轮 外径100 内径25 宽度30 3个槽 A型", kind="vbelt_pulley",
         want=dict(grooves=3, groove_type="A")),
    dict(p="四槽V带轮 基准直径160 内径35 A型", kind="vbelt_pulley",
         want=dict(groove_type="A")),
    # --- 大直径（应走辐条/辐板） ---
    dict(p="多槽带轮 基准直径315 内径50 槽数6 C型", kind="vbelt_pulley",
         want=dict(grooves=6, groove_type="C")),
    dict(p="V带轮 基准直径400 内径60 槽数8 D型", kind="vbelt_pulley",
         want=dict(grooves=8, groove_type="D")),
    # --- 小直径 ---
    dict(p="小带轮 基准直径50 内径12 槽数1 Z型", kind="vbelt_pulley",
         want=dict(grooves=1, groove_type="Z")),
    # --- 同步带轮 ---
    dict(p="同步带轮 节圆直径80 内径20 宽30 齿数20 XL型", kind="timing_pulley",
         want=dict(teeth=20, tooth_type="XL", bore_d=20)),
    dict(p="同步带轮 节圆直径120 内径30 齿数36 5M型", kind="timing_pulley",
         want=dict(teeth=36, tooth_type="5M")),
    dict(p="同步轮 齿数24 节圆直径60 内径15 T5型", kind="timing_pulley",
         want=dict(teeth=24, tooth_type="T5")),
    dict(p="圆弧齿带轮 节圆直径100 内径25 齿数32 8M型", kind="timing_pulley",
         want=dict(teeth=32, tooth_type="8M")),
]

# --------------------------------------------------------------------------
# 题库第 2 批：槽型/直径/写法的长尾（第 2 轮起加入）
# --------------------------------------------------------------------------
ROUND2 = [
    dict(p="多槽带轮 内径25 外径100 宽度30 槽数3 A型", kind="vbelt_pulley",
         want=dict(grooves=3, groove_type="A", bore_d=25)),
    dict(p="SPA型V带轮 基准直径140 内径32 槽数4", kind="vbelt_pulley",
         want=dict(groove_type="A")),
    dict(p="SPC带轮 基准直径250 内径55 槽数6", kind="vbelt_pulley",
         want=dict(groove_type="C")),
    dict(p="Y型带轮 基准直径40 内径10 槽数1 宽20", kind="vbelt_pulley",
         want=dict(groove_type="Y")),
    dict(p="V带轮 槽型B 基准直径200 孔径45 槽数5", kind="vbelt_pulley",
         want=dict(groove_type="B", grooves=5)),
    dict(p="皮带轮 基准直径112 内孔28 槽数3 带型A", kind="vbelt_pulley",
         want=dict(groove_type="A", grooves=3)),
    dict(p="B带轮 基准直径180 内径40 槽数4", kind="vbelt_pulley",
         want=dict(groove_type="B")),
    dict(p="Z带轮 基准直径63 内径12 槽数2", kind="vbelt_pulley",
         want=dict(groove_type="Z")),
    dict(p="同步带轮 XL型 节圆直径50 内径12 齿数10", kind="timing_pulley",
         want=dict(tooth_type="XL", teeth=10)),
    dict(p="同步带轮 齿数40 节圆直径200 内径40 14M型", kind="timing_pulley",
         want=dict(tooth_type="14M", teeth=40)),
    dict(p="带轮 节圆直径90 内径20 齿数18 L型", kind="timing_pulley",
         want=dict(tooth_type="L")),
    dict(p="多槽带轮 槽数9 基准直径450 内径70 E型", kind="vbelt_pulley",
         want=dict(grooves=9, groove_type="E")),
]

# --------------------------------------------------------------------------
# 题库第 3 批：混入"不该被带轮抢走"的干扰句（防止改规则改坏别的零件）
# --------------------------------------------------------------------------
ROUND3 = [
    dict(p="齿轮 模数3 齿数24 厚20", kind="gear", want=dict()),
    dict(p="法兰 外径160 内径60 厚20 均布6个直径12的螺栓孔", kind="flange", want=dict()),
    dict(p="深沟球轴承 内径20 外径47 宽度14", kind="bearing", want=dict()),
    dict(p="阶梯轴 直径40 长60 直径30 长50 直径20 长40", kind="shaft", want=dict()),
    dict(p="底板 200×150×12 四角各打一个直径8的通孔", kind="box", want=dict()),
    dict(p="圆柱 直径60 高120", kind="cylinder", want=dict()),
    dict(p="链轮 内径25 外径120 宽度20", kind="sprocket", want=dict()),
    dict(p="带轮槽 宽10 深5 长60 的键槽板", kind="box", want=dict()),
]


def load_bank():
    """题库优先从 kb/prompt_cases_pulley.json 读（可用它扩充），没有就用内置的三批。"""
    if os.path.exists(BANK):
        try:
            with io.open(BANK, encoding="utf-8") as f:
                data = json.load(f)
            cases = data.get("cases") or []
            if cases:
                return cases
        except Exception:
            pass
    return ROUND1 + ROUND2 + ROUND3


def save_bank(cases):
    with io.open(BANK, "w", encoding="utf-8") as f:
        json.dump({"_about": "带轮/标准件 提示词题库（多轮学习用，可自行扩充）",
                   "count": len(cases), "cases": cases}, f, ensure_ascii=False, indent=1)


def generate_round_cases(cases, round_no, add=12):
    """在已有题库基础上自动生成一批新题（写法组合），用于第 2 轮及以后。

    为什么要有这一步: 只跑同一批题, 第 2 轮和第 1 轮成绩必然一样, 那"多轮学习"
    就是假动作。这里按**排列组合**造新写法（零件名写法 × 参数写法 × 槽型），
    期望值由标准公式算出, 因此新题也是可判定的。
    """
    if round_no <= 1:
        return cases
    existing = {c["p"] for c in cases}
    new = []
    vtypes = ["Y", "Z", "A", "B", "C", "D", "E"]
    names = ["多槽带轮", "V带轮", "皮带轮", "多楔带轮", "窄V带轮"]
    bores = [12, 20, 25, 30, 40, 55]
    seed = round_no * 7 + len(cases)
    rnd = 0
    while len(new) < add and rnd < 400:
        rnd += 1
        gt = vtypes[(seed + rnd) % len(vtypes)]
        n = 1 + ((seed + rnd) % 6)
        name = names[(seed + rnd) % len(names)]
        bore = bores[(seed + rnd * 3) % len(bores)]
        table = std_pulley.diameter_series(gt)
        if not table:
            dd = 63.0 + 20.0 * ((seed + rnd) % 8)
        else:
            keys = sorted(table.keys())
            dd = keys[(seed + rnd * 5) % len(keys)]
        # 内孔必须明显小于基准直径，否则题目本身不成立（轮缘没地方开槽）
        while bore >= dd * 0.6 and bore > 12:
            bore = float(bores[(bores.index(bore) + 1) % len(bores)])
        style = (seed + rnd) % 4
        if style == 0:
            text = "%s 基准直径%g 内径%d 槽数%d %s型" % (name, dd, bore, n, gt)
        elif style == 1:
            text = "%s %d槽 %s型 内径%d 基准直径%g" % (name, n, gt, bore, dd)
        elif style == 2:
            text = "%s 基准直径%g 内径%d 轮宽40 槽数%d 槽型%s" % (name, dd, bore, n, gt)
        else:
            text = "%s 内径%d 基准直径%g 宽度40 %d个槽 %s型" % (name, bore, dd, n, gt)
        if text in existing:
            continue
        new.append(dict(p=text, kind="vbelt_pulley",
                        want=dict(grooves=n, groove_type=std_pulley.normalize_type(gt),
                                  bore_d=bore),
                        generated_in_round=round_no))
        existing.add(text)
    return cases + new


# 期望的"主特征类型" → 实际特征 kind 的允许集合
KIND_ALIAS = {
    "gear": ("gear", "helical_gear", "bevel_gear"),
    "bearing": ("cylinder", "ring", "box"),
    "shaft": ("cylinder", "revolve", "box"),
    "flange": ("flange", "cylinder"),
    "box": ("box", "cylinder", "extrude"),
    "cylinder": ("cylinder", "box"),
    "sprocket": ("sprocket", "vbelt_pulley", "cylinder", "box"),
}


def check_case(case):
    """跑一题，返回 (是否通过, 失败原因列表, 实际结果摘要)。"""
    text = case["p"]
    fails = []
    try:
        r = nlparser.parse(text)
    except Exception as e:                       # 解析异常也算失败, 但不能中断整轮
        return False, ["解析抛异常: %s: %s" % (type(e).__name__, e)], {}
    feats = [f for f in r.doc.features if f.enabled]
    kinds = [f.kind for f in feats]
    want_kind = case.get("kind")
    info = {"ok": r.ok, "kinds": kinds, "warnings": list(r.warnings),
            "steps": len(r.steps)}

    if not r.ok or not feats:
        fails.append("未识别（%s）" % (r.message or "无有效特征"))
        return False, fails, info

    allowed = KIND_ALIAS.get(want_kind, (want_kind,))
    if want_kind and not any(k in allowed for k in kinds):
        fails.append("主特征不对: 期望 %s, 实际 %s" % (want_kind, "/".join(kinds) or "无"))

    # 带轮类：检查参数与标准推导
    pu = next((f for f in feats if f.kind in ("vbelt_pulley", "timing_pulley")), None)
    if want_kind in ("vbelt_pulley", "timing_pulley"):
        if pu is None:
            fails.append("没有生成带轮特征")
            return False, fails, info
        if pu.kind != want_kind:
            fails.append("带轮类型不对: 期望 %s, 实际 %s" % (want_kind, pu.kind))
        pr = pu.params or {}
        want = case.get("want") or {}
        for key, exp in want.items():
            got = pr.get(key)
            if got is None:
                fails.append("缺参数 %s（期望 %s）" % (key, exp))
            elif isinstance(exp, (int, float)) and not isinstance(exp, bool):
                if abs(float(got) - float(exp)) > 0.01:
                    fails.append("参数 %s = %s，期望 %s" % (key, got, exp))
            elif str(got).upper() != str(exp).upper():
                fails.append("参数 %s = %s，期望 %s" % (key, got, exp))
        # 标准推导：V 带轮外径必须等于 基准直径 + 2·ha
        if pu.kind == "vbelt_pulley" and pr.get("groove_type"):
            try:
                s = std_pulley.spec(float(pr["pitch_d"]), int(pr["grooves"]),
                                    pr["groove_type"], None,
                                    bore_d=float(pr.get("bore_d") or 0.0))
                if abs(float(pr.get("outer_d", 0.0)) - s["outer_d"]) > 0.05:
                    fails.append("外径不符合 da=dd+2·ha：%s ≠ %s"
                                 % (pr.get("outer_d"), s["outer_d"]))
            except Exception as e:
                fails.append("标准推导异常: %s" % e)
        # 可解释性：必须给出"提示词 → 特征"对照表
        if not pr.get("explain"):
            fails.append("没有输出『提示词 → 生成特征』对照说明")
        # 不该出现"把带轮当圆柱+方槽"这类多余切除
        bad = [k for k in kinds if k in ("pocket", "box") and pu.kind == "vbelt_pulley"]
        if bad:
            fails.append("出现多余特征: %s" % "/".join(bad))
    return (not fails), fails, info


def run_round(cases, round_no):
    rows = []
    stat = Counter()
    fail_reason = Counter()
    for c in cases:
        ok, fails, info = check_case(c)
        rows.append({"case": c, "pass": ok, "fails": fails, "info": info})
        stat["total"] += 1
        stat["pass" if ok else "fail"] += 1
        for f in fails:
            fail_reason[re.sub(r"[（(].*", "", f)[:28]] += 1
    res = {
        "round": round_no,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": stat["total"], "passed": stat["pass"], "failed": stat["fail"],
        "accuracy": round(100.0 * stat["pass"] / max(1, stat["total"]), 1),
        "fail_reasons": dict(fail_reason),
        "rows": rows,
    }
    path = os.path.join(REPORTS, "pulley_learning_round%d.json" % round_no)
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res, path


def write_report(rounds):
    md = ["# 带轮提示词识别 —— 多轮学习报告", "",
          "> 由 `tools/learn_pulley_prompts.py` 自动生成；每一轮跑同一题库并记录逐题结果。",
          "> 这里的“学习”= 规则与标准知识库的迭代（跑题库 → 定位失败 → 改规则/补标准 → 再跑），",
          "> 不是训练神经网络。全程可复现、可对比。", "",
          "## 一、逐轮成绩", "",
          "| 轮次 | 时间 | 题量 | 通过 | 准确率 |", "|---|---|---|---|---|"]
    for r in rounds:
        md.append("| 第 %d 轮 | %s | %d | %d | **%.1f%%** |"
                  % (r["round"], r["time"], r["total"], r["passed"], r["accuracy"]))
    md += ["", "## 二、每轮失败原因统计", ""]
    for r in rounds:
        md.append("**第 %d 轮**（准确率 %.1f%%）" % (r["round"], r["accuracy"]))
        if not r["fail_reasons"]:
            md.append("")
            md.append("- 无失败")
        for k, v in sorted(r["fail_reasons"].items(), key=lambda x: -x[1]):
            md.append("- %s：%d 次" % (k, v))
        md.append("")
    md += ["## 三、最后一轮逐题明细", "",
           "| # | 提示词 | 期望 | 结果 | 实际特征 | 失败原因 |", "|---|---|---|---|---|---|"]
    last = rounds[-1] if rounds else None
    if last:
        for i, row in enumerate(last["rows"], 1):
            c = row["case"]
            md.append("| %d | `%s` | %s | %s | %s | %s |"
                      % (i, c["p"], c.get("kind", "-"),
                         "✅" if row["pass"] else "❌",
                         "/".join(row["info"].get("kinds") or []) or "-",
                         "；".join(row["fails"]) or "-"))
    md += ["", "## 四、题库", "",
           "题库文件：`kb/prompt_cases_pulley.json`（%d 题）。"
           "扩充题库后重跑本脚本即可得到新的一轮报告。" % (last["total"] if last else 0), ""]
    path = os.path.join(REPORTS, "带轮提示词学习报告.md")
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="跑几轮（默认 3）")
    args = ap.parse_args()

    cases = load_bank()
    if not os.path.exists(BANK):
        save_bank(cases)
    print("题库: %d 题（%s）" % (len(cases), BANK))

    rounds = []
    for rno in range(1, max(1, args.rounds) + 1):
        if rno > 1:
            before = len(cases)
            cases = generate_round_cases(cases, rno)
            if len(cases) > before:
                print("第 %d 轮新增 %d 道题（写法组合自动生成）" % (rno, len(cases) - before))
                save_bank(cases)
        res, path = run_round(cases, rno)
        rounds.append(res)
        print("第 %d 轮: %d/%d 通过（%.1f%%） -> %s"
              % (rno, res["passed"], res["total"], res["accuracy"], path))
        if res["fail_reasons"]:
            for k, v in sorted(res["fail_reasons"].items(), key=lambda x: -x[1]):
                print("    失败原因: %s × %d" % (k, v))
    report = write_report(rounds)
    print("报告: %s" % report)
    return 0 if rounds and rounds[-1]["accuracy"] >= 90.0 else 1


if __name__ == "__main__":
    sys.exit(main())
