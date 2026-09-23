# -*- coding: utf-8 -*-
r"""二次深挖：螺钉库的 Z0/变体交叉表，外购件 EGT 型材/连接件的完整型号规律。"""
from __future__ import annotations

import io
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_new_tables import BASE, read_xlsx, num  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "reports", "新表分析_深挖.md")


def main():
    L = []
    L.append("# 新表深挖")
    L.append("")

    # ---------- 螺钉 ----------
    sheets = read_xlsx(os.path.join(BASE, "螺钉MyBoltLibrary数据整理.xlsx"))
    rows = list(sheets.values())[0]
    head = rows[0]
    ix = {h: i for i, h in enumerate(head)}
    data = [r for r in rows[1:] if any(str(x).strip() for x in r)]

    L.append("## 一、螺钉：类型 × 规格 → Z0 交叉表")
    L.append("")
    cross = defaultdict(set)
    cnt = Counter()
    for r in data:
        t = str(r[ix["螺栓类型"]]).strip()
        sp = str(r[ix["螺纹规格"]]).strip()
        z = num(r[ix["Z0"]])
        cross[(t, sp)].add(z)
        cnt[(t, sp)] += 1
    L.append("| 螺栓类型 | 规格 | 件数 | Z0 |")
    L.append("| --- | --- | --- | --- |")
    for k in sorted(cross, key=lambda k: (k[0], k[1])):
        L.append("| %s | %s | %d | %s |" % (k[0], k[1], cnt[k],
                                            sorted(x for x in cross[k] if x is not None)))
    L.append("")

    L.append("### 1.1 每个规格下两种类型的 Z0 差值（= 平垫厚度？）")
    L.append("")
    specs = sorted(set(k[1] for k in cross), key=lambda s: (len(s), s))
    L.append("| 规格 | 带弹垫 Z0 | 带弹垫+平垫 Z0 | 差值 |")
    L.append("| --- | --- | --- | --- |")
    for sp in specs:
        a = cross.get(("内六角螺栓-带弹垫", sp), set())
        b = cross.get(("内六角螺栓-带弹垫和平垫", sp), set())
        av = sorted(x for x in a if x is not None)
        bv = sorted(x for x in b if x is not None)
        diff = [round(abs(x - y), 3) for x in av for y in bv if x is not None and y is not None]
        L.append("| %s | %s | %s | %s |" % (sp, av, bv, sorted(set(diff))))
    L.append("")

    L.append("### 1.2 同一规格下 Z0 是否随长度变化")
    L.append("")
    zlen = defaultdict(set)
    for r in data:
        t = str(r[ix["螺栓类型"]]).strip()
        sp = str(r[ix["螺纹规格"]]).strip()
        ln = num(r[ix["长度"]])
        z = num(r[ix["Z0"]])
        zlen[(t, sp)].add((ln, z))
    for k in sorted(zlen, key=lambda k: (k[0], k[1])):
        pairs = sorted(zlen[k])
        L.append("- %s / %s: %s" % (k[0], k[1],
                                    ", ".join("L=%s→Z0=%s" % p for p in pairs)))
    L.append("")

    L.append("### 1.3 与真实几何对照（kb/real_geometry.json 中头部高度）")
    L.append("")
    try:
        rg = __import__("json").load(io.open(
            os.path.join(os.path.dirname(BASE), "kb", "real_geometry.json"),
            encoding="utf-8"))
        recs = rg.get("parts", rg) if isinstance(rg, dict) else rg
        got = {}
        for name, rec in (recs.items() if isinstance(recs, dict) else []):
            code = re.search(r"(M\d+)x([\d.]+)", name)
            if not code:
                continue
            cs, cl = code.group(1), float(code.group(2))
            bbox = rec.get("bbox")
            if not bbox:
                continue
            h = float(bbox[5]) - float(bbox[2])
            got.setdefault(cs, set()).add((cl, round(h, 2)))
    except Exception as e:
        L.append("（读取 real_geometry 失败: %s）" % e)
        got = {}
    for cs in sorted(got, key=lambda s: (len(s), s)):
        L.append("- %s 实测总高-长度差: %s" % (
            cs, sorted(set(round(h - l, 2) for l, h in got[cs] if h > l))[:6]))
    L.append("")

    # ---------- 外购件 ----------
    sheets2 = read_xlsx(os.path.join(BASE, "外购件数据整理.xlsx"))
    rows2 = list(sheets2.values())[0]
    head2 = rows2[0]
    ix2 = {h: i for i, h in enumerate(head2)}
    data2 = [r for r in rows2[1:] if any(str(x).strip() for x in r)]

    L.append("## 二、外购件：EGT 家族完整规律")
    L.append("")
    egt = [r for r in data2 if str(r[ix2["来源Sheet"]]).strip() == "EGT型材"]
    prof = [r for r in egt if str(r[ix2["描述"]]).strip() == "型材"]
    conn = [r for r in egt if str(r[ix2["描述"]]).strip() == "连接件"]

    L.append("### 2.1 型材：型号解析")
    L.append("")
    L.append("- 共 %d 项" % len(prof))
    fam = defaultdict(list)
    for r in prof:
        mo = str(r[ix2["型号"]]).strip()
        m = re.match(r"^(EGT\d+)\s+((?:SW|D)\d+)\s*x\s*(\d+)$", mo, re.I)
        if not m:
            L.append("- ✗ 无法解析: `%s`" % mo)
            continue
        fam[(m.group(1), m.group(2))].append(int(m.group(3)))
    L.append("")
    L.append("| 家族 | 断面(SW) | 件数 | 长度范围 | 长度步长 | 长度序列 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for k in sorted(fam, key=lambda k: (k[0], k[1])):
        v = sorted(fam[k])
        d = sorted(set(v[i + 1] - v[i] for i in range(len(v) - 1))) if len(v) > 1 else []
        L.append("| %s | %s | %d | %d~%d | %s | %s |" % (
            k[0], k[1], len(v), v[0], v[-1], d,
            ",".join(str(x) for x in v[:14]) + ("..." if len(v) > 14 else "")))
    L.append("")

    L.append("### 2.2 连接件：完整型号表")
    L.append("")
    L.append("- 共 %d 项" % len(conn))
    L.append("")
    L.append("| 序号 | 型号 | 备注 |")
    L.append("| --- | --- | --- |")
    for r in conn:
        L.append("| %s | `%s` | %s |" % (str(r[ix2["序号"]]).strip(),
                                         str(r[ix2["型号"]]).strip(),
                                         str(r[ix2["备注"]]).strip()[:30]
                                         if len(r) > 5 else ""))
    L.append("")

    L.append("### 2.3 非 EGT 外购件完整表")
    L.append("")
    L.append("| 来源 | 描述 | 品牌 | 型号 |")
    L.append("| --- | --- | --- | --- |")
    for r in data2:
        if str(r[ix2["来源Sheet"]]).strip() == "EGT型材":
            continue
        L.append("| %s | %s | %s | `%s` |" % (
            str(r[ix2["来源Sheet"]]).strip(), str(r[ix2["描述"]]).strip(),
            str(r[ix2["品牌"]]).strip(), str(r[ix2["型号"]]).strip()))
    L.append("")

    txt = "\n".join(L)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print("已写出 %s (%d 字符)" % (OUT, len(txt)))
    print("型材 %d 项, 连接件 %d 项" % (len(prof), len(conn)))


if __name__ == "__main__":
    main()
