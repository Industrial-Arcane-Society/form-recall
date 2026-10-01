# -*- coding: utf-8 -*-
r"""
U2 落地层：把"零件定义 + 提示词"变成**参数引用表**，并补齐三层来源。

分工（升级文档 U1/U2 的实现）
    params.py     —— 纯取值基元与 ParamTable（只做"抽数 + 记出处"）
    extract.py    —— 本文件：把 partlib 的零件定义适配成 Spec，
                     再按 顺序补齐 standard / derived / default 三层，
                     最后产出 (ParamTable, 可直接建特征用的 params 字典, 提示列表)
    解析主链       —— 只消费这里的结果，构建期不再解析文本

为什么要单独一层:
    · 抽取器只认"用户写出来的数"，不猜、不套典型值；
    · 标准值/公式推导/默认值都在这里显式补，且**都带出处**，界面上分色显示；
    · 因此"用户没写"和"程序猜的"第一次被真正区分开（升级文档 U2 规则 3）。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from ai_modeler.core import params as P

ROOT = str(PROJECT_ROOT)
#: 参数键的数值量级（剔除串列误读）；与旧解析保持一致的保守范围
RANGES: Dict[str, Tuple[float, float]] = {
    "d": (0.1, 5000), "D": (0.1, 5000), "L": (0.1, 8000), "H": (0.1, 5000),
    "B": (0.1, 2000), "b": (0.1, 2000), "h": (0.1, 2000), "T": (0.1, 2000),
    "W": (0.05, 500), "s": (0.5, 500), "k": (0.1, 500), "m": (0.1, 100),
    "z": (1, 999), "n": (1, 999), "dh": (0.1, 2000), "K": (0.5, 5000),
    "alpha": (1, 90), "beta": (1, 89), "delta": (1, 89), "S": (0.1, 3000),
    "dp": (0.1, 3000), "dj": (0.1, 2000), "R": (0.1, 3000), "t": (0.1, 500),
    "thick": (0.1, 500), "dh2": (0.1, 2000), "ring": (0.5, 500),
}

#: 这些键由"公式/标准"派生，缺了不算缺（旧解析里也是推定的）
DERIVED_KEYS = ("s", "k", "W", "ring", "thick", "dh2", "K", "alpha", "beta",
                "delta", "m", "H", "t", "beta")

#: 代号（槽型/齿型）取值集合
V_GROOVE_CODES = ("SPZ", "SPA", "SPB", "SPC", "Y", "Z", "A", "B", "C", "D", "E")
TIMING_CODES = ("XXH", "XH", "MXL", "XL", "L", "H", "T2.5", "T5", "T10", "T20",
                "14M", "8M", "5M", "3M")

#: 零件类别需要的"提示词信号词"（用于决定按 V 带轮还是同步带轮解析）
V_SIGNALS = ("槽数", "槽型", "带型", "V带", "V型", "多槽", "多楔", "皮带", "窄V",
             "个槽", "槽")
T_SIGNALS = ("同步", "齿数", "齿型", "周节制", "圆弧齿")

#: 带轮/同步带轮的最短别名（旧解析靠语义兜底认这些，这里显式补上）
EXTRA_ALIASES = {
    "多槽带轮": {"B": ["宽", "轮宽", "厚"], "D": ["直径"], "d": ["内孔", "内径"]},
    "同步带轮": {"B": ["宽", "轮宽", "厚"], "D": ["直径"], "d": ["内孔", "内径"],
                 "z": ["齿", "齿数"]},
    "盘形凸轮": {"d": ["基圆", "基圆直径", "直径"], "b": ["厚"]},
    "深沟球轴承": {"B": ["厚"]},
    "圆锥滚子轴承": {"T": ["总宽", "厚"]},
    "螺旋弹簧": {"H": ["自由高度", "总高", "高"], "n": ["圈"]},
    "锥形弹簧": {"H": ["自由高度", "总高", "高"], "n": ["圈"]},
    "碟形弹簧": {"h": ["高", "厚度"]},
    "螺母": {"m": ["高"]},
    "螺栓": {"k": ["头高"]},
    "轴套": {"L": ["总长", "全长"]},
    "平键": {"L": ["总长", "全长"]},
    "联轴器": {"L": ["总长", "全长", "长度"]},
    "轴承座": {"H": ["高"]},
    "辐条手轮": {"z": ["辐条"]},
    "飞轮": {"h": ["厚"]},
    "O形圈": {"D": ["外径"]},
    "垫圈": {"h": ["厚", "厚度"]}, "O形圈": {"W": ["线径", "截面"], "D": ["外径"]},
}


def _norm_type(code):
    try:
        from ai_modeler.parts.standard.std_pulley import normalize_type
        return normalize_type(code) or code
    except Exception:
        return code


def make_spec(cat: str, d: dict) -> P.Spec:
    """把 partlib 的零件定义适配成 params.Spec（自动，不需要手工写几十份）。"""
    spec = P.Spec(name=cat)
    # ⚠️ 哪些键是"字母代号"（槽型/齿型）必须由**零件定义自己声明**（code_keys）。
    #    早先这里硬编码 `if key in ("t","gt","type","tooth_type"): continue`，
    #    把单字母 `t` 无条件当成代号——但 `t` 在别的零件里是**真实尺寸**：
    #    涡旋盘的"壁厚"、基板/折弯件/连接件的"厚度"都叫 t。
    #    结果这些尺寸被静默丢掉（实测「涡旋盘 外径120 高度40 壁厚4 圈数3」
    #    抽到的表里根本没有 t，壁厚永远用默认值）。
    code_keys = set(d.get("code_keys") or ())
    aliases: Dict[str, List[str]] = {}
    for key, al in (d.get("params") or {}).items():
        if key in code_keys:
            continue
        aliases[key] = list(al)
        rng = RANGES.get(key)
        if rng:
            spec.ranges[key] = rng
    for key, extra in (EXTRA_ALIASES.get(cat) or {}).items():
        if key in aliases:
            for a in extra:
                if a not in aliases[key]:
                    aliases[key].append(a)
    spec.aliases = aliases
    try:
        from ai_modeler.parts import partlib
        spec.optional = tuple(getattr(partlib, "OPTIONAL_PARAMS", ()))
    except Exception:
        spec.optional = ()
    spec.symbols = {k: k for k in aliases if len(k) == 1 and k.isalpha() and k != "M"}
    if cat == "多槽带轮":
        spec.codes = {"t": V_GROOVE_CODES}
        spec.count_words = {"n": ("槽",)}
    elif cat in ("同步带轮", "同步轮"):
        spec.codes = {"t": TIMING_CODES}
        spec.count_words = {"z": ("齿",)}
    if cat == "螺栓":
        spec.aliases.setdefault("d", []).append("M")
    # 螺纹件的行业写法是 `M10`、`M10x30`，用户往往**不写"螺纹规格"四个字**。
    # 凡别名里含"螺纹规格/公称直径"的零件，都给螺纹那个键补上 `M` 别名 ——
    # 这样 `内六角螺钉 M10x30` 里的 d 才读得到（实测不补则整条无特征）。
    # （`M10x30` 里的长度由 partlib 的构建函数从 `__text` 兜底补出，见 _socket_bolt）
    for key, als in spec.aliases.items():
        if any(("螺纹规格" in a or "公称直径" in a) for a in als):
            if "M" not in als:
                als.append("M")
    return spec


def extract(cat: str, spec: P.Spec, text: str) -> P.ParamTable:
    """抽参数表（只含用户写出来的数），并按零件类型补标准值。

    先 add_boundaries 补边界，再抽取：这样连写的两个参数（"宽度40"+"4个槽"）
    在进入抽取器之前就已经分开，抽取器无需"事后猜切点"。
    """
    names = set(P._PARAM_NAMES)
    for al in (spec.aliases or {}).values():
        names.update(al)
    work = P.add_boundaries(text, names)
    tab = P.Extractor(spec, text, work=work).run()
    _fill_code(cat, spec, tab, text)
    _fill_thread_tail(cat, spec, tab, text)
    _apply_standard(cat, tab, text)
    return tab


def _fill_code(cat: str, spec: P.Spec, tab: P.ParamTable, text: str):
    """抽「型号」这类**字母数字混合**的代号（别名规则认不了它们）。

    典型：定位销 `KMS-M01-P097-A20`、轴承 `6204`（那个走别名）。
    规则：只有零件定义里真有 `code` 参数时才抽，且取**最长**的那个匹配，
    避免把文本里别的数字串误当型号。
    """
    if "code" not in (spec.aliases or {}) or tab.has("code") or not text:
        return
    best = None
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+", text or ""):
        tok = m.group(0).upper()
        if len(tok) < 5:
            continue
        if best is None or len(tok) > len(best[0]):
            best = (tok, m.start(), m.end())
    if best:
        tab.set("code", best[0], unit="", span=(best[1], best[2]),
                raw=best[0], source="型号写法")


def _fill_thread_tail(cat: str, spec: P.Spec, tab: P.ParamTable, text: str):
    """从 `M10x30` 这种螺纹件标准写法里补出**长度**。

    为什么必须在这里补：`M` 别名能让别名规则读到 d=10，但 `x` 后面的长度
    **没有任何别名可依**，于是 L 缺 → model_params 判"读不全"→ 整条无特征
    （实测 `内六角螺钉 M10x30` 就这么失败的）。
    这里只补"长度"这一个语义明确的量，不做别的推断；已有值时绝不覆盖。
    """
    if tab.has("L") or not text:
        return
    if "L" not in (spec.aliases or {}):
        return
    m = re.search(r"[Mm]\s*\d+(?:\.\d+)?\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return
    try:
        v = float(m.group(1))
    except ValueError:
        return
    lo, hi = (spec.ranges or {}).get("L", (None, None))
    if lo is not None and not (lo <= v <= hi):
        return
    tab.set("L", v, span=(m.start(1), m.end(1)), raw=m.group(0),
            source="螺纹规格写法 M10x30 中的长度")


def _apply_standard(cat: str, tab: P.ParamTable, text: str):
    """标准解析层：由标准表补出参数（带 provenance=standard）。"""
    if cat == "深沟球轴承":
        # U5：写了轴承代号（6204/6305…）就直接查 GB/T 276—2013 标准尺寸表，
        # 用户不必再手抄内径/外径/宽度，也不会抄错。
        # 查不到 -> 只记一条说明，不阻断原解析（用户可能只写了内径外径）。
        try:
            from ai_modeler.parts import std_bearing as SB
            code = _bearing_code(tab, text)
            if code:
                s = SB.spec(code=code)
                if s.get("error"):
                    tab.notes.append(s["error"])
                else:
                    for k in ("d", "D", "B"):
                        if s.get(k) is not None:
                            tab.set(k, s[k], provenance=P.STANDARD,
                                    source="%s 外形尺寸表（%s）"
                                           % (SB.standard(), s.get("series_name") or ""),
                                    raw=str(code))
                    tab.set("code", s["code"], unit="", provenance=P.STANDARD,
                            source="%s 外形尺寸表" % SB.standard(), raw=str(code))
                    tab.notes.append("已按轴承代号 %s 取标准尺寸：%s"
                                     % (s["code"], SB.summary(s)))
        except Exception as e:
            tab.notes.append("轴承标准表查询未完成：%s" % e)
    if cat in ("同步带轮", "同步轮"):
        # ⚠️ 同步带轮走**齿型**（XL/T5/5M…），不是 V 带轮的轮槽截面。
        #    早先这里和"多槽带轮"共用一段代码，于是没写齿型时会按直径推荐
        #    V 带轮的槽型（直径 50 → 推荐 "Y"），再拿去查 GB/T 10412 轮槽表，
        #    结果同步带轮被套上 V 槽尺寸 —— 实测齿型读成 Y。
        #    这里只做"没写齿型就沿用默认 XL"的说明，不查轮槽表。
        if not tab.get("t"):
            tab.set("t", "XL", unit="", provenance=P.DEFAULT,
                    source="未写齿型，按最常用的周节制 XL 齿型（可改）")
            tab.notes.append("没写齿型，已按 XL 型生成（GB/T 11361 周节制最常用档，可在右侧改）")
        return
    if cat == "多槽带轮":
        try:
            from ai_modeler.parts import std_pulley as SP
            gt = tab.get("t")
            dd = tab.get("D")
            if dd is None:
                return
            if not gt:
                # 没写槽型 -> 按基准直径推荐（升级文档 U5：给出推导依据）
                gtype = ("Y" if dd < 60 else "Z" if dd < 100 else "A"
                         if dd < 200 else "B" if dd < 320 else "C")
                tab.set("t", gtype, unit="", provenance=P.STANDARD,
                        source="按基准直径推荐（GB/T 10412 常用档）")
                gt = gtype
            g = SP.groove_table().get(gt)
            if not g:
                return
            for k, std_key in (("bd", "base_width_bd"), ("ha", "addendum_ha"),
                               ("hf", "dedendum_hf"), ("e", "pitch_e")):
                tab.set(k, g[std_key], provenance=P.STANDARD,
                        source="GB/T 10412—2002 轮槽截面尺寸")
            series = SP.diameter_series(gt)
            if dd in series:
                tab.set("outer_d", series[dd], provenance=P.STANDARD,
                        source="GB/T 10412—2002 直径系列表")
                tab.notes.append("基准直径 ⌀%g 命中标准系列，外径取表值 ⌀%g" % (dd, series[dd]))
            else:
                tab.set("outer_d", dd + 2.0 * g["addendum_ha"], provenance=P.DERIVED,
                        source="da = dd + 2·ha")
                tab.notes.append("基准直径 ⌀%g 不在手册系列表内，外径按 da = dd + 2·ha 推导"
                                 % dd)
        except Exception as e:
            tab.notes.append("标准解析未完成：%s" % e)


def derive(cat: str, tab: P.ParamTable):
    """推导层：由已有参数算出的尺寸（provenance=derived）。"""
    try:
        if cat == "多槽带轮":
            dd, n, gt = tab.get("D"), tab.get("n"), tab.get("t")
            ha = tab.get("ha")
            if dd and n and gt and tab.get("e"):
                tip_w = tab.get("tip_w")
                if tip_w is None:
                    phi = _groove_angle(dd)
                    tab.set("phi", phi, unit="°", provenance=P.STANDARD,
                            source="槽角按基准直径分档（32/34/36/38°）")
                    tip_w = max(1.0, tab.get("bd") - 2.0 * ha *
                                __import__("math").tan(__import__("math").radians(phi / 2.0)))
                    tab.set("tip_w", tip_w, provenance=P.DERIVED,
                            source="槽顶宽 = bd − 2·ha·tan(φ/2)")
                rim_min = (n - 1) * tab.get("e") + tip_w
                tab.set("rim_w_min", rim_min, provenance=P.DERIVED,
                        source="轮缘最小宽度 = (n−1)·e + 2f")
                if not tab.has("B") or tab.get("B") < rim_min:
                    if tab.has("B"):
                        # ⚠️ 先把用户**原文写的**轮宽记下来再覆盖：下游（kernel/partlib）
                        #    还会再跑一次 std_pulley.spec()，提示语若拿放大后的值当
                        #    "原输入的 X"，用户会看到一个自己从没写过的数
                        #    （实测提示成"原输入的 39.3185 mm"，旧解析路径是 30 mm）。
                        tab.set("__user_width", tab.get("B"), provenance=P.DERIVED,
                                source="用户原文轮宽（仅供提示语措辞）")
                        tab.notes.append("轮缘宽度 %g 小于标准最小值 %g，已按标准取 %g"
                                         % (tab.get("B"), rim_min, rim_min))
                    tab.set("B", rim_min, provenance=P.DERIVED,
                            source="轮缘最小宽度公式（槽不干涉）")
        if cat == "螺栓":
            if not tab.has("s") and tab.has("d"):
                tab.set("s", round(1.5 * tab.get("d"), 1), provenance=P.DERIVED,
                        source="GB/T 5782 对边 s ≈ 1.5d")
            if not tab.has("k") and tab.has("d"):
                tab.set("k", round(0.7 * tab.get("d"), 1), provenance=P.DERIVED,
                        source="GB/T 5782 头高 k ≈ 0.7d")
    except Exception as e:
        tab.notes.append("推导未完成：%s" % e)


def _bearing_code(tab, text: str):
    """找轴承代号：先看别名抽出来的 '代号6206'，再在**未被占用的**文本里找裸代号。

    ⚠️ 两个坑：
      1) 别名规则把代号当数字抽，得到的是 float（6206.0）。直接拿去查表必然查不到
         （表键是字符串 "6206"），必须先规范化回整数写法。
      2) 用户常常只写「深沟球轴承 6204」，没有"代号"两字。这时按"未被占用的 4~5 位
         数字段"去找，并且**必须查表确认**才采用 —— 否则内径/外径/宽度这些数会被误当代号。
    """
    try:
        from ai_modeler.parts import std_bearing as SB
    except Exception:
        return None
    raw = tab.items.get("code")
    if raw is not None:
        v = raw.value
        if isinstance(v, float) and abs(v - round(v)) < 1e-9:
            v = int(round(v))
        key = SB.normalize_code(v)
        if key:
            return key
    # 裸代号：在原文里找，且要求该数字段未被任何参数占用
    names = set(P._PARAM_NAMES)
    for al in (("内径", "轴径", "孔径", "外径", "宽度", "厚", "宽", "代号", "轴承代号"),):
        names.update(al)
    o2w = getattr(tab, "_o2w_map", None)
    for m in re.finditer(r"(?<![0-9.])(\d{4,5})(?![0-9.])", text or ""):
        tok = m.group(1)
        key = SB.normalize_code(tok)
        if not key:
            continue
        # 该数字段若已被别的参数占用，就不能再当代号
        a, b = m.start(1), m.end(1)
        if o2w and a < len(o2w):
            a = o2w[a]
            b = o2w[min(b, len(o2w) - 1)]
        if not tab.is_free(a, b):
            continue
        return key
    return None


def _groove_angle(dd):
    for bound, ang in ((60.0, 32.0), (118.0, 34.0), (190.0, 36.0), (float("inf"), 38.0)):
        if dd <= bound:
            return ang
    return 38.0


def to_partlib_params(cat: str, tab: P.ParamTable, d: dict) -> Dict:
    """把参数表转成 partlib 的 build() 认识的那套键（保持向后兼容）。"""
    out: Dict = {}
    _ALLOWED_EXTRA = ("outer_d", "ha", "hf", "e", "phi", "tip_w", "rim_w_min")
    for key in (d.get("params") or {}):
        if tab.has(key):
            out[key] = tab.value_of(key)
    # 只允许白名单里的额外键进入 params（见上面注释：多余键会污染 _clamp_cuts）
    # 带轮额外要传给 build 的键
    # ⚠️ 只传**规范允许的**键：早先把内部量 pitch_d 也塞进 params，
    #    结果 partlib._clamp_cuts() 把它当成"外径候选"，把其它零件的轮廓钳小了
    #    （实测轴承外径被钳到 4mm、齿轮齿顶被切）—— 参数表只应含零件定义里的键。
    if cat in ("多槽带轮",):
        for k in ("outer_d", "ha", "hf", "e", "phi", "tip_w", "rim_w_min"):
            if tab.has(k):
                out[k] = tab.value_of(k)
    # 轮宽提示语要显示**用户写的原文值**：参数表路径下标准层可能已把轮宽放大到
    # 最小轮缘宽度，直接拿放大后的值写"原输入的 X mm 不足"会显示一个用户从没写过的数
    # （实测提示成"原输入的 39.3185 mm"，旧解析路径是"原输入的 30 mm"）。
    if cat in ("多槽带轮", "V带轮", "皮带轮"):
        if tab.has("__user_width"):
            out["__user_width"] = tab.value_of("__user_width")
        else:
            v = tab.items.get("B")
            if v is not None and v.provenance == P.EXPLICIT:
                out["__user_width"] = v.value
    out["__text"] = tab.text
    out["__table"] = tab
    return out


def fill_defaults(cat: str, tab: P.ParamTable, d: dict, typical: dict, need) -> List[str]:
    """默认层：缺的可选参数按典型值补，**必须标记 provenance=default** 并返回提示。"""
    notes = []
    defaulted = []
    for k in need:
        if tab.has(k) or k in DERIVED_KEYS:
            continue
        if k in (typical or {}):
            tab.set(k, typical[k], provenance=P.DEFAULT, source="零件库典型值")
            defaulted.append("%s=%g" % (k, typical[k]))
    if defaulted:
        notes.append("这些参数没写，已按零件库典型值代入：%s（可在右侧「参数」页改）"
                     % "、".join(defaulted))
    return notes
