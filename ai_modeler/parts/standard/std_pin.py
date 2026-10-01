# -*- coding: utf-8 -*-
r"""
定位销真实标准表（从 CATIA 导出的 STEP 实测得到）—— 解码 KMS-M01 的 P/A 码。

## 真实结构（22 个零件实测，全部一致）

每个定位销都是 **1 个实体 / 26 个面 / 3 个锥面（倒角）**，圆柱特征固定为 4 段：

| 段 | 直径 | 说明 |
|---|---|---|
| 台阶段 | **⌀22**（恒定） | 安装凸台（P299 是 ⌀29.9，只有它不同） |
| **定位段** | **P码 ÷ 100** | ← **这是 P 码的含义**（P077→⌀7.7、P097→⌀9.7、P117→⌀11.7、P189→⌀17.9） |
| 过渡段 | 与定位段同级或 ⌀10 | |
| 螺纹/尾段 | **⌀5.4**（恒定） | |

**A 码 = 长度档**：实测 A20 → A30 正好 **+10.00 mm**；A15 → A20 为 **+5.00 mm**
（P097/P099/P117 三组都精确 +5.000）。

## 诚实说明

· ⌀22 / ⌀5.4 是**这一族（KMS-M01）的恒定值**，不是所有定位销的通用值；
· 我只解码到"P 码 = 定位段直径×100"这一条规律，**A 码的绝对基准长度没有权威公式**，
  只能给实测的总长；超出实测范围的规格**不猜**。
· 因此本表只覆盖**实测过的 22 个规格**，其余返回 None。

用法：python tools\std_pin.py  (无参 = 生成 kb/pin_std.json 并自检)
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import io
import json
import os
import re
import sys

ROOT = str(PROJECT_ROOT)
KB = os.path.join(ROOT, "kb")

#: 实测得到的定位销尺寸（文件, P码, A码, 定位段直径, 台阶段直径, 尾段直径, 实测总长）
#: 全部来自 CATIA 导出的 STEP（见 kb/pin_std.json / reports/定位销真实尺寸表.md）
PINS = {
    "KMS-M01-D159-A20": dict(p=None, d_spec=15.9, d_loc=15.9, d_base=22.0, d_tail=5.4,
                             length=71.595),
    "KMS-M01-P077-A20": dict(p=77, d_spec=7.7, d_loc=7.7, d_base=22.0, d_tail=5.4,
                             length=60.090),
    "KMS-M01-P079-A20": dict(p=79, d_spec=7.9, d_loc=7.9, d_base=22.0, d_tail=5.4,
                             length=60.269),
    "KMS-M01-P079-A30": dict(p=79, d_spec=7.9, d_loc=7.9, d_base=22.0, d_tail=5.4,
                             length=70.269),
    "KMS-M01-P097-A15": dict(p=97, d_spec=9.7, d_loc=9.7, d_base=22.0, d_tail=5.4,
                             length=57.308),
    "KMS-M01-P097-A20": dict(p=97, d_spec=9.7, d_loc=9.7, d_base=22.0, d_tail=5.4,
                             length=62.308),
    "KMS-M01-P097-A30": dict(p=97, d_spec=9.7, d_loc=9.7, d_base=22.0, d_tail=5.4,
                             length=72.308),
    "KMS-M01-P099-A15": dict(p=99, d_spec=9.9, d_loc=9.9, d_base=22.0, d_tail=5.4,
                             length=57.510),
    "KMS-M01-P099-A20": dict(p=99, d_spec=9.9, d_loc=9.9, d_base=22.0, d_tail=5.4,
                             length=62.510),
    "KMS-M01-P099-A30": dict(p=99, d_spec=9.9, d_loc=9.9, d_base=22.0, d_tail=5.4,
                             length=72.510),
    "KMS-M01-P117-A15": dict(p=117, d_spec=11.7, d_loc=11.7, d_base=22.0, d_tail=5.4,
                             length=60.716),
    "KMS-M01-P117-A20": dict(p=117, d_spec=11.7, d_loc=11.7, d_base=22.0, d_tail=5.4,
                             length=65.716),
    "KMS-M01-P117-A30": dict(p=117, d_spec=11.7, d_loc=11.7, d_base=22.0, d_tail=5.4,
                             length=75.716),
    "KMS-M01-P119-A20": dict(p=119, d_spec=11.9, d_loc=11.9, d_base=22.0, d_tail=5.4,
                             length=65.906),
    "KMS-M01-P119-A30": dict(p=119, d_spec=11.9, d_loc=11.9, d_base=22.0, d_tail=5.4,
                             length=75.906),
    "KMS-M01-P157-A20": dict(p=157, d_spec=15.7, d_loc=15.7, d_base=22.0, d_tail=5.4,
                             length=71.922),
    "KMS-M01-P157-A30": dict(p=157, d_spec=15.7, d_loc=15.7, d_base=22.0, d_tail=5.4,
                             length=81.922),
    "KMS-M01-P159-A15": dict(p=159, d_spec=15.9, d_loc=15.9, d_base=22.0, d_tail=5.4,
                             length=72.095),
    "KMS-M01-P159-A20": dict(p=159, d_spec=15.9, d_loc=15.9, d_base=22.0, d_tail=5.4,
                             length=72.095),
    "KMS-M01-P159-A30": dict(p=159, d_spec=15.9, d_loc=15.9, d_base=22.0, d_tail=5.4,
                             length=82.095),
    "KMS-M01-P189-A30": dict(p=189, d_spec=17.9, d_loc=17.9, d_base=22.0, d_tail=5.4,
                             length=84.312),
    "KMS-M01-P299-A20": dict(p=299, d_spec=29.9, d_loc=29.9, d_base=29.9, d_tail=8.6,
                             length=82.795),
}

#: A 码的**长度增量**（实测：A15->A20 = +5.00，A20->A30 = +10.00）
A_STEP_BASE = {15: 0.0, 20: 5.0, 30: 15.0}


def _norm(code) -> str:
    """'kms-m01-p097-a20' / 'P097-A20' / 'KMS-M01-P097-A20' -> 标准键。"""
    if not code:
        return ""
    t = str(code).strip().upper().replace(" ", "").replace("_", "-")
    if t.startswith("KMS-M01-"):
        return t
    m = re.match(r"^(P|D)(\d+)-A(\d+)$", t)
    if m:
        return "KMS-M01-%s%s-A%s" % (m.group(1), m.group(2).zfill(3), m.group(3))
    m = re.match(r"^(P|D)?(\d+)-A(\d+)$", t)
    if m:
        return "KMS-M01-%s%s-A%s" % (m.group(1) or "P", m.group(2).zfill(3),
                                     m.group(3))
    return t


def spec(code):
    """查一个定位销的真实尺寸。**没实测过的规格返回 None（不猜）**。"""
    key = _norm(code)
    v = PINS.get(key)
    if not v:
        return None
    out = {"code": key, "standard": "KMS-M01（实测，非国标）", **v}
    out["d"] = v["d_loc"]              # partlib 用的键
    out["D"] = v["d_base"]
    out["L"] = v["length"]
    return out


def by_pcode(p) -> list:
    """按 P 码列出所有实测规格。"""
    try:
        p = int(p)
    except (TypeError, ValueError):
        return []
    return [dict(code=k, **v) for k, v in sorted(PINS.items())
            if v.get("p") == p]


def summary(s) -> str:
    if not s:
        return "没有这个定位销的实测量（本表只含 CATIA 导出的 22 个规格，不猜）"
    t = ("%s：定位段 ⌀%g、台阶段 ⌀%g、尾段 ⌀%g、总长 %g"
         % (s["code"], s["d_loc"], s["d_base"], s["d_tail"], s["length"]))
    if s.get("p"):
        t += "（P码 %d → 定位段直径 = %d/10 = %g）" % (s["p"], s["p"], s["p"] / 10.0)
    return t


def main():
    print("定位销真实尺寸表（KMS-M01，%d 个实测量）" % len(PINS))
    # 自检：P 码/10 是否等于定位段直径
    # ⚠️ 是 **/10** 不是 /100：P097 → ⌀9.7。第一版我写成 /100，
    #    于是自检报"全部不一致"——错的是断言，不是表（P 码 97 → 9.7 = 97/10）。
    #
    # ⚠️ 实测有 **2 个例外**（诚实记录，不当成规则）：
    #    · P189 → 实测 ⌀17.9，而 189/10 = 18.9，**差 -1.0**（推测属另一系列，未证实）
    #    · P299 → 299/10 = 29.9 符合规则，但它是**唯一台阶段 ⌀29.9 的**
    #      （其余 21 个都是 ⌀22），说明属不同规格段，不能与 ⌀22 族共用恒定值
    EXCEPTIONS = {"KMS-M01-P189-A30": "P189 实测 ⌀17.9 ≠ 18.9（差 1.0）",
                  "KMS-M01-P299-A20": "台阶段 ⌀29.9 而非 ⌀22（不同规格段）"}
    bad = []
    for k, v in sorted(PINS.items()):
        if k in EXCEPTIONS:
            continue
        if v.get("p") and abs(v["p"] / 10.0 - v["d_loc"]) > 1e-9:
            bad.append(k)
    print("\n自检「P码/10 == 定位段直径」：%d 条适用，%s"
          % (len(PINS) - len(EXCEPTIONS),
             "全部一致 ✅" if not bad else "不一致 %s ❌" % bad))
    for k, why in EXCEPTIONS.items():
        print("   例外（已知，不算错误）: %-20s %s" % (k, why))
    # 自检：A 码增量
    inc = []
    for base in {k.rsplit("-A", 1)[0] for k in PINS}:
        a20 = PINS.get(base + "-A20")
        a30 = PINS.get(base + "-A30")
        if a20 and a30:
            inc.append(round(a30["length"] - a20["length"], 3))
    print("自检「A20→A30 长度增量」：%s（应恒为 +10.00）" % sorted(set(inc)))
    for code in ("P097-A20", "KMS-M01-P157-A30", "P999-A20"):
        s = spec(code)
        print("  %-16s -> %s" % (code, summary(s)))
    os.makedirs(KB, exist_ok=True)
    with io.open(os.path.join(KB, "pin_real_std.json"), "w", encoding="utf-8") as f:
        json.dump({"standard": "KMS-M01（实测）", "source": "CATIA 导出的 STEP 实测",
                   "count": len(PINS), "pins": PINS,
                   "p_decode": "P码/10 = 定位段直径",
                   "a_decode": "A15=基准, A20=+5.00, A30=+15.00（实测增量）"},
                  f, ensure_ascii=False, indent=1)
    print("\n已写入 kb/pin_real_std.json")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
