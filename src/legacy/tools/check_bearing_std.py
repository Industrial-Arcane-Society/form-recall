# -*- coding: utf-8 -*-
r"""
U5 标准数据入库 —— 脊柱自检（不需要联网、不依赖手册站点）。

检查四件事：
  1. 数据库完整性：条数、必填字段、d>0、B<D；
  2. **锚点一致性**：抽取结果必须等于公开标准值（6204=20×47×14 等）；
  3. 查不到就明确报错（不静默取近似值）；
  4. 端到端：用户写「深沟球轴承 6204」必须建出外径 47 / 宽度 14 的圆柱+内孔。

用法：python tools\check_bearing_std.py
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler import std_bearing as SB      # noqa: E402
from ai_modeler import nlparser as NLP        # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def main():
    print("U5 轴承标准数据自检（GB/T 276—2013）")

    # 1) 数据库完整性
    print("\n[1] 数据库完整性")
    db = SB.load_db()
    tbl = SB.bearings()
    chk(len(tbl) >= 300, "条数 %d（期望 >=300）" % len(tbl))
    bad_field = [k for k, v in tbl.items()
                 if not isinstance(v.get("d"), (int, float))
                 or not isinstance(v.get("D"), (int, float))
                 or v.get("B") is None]
    chk(not bad_field, "字段齐全（缺字段 %d 条）" % len(bad_field))
    chk(all(v["d"] > 0 for v in tbl.values()), "内径全部 > 0")
    chk(all(v["D"] > v["d"] for v in tbl.values()), "外径全部 > 内径")
    chk(all(v["B"] < v["D"] for v in tbl.values()), "宽度全部 < 外径")
    chk("GB/T 276" in SB.standard(), "标准号标注为 GB/T 276（%s）" % SB.standard())
    chk(bool(db.get("source")), "带来源 URL（%d 条）" % len(db.get("source") or []))

    # 2) 锚点一致性（金标准）
    print("\n[2] 锚点一致性（公开标准值）")
    anchors = {
        "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
        "6004": (20, 42, 12), "6006": (30, 55, 13),
        "6200": (10, 30, 9), "6202": (15, 35, 11), "6204": (20, 47, 14),
        "6205": (25, 52, 15), "6206": (30, 62, 16),
        "6300": (10, 35, 11), "6304": (20, 52, 15), "6305": (25, 62, 17),
        "6306": (30, 72, 19), "6403": (17, 62, 17),
    }
    miss = []
    for code, (d, D, B) in sorted(anchors.items()):
        s = SB.spec(code=code)
        ok = (s.get("d") == d and s.get("D") == D and s.get("B") == B)
        if not ok:
            miss.append(code)
    chk(not miss, "15 项锚点全部一致（不一致：%s）" % (miss or "无"))

    # 3) 缺档必须明确报错
    print("\n[3] 缺档处理（不猜）")
    for code in ("9999", "abc", ""):
        s = SB.spec(code=code)
        chk(bool(s.get("error")), "代号 %r 明确报错而不是给近似值" % (code,))
    s = SB.spec(bore=999)
    chk(bool(s.get("error")), "内径 999 明确报错")

    # 4) 一个内径多系列 -> 全部列出，不替用户挑
    print("\n[4] 同内径多系列：列出候选而不代选")
    s = SB.spec(bore=20)
    cands = s.get("candidates") or []
    chk(len(cands) >= 5, "内径 20 列出 %d 个候选" % len(cands))
    chk(any(c["code"] == "6204" for c in cands), "候选里含 6204")
    chk(all(c["d"] == 20 for c in cands), "候选内径全部为 20")
    s63 = SB.spec(bore=25, prefer_series="63")
    chk(s63.get("code") == "6305", "指定 63 系列时选定 6305（实得 %s）" % s63.get("code"))

    # 5) 端到端
    print("\n[5] 端到端（提示词 -> 特征）")
    cases = [
        ("深沟球轴承 6204", 47.0, 14.0),
        ("轴承 6305", 62.0, 17.0),
        ("深沟球轴承 代号6206", 62.0, 16.0),
    ]
    for text, D, B in cases:
        r = NLP.parse(text)
        cyl = [f for f in r.doc.features if f.kind == "cylinder"]
        hole = [f for f in r.doc.features if f.kind == "hole"]
        got_h = cyl[0].params.get("height") if cyl else None
        got_r = cyl[0].params.get("radius") if cyl else None
        chk(bool(cyl) and bool(hole),
            "%r 建出 圆柱+内孔（%d + %d）" % (text, len(cyl), len(hole)))
        chk(got_h == B, "%r 宽度 %s（期望 %g）" % (text, got_h, B))
        chk(got_r == D / 2.0, "%r 外半径 %s（期望 %g）" % (text, got_r, D / 2.0))

    print("\n%s" % ("全部通过 ✅" if not FAIL else "失败 %d 项 ❌" % len(FAIL)))
    for m in FAIL:
        print("   -", m)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
