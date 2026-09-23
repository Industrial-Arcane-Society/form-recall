# -*- coding: utf-8 -*-
r"""建立「型号 → 真实几何」对照表。

把用户新增的两个数据整理表（螺钉库 / 外购件库）与 521 个真实 STEP 的解析结果
(`kb/real_geometry.json`) 做连接，输出：

    kb/model_geometry.json          机器可读对照表
    reports/型号真实几何对照表.md    人读对照表

用法:
    python tools\build_model_geometry_map.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from analyze_new_tables import BASE, read_xlsx, num  # noqa: E402

KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")


def hdr(rows):
    return {h: i for i, h in enumerate(rows[0])}


def stem(name):
    return re.sub(r"\.(CATPart|stp|step)$", "", str(name).strip(), flags=re.I)


def load_bolts():
    p = os.path.join(BASE, "螺钉MyBoltLibrary数据整理.xlsx")
    rows = list(read_xlsx(p).values())[0]
    ix = hdr(rows)
    out = []
    for r in rows[1:]:
        if not any(str(x).strip() for x in r):
            continue
        out.append({
            "file": str(r[ix["文件名"]]).strip(),
            "stem": stem(r[ix["文件名"]]),
            "kind": str(r[ix["螺栓类型"]]).strip(),
            "spec": str(r[ix["螺纹规格"]]).strip(),
            "length": num(r[ix["长度"]]),
            "insert": [num(r[ix[k]]) for k in ("X0", "Y0", "Z0")],
            "orient": [[num(r[ix[a + b]]) for b in "xyz"] for a in "XYZ"],
        })
    return out


def load_purchased():
    p = os.path.join(BASE, "外购件数据整理.xlsx")
    rows = list(read_xlsx(p).values())[0]
    ix = hdr(rows)
    out = []
    for r in rows[1:]:
        if not any(str(x).strip() for x in r):
            continue
        out.append({
            "src": str(r[ix["来源Sheet"]]).strip(),
            "seq": str(r[ix["序号"]]).strip(),
            "desc": str(r[ix["描述"]]).strip(),
            "brand": str(r[ix["品牌"]]).strip(),
            "model": str(r[ix["型号"]]).strip(),
            "note": str(r[ix["备注"]]).strip() if len(r) > 5 else "",
        })
    return out


# ---------- EGT 型材型号解析 ----------
_RE_SW = re.compile(r"^(EGT\d+)\s+(SW\d+)\s*x\s*(\d+)$", re.I)
_RE_D = re.compile(r"^(EGT\d+)\s+(D\d+)\s*x\s*(\d+)$", re.I)


def parse_egt_profile(model):
    m = _RE_SW.match(model) or _RE_D.match(model)
    if not m:
        return None
    return {"family": m.group(1).upper(), "section": m.group(2).upper(),
            "code": int(m.group(3))}


def summarize(rec):
    if not rec:
        return None
    b = rec["bbox"]["size"]
    return {
        "n_solids": rec["n_solids"], "n_faces": rec["n_faces"],
        "n_cylinders": rec["n_cylinders"], "n_planes": rec.get("n_planes"),
        "n_cones": rec.get("n_cones", 0), "n_tori": rec.get("n_tori", 0),
        "bbox_min": rec["bbox"]["min"], "bbox_max": rec["bbox"]["max"],
        "bbox_size": b,
        "hole_diameters": rec.get("hole_diameters", {}),
        # 主轴 = 三个尺寸里最大者（长型材）
        "axis": "XYZ"[max(range(3), key=lambda i: abs(b[i]))],
        "axis_len": max(b, key=abs),
        "sec": sorted(abs(v) for v in b)[:2],
    }


def main():
    def _load(name):
        with io.open(os.path.join(KB, name), encoding="utf-8") as f:
            return json.load(f)

    rg = _load("real_geometry.json")

    def norm(s):
        """统一大小写、去掉尾部的 `_`/`-`/空格、把 × 归一成 x。"""
        s = str(s).strip()
        s = re.sub(r"\.(CATPart|stp|step)$", "", s, flags=re.I)
        s = s.replace("\u00d7", "x").replace("*", "x").strip()
        s = re.sub(r"[_\-\s]+$", "", s)
        return s.lower()

    real = {}          # 归一化 stem → rec
    real_list = []     # 保序列表，用于前缀匹配
    for rec in rg["parts"]:
        s = stem(rec["file"])
        real[norm(s)] = rec
        real_list.append((norm(s), rec))
    print("真实几何 %d 件" % len(real))

    def find(model):
        """三段匹配：① 归一化精确 → ② 前缀（后随分隔符）→ ③ 最长公共前缀兜底。"""
        m = norm(model)
        hit = real.get(m)
        if hit:
            return hit
        for sep in (" ", "-", "_", ".", "("):
            cands = [r for s, r in real_list if s.startswith(m + sep)]
            if cands:
                # 多个候选取名字最短者（无附加尺寸后缀）
                return sorted(cands, key=lambda r: len(stem(r["file"])))[0]
        cands = [r for s, r in real_list if s.startswith(m)]
        if cands:
            return sorted(cands, key=lambda r: len(stem(r["file"])))[0]
        return None

    bolts = load_bolts()
    purch = load_purchased()
    print("螺钉表 %d 行, 外购件表 %d 行" % (len(bolts), len(purch)))

    # ---------------- 螺钉 ----------------
    bolt_out = OrderedDict()
    miss_bolt = []
    for b in bolts:
        rec = find(b["stem"])
        key = "%s|%s" % (b["spec"], b["length"])
        e = bolt_out.setdefault(key, {
            "spec": b["spec"], "length": b["length"],
            "variants": OrderedDict(),
        })
        g = summarize(rec) if rec else None
        e["variants"][b["kind"]] = {
            "file": b["file"], "insert": b["insert"], "orient": b["orient"],
            "real": g,
        }
        if rec is None:
            miss_bolt.append(b["file"])

    n_bolt_real = sum(1 for e in bolt_out.values()
                      for v in e["variants"].values() if v["real"])
    n_bolt = sum(len(e["variants"]) for e in bolt_out.values())
    print("螺钉: %d 个(规格,长度)组合 / %d 行, 有真实几何 %d" %
          (len(bolt_out), n_bolt, n_bolt_real))

    # ---------------- 型材 ----------------
    prof = OrderedDict()
    conn = OrderedDict()
    miss_purch = []
    for p in purch:
        if p["src"] != "EGT型材":
            continue
        rec = find(p["model"])
        g = summarize(rec) if rec else None
        if g is None:
            miss_purch.append(p["model"])
        if p["desc"] == "型材":
            q = parse_egt_profile(p["model"])
            fam = q["family"] if q else "(未解析)"
            sec = q["section"] if q else "(未解析)"
            code = q["code"] if q else None
            e = prof.setdefault(fam, {"family": fam, "section": sec,
                                      "items": OrderedDict()})
            e["items"][p["model"]] = {"code": code, "real": g}
        else:
            conn[p["model"]] = {"desc": p["desc"], "brand": p["brand"],
                                "real": g}

    print("型材家族 %d 个 / %d 项, 连接件 %d 项" %
          (len(prof), sum(len(v["items"]) for v in prof.values()), len(conn)))
    print("无真实几何: 型材/连接件 %d 项, 螺钉 %d 项" %
          (len(miss_purch), len(miss_bolt)))

    # ---------------- 对照表 JSON ----------------
    doc = {
        "source": {
            "geometry": "kb/real_geometry.json (521 STEP)",
            "tables": ["零件参数/螺钉MyBoltLibrary数据整理.xlsx",
                       "零件参数/外购件数据整理.xlsx"],
        },
        "bolts": bolt_out,
        "egt_profiles": prof,
        "egt_connectors": conn,
        "purchased_other": [p for p in purch if p["src"] != "EGT型材"],
        "missing_geometry": {"bolts": miss_bolt, "egt": miss_purch},
    }
    os.makedirs(KB, exist_ok=True)
    with io.open(os.path.join(KB, "model_geometry.json"), "w",
                 encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print("→ kb/model_geometry.json")

    # ---------------- 人读对照表 ----------------
    L = []
    L.append("# 型号 → 真实几何 对照表")
    L.append("")
    L.append("由 `tools/build_model_geometry_map.py` 生成。")
    L.append("")
    L.append("- 真实几何来源：`kb/real_geometry.json`（521 个 STEP 实测解析）")
    L.append("- 型号来源：`零件参数\\螺钉MyBoltLibrary数据整理.xlsx`、"
             "`零件参数\\外购件数据整理.xlsx`")
    L.append("- `bbox` 为 STEP 实测包围盒（mm），`faces/cyl` 为实测面数与圆柱面数")
    L.append("")

    L.append("## 一、内六角螺钉（%d 个规格×长度组合）" % len(bolt_out))
    L.append("")
    L.append("| 螺纹规格 | 长度 L | 装入点 (X0,Y0,Z0) | 实测总高 | 实测头径 | 实体数 | 面数 | 圆柱面 | 真实文件 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, e in bolt_out.items():
        v = list(e["variants"].values())[0]
        g = v["real"]
        other = list(e["variants"].values())[1] if len(e["variants"]) > 1 else None
        tot = ""; hd = ""
        if g:
            zsz = abs(g["bbox_size"][2]) if g["axis"] == "Z" else g["axis_len"]
            tot = "%.1f" % g["axis_len"]
            hd = "%.1f" % (max(float(k) for k in g["hole_diameters"])
                           if g["hole_diameters"] else 0)
        extra = ""
        if other and other["real"] and g:
            d = abs(other["real"]["axis_len"] - g["axis_len"])
            if d > 1e-6:
                extra = " / 平垫版 %.1f" % other["real"]["axis_len"]
        L.append("| %s | %s | %s | %s%s | %s | %s | %s | %s | `%s` |" % (
            e["spec"], e["length"], v["insert"], tot, extra, hd,
            g["n_solids"] if g else "-", g["n_faces"] if g else "-",
            g["n_cylinders"] if g else "-", v["file"]))
    L.append("")

    L.append("## 二、EGT 型材")
    L.append("")
    for fam, e in prof.items():
        L.append("### %s（断面 %s，%d 档长度）" % (fam, e["section"],
                                                  len(e["items"])))
        L.append("")
        L.append("| 型号 | 型号码 | 实测长度 | 实测断面 | 实体 | 面 | 圆柱 | 中心孔 |")
        L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for model, it in e["items"].items():
            g = it["real"]
            if not g:
                L.append("| `%s` | %s | ⚠ 无真实几何 | - | - | - | - | - |"
                         % (model, it["code"]))
                continue
            sec = "×".join("%.1f" % s for s in g["sec"])
            holes = ", ".join("⌀%s×%s" % (k, v)
                              for k, v in sorted(g["hole_diameters"].items(),
                                                 key=lambda kv: -float(kv[0]))[:3])
            L.append("| `%s` | %s | %.2f (%s轴) | %s | %d | %d | %d | %s |" % (
                model, it["code"], g["axis_len"], g["axis"], sec,
                g["n_solids"], g["n_faces"], g["n_cylinders"], holes))
        L.append("")

    L.append("## 三、EGT/VGT 连接件（%d 项）" % len(conn))
    L.append("")
    L.append("| 型号 | 实测包围盒 (X×Y×Z) | 实体 | 面 | 圆柱 | 平面 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for model, it in conn.items():
        g = it["real"]
        if not g:
            L.append("| `%s` | ⚠ 无真实几何 | - | - | - | - |" % model)
            continue
        L.append("| `%s` | %s | %d | %d | %d | %s |" % (
            model, "×".join("%.1f" % s for s in g["bbox_size"]),
            g["n_solids"], g["n_faces"], g["n_cylinders"], g["n_planes"]))
    L.append("")

    L.append("## 四、其他外购件")
    L.append("")
    L.append("| 来源 | 描述 | 品牌 | 型号 | 真实几何 | 包围盒 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for p in doc["purchased_other"]:
        rec = find(p["model"])
        g = summarize(rec) if rec else None
        L.append("| %s | %s | %s | `%s` | %s | %s |" % (
            p["src"], p["desc"], p["brand"], p["model"],
            "✓ %s" % stem(rec["file"]) if g else "⚠ 缺",
            "×".join("%.1f" % s for s in g["bbox_size"]) if g else "-"))
    L.append("")

    L.append("## 五、缺口清单（表里有、导出的 STEP 里没有）")
    L.append("")
    L.append("- 螺钉：%s" % (", ".join("`%s`" % x for x in miss_bolt) or "无"))
    L.append("- EGT 型材/连接件：%s" % (", ".join("`%s`" % x for x in miss_purch)
                                       or "无"))
    L.append("")

    with io.open(os.path.join(REPORTS, "型号真实几何对照表.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(L))
    print("→ reports/型号真实几何对照表.md")
    return doc


if __name__ == "__main__":
    main()
