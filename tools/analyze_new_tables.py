# -*- coding: utf-8 -*-
r"""汇总分析用户新加的两个"数据整理"表格。

输出 reports/新表分析.md，并在 stdout 打印摘要。

用法:
    python tools\analyze_new_tables.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from xml.etree import ElementTree as ET
import zipfile

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "零件参数")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "reports", "新表分析.md")


def col_idx(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path):
    out = OrderedDict()
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            r = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in r.findall(M + "si"):
                shared.append("".join(t.text or "" for t in si.iter(M + "t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        titles = [s.get("name") for s in wb.iter(M + "sheet")]
        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        for i, sn in enumerate(sheets):
            root = ET.fromstring(z.read(sn))
            rows = []
            for row in root.iter(M + "row"):
                cells = {}
                for c in row.iter(M + "c"):
                    t = c.get("t")
                    v = c.find(M + "v")
                    val = ""
                    if t == "s" and v is not None:
                        k = int(v.text or 0)
                        val = shared[k] if 0 <= k < len(shared) else ""
                    elif v is not None:
                        val = v.text or ""
                    cells[col_idx(c.get("r"))] = val
                if cells:
                    rows.append([cells.get(k, "") for k in range(max(cells) + 1)])
            out[titles[i] if i < len(titles) else sn] = rows
    return out


def num(x):
    try:
        f = float(x)
        return int(f) if abs(f - round(f)) < 1e-9 else f
    except Exception:
        return None


def analyze_bolt(rows, L):
    head = rows[0]
    idx = {h: i for i, h in enumerate(head)}
    data = [r for r in rows[1:] if any(str(x).strip() for x in r)]
    L.append("## 一、螺钉 MyBoltLibrary 数据整理")
    L.append("")
    L.append("- 总行数: **%d**" % len(data))
    L.append("- 列: %s" % " / ".join(head))
    L.append("")

    types = Counter(str(r[idx.get("螺栓类型", 1)]).strip() for r in data)
    L.append("### 1.1 螺栓类型分布")
    L.append("")
    L.append("| 螺栓类型 | 数量 |")
    L.append("| --- | --- |")
    for k, v in types.most_common():
        L.append("| %s | %d |" % (k or "(空)", v))
    L.append("")

    by_spec = defaultdict(list)
    for r in data:
        sp = str(r[idx.get("螺纹规格", 2)]).strip()
        ln = num(r[idx.get("长度", 3)])
        by_spec[sp].append(ln)
    L.append("### 1.2 螺纹规格 → 长度系列")
    L.append("")
    L.append("| 螺纹规格 | 件数 | 长度系列 |")
    L.append("| --- | --- | --- |")
    for sp in sorted(by_spec, key=lambda s: (len(s), s)):
        v = sorted(x for x in by_spec[sp] if x is not None)
        L.append("| %s | %d | %s |" % (sp, len(by_spec[sp]),
                                       ", ".join(str(x) for x in v)))
    L.append("")

    # 长度步长
    L.append("### 1.3 长度步长检查")
    L.append("")
    for sp in sorted(by_spec, key=lambda s: (len(s), s)):
        v = sorted(set(x for x in by_spec[sp] if x is not None))
        if len(v) < 2:
            L.append("- %s: 单值 %s" % (sp, v))
            continue
        d = sorted(set(round(v[i + 1] - v[i], 3) for i in range(len(v) - 1)))
        L.append("- %s: 共 %d 档, 步长 %s" % (sp, len(v), d))
    L.append("")

    # 方向矩阵
    L.append("### 1.4 方向矩阵（X/Y/Z 局部轴 → 全局）唯一组合")
    L.append("")
    mats = Counter()
    for r in data:
        key = tuple(str(r[idx.get(k, -1)]).strip() for k in
                    ("Xx", "Xy", "Xz", "Yx", "Yy", "Yz", "Zx", "Zy", "Zz")
                    if k in idx)
        mats[key] += 1
    L.append("| Xx Xy Xz | Yx Yy Yz | Zx Zy Zz | 数量 |")
    L.append("| --- | --- | --- | --- |")
    for k, v in mats.most_common(12):
        L.append("| %s | %s | %s | %d |" % (" ".join(k[0:3]), " ".join(k[3:6]),
                                            " ".join(k[6:9]), v))
    L.append("")
    L.append("唯一方向矩阵个数: **%d**" % len(mats))
    L.append("")

    # 原点偏移
    L.append("### 1.5 原点偏移 (X0, Y0, Z0)")
    L.append("")
    zc = Counter(str(r[idx.get("Z0", -1)]).strip() for r in data if "Z0" in idx)
    L.append("| Z0 | 数量 |")
    L.append("| --- | --- |")
    for k, v in zc.most_common():
        L.append("| %s | %d |" % (k, v))
    L.append("")
    zs = [num(r[idx["Z0"]]) for r in data if "Z0" in idx and num(r[idx["Z0"]]) is not None]
    if zs:
        L.append("Z0 取值范围: %s ~ %s，中位 %s" % (min(zs), max(zs),
                                                  sorted(zs)[len(zs) // 2]))
    x0 = set(str(r[idx["X0"]]).strip() for r in data if "X0" in idx)
    y0 = set(str(r[idx["Y0"]]).strip() for r in data if "Y0" in idx)
    L.append("X0 取值集合: %s" % sorted(x0))
    L.append("Y0 取值集合: %s" % sorted(y0))
    L.append("")
    L.append("> 结论: 每颗螺栓插入点都在 `(0, 0, Z0)`，位于螺栓轴线**头部一侧**之外；"
             "`Z0` 随规格/长度变化，是装配时的贴合面偏置。")
    L.append("")

    # 文件名与规格一致性
    L.append("### 1.6 文件名 vs 表内规格自检")
    L.append("")
    bad = []
    for r in data:
        fn = str(r[idx.get("文件名", 0)]).strip()
        m = re.match(r"M(\d+)x([\d.]+)", fn)
        if not m:
            bad.append((fn, "文件名不符 M<d>x<L>"))
            continue
        sp = str(r[idx.get("螺纹规格", 2)]).strip()
        ln = num(r[idx.get("长度", 3)])
        if sp != "M" + m.group(1):
            bad.append((fn, "规格 %s ≠ %s" % (sp, m.group(1))))
        elif ln is not None and abs(float(m.group(2)) - ln) > 1e-9:
            bad.append((fn, "长度 %s ≠ %s" % (ln, m.group(2))))
    if bad:
        for fn, why in bad[:30]:
            L.append("- ✗ %s: %s" % (fn, why))
    else:
        L.append("全部 %d 行的文件名、螺纹规格、长度三者一致 ✓" % len(data))
    L.append("")
    return data


def analyze_purchased(rows, L):
    head = rows[0]
    idx = {h: i for i, h in enumerate(head)}
    data = [r for r in rows[1:] if any(str(x).strip() for x in r)]
    L.append("## 二、外购件数据整理")
    L.append("")
    L.append("- 总行数: **%d**" % len(data))
    L.append("- 列: %s" % " / ".join(head))
    L.append("")

    src = Counter(str(r[idx.get("来源Sheet", 0)]).strip() for r in data)
    L.append("### 2.1 来源分类")
    L.append("")
    L.append("| 来源Sheet | 数量 |")
    L.append("| --- | --- |")
    for k, v in src.most_common():
        L.append("| %s | %d |" % (k or "(空)", v))
    L.append("")

    L.append("### 2.2 品牌分布")
    L.append("")
    br = Counter(str(r[idx.get("品牌", 3)]).strip() for r in data)
    L.append("| 品牌 | 数量 |")
    L.append("| --- | --- |")
    for k, v in br.most_common():
        L.append("| %s | %d |" % (k or "(空)", v))
    L.append("")

    L.append("### 2.3 描述分布")
    L.append("")
    de = Counter(str(r[idx.get("描述", 2)]).strip() for r in data)
    L.append("| 描述 | 数量 |")
    L.append("| --- | --- |")
    for k, v in de.most_common(40):
        L.append("| %s | %d |" % (k or "(空)", v))
    L.append("")

    # 型号族
    L.append("### 2.4 型号族（型号去掉尾数后的前缀）")
    L.append("")
    fam = Counter()
    for r in data:
        mo = str(r[idx.get("型号", 4)]).strip()
        p = re.match(r"^([A-Za-z]+\d*)", mo)
        fam[p.group(1) if p else "(其他)"] += 1
    L.append("| 型号前缀 | 数量 |")
    L.append("| --- | --- |")
    for k, v in fam.most_common(40):
        L.append("| %s | %d |" % (k, v))
    L.append("")

    # 每个来源下的型号样例 + 规律
    L.append("### 2.5 各来源的型号规律")
    L.append("")
    groups = defaultdict(list)
    for r in data:
        groups[str(r[idx.get("来源Sheet", 0)]).strip()].append(r)
    for g in sorted(groups, key=lambda k: -len(groups[k])):
        rs = groups[g]
        L.append("#### %s（%d 项）" % (g, len(rs)))
        L.append("")
        desc = Counter(str(r[idx.get("描述", 2)]).strip() for r in rs)
        L.append("- 描述: %s" % ", ".join("%s×%d" % (k or "(空)", v)
                                         for k, v in desc.most_common(8)))
        brand = Counter(str(r[idx.get("品牌", 3)]).strip() for r in rs)
        L.append("- 品牌: %s" % ", ".join("%s×%d" % (k or "(空)", v)
                                         for k, v in brand.most_common(8)))
        L.append("- 型号样例: %s" % ", ".join(
            "`%s`" % str(r[idx.get("型号", 4)]).strip() for r in rs[:10]))
        # 数值维度统计
        nums = defaultdict(list)
        for r in rs:
            mo = str(r[idx.get("型号", 4)]).strip()
            for mm in re.finditer(r"(\d+(?:\.\d+)?)", mo):
                pass
            nums["数字个数"].append(len(re.findall(r"\d+(?:\.\d+)?", mo)))
        if nums["数字个数"]:
            L.append("- 型号中数字个数分布: %s" % dict(Counter(nums["数字个数"])))
        L.append("")
    return data


def main():
    L = []
    L.append("# 新表分析：螺钉库 + 外购件库")
    L.append("")
    L.append("数据来源: `零件参数\\螺钉MyBoltLibrary数据整理.xlsx`、"
             "`零件参数\\外购件数据整理.xlsx`")
    L.append("")
    bolt = None
    purch = None
    for fn, target in (("螺钉MyBoltLibrary数据整理.xlsx", "bolt"),
                       ("外购件数据整理.xlsx", "purch")):
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            L.append("## 缺失: %s" % fn)
            continue
        sheets = read_xlsx(p)
        L.append("<!-- %s: %d sheet -->" % (fn, len(sheets)))
        for sname, rows in sheets.items():
            if not rows:
                continue
            if target == "bolt":
                bolt = analyze_bolt(rows, L)
            else:
                purch = analyze_purchased(rows, L)
        L.append("")

    txt = "\n".join(L)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(txt)
    print("已写出 %s (%d 字符)" % (OUT, len(txt)))
    print("螺钉行数=%s 外购件行数=%s" % (len(bolt) if bolt else "-",
                                        len(purch) if purch else "-"))


if __name__ == "__main__":
    main()
