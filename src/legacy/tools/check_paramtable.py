# -*- coding: utf-8 -*-
r"""
U2 验证器：新参数表（params.ParamTable）与旧解析（partlib.model_params）**同口径对拍**。

做法
    · 把 partlib 里 41 类零件的参数定义自动适配成 params.Spec（零手工、覆盖全库）；
    · 用同一批提示词分别跑"新表"和"旧解析"，逐键比对；
    · 差异分三类：① 新表更对（旧的是 bug）② 旧的对（新表要补）③ 都没取到（都缺）。
    只有把差异按这三类看完，才允许把主解析切到新表（升级文档 U2 的迁移前置条件）。

输出：reports\paramtable_parity.json、reports\参数表对拍报告.md
运行: python tools\check_paramtable.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import params as P            # noqa: E402
from ai_modeler import partlib                 # noqa: E402

REPORTS = os.path.join(ROOT, "reports")

# 数值量级（用于剔除串列误读）：键 -> (min, max)
RANGES = {
    "d": (0.5, 5000), "D": (0.5, 5000), "L": (0.5, 8000), "H": (0.5, 5000),
    "B": (0.5, 2000), "b": (0.5, 2000), "h": (0.5, 2000), "T": (0.5, 2000),
    "W": (0.05, 500), "s": (0.5, 500), "k": (0.1, 500), "m": (0.1, 100),
    "z": (1, 999), "n": (1, 999), "dh": (0.5, 2000), "K": (0.5, 5000),
    "alpha": (1, 90), "beta": (1, 89), "delta": (1, 89), "S": (0.5, 3000),
    "dp": (0.5, 3000), "dj": (0.5, 2000), "R": (0.5, 3000), "t": (0.1, 500),
    "thick": (0.1, 500), 'dh2': (0.5, 2000), 'ring': (0.5, 500),
}

# 对拍用的"硬提示词"：覆盖符号式/中文别名/连写/代号/计数词
CASES = [
    "多槽带轮 内径25 外径100 宽度30 槽数3 A型",
    "V带轮 基准直径120 内径30 轮宽40 槽数4 A型",
    "带轮 d25 D100 B30 n3 A型",
    "同步带轮 节圆直径80 内径20 宽30 齿数20 XL型",
    "法兰 外径160 内径60 厚20",
    "垫圈 内径20 外径40 厚度3",
    "深沟球轴承 内径20 外径47 宽度14",
    "轴套 内径30 外径45 长度60",
    "直齿圆柱齿轮 模数3 齿数24 齿宽20",
    "斜齿圆柱齿轮 模数2.06 齿数58 齿宽39.93 螺旋角22.03",
    "螺栓 螺纹规格16 长度80",
    "螺母 螺纹规格16",
    "平键 宽度10 高度8 长度40",
    "螺旋弹簧 线径3.2 外径38 自由高度150 圈数4.75",
    "碟形弹簧 内径33.35 外径39.11 厚度2.79",
    "皮带轮 节圆直径80 内径20 宽度20 槽数2 Z型",
    "多楔带轮 直径200 孔径45 槽数5 B型",
    "窄V带轮 SPB 基准直径180 内径40 槽数5",
    "双槽带轮 基准直径80 内径20",
    "V带轮 基准直径400 内径60 槽数8 D型",
    "带轮 外径100 内径25 宽度30 3个槽 A型",
    "花键轴 小径58 大径65 长度90 齿数12",
    "盘形凸轮 基圆直径73 外径60 厚度17",
    "蜗杆 分度圆直径30 外径24 长度124",
    "辐条手轮 外径215 内径19 厚度15 辐条数7",
    "制动鼓 外径352 内径230 高度52",
    "轴承座 内径28 外径135 高度83",
    "联轴器 外径85 内孔32 长72",
    "活塞 外径100 高度79 销孔37 环槽数5",
    "飞轮 外径370 内径108 厚度43",
]


def make_spec(cat: str, d: dict) -> P.Spec:
    """把 partlib 的零件定义适配成 params.Spec（自动，不手工写 41 份）。"""
    spec = P.Spec(name=cat)
    aliases = {}
    for key, al in (d.get("params") or {}).items():
        if key in ("t", "gt", "type", "tooth_type"):
            continue                      # 代号类参数单独处理
        # 注意：**不要**丢掉单字母别名（M 就是螺栓的螺纹规格别名）
        aliases[key] = list(al)
        rng = RANGES.get(key)
        if rng:
            spec.ranges[key] = rng
    spec.aliases = aliases
    spec.optional = tuple(partlib.OPTIONAL_PARAMS)
    # 符号式：单字母键（d/D/B/L/H/n/z…）—— 但 M 是螺纹规格，取值形态特殊，排除
    spec.symbols = {k: k for k in aliases if len(k) == 1 and k.isalpha() and k != "M"}
    # 代号：槽型/齿型
    if cat in ("多槽带轮",):
        spec.codes = {"t": ("SPZ", "SPA", "SPB", "SPC", "Y", "Z", "A", "B", "C", "D", "E")}
    elif cat in ("同步带轮", "同步轮"):
        spec.codes = {"t": ("XXH", "XH", "MXL", "XL", "L", "H", "T2.5", "T5", "T10",
                            "T20", "14M", "8M", "5M", "3M")}
    # 计数词
    if cat in ("多槽带轮",):
        spec.count_words = {"n": ("槽",)}
    elif cat in ("同步带轮", "同步轮"):
        spec.count_words = {"z": ("齿",)}
    # 螺栓的 M 别名：螺纹规格 M16 / 螺纹规格16
    if cat == "螺栓":
        spec.aliases.setdefault("d", []).append("M")
    return spec


# 旧解析里"按公式/比例推定"的键（新表不算作缺失，由上层派生）
DERIVED_KEYS_VIA_FORMULA = {"s", "k", "m", "H", "W", "ring", "thick", "t", "dh2", "K", "alpha", "beta", "delta"}


def run_case(text: str):
    """对拍一题：新参数表（生产路径 extract.py）vs 旧解析（_model_params_legacy）。"""
    cat = partlib.find_part(text)
    if not cat:
        return {"text": text, "cat": None, "new": {}, "old": {}, "diff": {}}
    d = partlib.PARTS[cat]
    spec = make_spec(cat, d)
    tab = P.add_boundaries  # noqa: F841  (占位，避免误用)
    from ai_modeler import extract as _ex
    t2 = _ex.extract(cat, _ex.make_spec(cat, d), text)
    _ex.derive(cat, t2)
    new = {k: v.value for k, v in t2.items.items() if not k.startswith("__")}
    old_raw = partlib._model_params_legacy(text, extended=True)
    old = dict((old_raw or {}).get("params") or {})
    old.pop("__text", None)
    keys = sorted(set(list(new.keys()) + [k for k in old if k in d.get("params", {})]))
    diff = {}
    for k in keys:
        nv = new.get(k)
        ov = old.get(k)
        if isinstance(nv, str) or isinstance(ov, str):
            same = (str(nv).upper() == str(ov).upper())
        elif nv is None or ov is None:
            same = (nv is None and ov is None)
        else:
            try:
                same = abs(float(nv) - float(ov)) < 1e-6
            except (TypeError, ValueError):
                same = (str(nv) == str(ov))
        if not same:
            diff[k] = {"new": nv, "old": ov}
    return {"text": text, "cat": cat, "new": new, "old": old, "diff": diff}


def main():
    rows = [run_case(t) for t in CASES]
    n_diff = sum(1 for r in rows if r["diff"])
    n_no_cat = sum(1 for r in rows if not r["cat"])
    per_key = {}
    for r in rows:
        for k, v in r["diff"].items():
            per_key.setdefault(k, []).append((r["text"], v))

    res = {"total": len(rows), "with_diff": n_diff, "no_category": n_no_cat,
           "per_key": {k: [list(x) for x in v] for k, v in per_key.items()},
           "rows": rows}
    os.makedirs(REPORTS, exist_ok=True)
    with io.open(os.path.join(REPORTS, "paramtable_parity.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    md = ["# 参数表对拍报告（新 ParamTable vs 旧 model_params）", "",
          "> 由 `tools/check_paramtable.py` 生成。目的：确认新表在**当前题库**上与旧解析结果一致，",
          "> 之后再逐步加入新能力（标准解析/推导/出处标记）。", "",
          "| 指标 | 数值 |", "|---|---|",
          "| 用例数 | %d |" % len(rows),
          "| 完全一致 | %d |" % (len(rows) - n_diff),
          "| 存在差异 | %d |" % n_diff,
          "| 未识别零件 | %d |" % n_no_cat, ""]
    if per_key:
        md += ["## 差异按参数归并", "", "| 参数 | 次数 | 例 |", "|---|---|---|"]
        for k, v in sorted(per_key.items(), key=lambda kv: -len(kv[1])):
            md.append("| %s | %d | %s -> 新=%s 旧=%s |"
                      % (k, len(v), v[0][0][:26], v[0][1].get("new"), v[0][1].get("old")))
    md += ["", "## 逐题明细", "", "| 提示词 | 零件 | 差异键 |", "|---|---|---|"]
    for r in rows:
        md.append("| `%s` | %s | %s |" % (r["text"][:34], r["cat"] or "-",
                                          "、".join(r["diff"].keys()) or "一致"))
    with io.open(os.path.join(REPORTS, "参数表对拍报告.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print("用例 %d，完全一致 %d，有差异 %d，未识别 %d"
          % (len(rows), len(rows) - n_diff, n_diff, n_no_cat))
    for k, v in sorted(per_key.items(), key=lambda kv: -len(kv[1]))[:12]:
        print("   %-6s 差异 %2d 次  例: %-24s 新=%-8s 旧=%s"
              % (k, len(v), v[0][0][:24], v[0][1].get("new"), v[0][1].get("old")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
