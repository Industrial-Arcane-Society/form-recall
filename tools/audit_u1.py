# -*- coding: utf-8 -*-
r"""
U1 两段式解析：**现状审计**（先量出来，再决定改哪）。

U1 的目标（来自升级文档）：
    · 规划期（Plan）：只产出"要建哪些特征、它们怎么连接"，**不出现数字字面量**；
    · 构建期（Construct/Bind）：只把参数表里的值绑进特征，**不再解析文本**。

审计口径（可量化、不靠感觉）：
    A) 有多少特征参数能追到**出处**（explicit / standard / derived / default）？
       追不到的说明"构建期仍在凭空产生数字"。
    B) 构建期是否还在解析文本（`Feature.params['__text']` 之外的隐式再解析）？
    C) 同一零件在不同写法下，特征参数是否一致（规划期稳定的必要条件）。

用法：python tools\audit_u1.py
输出：reports/U1审计.md、reports/u1_audit.json
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP        # noqa: E402
from ai_modeler import partlib as PL          # noqa: E402

CASES = [
    "\u957f\u65b9\u4f53\u7684\u957f120 \u5bbd80 \u9ad815",
    "\u5706\u67f1 \u76f4\u5f8460 \u9ad8120",
    "\u6cd5\u5170 \u5916\u5f84160 \u5185\u5f8460 \u539a20",
    "\u57ab\u5708 \u5185\u5f8420 \u5916\u5f8440 \u539a\u5ea63",
    "\u6df1\u6c9f\u7403\u8f74\u627f 6204",
    "\u8f74\u627f 6305",
    "\u8f74\u5957 \u5185\u5f8430 \u5916\u5f8445 \u957f\u5ea660",
    "\u76f4\u9f7f\u5706\u67f1\u9f7f\u8f6e \u6a21\u65703 \u9f7f\u657024 \u9f7f\u5bbd20",
    "\u591a\u69fd\u5e26\u8f6e \u5185\u5f8425 \u5916\u5f84100 \u5bbd\u5ea630 \u69fd\u65703 A\u578b",
    "\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8450 \u5185\u5f8412 \u9f7f\u657010 \u5bbd10",
    "\u540c\u6b65\u5e26\u8f6e \u8282\u5706\u76f4\u5f8450 10\u9f7f \u5bbd10",
    "\u5e73\u952e \u5bbd\u5ea610 \u9ad8\u5ea68 \u957f\u5ea640",
    "\u87ba\u6813 \u87ba\u7eb9\u89c4\u683c16 \u957f\u5ea680",
    "\u87ba\u65cb\u5f39\u7c27 \u7ebf\u5f843.2 \u5916\u5f8438 \u81ea\u7531\u9ad8\u5ea6150 \u5708\u65704.75",
    "\u8f90\u6761\u624b\u8f6e \u5916\u5f84215 \u5185\u5f8419 \u539a\u5ea615 \u8f90\u6761\u65707",
]

#: 允许出现在特征参数里、但**不属于零件尺寸**的键（连接/说明用）
NON_DIM_KEYS = {"center", "explain", "std_warnings", "axis", "name", "note",
                "tag", "kind", "op", "mode"}

#: **几何开关/内核参数**：不是用户量出来的尺寸，追不出处是正常的。
#: 必须与"真正的尺寸却追不出处"分开报，否则会把两类混成一个难看的数字。
#: （early 版本把 through/base/segments 也当成尺寸，于是"追不出处"里有一大半
#:  其实是布尔开关和分解精度——那不是缺陷。）
KERNEL_KEYS = {"through", "base", "segments", "corner_mode", "pos", "axis",
               "radius2", "enabled", "tag", "count_mode",
               # 螺旋线的采样/分段/锥度控制：网格生成参数，不是零件尺寸
               "seg_per_turn", "wire_seg", "taper", "taper_start",
               "helix_deg", "twist", "lobes"}


def audit_one(text):
    try:
        r = NLP.parse(text)
    except Exception as e:
        return {"text": text, "error": str(e)[:120]}
    feats = list(r.doc.features)
    prov = Counter()
    untraceable = []        # 真正的尺寸但追不出处
    kernel_only = []        # 几何开关/内核参数（追不出处是正常的）
    steps = len(r.steps)
    for f in feats:
        p = dict(f.params or {})
        table = p.pop("__table", None)
        p.pop("__text", None)
        fprov = dict(getattr(f, "provenance", {}) or {})
        for k, v in p.items():
            if k in NON_DIM_KEYS or k.startswith("__"):
                continue
            if k in fprov:
                prov[fprov[k]] += 1
            elif table is not None and k in getattr(table, "items", {}):
                prov[table.items[k].provenance] += 1
            elif k in KERNEL_KEYS:
                kernel_only.append("%s.%s" % (f.kind, k))
            else:
                untraceable.append("%s.%s" % (f.kind, k))
    with_text = sum(1 for f in feats if "__text" in (f.params or {}))
    return {
        "text": text, "n_feat": len(feats), "steps": steps,
        "prov": dict(prov), "untraceable": untraceable,
        "kernel_only": kernel_only, "feat_with_text": with_text,
        "kinds": [f.kind for f in feats],
    }


def main():
    rows = [audit_one(t) for t in CASES]
    n_feat = sum(r.get("n_feat", 0) for r in rows)
    trace = sum(sum(r.get("prov", {}).values()) for r in rows)
    untrace = sum(len(r.get("untraceable", [])) for r in rows)
    kernel = sum(len(r.get("kernel_only", [])) for r in rows)
    with_text = sum(r.get("feat_with_text", 0) for r in rows)
    prov_total = Counter()
    for r in rows:
        prov_total.update(r.get("prov", {}))
    #: "尺寸类参数"总数 = 可追出处 + 追不出处（几何开关不算在内）
    dim_total = trace + untrace
    pct = (100.0 * trace / dim_total) if dim_total else 0.0

    print("=" * 74)
    print("U1 现状审计：%d 条用例，%d 个特征" % (len(CASES), n_feat))
    print("  尺寸类参数总数     : %d" % dim_total)
    print("  可追出处的         : %d  (%.1f%%)" % (trace, pct))
    print("  **追不出处**的尺寸 : %d  <- 构建期仍在凭空产生数字" % untrace)
    print("  几何开关/内核参数  : %d  <- 追不出处是正常的（不是尺寸）" % kernel)
    print("  仍带 __text 的特征 : %d  <- 构建期仍可能再解析文本" % with_text)
    print("  出处分布           : %s" % dict(prov_total))
    if untrace:
        c = Counter(u.split(".")[0] for r in rows for u in r.get("untraceable", []))
        print("  追不出处的特征类型 : %s" % dict(c))
        print("  样例：")
        seen = set()
        for r in rows:
            for u in r.get("untraceable", []):
                if u not in seen:
                    seen.add(u)
                    if len(seen) <= 12:
                        print("     %-40s <- %s" % (u, r["text"][:30]))
    print("=" * 74)

    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    data = {"cases": len(CASES), "features": n_feat, "dim_total": dim_total,
            "traceable": trace, "untraceable": untrace, "kernel_only": kernel,
            "traceable_pct": round(pct, 2), "feat_with_text": with_text,
            "provenance": dict(prov_total), "rows": rows}
    with io.open(os.path.join(ROOT, "reports", "u1_audit.json"), "w",
                 encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    md = ["# U1 两段式解析 —— 现状审计", "",
          "口径：规划期不得出现数字字面量；构建期不得再解析文本。",
          "本审计量的是**可追出处的特征参数占比**与**仍带文本的特征数**。", "",
          "| 指标 | 数值 |", "|---|---|",
          "| 用例数 | %d |" % len(CASES),
          "| 特征数 | %d |" % n_feat,
          "| 可追出处的特征参数 | %d |" % trace,
          "| **追不出处的特征参数** | **%d** |" % untrace,
          "| 仍带 `__text` 的特征 | %d |" % with_text,
          "| 出处分布 | %s |" % dict(prov_total), ""]
    if untrace:
        md += ["## 追不出处的参数（构建期凭空产生的数字）", "",
               "| 特征类型 | 出现次数 | 用例样例 |", "|---|---|---|"]
        for kind, n in Counter(u.split(".")[0] for r in rows
                               for u in r.get("untraceable", [])).most_common():
            sample = next((r["text"] for r in rows
                           if any(u.startswith(kind + ".") for u in r.get("untraceable", []))), "")
            md.append("| %s | %d | %s |" % (kind, n, sample[:34]))
    with io.open(os.path.join(ROOT, "reports", "U1审计.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("已写入 reports/U1审计.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
