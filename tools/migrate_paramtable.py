# -*- coding: utf-8 -*-
r"""
U2 迁移诊断：把"参数表开关 关/开"时的**特征参数差异**逐条摊开。

用法：python tools\migrate_paramtable.py            # 只看差异清单
      python tools\migrate_paramtable.py --detail   # 加打印参数表与工作文本
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import nlparser as NLP      # noqa: E402
from ai_modeler import partlib              # noqa: E402
from ai_modeler import params as P          # noqa: E402

CASES = [
    "长方体的长120 宽80 高15",
    "圆柱 直径60 高120",
    "圆柱 直径60 高20, 中心打一个直径20的通孔",
    "底板 200×150×12, 四角各打一个直径8的通孔, 内缩12",
    "法兰 外径160 内径60 厚20",
    "垫圈 内径20 外径40 厚度3",
    "深沟球轴承 内径20 外径47 宽度14",
    "轴套 内径30 外径45 长度60",
    "直齿圆柱齿轮 模数3 齿数24 齿宽20",
    "多槽带轮 内径25 外径100 宽度30 槽数3 A型",
    "同步带轮 节圆直径80 内径20 宽30 齿数20 XL型",
    "平键 宽度10 高度8 长度40",
    "辐条手轮 外径215 内径19 厚度15 辐条数7",
    "螺旋弹簧 线径3.2 外径38 自由高度150 圈数4.75",
    "螺栓 螺纹规格16 长度80",
]


def snap():
    """用同一进程分别跑 开/关 两种开关，返回 {文本: (关特征, 开特征)}。"""
    out = {}
    for text in CASES:
        os.environ["PARAM_TABLE"] = "0"
        P.USE_PARAM_TABLE = False
        off = feats(text)
        os.environ["PARAM_TABLE"] = "1"
        P.USE_PARAM_TABLE = True
        on = feats(text)
        out[text] = (off, on)
    os.environ["PARAM_TABLE"] = "0"
    P.USE_PARAM_TABLE = False
    return out


def feats(text):
    try:
        r = NLP.parse(text)
        return [(f.kind, {k: v for k, v in (f.params or {}).items()
                          if not k.startswith("__") and k not in ("center", "explain")})
                for f in r.doc.features]
    except Exception as e:
        return [("__err__", str(e)[:60])]


def _flat(feats):
    """把特征列表摊平成 {下标.键: 值}，便于逐字段比对。"""
    out = {}
    for i, (kind, params) in enumerate(feats):
        out["%d.kind" % i] = kind
        for k, v in sorted(params.items()):
            out["%d.%s" % (i, k)] = v
    return out


def field_diff(off, on):
    """返回 [(键, 关值, 开值)]，只列真正不同的字段。"""
    a, b = _flat(off), _flat(on)
    keys = sorted(set(a) | set(b))
    return [(k, a.get(k, "<无>"), b.get(k, "<无>")) for k in keys if a.get(k) != b.get(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--full", action="store_true", help="差异值不截断")
    args = ap.parse_args()

    data = snap()
    rows = []
    for text, (off, on) in data.items():
        same = off == on
        rows.append({"text": text, "same": same, "off": off, "on": on})
    n_same = sum(1 for r in rows if r["same"])
    print("完全一致 %d/%d" % (n_same, len(rows)))
    for r in rows:
        if r["same"]:
            continue
        print("\n题:", r["text"][:44])
        for k, ov, nv in field_diff(r["off"], r["on"]):
            so, sn = repr(ov), repr(nv)
            if not args.full:
                so, sn = so[:60], sn[:60]
            print("   %-24s 关 %-62s 开 %s" % (k, so, sn))
        if args.detail:
            cat = partlib.find_part(r["text"])
            print("   类别:", cat)
            for variant in (r["text"], "".join(r["text"].split())):
                work = P.add_boundaries(variant, set(P._PARAM_NAMES))
                print("     变体 %-34r -> work %r" % (variant[:34], work))
    return 0


if __name__ == "__main__":
    sys.exit(main())
