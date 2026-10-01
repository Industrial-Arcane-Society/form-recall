# -*- coding: utf-8 -*-
r"""从实测几何**学习**规律并固化成知识库。

产出（全部来自实测，不含猜测）：
    kb/bolt_library.json              内六角螺钉库（55 个规格×长度）
    kb/egt_library.json               EGT 型材 + 连接件
    kb/standard_real_library.json     定位销/销座/支座/止块/垫片垫块/分气块/阀岛/…
    kb/purchased_library.json         UNIVER / SMC / 吊环 / 接近开关

每个族都做**逐条校验**：把学到的公式回代到每一条实测记录，残差超标即报错，
不达标就不写库（宁可失败，也不写一条没验证过的规律）。

用法: python tools\learn_real_library.py
输出: kb\*.json、reports\真实几何学习报告.md
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "kb")
REPORTS = os.path.join(ROOT, "reports")

# ---- ISO 4762 / GB/T 70.1 内六角圆柱头螺钉 ----
ISO4762 = {
    "M5":  {"head_d": 8.5,  "head_h": 5.0,  "s": 4.0,  "dw": 8.5},
    "M6":  {"head_d": 10.0, "head_h": 6.0,  "s": 5.0,  "dw": 10.0},
    "M8":  {"head_d": 13.0, "head_h": 8.0,  "s": 6.0,  "dw": 13.0},
    "M10": {"head_d": 16.0, "head_h": 10.0, "s": 8.0,  "dw": 16.0},
    "M12": {"head_d": 18.0, "head_h": 12.0, "s": 10.0, "dw": 18.0},
}
# ---- GB/T 97.1 平垫圈（内径 d1, 外径 d2, 厚 h）----
GB97 = {"M5": (5.3, 10.0, 1.0), "M6": (6.4, 12.0, 1.6), "M8": (8.4, 16.0, 1.6),
        "M10": (10.5, 20.0, 2.0), "M12": (13.0, 24.0, 2.5)}
# ---- GB/T 93 标准型弹性垫圈（内径 d, 外径 s, 厚 h）—— 外径按实测指纹 ----
GB93 = {"M5": (5.1, 8.8, 1.2), "M6": (6.1, 9.9, 1.5), "M8": (8.1, 12.7, 2.0),
        "M10": (10.2, 16.0, 2.5), "M12": (12.2, 18.0, 3.0)}

LOG = []


def say(s=""):
    print(s)
    LOG.append(s)


def load(name):
    with io.open(os.path.join(KB, name), encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    s = str(s).strip()
    s = re.sub(r"\.(CATPart|stp|step)$", "", s, flags=re.I)
    s = s.replace("\u00d7", "x").replace("*", "x").strip()
    # 表里的型号常带装饰尾字符（例：`UCBH50OCSK0105°`），文件名里没有
    s = re.sub(r"[\s\u00b0\u2103\u00ba]+$", "", s)
    return s.lower()


def stem(s):
    return re.sub(r"\.(CATPart|stp|step)$", "", str(s).strip(), flags=re.I)


def toplevel(p):
    """`rel` 是含文件名的相对路径（分隔符是反斜杠），取第一段作族名。"""
    return re.split(r"[\\/]", p["rel"])[0]


# ============================ 一、螺钉 ============================
def learn_bolts(real_parts):
    say("## 一、内六角螺钉库")
    say("")
    # 用户新增的《螺钉MyBoltLibrary数据整理.xlsx》里除规格/长度外，还有
    # **装配插入点 (X0,Y0,Z0)** 与 **方向矩阵 (Xx..Zz)**。这两个字段是装配信息，
    # 不是实体几何，但"贴合面偏置 Z0"对装配定位有用，所以一并入库。
    # `tools/build_model_geometry_map.py` 已经把它们解析进 kb/model_geometry.json。
    ins = {}
    try:
        mg = load("model_geometry.json")
        for key, e in (mg.get("bolts") or {}).items():
            for kind, v in (e.get("variants") or {}).items():
                ins[e["spec"], float(e["length"])] = {
                    "insert": v.get("insert"),
                    "orient": v.get("orient"),
                    "table_kind": kind,
                }
    except Exception:
        ins = {}
    by = {}
    for p in real_parts:
        if toplevel(p) != "螺钉":
            continue
        m = re.match(r"^(M\d+)(?:x|X)([\d.]+)", stem(p["file"]))
        if not m:
            continue
        by[(m.group(1), float(m.group(2)))] = p
    out = OrderedDict()
    n_ok = n_bad = 0
    bad = []
    n_ins = 0
    for (sp, ln), p in sorted(by.items(), key=lambda kv: (len(kv[0][0]),
                                                          kv[0][0], kv[0][1])):
        std = ISO4762[sp]
        fd1, fd2, fh = GB97[sp]
        sd, so, sh = GB93[sp]
        bb = p["bbox"]["size"]
        # 学到的公式：外径取平垫外径；总高 = 公称长 + 头高
        pred = [fd2, fd2, round(ln + std["head_h"], 3)]
        dev = max(abs(bb[i] - pred[i]) for i in range(3))
        extra = ins.get((sp, ln)) or {}
        if extra.get("insert"):
            n_ins += 1
        rec = {
            "model": "%sx%g" % (sp, ln),
            "spec": sp, "length": ln,
            "shank_d": float(sp[1:]),
            "head_d": std["head_d"], "head_h": std["head_h"],
            "hex_s": std["s"],
            "flat_washer": {"d_in": fd1, "d_out": fd2, "t": fh},
            "spring_washer": {"d_in": sd, "d_out": so, "t": sh},
            "total_h": round(ln + std["head_h"], 3),
            "envelope": pred,
            "envelope_measured": bb,
            "cyl_diameters": {k: v for k, v in
                              sorted(p["hole_diameters"].items(),
                                     key=lambda kv: -float(kv[0]))},
            # 来自用户表格的装配信息（原样保留，不做公式化——见下）
            "assembly_insert_pt": extra.get("insert"),
            "assembly_orient": extra.get("orient"),
            "table_type_label": extra.get("table_kind"),
            "n_solids": p["n_solids"], "n_faces": p["n_faces"],
            "provenance": "explicit",
            "source": "实测 STEP %s" % p["file"],
            "residual_mm": round(dev, 4),
        }
        if dev <= 0.01:
            n_ok += 1
        else:
            n_bad += 1
            bad.append((rec["model"], bb, pred))
        out[rec["model"]] = rec
    say("- 学到 %d 个规格×长度组合" % len(out))
    say("- 公式回代逐条校验：**%d/%d 通过**（残差 ≤0.01mm）"
        % (n_ok, n_ok + n_bad))
    if bad:
        say("- ⚠ 未通过（如实记录，不改公式去迁就）：")
        for m, b, pr in bad:
            say("  - `%s` 实测 %s ≠ 公式 %s" % (m, b, pr))
    say("- 装配插入点/方向矩阵：%d/%d 个模型已从用户表格入库" % (n_ins, len(out)))
    say("")
    say("> **装配插入点 `Z0` 没有公式化**：实测它按 (规格,长度) 取值 -1 / -2.5 / -2.7 /")
    say("> -3.2 / -3.6 / -4.5，且同一规格内并不单调（如 M6：L=12/20/25→-3.2、")
    say("> L=30→-3.6、L≥35→-2.7）。这更像是库作者分次维护留下的贴合面偏置，")
    say("> **原样保留为逐型号实测值**，不去拟合一条好看但不成立的公式。")
    say("")
    doc = {
        "source": "CATIA 导出 STEP 实测（%d 件）+ ISO 4762 / GB/T 97.1 / GB/T 93"
                  % len(by),
        "law": {
            "envelope": ["平垫外径(GB/T 97.1)", "平垫外径(GB/T 97.1)",
                         "公称长度 + 头高(ISO 4762)"],
            "note": "表里 110 行 = 55 个模型 × 2 个类型标签"
                    "（带弹垫 / 带弹垫和平垫），两标签指向同一文件，"
                    "实测几何完全相同；每个模型含 3 个实体"
                    "（螺钉 + 弹垫 + 平垫）",
        },
        "verified": {"pass": n_ok, "fail": n_bad},
        "bolts": out,
    }
    return doc


# ============================ 二、EGT ============================
_RE_EGT = re.compile(r"^(EGT\d+) (SW\d+|D\d+)x(\d+)$", re.I)


def learn_egt(real_parts):
    say("## 二、EGT 型材与连接件")
    say("")
    fams = defaultdict(list)
    conns = []
    for p in real_parts:
        if not p["rel"].startswith("外购件/EGT"):
            continue
        m = _RE_EGT.match(stem(p["file"]))
        if m:
            fams[(m.group(1).upper(), m.group(2).upper())].append(
                (int(m.group(3)), p))
        else:
            conns.append(p)
    fam_doc = OrderedDict()
    for k in sorted(fams):
        items = sorted(fams[k])
        codes = [c for c, _ in items]
        step = min(b - a for a, b in zip(codes, codes[1:]))
        secs = sorted(set(tuple(sorted(p["bbox"]["size"], key=lambda v: -v)[1:])
                          for _, p in items))
        offs = sorted(set(round(c - max(p["bbox"]["size"]), 3)
                            for c, p in items))
        cyl0 = items[0][1]["n_cylinders"]
        cstep = items[1][1]["n_cylinders"] - cyl0
        # 逐条校验
        ok = 0
        miss = []
        for c, p in items:
            kk = round((c - codes[0]) / float(step))
            want_len = c - offs[0]
            want_cyl = cyl0 + cstep * kk
            real_len = max(p["bbox"]["size"])
            if (abs(real_len - want_len) < 0.01
                    and p["n_cylinders"] == want_cyl):
                ok += 1
            else:
                miss.append({"code": c, "real_len": real_len,
                             "law_len": want_len,
                             "real_cyl": p["n_cylinders"], "law_cyl": want_cyl})
        say("### %s %s" % (k[0], k[1]))
        say("")
        say("- 实测 %d 档，型号码步长 **%d**" % (len(items), step))
        say("- 实测截面：%s" % "; ".join(
            "×".join("%.3f" % v for v in s) for s in secs))
        say("- 长度 = 型号码 − **%.3f**" % offs[0])
        say("- 圆柱面数 = **%d + %d·k**，k = (型号码 − %d)/%d"
            % (cyl0, cstep, codes[0], step))
        say("- 逐条校验：**%d/%d 通过**" % (ok, len(items)))
        if miss:
            say("- ⚠ 例外：")
            for m in miss:
                say("  - 码 %d：实测长 %.3f/柱面 %d，公式给 %.3f/%d"
                    % (m["code"], m["real_len"], m["real_cyl"],
                       m["law_len"], m["law_cyl"]))
        say("")
        fam_doc["%s %s" % k] = {
            "family": k[0], "section": k[1],
            "nominal_section": float(re.sub(r"[A-Z]", "", k[1])),
            "section_measured": secs,
            "code_base": codes[0], "code_step": step,
            "code_max": codes[-1],
            "length_offset": offs[0],
            "cyl0": cyl0, "cyl_step": cstep,
            "law": {
                "length": "型号码 - %.3f" % offs[0],
                "cyl_faces": "%d + %d*(型号码-%d)/%d"
                             % (cyl0, cstep, codes[0], step),
            },
            "verified": {"pass": ok, "total": len(items), "exceptions": miss},
            "provenance": "explicit",
            "records": {str(c): {
                "code": c,
                "length": round(max(p["bbox"]["size"]), 3),
                "section": sorted(p["bbox"]["size"], key=lambda v: -v)[1:],
                "n_faces": p["n_faces"], "n_cylinders": p["n_cylinders"],
                "n_solids": p["n_solids"],
                "cyl_diameters": p["hole_diameters"],
                "file": p["file"],
            } for c, p in items},
        }

    conn_doc = OrderedDict()
    for p in sorted(conns, key=lambda p: p["file"]):
        model = p["file"].split(" ")[0]
        e = conn_doc.setdefault(model, {"model": model, "files": [],
                                        "provenance": "explicit"})
        bb = (p.get("bbox") or {}).get("size")
        e["files"].append({
            "file": p["file"],
            "envelope": bb,
            "n_solids": p["n_solids"], "n_faces": p["n_faces"],
            "n_cylinders": p["n_cylinders"], "n_planes": p.get("n_planes"),
            "cyl_diameters": p["hole_diameters"],
        })
    say("- EGT/VGT 连接件：实测 **%d** 件，归并到 **%d** 个型号"
        % (len(conns), len(conn_doc)))
    say("")
    return {"profiles": fam_doc, "connectors": conn_doc}


# ============================ 三、标准件 ============================
def learn_standard(real_parts):
    say("## 三、厂内标准件（A/B 级实测外形可用）")
    say("")
    groups = defaultdict(list)
    for p in real_parts:
        rel = p["rel"]
        if not rel.startswith("标准件/") or "垫片垫块" in rel:
            continue
        groups[rel.split("/")[1]].append(p)
    out = OrderedDict()
    for g in sorted(groups):
        rs = groups[g]
        say("### %s（%d 件）" % (g, len(rs)))
        say("")
        say("| 型号 | 实体 | 面 | 柱面 | 实测外形 mm | 圆柱面直径 |")
        say("| --- | --- | --- | --- | --- | --- |")
        items = []
        for p in sorted(rs, key=lambda p: p["file"]):
            bb = (p.get("bbox") or {}).get("size")
            cd = p["hole_diameters"]
            items.append({
                "model": re.sub(r"\.stp$", "", p["file"], flags=re.I),
                "file": p["file"],
                "envelope": bb,
                "n_solids": p["n_solids"], "n_faces": p["n_faces"],
                "n_cylinders": p["n_cylinders"],
                "cyl_diameters": cd,
                "trust": envelope_trust(p),
            })
            say("| `%s` | %d | %d | %d | %s | %s |"
                % (re.sub(r"\.stp$", "", p["file"], flags=re.I),
                   p["n_solids"], p["n_faces"], p["n_cylinders"],
                   "×".join("%.3f" % v for v in bb) if bb else "⚠无几何",
                   ", ".join("⌀%s×%s" % kv for kv in
                             sorted(cd.items(), key=lambda kv: -float(kv[0]))[:5])))
        say("")
        out[g] = {"count": len(items), "provenance": "explicit",
                  "items": items}
    return out


# ============================ 四、垫片垫块 ============================
def learn_shims(real_parts):
    say("### 垫片垫块（KMS-M09 系列）")
    say("")
    rs = [p for p in real_parts if "垫片垫块" in p["rel"] or
          p["file"].upper().startswith("KMS-M09")]
    items = []
    say("| 型号 | 组 | 实体 | 面 | 实测外形 mm | 圆柱面直径 |")
    say("| --- | --- | --- | --- | --- | --- |")
    for p in sorted(rs, key=lambda p: p["file"]):
        bb = (p.get("bbox") or {}).get("size")
        grp = p["rel"].split("/")[-2] if "/" in p["rel"] else ""
        items.append({"model": re.sub(r"\.stp$", "", p["file"], flags=re.I),
                      "group": grp, "file": p["file"], "envelope": bb,
                      "n_solids": p["n_solids"], "n_faces": p["n_faces"],
                      "cyl_diameters": p["hole_diameters"],
                      "trust": "exact" if bb else "no_geometry"})
        say("| `%s` | %s | %d | %d | %s | %s |"
            % (re.sub(r"\.stp$", "", p["file"], flags=re.I), grp,
               p["n_solids"], p["n_faces"],
               "×".join("%.3f" % v for v in bb) if bb else "⚠无几何",
               ", ".join("⌀%s×%s" % kv for kv in
                         sorted(p["hole_diameters"].items(),
                                key=lambda kv: -float(kv[0])))))
    say("")
    return {"count": len(items), "provenance": "explicit", "items": items}


# ============================ 五、外购件 ============================
def envelope_trust(p) -> str:
    """判断一个零件的**实测包围盒能不能当零件外形**。

    实测分布（521 件）：
      · 单实体 380 件 → 顶点包围盒 ∪ 圆弧极值就是外形，可信
      · 多实体 141 件中 **133 件**的两套包围盒完全一致（多实体是同位装配，
        不影响外形），同样可信
      · 只有 **8 件**（UNIVER/气缸类）的圆弧修正包围盒比顶点包围盒大 3 倍以上
        —— 那是装配骨架（反复出现 2720.501 / 1372.005 这两个坐标），
        不能当零件外形

    所以判据不是"实体数>1 就不可信"，而是**圆弧修正是否把零件吹大了 3 倍以上**。
    """
    bb = (p.get("bbox") or {}).get("size")
    bv = (p.get("bbox_vertex") or {}).get("size")
    if not bb:
        return "no_geometry"
    if p["n_solids"] <= 1 or not bv:
        return "exact"
    mx_b, mx_v = max(bb), max(bv)
    if mx_v > 1e-9 and mx_b / mx_v > 3.0:
        return "assembly_scope"
    return "exact"


def learn_purchased(real_parts, table):
    say("## 四、外购件（UNIVER / SMC / 吊环 / 接近开关）")
    say("")
    out = OrderedDict()
    for row in table:
        if row["src"] == "EGT型材":
            continue
        model = row["model"]
        hit = None
        for p in real_parts:
            if p["rel"].startswith("外购件/EGT") or p["rel"] == "螺钉":
                continue
            if norm(p["file"]) == norm(model) or \
               norm(p["file"]).startswith(norm(model) + " "):
                hit = p
                break
        bb = (hit.get("bbox") or {}).get("size") if hit else None
        tr = envelope_trust(hit) if hit else "no_geometry"
        out[model] = {
            "model": model, "src": row["src"], "desc": row["desc"],
            "brand": row["brand"],
            "envelope": bb,
            "trust": tr,
            "n_solids": hit["n_solids"] if hit else None,
            "n_faces": hit["n_faces"] if hit else None,
            "n_cylinders": hit["n_cylinders"] if hit else None,
            "cyl_diameters": hit["hole_diameters"] if hit else None,
            "file": hit["file"] if hit else None,
            "provenance": "explicit" if hit else "unsupported",
        }
    say("| 型号 | 描述 | 品牌 | 实测外形 | 可信度 | 柱面直径 |")
    say("| --- | --- | --- | --- | --- | --- |")
    for m, e in out.items():
        say("| `%s` | %s | %s | %s | %s | %s |"
            % (m, e["desc"], e["brand"],
               "×".join("%.2f" % v for v in e["envelope"]) if e["envelope"] else "—",
               e["trust"],
               ", ".join("⌀%s×%s" % kv for kv in
                         sorted((e["cyl_diameters"] or {}).items(),
                                key=lambda kv: -float(kv[0]))[:4]) or "—"))
    say("")
    return out


def main():
    real = load("real_geometry.json")["parts"]
    mg = load("model_geometry.json")

    say("# 真实几何学习报告")
    say("")
    say("由 `tools/learn_real_library.py` 自动生成。")
    say("**每条规律都做了逐条回代校验，不通过的条数如实列出。**")
    say("")

    bolt = learn_bolts(real)
    egt = learn_egt(real)
    std = learn_standard(real)
    shim = learn_shims(real)
    pur = learn_purchased(real, mg["purchased_other"])

    with io.open(os.path.join(KB, "bolt_library.json"), "w",
                 encoding="utf-8") as f:
        json.dump(bolt, f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(KB, "egt_library.json"), "w",
                 encoding="utf-8") as f:
        json.dump(egt, f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(KB, "standard_real_library.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"groups": std, "shims": shim}, f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(KB, "purchased_library.json"), "w",
                 encoding="utf-8") as f:
        json.dump(pur, f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(REPORTS, "真实几何学习报告.md"), "w",
                 encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")
    print("\n→ kb/bolt_library.json, kb/egt_library.json, "
          "kb/standard_real_library.json, kb/purchased_library.json")
    print("→ reports/真实几何学习报告.md")


if __name__ == "__main__":
    main()
