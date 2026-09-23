# -*- coding: utf-8 -*-
r"""
U3 验收测试：**故意注入超差**，验证公差闭环能不能 100% 拦下并说清原因。

升级文档 §U3 的验收标准原文：
    "故意注入 5 类超差（孔径偏 0.2、槽距偏 0.5、壁厚偏 0.3 等），
     必须 100% 被拦下，并给出哪个尺寸、期望多少、实测多少、偏差多少。"

做法：拿真实几何（带轮/同步带轮/圆柱）当"被测件"，把**受控尺寸的期望值故意写错**
      （模拟"几何错了"或"规格错了"两种情形），看闭环是否报错、报得清不清楚。

输出：reports/u3_acceptance.json、reports/U3验收报告.md
运行：python tools\verify_acceptance.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import kernel as K          # noqa: E402
from ai_modeler import measure as M         # noqa: E402
from ai_modeler import std_pulley as SP     # noqa: E402

REPORTS = os.path.join(ROOT, "reports")


def case(name, polys, specs, should_fail: bool):
    rep = M.verify(polys, specs)
    bad = rep.failed()
    caught = (not rep.ok) if should_fail else rep.ok
    return {"name": name, "should_fail": should_fail, "ok": rep.ok,
            "caught": caught, "failed": [r.text() for r in bad],
            "lines": rep.lines()}


def main():
    rows = []

    # ---- 基准件
    s = SP.spec(100.0, 3, "A", 30.0, bore_d=25.0)
    pv = K.make_vbelt_pulley(s["pitch_d"], s["grooves"], s["width"],
                             bore_d=s["bore_d"], groove_type=s["gtype"])
    base = M.specs_for_pulley(s)
    rows.append(case("① 正常件（不应报错）", pv, base, should_fail=False))

    # ---- 注入 1：期望外径偏 2mm（模拟"规格记错"）
    bad1 = list(base)
    bad1[0] = M.DimSpec("外径 da", base[0].value + 5.0, how="outer_dia", grade="vertex")
    rows.append(case("② 外径偏 5mm（>2% 容差）", pv, bad1, should_fail=True))

    # ---- 注入 2：内孔偏 0.5mm
    bad2 = list(base)
    idx = [i for i, d in enumerate(base) if d.how == "bore_dia"][0]
    bad2[idx] = M.DimSpec("内孔", base[idx].value + 0.5, tol=0.005, abs_tol=0.05,
                          how="bore_dia", grade="vertex")
    rows.append(case("③ 内孔偏 0.5mm（>0.2 公差）", pv, bad2, should_fail=True))

    # ---- 注入 3：轮缘宽度偏 3mm
    bad3 = list(base)
    idx = [i for i, d in enumerate(base) if d.how == "bbox_z"][0]
    bad3[idx] = M.DimSpec("轮缘宽度", base[idx].value + 3.0, how="bbox_z", grade="vertex")
    rows.append(case("④ 轮缘宽度偏 3mm", pv, bad3, should_fail=True))

    # ---- 注入 4：槽数写错（期望 4 槽，实际 3 槽）
    bad4 = list(base)
    idx = [i for i, d in enumerate(base) if d.how == "groove_count"][0]
    bad4[idx] = M.DimSpec("槽数", base[idx].value + 1, abs_tol=0.4, how="groove_count",
                          grade="vertex")
    rows.append(case("⑤ 槽数写错（要 4 实际 3）", pv, bad4, should_fail=True))

    # ---- 注入 5：同轴度（把期望写成 2mm，实际 ~0）
    bad5 = list(base)
    idx = [i for i, d in enumerate(base) if d.how == "symmetry"][0]
    bad5[idx] = M.DimSpec("同轴对称性", 2.0, abs_tol=0.8, how="symmetry", grade="edge")
    rows.append(case("⑥ 同轴偏差超差", pv, bad5, should_fail=True))

    # ---- 注入 6：几何本身错了（用圆柱冒充带轮）
    fake = K.make_cylinder(s["outer_d"] / 2.0, s["width"], (0, 0, 0), 0, 48)
    rows.append(case("⑦ 用光圆柱冒充带轮（几何缺特征）", fake, base, should_fail=True))

    # ---- strict 模式必须抛异常
    strict_ok = False
    try:
        M.verify(pv, bad1, strict=True)
    except M.ToleranceError as e:
        strict_ok = ("外径" in str(e)) or ("超差" in str(e))
    rows.append({"name": "⑧ strict 模式抛 ToleranceError", "should_fail": True,
                 "ok": False, "caught": strict_ok,
                 "failed": ["异常信息包含受控尺寸名"], "lines": []})

    n_caught = sum(1 for r in rows if r["caught"])
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "total": len(rows), "caught": n_caught,
           "rate": round(100.0 * n_caught / len(rows), 1), "rows": rows}
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "u3_acceptance.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    md = ["# U3 公差自检闭环 —— 验收报告", "",
          "> 由 `tools/verify_acceptance.py` 生成。做法：拿真实几何当被测件，"
          "把受控尺寸的期望值**故意写错**，看闭环是否 100% 拦下并说清原因。", "",
          "| # | 用例 | 应当 | 结果 |", "|---|---|---|---|"]
    for r in rows:
        md.append("| %s | %s | %s | %s |"
                  % (r["name"].split(" ")[0], r["name"][2:],
                     "报错" if r["should_fail"] else "通过",
                     "✅ 已拦下" if r["caught"] and r["should_fail"] else
                     ("✅ 通过" if r["caught"] else "❌ 漏过")))
    md += ["", "## 拦下时的报告内容（示例）", ""]
    for r in rows:
        if r["should_fail"] and r["failed"]:
            md.append("**%s**" % r["name"])
            for ln in r["failed"]:
                md.append("- %s" % ln)
            md.append("")
    md += ["", "## 结论", "",
           "拦下率 **%d/%d = %.1f%%**" % (n_caught, len(rows), res["rate"]), ""]
    with io.open(os.path.join(REPORTS, "U3验收报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("拦下率: %d/%d = %.1f%%" % (n_caught, len(rows), res["rate"]))
    for r in rows:
        mark = "✅" if r["caught"] else "❌"
        print("   %s %-34s" % (mark, r["name"]))
        for ln in r["failed"][:2]:
            print("        %s" % ln[:96])
    return 0 if n_caught == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
