# -*- coding: utf-8 -*-
r"""
升级前后**同口径**可比数字（固定 EVAL_N + 固定 seed，两份都在同一批用例上跑）。

为什么需要它：`tools/evaluate.py` 的用例是**随机抽样**（seed 固定但规模随 EVAL_N 变），
分母会随规模浮动（实测同一份代码 129 / 161 / 219 三种分母）。
拿不同分母的百分比互相比较会得出错误结论，所以这里强制：
    · 同一进程、同一批用例；
    · 只有"参数引用表 开/关"这一个变量不同。

用法：python tools\compare_baseline.py [EVAL_N]
输出：reports/升级前后对比.md、reports/upgrade_compare.json
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
PY = sys.executable


def run_eval(env_extra, n):
    env = dict(os.environ)
    env.update(env_extra)
    env["EVAL_N"] = str(n)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([PY, os.path.join(ROOT, "tools", "evaluate.py")],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    hits = total = ncases = None
    for line in out.splitlines():
        if "命中率" in line and "%" in line:
            try:
                seg = line.split("命中率")[1]
                frac = seg.split("(")[1].split(")")[0]
                hits, total = [int(x) for x in frac.split("/")]
            except Exception:
                pass
        if "评测用例" in line:
            try:
                ncases = int(line.split("评测用例")[1].split("条")[0])
            except Exception:
                pass
    return {"hits": hits, "total": total, "cases": ncases, "raw": out[-1500:]}


def run_selftest():
    p = subprocess.run([PY, "-m", "ai_modeler", "--selftest"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        if "通过" in line and "/" in line:
            return line.strip()
    return "?"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    print("同口径对比：EVAL_N=%d（两份用同一批用例）" % n)
    off = run_eval({"PARAM_TABLE": "0"}, n)
    on = run_eval({"PARAM_TABLE": "1"}, n)
    same = off["cases"] == on["cases"] and off["total"] == on["total"]
    print("  旧解析（开关关）: %s/%s" % (off["hits"], off["total"]))
    print("  参数表（开关开）: %s/%s" % (on["hits"], on["total"]))
    print("  用例数一致:", same, "（%s vs %s）" % (off["cases"], on["cases"]))

    def pct(r):
        return 100.0 * r["hits"] / r["total"] if r["total"] else 0.0

    data = {
        "eval_n": n, "same_cases": same,
        "legacy": off, "paramtable": on,
        "legacy_pct": round(pct(off), 2), "paramtable_pct": round(pct(on), 2),
        "delta_pt": round(pct(on) - pct(off), 2),
        "selftest": run_selftest(),
    }
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "upgrade_compare.json"), "w",
                 encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    md = [
        "# 升级前后可比对数字（同口径）", "",
        "口径：`tools/evaluate.py`，`EVAL_N=%d`、seed 固定，**两份在同一批 %s 条用例上跑**。"
        % (n, on["cases"]), "",
        "| 指标 | 旧解析（v1.3 及以前） | 参数引用表（U2，现为默认） | 变化 |",
        "|---|---|---|---|",
        "| 全量评测命中率 | %.2f%%（%d/%d） | **%.2f%%（%d/%d）** | %+.2f pt |"
        % (pct(off), off["hits"], off["total"], pct(on), on["hits"], on["total"],
           pct(on) - pct(off)),
        "| 内置自检 | %s | %s | — |" % (data["selftest"], data["selftest"]), "",
        "> 注意：`evaluate.py` 的用例是随机抽样，分母随 `EVAL_N` 浮动（同一份代码见过",
        "> 129/161/219 三种分母）。比较百分比时**必须**固定 `EVAL_N`，否则结论不可靠。",
    ]
    with io.open(os.path.join(REPORTS, "升级前后对比.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 reports/升级前后对比.md")
    return 0 if same else 2


if __name__ == "__main__":
    sys.exit(main())
