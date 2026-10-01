# -*- coding: utf-8 -*-
r"""真实型号库（真实几何驱动）。

设计目标（来自用户要求）：
    **先让 AI 明白"这是什么、现实长什么样"，再建模；不许凭空捏造。**

数据来源全部是**实测**：
    · `kb/real_geometry.json`        CATIA 导出的 521 个 STEP，逐个解析出的
                                     包围盒 / 面数 / 实体数 / **圆柱面直径指纹**
    · `kb/bolt_library.json`         55 个内六角螺钉（ISO 4762 + GB/T 97.1 + GB/T 93）
    · `kb/egt_library.json`          EGT001/SW80、EGT002/SW50、EGT006/D30 共 289 档
                                     型材 + 73 个 EGT/VGT 连接件
    · `kb/standard_real_library.json` 厂内标准件（定位销/销座/支座/止块/分气块/阀岛/
                                     标牌/补高台/气缸连接块/垫片垫块）
    · `kb/purchased_library.json`    外购件（UNIVER/SMC/吊环/接近开关）

`lookup(text)` 把一句中文/型号描述解析成**真实型号记录**；建不出实体的部分
一律在 `limits` 里如实说明，不假装修好了。

本模块**不 import partlib**（避免循环依赖），只依赖标准库 + kb 目录。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import io
import json
import os
import re
from typing import Dict, List, Optional

ROOT = str(PROJECT_ROOT)
KB = os.path.join(ROOT, "kb")

_CACHE: Dict[str, dict] = {}


def _load(name: str) -> dict:
    if name not in _CACHE:
        p = os.path.join(KB, name)
        try:
            with io.open(p, encoding="utf-8") as f:
                _CACHE[name] = json.load(f)
        except Exception:
            _CACHE[name] = {}
    return _CACHE[name]


def available() -> dict:
    """各库的条目数（用于"学到了多少"的报告）。"""
    b = _load("bolt_library.json")
    e = _load("egt_library.json")
    s = _load("standard_real_library.json")
    p = _load("purchased_library.json")
    prof = e.get("profiles", {})
    return {
        "螺钉": len(b.get("bolts", {})),
        "EGT型材": sum(len(v.get("records", {})) for v in prof.values()),
        "EGT连接件": len(e.get("connectors", {})),
        "厂内标准件": sum(v.get("count", 0)
                          for v in s.get("groups", {}).values()) +
                      s.get("shims", {}).get("count", 0),
        "外购件": len(p),
    }


# ---------------------------------------------------------------------------
# 文本归一化
# ---------------------------------------------------------------------------
_MUL = r"[xX*\u00d7\u2715\uff58]"


def _norm_model(s: str) -> str:
    s = re.sub(r"\.(CATPart|stp|step)$", "", str(s).strip(), flags=re.I)
    s = s.replace("\u00d7", "x").replace("*", "x")
    s = re.sub(r"[\s\u00b0\u2103\u00ba]+$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# 一、内六角螺钉  M10x30 / M10X30 / M10×30 / M10*30 / 帮我建M10x30内六角螺钉
# ---------------------------------------------------------------------------
# ⚠ 不能用 `\b` 做前边界：Python 的 `\b` 把中文也算 \w，「帮我建M5x35」里
#   「建M」之间没有词边界，`\bM` 会匹配失败 —— 训练语料自检实测抓到的。
#   前边界改用 `(?<![0-9A-Za-z])`；长度后加 `(?![0-9])`，避免 `M5x160`
#   被误读成 M5x16（那种型号库里不存在，不匹配才是对的）。
_RE_BOLT = re.compile(r"(?<![0-9A-Za-z])M(\d+(?:\.\d+)?)\s*" + _MUL +
                      r"\s*(\d+(?:\.\d+)?)(?![0-9])")


def _bolt(text):
    lib = _load("bolt_library.json").get("bolts", {})
    if not lib:
        return None
    for m in _RE_BOLT.finditer(text):
        sp = "M%s" % m.group(1)
        try:
            ln = float(m.group(2))
        except ValueError:
            continue
        key = "%sx%g" % (sp, ln)
        rec = lib.get(key)
        if rec is None:
            # 长度不在库里：用同规格最接近的一档，并如实标注"非库内档位"
            same = [v for k, v in lib.items() if v["spec"] == sp]
            if not same:
                continue
            rec = min(same, key=lambda v: abs(v["length"] - ln))
            rec = dict(rec)
            rec["nearest_of"] = rec["model"]
            rec["requested_length"] = ln
            rec["provenance"] = "derived"
        return _bolt_params(rec, text)
    return None


def _bolt_params(rec, text):
    # 带不带垫圈：文本里说了就照着建，没说按库里真实构成（3 个实体：螺钉+弹垫+平垫）
    want_spring = not re.search(r"(不带弹垫|无弹垫|不带垫|无垫圈)", text)
    want_flat = not re.search(r"(不带平垫|无平垫|只带弹垫|仅带弹垫)", text)
    p = {
        "d": rec["shank_d"], "L": rec["length"],
        "bolt_dk": rec["head_d"], "bolt_head_k": rec["head_h"],
        "bolt_hex_s": rec["hex_s"],
        # 内六角孔深：ISO 4762 的 t 表 = 0.5 × 头高
        # （M5 2.5 / M6 3 / M8 4 / M10 5 / M12 6，与该标准逐档一致），
        # 所以按头高推即可。旧值用 0.6 倍，M10 会做成 6 深（标准是 5）。
        "bolt_hex_t": round(rec["head_h"] * 0.5, 3),
        "code": rec["model"],
        "spring_washer": 1 if want_spring else 0,
        "flat_washer": 1 if want_flat else 0,
        "__real": rec,
    }
    return {
        "category": "内六角螺钉", "model": rec["model"], "params": p,
        "real": rec, "provenance": rec.get("provenance", "explicit"),
        "source": "kb/bolt_library.json（CATIA 导出 STEP 实测 + ISO 4762 / "
                  "GB/T 97.1 / GB/T 93）",
        "notes": _bolt_notes(rec),
        "limits": [],
    }


def _bolt_notes(rec):
    n = ["真实外形 %s mm（平垫外径 × 平垫外径 × 公称长+头高）"
         % "×".join("%g" % v for v in rec["envelope"]),
         "真实构成为 3 个实体：螺钉 + 弹性垫圈 + 平垫圈"]
    ins = rec.get("assembly_insert_pt")
    if ins:
        n.append("装配插入点 (X0,Y0,Z0) = (%s) —— 来自你的《螺钉MyBoltLibrary"
                 "数据整理》表，是**贴合面偏置**；实测各型号取值并不单调，"
                 "故逐型号保留、未公式化" % ",".join("%g" % v for v in ins))
    if rec.get("nearest_of"):
        n.append("⚠ 库里没有 %sx%g 这一档，按最接近的 %s 建模"
                 % (rec["spec"], rec["requested_length"], rec["nearest_of"]))
    if rec.get("residual_mm", 0) > 0.01:
        n.append("⚠ 该型号实测值与 ISO 4762 公式有 %.2f mm 偏差（源模型如此）"
                 % rec["residual_mm"])
    return n


# ---------------------------------------------------------------------------
# 二、EGT 型材
# ---------------------------------------------------------------------------
_RE_EGT_FULL = re.compile(
    r"(?<![0-9A-Za-z])(EGT\d+)\s*[-\s]?\s*((?:SW|D)\d+)\s*" + _MUL + r"\s*(\d+(?:\.\d+)?)",
    re.I)
_RE_EGT_SEC = re.compile(r"(?<![0-9A-Za-z])((?:SW|D)\d+)\s*" + _MUL + r"\s*(\d+(?:\.\d+)?)",
                         re.I)


def _egt_profile(text):
    prof = _load("egt_library.json").get("profiles", {})
    if not prof:
        return None
    fam = sec = None
    code = None
    m = _RE_EGT_FULL.search(text)
    if m:
        fam, sec, code = m.group(1).upper(), m.group(2).upper(), float(m.group(3))
    else:
        m2 = _RE_EGT_SEC.search(text)
        if not m2:
            return None
        sec, code = m2.group(1).upper(), float(m2.group(2))
        for k, v in prof.items():
            if v["section"].upper() == sec:
                fam = v["family"]
                break
        if fam is None:
            # 只有截面代号：默认 SW80
            fam = "EGT001" if sec == "SW80" else (
                "EGT002" if sec == "SW50" else "EGT006")
    key = None
    for k, v in prof.items():
        if v["family"] == fam and v["section"].upper() == sec:
            key = k
            break
    if key is None:
        for k, v in prof.items():
            if v["family"] == fam:
                key, sec = k, v["section"]
                break
    if key is None:
        return None
    v = prof[key]
    rec = v["records"].get(str(int(round(code)))) if code is not None else None
    if rec is None:
        # 型号档位不在实测表里：按学到的线性律推算，并标注为"按规律推算"
        if code is None:
            return None
        k = round((code - v["code_base"]) / float(v["code_step"]))
        rec = {
            "code": code, "length": round(code - v["length_offset"], 3),
            "section": [v["section_measured"][0][0],
                        v["section_measured"][0][1]],
            "n_faces": None,
            "n_cylinders": v["cyl0"] + v["cyl_step"] * k,
            "n_solids": 1, "cyl_diameters": None, "file": None,
        }
        prov = "derived"
    else:
        prov = "explicit"
    sect = rec["section"]
    p = {
        "L": rec["length"], "sec_w": max(sect), "sec_h": min(sect),
        "sec_b": max(sect), "sec_t": min(sect),
        "code": rec["code"], "family": v["family"], "section_code": v["section"],
        # 真实外形：截面两轴 + 长度。**必须显式给出**，否则 bbox 校验会
        # 拿到默认值（实测踩过：SW80x118 被算成 118×50×50 而不是 118×80×80）。
        "__env_sorted": sorted([max(sect), min(sect), rec["length"]],
                               reverse=True),
        "__real": rec, "__family": v,
    }
    return {
        "category": "EGT型材", "model": "%s %sx%03d" % (v["family"], v["section"],
                                                     int(rec["code"])),
        "params": p, "real": rec, "family": v, "provenance": prov,
        "source": "kb/egt_library.json（TUNKERS EGT 型材实测 %d 档）"
                  % sum(len(x["records"]) for x in prof.values()),
        "notes": [
            "实测截面 %s mm（型号里的 %s 就是截面代号）"
            % ("×".join("%g" % s for s in sect), v["section"]),
            "实测长度 %.3f mm = 型号码 %g − %.3f"
            % (rec["length"], rec["code"], v["length_offset"]),
            "该族真实模型含圆柱面 %s 处" % (rec["n_cylinders"] or "?"),
        ],
        "limits": [
            "型材内部齿纹/内腔截面是 TUNKERS 目录数据，实测只拿到外形、长度与"
            "圆柱面指纹；本次按**实测外形**（截面 × 长度）建，不冒充齿纹截面，"
            "也不把未量到位置的圆柱面硬塞成内孔",
        ],
    }


def _egt_connector(text):
    conn = _load("egt_library.json").get("connectors", {})
    if not conn:
        return None
    for m in re.finditer(r"(?<![0-9A-Za-z])([EV]GT\d+(?:[.-]\d+)?)", text, re.I):
        code = m.group(1).upper()
        rec = conn.get(code)
        if rec is None:
            for k, v in conn.items():
                if k.upper().startswith(code):
                    rec, code = v, k
                    break
        if rec is None:
            continue
        f0 = rec["files"][0]
        env = f0.get("envelope")
        # ⚠ 连接件多数是**装配导出**（73 件里 55 件多实体）：坐标里混了装配骨架，
        #   孔位不可信。必须把可信度一起带出去 —— 否则 `_trust_of` 退回默认的
        #   exact，就会拿脏坐标去建孔。实测后果：EGT054 / EGT046 / EGT080 被建成
        #   一堆孔乱飞，外形分数直接掉到 **0**。
        trust = (("exact" if f0.get("n_solids", 1) <= 1 else "assembly_scope")
                 if env else "no_geometry")
        f = dict(f0)
        f["trust"] = trust
        p = {"code": code, "__real": f, "__file": f}
        if env:
            srt = sorted(env, reverse=True)
            p.update({"L": srt[0], "W": srt[1], "H": srt[2],
                      "__env_sorted": srt})
        return {
            "category": "EGT连接件", "model": code, "params": p,
            "real": f, "provenance": "explicit",
            "source": "kb/egt_library.json（TUNKERS EGT Connector 实测 %d 件）"
                      % len(conn),
            "notes": ([
                "真实文件：`%s`" % f["file"],
                "实测外形 %s mm（可信度 %s）" % ("×".join("%g" % s for s in env),
                                            trust),
                "实体 %d / 面 %d / 圆柱面 %d"
                % (f["n_solids"], f["n_faces"], f["n_cylinders"]),
            ] if env else ["真实文件：`%s`（该导出不含可用外形）" % f["file"]]),
            "limits": ["连接件是斜向/异形铸件，本次按实测外形包络建，"
                       "不复刻内部加强筋"] + (
                ["该件是**装配导出**，孔位坐标含装配骨架，本次不建孔（宁可少建"
                 "也不拿脏坐标编造）"] if trust != "exact" else []),
        }
    return None


# ---------------------------------------------------------------------------
# 三、厂内标准件 KMS-M** / LZ********
# ---------------------------------------------------------------------------
#: 型号前缀 → 类别。`M05` 有两个子族，按第二段区分（实测发现：
#: `M05-010-***` 是支座，`M05-020-***` 是补高台）。
_KMS_CAT = {
    "M01": "定位销", "M02": "销座", "M05": "支座", "M06": "气缸连接块",
    "M09": "垫片垫块", "M11": "止块", "M13": "阀岛", "M14": "标牌",
    "M15": "分气块",
}
_KMS_SUB = {("M05", "020"): "补高台"}


def _kms_cat_of(code: str, m: Optional[str]) -> Optional[str]:
    if not m:
        return None
    seg = code.upper().split("-")
    if len(seg) >= 3:
        sub = _KMS_SUB.get((m.upper(), seg[2]))
        if sub:
            return sub
    return _KMS_CAT.get(m.upper())


_RE_KMS = re.compile(r"(?<![0-9A-Za-z])KMS-(M\d{2})-([A-Za-z0-9._\-]+)", re.I)


def _kms_code(text):
    m = _RE_KMS.search(text)
    if m:
        return ("KMS-%s-%s" % (m.group(1).upper(), m.group(2).upper()),
                m.group(1).upper())
    # 只写裸码（P097-A20 / H04-20-RM）
    m2 = re.search(r"(?<![0-9A-Za-z])(P\d{3}|D\d{3}|H\d{2}|V\d{2})-([A-Za-z0-9\-]+)", text)
    if m2:
        return m2.group(0).upper(), None
    return None, None


def _find_in_std(code: str, cat: Optional[str]):
    std = _load("standard_real_library.json")
    groups = std.get("groups", {})
    shims = std.get("shims", {})
    cands = []
    if cat:
        for it in groups.get(cat, {}).get("items", []):
            cands.append((cat, it))
        if cat == "垫片垫块":
            for it in shims.get("items", []):
                cands.append((cat, it))
    if not cands:
        for g, v in groups.items():
            for it in v.get("items", []):
                cands.append((g, it))
        for it in shims.get("items", []):
            cands.append(("垫片垫块", it))
    up = code.upper()
    for g, it in cands:
        if it["model"].upper() == up:
            return g, it
    for g, it in cands:
        if it["model"].upper().startswith(up):
            return g, it
    return None, None


def _std_part(text):
    code, m = _kms_code(text)
    cat = _kms_cat_of(code, m) if code else None
    if not code:
        # 支座(铝) 型号 LZ0100100n_H###
        mm = re.search(r"(?<![0-9A-Za-z])LZ\d{8}_H\d+", text, re.I)
        if not mm:
            return None
        code, cat = mm.group(0).upper(), None
    g, it = _find_in_std(code, cat)
    if it is None:
        return None
    catname = g
    if catname == "支座" and code.upper().startswith("LZ"):
        catname = "支座(铝)"
    env = it.get("envelope")
    p = {"code": it["model"], "__real": it}
    if env:
        # 统一按**从大到小**排，消除"长宽高的轴向命名"这种无法从 STEP 得知的信息
        srt = sorted((float(v) for v in env), reverse=True)
        p.update({"L": srt[0], "W": srt[1], "H": srt[2], "D": srt[0],
                  "__env_sorted": srt})
    # 定位销：从实测圆柱面直径里取台阶/定位段
    if catname == "定位销" and it.get("cyl_diameters"):
        cds = sorted((float(k) for k in it["cyl_diameters"]), reverse=True)
        if cds:
            p["D"] = cds[0]
        if len(cds) > 1:
            p["d"] = cds[1]
        p["L"] = float(env[2]) if env else p.get("L")
    cd = it.get("cyl_diameters") or {}
    return {
        "category": catname, "model": it["model"], "params": p,
        "real": it, "provenance": "explicit",
        "source": "kb/standard_real_library.json（%s，CATIA 导出 STEP 实测）" % g,
        "notes": ([
            "实测外形 %s mm" % "×".join("%g" % v for v in env),
            "实测圆柱面直径：%s" % ", ".join("⌀%s×%s" % kv for kv in
                                        sorted(cd.items(),
                                               key=lambda kv: -float(kv[0]))),
            "实体 %d / 面 %d" % (it["n_solids"], it["n_faces"]),
        ] if env else ["真实文件无几何（空 SHAPE_REPRESENTATION）"]),
        "limits": [] if it.get("trust") == "exact" else [
            "该件是**装配导出**，包围盒是装配体范围，不是零件外形；"
            "本次只采用圆柱面指纹"],
    }


# ---------------------------------------------------------------------------
# 四、外购件
# ---------------------------------------------------------------------------
def _purchased(text):
    lib = _load("purchased_library.json")
    if not lib:
        return None

    def norm(s):
        s = re.sub(r"[\s_\-]+", "", str(s)).upper()
        return re.sub(r"[\u00b0\u2103\u00ba]+$", "", s)

    tnorm = norm(text)
    best = None
    for model, rec in lib.items():
        nm = norm(model)
        if nm and nm in tnorm:
            if best is None or len(nm) > len(norm(best[0])):
                best = (model, rec)
    # 吊环 GB825_88_M16 的短写法：GB825 M16
    if best is None:
        m = re.search(r"GB\s*825\D{0,4}M\s*(\d+)", text, re.I)
        if m:
            want = norm("GB825_88_M%s" % m.group(1))
            for model, rec in lib.items():
                if norm(model) == want:
                    best = (model, rec)
                    break
    if best is None:
        return None
    model, rec = best
    env = rec.get("envelope")
    exact = rec.get("trust") == "exact"
    p = {"code": model, "__real": rec}
    if env:
        srt = sorted((float(v) for v in env), reverse=True)
        if exact:
            # 单实体件：实测包围盒就是零件外形，照用
            p.update({"L": srt[0], "W": srt[1], "H": srt[2], "D": srt[0],
                      "__env_sorted": srt})
        else:
            # ⚠ 装配导出：包围盒是**装配体范围**（实测反复出现 2720.50/1372.01
            #   这类共同骨架坐标），绝不能当零件外形。
            #   这里只把已确认的规格量放进去：气缸的**缸径/行程/宽度**从型号
            #   编码读取 —— 那是厂商样本上的数，不是我们从脏包围盒里猜的。
            spec = _purchased_nominal(model, rec)
            if spec:
                p.update(spec)
    cd = rec.get("cyl_diameters") or {}
    limits = []
    if not exact:
        limits.append("该件是**装配导出**，实体包围盒含装配范围（实测反复出现 "
                      "2720.50 / 1372.01 这类共同骨架坐标），**不能当零件外形**；"
                      "本次按型号规格 + 实测圆柱面指纹建简化体，并明确标注")
    return {
        "category": rec["desc"] or "外购件", "model": model, "params": p,
        "real": rec, "provenance": rec.get("provenance", "explicit"),
        "source": "kb/purchased_library.json（%s，CATIA 导出 STEP 实测）"
                  % (rec.get("src") or rec.get("brand") or "外购件"),
        "notes": ([
            "实测实体 %s / 面 %s / 圆柱面 %s"
            % (rec.get("n_solids"), rec.get("n_faces"), rec.get("n_cylinders")),
            "实测圆柱面直径：%s" % ", ".join("⌀%s×%s" % kv for kv in
                                        sorted(cd.items(),
                                               key=lambda kv: -float(kv[0]))[:6]),
            "实测包围盒 %s mm（可信度：%s）"
            % ("×".join("%.2f" % v for v in env), rec.get("trust")),
        ] if env else ["该型号在导出的 521 个 STEP 里**没有文件**，无法给出真实几何"]),
        "limits": limits,
    }


#: 外购气缸的**厂商样本规格**（型号编码里就写着，不是我猜的）
#:   UCBH50 / UCBM63 → 缸径 ⌀50 / ⌀63
#:   ULA63040 → 缸径 ⌀63、行程 40；ULA63060 → 缸径 ⌀63、行程 60
#:   MGPA50-50Z → 缸径 ⌀50、行程 50；MGPA50-70Z → 行程 70；MGPA50-100Z → 行程 100
def _purchased_nominal(model: str, rec: dict) -> dict:
    m = re.match(r"UCB[HM](\d{2})", model, re.I)
    if m:
        bore = float(m.group(1))
        return {"bore": bore, "D": bore, "L": bore * 5.0, "W": bore * 2.5,
                "H": bore * 2.5, "__nominal": "缸径 ⌀%g（型号编码）" % bore}
    m = re.match(r"ULA(\d{2})(\d{3})", model, re.I)
    if m:
        bore, stroke = float(m.group(1)), float(m.group(2))
        return {"bore": bore, "stroke": stroke, "D": bore,
                "L": stroke + bore * 2.0, "W": bore * 2.0, "H": bore * 2.0,
                "__nominal": "缸径 ⌀%g、行程 %g（型号编码）" % (bore, stroke)}
    m = re.match(r"MGPA(\d{2})-(\d+)", model, re.I)
    if m:
        bore, stroke = float(m.group(1)), float(m.group(2))
        return {"bore": bore, "stroke": stroke, "D": bore,
                "L": stroke + bore * 1.5, "W": bore * 2.2, "H": bore * 2.2,
                "__nominal": "缸径 ⌀%g、行程 %g（型号编码）" % (bore, stroke)}
    return {}


# ---------------------------------------------------------------------------
# 五、总入口
# ---------------------------------------------------------------------------
_ORDER = (_bolt, _egt_profile, _egt_connector, _std_part, _purchased)


def lookup(text: str) -> Optional[dict]:
    """把描述解析成真实型号记录；没有命中的真实型号返回 None（不猜）。"""
    if not text or not text.strip():
        return None
    for fn in _ORDER:
        try:
            r = fn(text)
        except Exception:
            r = None
        if r:
            r["resolver"] = fn.__name__
            return r
    return None


def to_table_overlay(rec: dict) -> Dict[str, tuple]:
    """把真实值转成 `{参数: (值, 出处说明)}`，供参数表按最高优先级覆盖。"""
    out = {}
    for k, v in (rec.get("params") or {}).items():
        if k.startswith("__"):
            continue
        out[k] = (v, "%s（%s）" % (rec["model"], rec["source"]))
    return out


def explain(text: str) -> str:
    """人读解释：这个型号到底是什么、现实长什么样。"""
    r = lookup(text)
    if not r:
        return "未在真实型号库中命中：%s" % text
    L = ["【这是什么】%s —— %s" % (r["category"], r["model"]),
         "【数据出处】%s" % r["source"],
         "【现实长什么样】"]
    for n in r.get("notes") or []:
        L.append("  · " + n)
    if r.get("limits"):
        L.append("【本次建模的诚实边界】")
        for n in r["limits"]:
            L.append("  · " + n)
    return "\n".join(L)
