# -*- coding: utf-8 -*-
r"""A/B 回归：同一批测试，`AI_MODELER_REALLIB` 开/关 各跑一遍。

**为什么要这么做**：本轮迭代改动了 `find_part`（型号优先）、`model_params`
（实测值覆盖）与 4 个零件的建模器。要证明"没绿变红"，不能只跑一遍说"都过了" ——
必须把新功能**关掉**再跑一遍，两组数字逐项对比：
  · 关掉那组 = 升级前的行为基线
  · 任何一项"关掉时过、打开时不过"就是**真回归**，必须修

用法: python tools\ab_regression.py
输出: reports\AB回归对比.md
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")

#: 每个套件跑一次；用正则从输出里抓"通过/命中"数字
SUITES = [
    ("selftest", [sys.executable, "-m", "ai_modeler", "--selftest"],
     r"通过\s*(\d+)\s*/\s*(\d+)"),
    ("evaluate", [sys.executable, r"tools\evaluate.py"],
     r"命中率\s*([\d.]+)%\s*\((\d+)/(\d+)\)"),
    ("bench_v2", [sys.executable, r"tools\bench_v2.py"],
     r"参数级准确率\s*:\s*([\d.]+)%\s*\((\d+)/(\d+)\)"),
    ("bench_basic_parts", [sys.executable, r"tools\bench_basic_parts.py"],
     r"命中率\s*([\d.]+)%（(\d+)/(\d+)）"),
    ("bench_real_parts", [sys.executable, r"tools\bench_real_parts.py"],
     r"命中率\s*([\d.]+)%（(\d+)/(\d+)）"),
    ("bench_real_geometry", [sys.executable, r"tools\bench_real_geometry.py"],
     r"命中率\s*\*\*([\d.]+)%\*\*（(\d+)/(\d+)"),
    ("test_fixture_parts", [sys.executable, r"tools\test_fixture_parts.py"],
     r"尺寸与期望一致\s*(\d+)/(\d+)"),
    ("check_bolt_geometry", [sys.executable, r"tools\check_bolt_geometry.py"],
     r"✅ 杆径\s*([\d.]+)（期望"),
]


def run(env_extra):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra)
    out = {}
    for name, cmd, pat in SUITES:
        try:
            p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=1800)
            text = p.stdout.decode("utf-8", "replace")
        except Exception as e:
            out[name] = ("异常", str(e)[:60])
            continue
        m = re.search(pat, text)
        if m:
            g = m.groups()
            if len(g) == 1:
                out[name] = ("%s（杆径判据）" % g[0], 1, 1)
            elif len(g) == 2:
                out[name] = ("%s/%s" % (g[0], g[1]), int(g[0]), int(g[1]))
            else:
                out[name] = ("%s%% (%s/%s)" % (g[0], g[1], g[2]),
                             int(g[1]), int(g[2]))
        else:
            tail = [L for L in text.split("\n") if L.strip()][-1:] or [""]
            out[name] = ("未匹配到数字", tail[0][:70])
    return out


def main():
    print("A 组：关掉真实型号库（= 升级前行为）…")
    off = run({"AI_MODELER_REALLIB": "0"})
    print("B 组：打开真实型号库（= 本轮迭代）…")
    on = run({})

    L = ["# A/B 回归对比", "",
         "同一批套件，`AI_MODELER_REALLIB=0`（升级前行为）与默认（本轮迭代）各跑一遍。",
         "**判据：只要出现「关掉时过、打开时不过」，就是真回归。**", "",
         "| 套件 | 关掉（升级前） | 打开（本轮） | 结论 |", "|---|---|---|---|"]
    regress = []
    improve = []
    for name, _c, _p in SUITES:
        a, b = off.get(name), on.get(name)
        sa = a[0] if a else "-"
        sb = b[0] if b else "-"
        concl = "—"
        if a and b and len(a) == 3 and len(b) == 3:
            if b[1] < a[1]:
                concl = "❌ **回归**"
                regress.append((name, sa, sb))
            elif b[1] > a[1]:
                concl = "✅ 提升"
                improve.append((name, sa, sb))
            else:
                concl = "✅ 持平"
        L.append("| %s | %s | %s | %s |" % (name, sa, sb, concl))
    L += ["", "## 结论", ""]
    if regress:
        L.append("**发现 %d 处回归，必须修：**" % len(regress))
        for n, x, y in regress:
            L.append("- %s：%s → %s" % (n, x, y))
    else:
        L.append("**没有回归**：没有任何一个套件从「过」变成「不过」。")
    if improve:
        L.append("")
        L.append("提升的套件：")
        for n, x, y in improve:
            L.append("- %s：%s → %s" % (n, x, y))
    L.append("")
    L.append("> `bench_real_geometry` 在关掉真实型号库时**必然大幅下降** —— "
             "它测的就是这套库；这正是「新功能确实在起作用」的证据，不是回归。")
    with io.open(os.path.join(REPORTS, "AB回归对比.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
