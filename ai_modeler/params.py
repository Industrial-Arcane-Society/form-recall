# -*- coding: utf-8 -*-
r"""
U2：参数引用表（ParamTable）—— 全程序**唯一**的数值来源。

为什么要这一层（升级文档 U2）
    以前数值在 nlparser 与 partlib 两处各解析一遍、互相污染：
      · 「带轮 d25 D100 B30 n3 A型」里 n3 被 B30 干扰；
      · 「宽度303个槽」把宽度读成 303、槽数读成 0；
      · 「5槽 Z型 内径12」把 12 读成槽数。
    本质原因是"边解析边建特征"：识别、取值、建几何耦合在一起，一个字符没认出来
    就会把错误传播下去（级联失败）。

    本模块把"取数"独立成一层，并且：
      1) 每个值都记 **span**（原文位置）→ 可回原文高亮、可让人核对；
      2) 每个值都记 **provenance**（出处）→ 用户写的 / 标准查的 / 公式推的 / 默认套的，
         界面上分色显示，绝不把"猜的"当"用户说的"；
      3) 取值规则固定有序，且**命中即占用 span**，后面任何规则都拿不到这段文本
         → 同一个数字不可能被两个参数抢；
      4) 缺什么参数能明确报出来（`missing()`），交给上层"提问或给候选"，
         而不是静默套典型值。

参考（升级文档 §1.2）
    Pointer-CAD v2：把参数推理与几何构造解耦，用**指针/引用**从参数表取值，
    且不做数值量化；评测按顶点/边/面三级在公差内判定。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- provenance
EXPLICIT = "explicit"      # 用户提示词里写出来的（带 span）
STANDARD = "standard"      # 从国家标准/手册表查出来的
DERIVED = "derived"        # 由公式推导出来的
DEFAULT = "default"        # 按零件库典型值套的（必须显示给用户）
ASKED = "asked"            # 由用户交互确认的

#: 非"用户明说"的出处，界面上要用不同颜色显示
NOT_EXPLICIT = (STANDARD, DERIVED, DEFAULT, ASKED)

#: U2 参数引用表总开关。默认**开启**（2026 迁移完成）：
#: 对拍口径见 reports/参数表对拍报告.md，翻默认值前后的可比数字：
#:   全量评测命中率 87.58%（141/161） -> 93.17%（150/161）
#:   带轮题库       138/138 保持满分；内置自检 34/34；不变量 14/14；L2/L3 16/16
#: 临时回退旧解析：环境变量 PARAM_TABLE=0。
USE_PARAM_TABLE = True

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "双": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 数字后面如果是这些字，说明这个数字属于**下一个参数**（不能算给当前别名）
_PARAM_HEAD_CHARS = "内外基宽厚高长直齿螺中型节孔径轮槽带数度纹对头升跨壁圈拐"

#: 别名后面若紧跟这些字，说明本别名只是更长参数名的一部分（"槽"之于"槽数/槽型"），
#: 该匹配作废，交给更长的别名去认。
#: 实测：「槽数3」被短别名"槽"抢走，槽数被读成 30/40 这类怪值。
_ALIAS_SUFFIX_BLOCK = "数型宽距深齿向长厚高径度纹圈条"
_PARAM_PATTERN = (r"^(外径|内径|内孔|基准直径|基准|宽度|轮宽|厚度|齿数|槽数|槽型|孔径|轴径|"
                  r"高度|长度|带型|型号|模数|升程|跨距|壁厚|宽|厚|齿|槽|长|高|内|中|节)")
_DASHES = ("\u2014", "-", "\uff0d", "\u00d7", "\u00d7\u00d7", "/")


@dataclass
class Value:
    """一个参数值，连同它的出处。"""
    value: object
    unit: str = "mm"
    span: Tuple[int, int] = (-1, -1)     # 原文位置（-1 表示非用户明说）
    provenance: str = EXPLICIT
    source: str = ""                     # 标准号 / 公式名，便于在界面上写明依据
    raw: str = ""                        # 原文片段

    def __repr__(self):
        return "Value(%r, %s, span=%s, %s)" % (self.value, self.unit, self.span, self.provenance)


@dataclass
class Spec:
    """某个零件/特征需要哪些参数、每个参数怎么认。"""
    name: str
    #: 键 -> 中文别名（按顺序试，命中即止）
    aliases: Dict[str, Sequence[str]] = field(default_factory=dict)
    #: 键 -> 符号（d/D/B/L/H/n/z/t…）；符号取值要求独立成词
    symbols: Dict[str, str] = field(default_factory=dict)
    #: 字母代号参数（槽型 A/SPZ、齿型 XL/5M…）：键 -> 候选代号集合
    codes: Dict[str, Sequence[str]] = field(default_factory=dict)
    #: 数值范围（用于剔除串列/误读）
    ranges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    #: 可选参数（缺失时允许用典型值，必须标 DEFAULT）
    optional: Sequence[str] = ()
    #: 计数类别名（"3个槽" "双槽"）
    count_words: Dict[str, Sequence[str]] = field(default_factory=dict)


class ParamTable(object):
    """参数引用表：键 -> Value。所有下游只准读这里。"""

    def __init__(self, text: str = "", work: str = None):
        self.text = text or ""
        #: 实际用于抽取的文本（可能被 add_boundaries 插过空格）。
        #: span 一律**映射回原文**，这样界面上高亮的位置才对得上。
        self.work = self.text if work is None else work
        self._w2o = _work_to_orig_map(self.text, self.work)       # work 位置 -> 原文位置
        #: work 区间起点 -> 该区间覆盖到的最小原文位置（用于区间换算，必须用它而非 w2o）
        self._wfo = _work_first_orig(self.text, self.work)
        self._o2w_map = None
        if self.work != self.text:
            # 原文位置 -> work 位置（取第一个 >= 该原文位置的 work 下标）
            inv = [0] * (len(self.text) + 1)
            wi = 0
            for oi in range(len(self.text) + 1):
                while wi < len(self.work) and self._w2o[wi] < oi:
                    wi += 1
                inv[oi] = wi
            self._o2w_map = inv
        self.items: Dict[str, Value] = {}
        self.spans: List[Tuple[int, int]] = []      # 已被占用的原文区间
        self.notes: List[str] = []                  # 取值过程中的说明（给用户看）

    # ---------------- 读写 ----------------
    def set(self, key, value, unit="mm", span=(-1, -1), provenance=EXPLICIT,
            source="", raw=""):
        ospan = self.to_orig_span(span)
        self.items[key] = Value(value, unit, ospan, provenance, source, raw)
        return self.items[key]

    def to_orig_span(self, span):
        """把 **work 文本**上的区间映射回原文区间。

        ⚠️ 坐标契约（踩过一个大坑，写清楚免得再错）：
        `find_alias_value` / `find_symbol_value` / `find_code` / `cn_count` 拿到的
        是**原文**（未被 add_boundaries 插空格的文本），它们返回的 span 本来就是
        **原文坐标**。只有"在工作文本上扫描"的调用点才需要本函数换算。

        区间换算不能逐点映射后直接比较：插入的空格在原文没有对应字符，
        会让区间起点落在空格上。
        实测（原文"螺栓螺纹规格16长度80" / work"螺栓 螺纹规格16 长度80"）：
            work(3,9) 的正确原文区间是 (3,9)，逐点映射会得到 (2,8) —— 遮罩整体
            左移一格，把后面的"长度80"一起挖掉，螺栓的 L 直接丢失
            （表现为 KeyError: 'L'、整件退化成 0 特征）。
        """
        if not span or span[0] < 0:
            return (-1, -1)
        a, b = span
        m = self._w2o
        n = len(self.text)
        oa = m[a] if 0 <= a < len(m) else n
        ob = (m[b - 1] + 1) if (b - 1) >= 0 and (b - 1) < len(m) else oa
        oa = max(0, min(oa, n))
        ob = max(oa, min(ob, n))
        return (oa, ob)

    def w_span_to_orig(self, span):
        """work 区间 -> 原文区间（`to_orig_span` 的语义化别名，供取值基元回调使用）。"""
        return self.to_orig_span(span)

    def get(self, key, default=None):
        v = self.items.get(key)
        return default if v is None else v.value

    def has(self, key):
        return key in self.items

    def value_of(self, key):
        v = self.items.get(key)
        return None if v is None else v.value

    def missing(self, keys):
        return [k for k in keys if k not in self.items]

    def provenance_of(self, key):
        v = self.items.get(key)
        return v.provenance if v else None

    def not_explicit(self):
        """返回所有"不是用户明说"的参数（界面上要提示/分色显示）。"""
        return {k: v for k, v in self.items.items() if v.provenance in NOT_EXPLICIT}

    def explain(self):
        """给界面/日志用的逐条说明：这句话里的哪个数落到了哪个参数上。"""
        out = []
        for k, v in self.items.items():
            where = ("原文[%d:%d]「%s」" % (v.span[0], v.span[1], v.raw)) if v.span[0] >= 0 \
                else (v.source or v.provenance)
            out.append((k, v.value, v.provenance, where))
        return out

    # ---------------- span 占用 ----------------
    def claim(self, i: int, j: int):
        self.spans.append((int(i), int(j)))

    def is_free(self, i: int, j: int) -> bool:
        for a, b in self.spans:
            if not (j <= a or i >= b):
                return False
        return True

    def free_text(self):
        """把已占用的区间挖成占位符，返回"剩余未被任何参数使用的文本"（在 work 文本上）。

        注意：占位符是 `\\u0001`（不是空格）。判定边界时必须把它当空白看，
        否则「槽数3」被占用后剩下的 "数3" 会被短别名"槽"重新咬走
        —— 实测槽数被读成 30 就是这个原因。见 `blanked_text()`。
        """
        t = list(self.work)
        for a, b in self.spans:
            # ⚠️ spans 存的是**原文**区间，而这里挖的是 **work** 文本。
            #    add_boundaries 插过空格，两套坐标长度不同，必须映射后再挖；
            #    早先直接用原文下标去挖 work，导致遮罩整体偏位——
            #    实测「外径47」被挖成"外径4"，外径读成 4（本轮最难查的一个坑）。
            ka, kb = self._o2w(a), self._o2w(b)
            for k in range(max(0, ka), min(len(t), max(ka + 1, kb))):
                t[k] = "\u0001"
        return "".join(t)

    def _o2w(self, pos):
        """原文位置 -> work 位置。"""
        if not self._o2w_map:
            return pos
        pos = max(0, min(int(pos), len(self.text)))
        return self._o2w_map[pos]

    def blanked_text(self):
        """与 free_text 相同，但把占位符换成空格 —— 用于边界/跨度判定。"""
        return self.free_text().replace("\u0001", " ")


# ---------------------------------------------------------------- 取值基元
def _work_to_orig_map(orig: str, work: str):
    """work 文本每个字符位置 -> 原文位置（add_boundaries 只插空格，可精确映射）。

    ⚠️ 失配后**必须重新尝试同一个原文字符**（`i` 不前进、`j` 前进）：
    work 里插入的空格在原文找不到对应字符，跳过它之后必须拿下一个 work 字符
    继续和 `orig[i]` 比较。早期版本失配时把 `i` 也推进了，导致插入点之后
    **每一个位置都前移一位**。

    实测（原文"螺栓螺纹规格16长度80" / work"螺栓 螺纹规格16 长度80"）：
        w2o 退化成 [0,1,2,2,3,4,5,6,7,8,...]（正确应为 [0,1,2,2,3,4,5,6,7,8,...] 中
        位置 8 对应原文 7），于是 work 区间 (3,9) 被换算成原文 (2,8)：
        遮罩左移一格，把后面的"长度80"一起挖掉，螺栓的 L 直接丢失
        （表现为 KeyError: 'L'、整件退化成 0 特征）。
    """
    m = [0] * (len(work) + 1)
    i = j = 0
    while j < len(work):
        if i < len(orig) and work[j] == orig[i]:
            m[j] = i
            i += 1
        else:
            m[j] = i          # 插入字符（空格）：映射到当前原文游标，不推进 i
        j += 1
    m[len(work)] = len(orig)
    return m


def _work_first_orig(orig: str, work: str):
    """work 区间起点 -> 该区间覆盖到的**最小原文位置**（区间换算专用）。

    w2o 是**非递减**的（插入空格会让多个 work 下标映射到同一个原文位置），
    所以 `min{w2o[k] : k in [a,b)}` 就是 `w2o[a]`。这里把它预计算成前缀，
    便于 `to_orig_span` 直接用 `w2o[a]`（起点）与 `w2o[b-1]+1`（终点）。

    实测：原文"螺栓螺纹规格16长度80" / work"螺栓 螺纹规格16 长度80"
        work(3,9)   -> 原文(3,9)
        work(10,14) -> 原文(8,12)
    """
    return _work_to_orig_map(orig, work)


def _split_concat(tok: str) -> List[float]:
    """把被粘连的单元格/数字串拆开：'8.711'→[8.7,11]（抽取表格时用，这里复用同一思路）。"""
    tok = (tok or "").strip()
    parts = tok.split(".")
    if not re.match(r"^\d+$", parts[0]):
        return []
    out = [float(parts[0])] if len(parts) == 1 else []
    if len(parts) == 2:
        m = re.match(r"^(\d+)(\d*)$", parts[1])
        if not m:
            return []
        out.append(float("%s.%s" % (parts[0], m.group(1))))
        if m.group(2):
            out.append(float(m.group(2)))
    elif len(parts) > 2:
        out.append(float("%s.%s" % (parts[0], parts[1])))
        try:
            out.append(float(".".join(parts[2:])))
        except ValueError:
            pass
    return out


def num_start(m) -> int:
    """匹配里数字部分的起始位置。"""
    return m.start(1)


def end_of_num(m) -> int:
    """匹配里数字部分的结束位置。"""
    return m.end(1)


def find_alias_value(text: str, alias: str, lo=None, hi=None, names=None,
                     is_free=None, ctx: str = None):
    """按中文别名取一个数，并做**跨度对齐 + 边界**双校验。

    逐个出现位置试，两个校验缺一不可：
      · 跨度对齐：匹配到的原文片段必须以别名开头。
        实测过「宽度30 槽数3」里"槽"隔着"度30 "咬住了 30 —— 因为正则允许
        `\\s*` 跨过空格，raw 变成 '度30 槽'。这类匹配一律作废。
      · 边界判断：数字后面若紧跟另一个参数的开头字或量词，说明这个数不属于本参数
        （「5槽 Z型 内径12」里"槽"不能拿走 12）。
    返回 (value, span, raw) 或 None。
    """
    a = re.escape(alias)
    if alias in ("厚", "厚度"):
        a = r"(?<!壁)" + a
    for m in re.finditer(a + r"\s*(?:为|是|=|:|：)?\s*(?:[\u2300Rr])?\s*(\d+(?:\.\d+)?)(?![0-9])",
                         text):
        raw = m.group(0)
        # 跨度对齐：**别名与数字之间只允许空白/为是=:⌀R**，不允许出现别的字。
        # 「宽度30 槽数3」匹配到 raw='度30 槽' 时，别名"槽"后面先出现的是 '数3'→ 作废；
        # 「宽度30 槽」则因为数字后面是"槽"（量词）也作废。正常写法「宽度30」原样通过。
        _gap = raw[len(alias):]
        if not re.fullmatch(r"\s*(?:为|是|=|:|：)?\s*(?:[\u2300Rr])?\s*" + re.escape(m.group(1)),
                            _gap):
            continue
        # 别名后缀消歧：别名后面若紧跟能"组成更长参数名"的字，就说明这是短别名误配。
        # 实测「槽数3」被短别名"槽"抢走（槽数被读成 30 / 40 这类怪值）。
        # ⚠️ 别名后面若是"另一个别名的开头 + 数字"，说明正则在跨词匹配（`\s*` 允许跨空格），
        #    典型如 "内径30 外径45 长度60" 里 "长" 跳到了 "长度60"（实测长度读不到）。
        #    判定：别名结束处到数字开始处之间若还有别的字 -> 作废。
        _mid = text[m.end(0):m.start(1)].strip()
        if _mid and _mid[0] not in "为是=:：⌀Rr":
            continue
        # ⚠️ 后缀消歧只对"可能只是更长参数名开头"的短别名生效。
        #    别名本身已是**完整计数写法**（齿数/槽数）时直接跳过这一关：
        #    "数" 就是计数词的规范后缀，后面跟什么字都不改变"这是齿数"的事实。
        #    不豁免时「齿数10宽10」里 '宽' 会被当成"短别名误配"而整条作废，
        #    同步带轮齿数退化成由节圆直径反推的 31（实测就是这么错的）。
        _tail = text[m.end(1):m.end(1) + 1]
        _complete_count = alias.endswith("数")
        if (not _complete_count) and _tail and _tail in set(_ALIAS_SUFFIX_BLOCK):
            continue
        # ⚠️ 这里**不做**"事后拆数字"：连写问题一律由 add_boundaries() 在抽取前解决。
        #    事后拆要猜"切在哪"（404 → 40|4 还是 4|04），实测把「孔径45」拆成了 4 + 5。
        num = m.group(1)
        end = m.end()
        if end < len(text) and text[end].isdigit():
            continue
        rest = text[end:].lstrip()
        nxt = rest[:1]
        if nxt == "个":
            if not any(w in alias for w in ("槽", "齿")):
                continue
            rest = rest[1:].lstrip()
            nxt = rest[:1]
            if nxt in ("槽", "齿") and not any(w in alias for w in ("槽", "齿")):
                continue
        # 注意：这里**不再**因为"数字后面紧跟汉字"就作废。
        # 连写场景（「槽数3A型」「宽度30槽数3」）由 add_boundaries() 在抽取前补边界处理；
        # 用汉字边界作废会误杀大量合法写法（实测槽数被读成 0）。

        try:
            v = float(num)
        except ValueError:
            continue
        if lo is not None and not (lo <= v <= hi):
            continue
        if is_free is not None and not is_free(num_start(m), end):
            continue          # 这个数字已被别的参数占用 -> 换下一个出现位置
        # ⚠️ 占用区间必须**从别名开头**到数字结尾整段盖掉。
        #    只盖数字的话，剩下的别名残字（"槽数3" 里剩下的 "数3"）会被短别名再次咬走
        #    —— 实测「宽度30 槽数3」的槽数被读成 30 就是这个原因。
        # 数字紧前是别的参数名 -> 那个数不属于本别名（如「宽度30 槽」）。
        # ⚠️ 必须用**原文**判断：text 是挖过遮罩的版本，"内径22 宽度12" 里
        #    宽度12 前面残留的遮罩会让判定误伤（实测轴承宽度被换成典型值 14）。
        # 注：「数字紧前是别的参数名」这类判定交给计数规则（cn_count）自己做。
        #     在这里做会误伤大量合法别名取值（实测长度/厚度整类读不到），已移除。
        return v, (m.start(0), end), raw
    return None


def find_symbol_value(text: str, sym: str, lo=None, hi=None, is_free=None):
    """按符号取数：「d25」「D100」「n3」「B30」。

    要求符号**独立成词**（前面不能是字母数字），否则 'n3' 会被 'B30' 这类串扰。
    """
    for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(sym) +
                         r"\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)(?!\d)", text):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if lo is not None and not (lo <= v <= hi):
            continue
        if is_free is not None and not is_free(m.start(1), m.end()):
            continue
        return v, (m.start(1), m.end()), m.group(0)
    return None


def add_boundaries(text: str, names=()) -> str:
    """在参数名/量词前面补空格，把"连写的两个参数"提前分开。

    为什么在抽取**之前**做，而不是事后拆数字:
        连写时数字会粘成一个（「宽度40」+「4个槽」→「宽度404个槽」）。
        事后拆要猜"切在哪"（404 → 40|4 还是 4|04），既容易错又误伤正常写法
        （实测「孔径45」被拆成 4 + 5）。**提前补边界**之后，抽取器只需贪婪取值，
        完全不需要猜 —— 这就是升级文档 U2 第 3 条"粘连拆分"的稳妥实现。
    """
    t = text or ""
    if not t:
        return t
    words = set(w for w in (names or ()) if len(w) >= 2)
    words.update(("内径", "外径", "内孔", "孔径", "轴径", "宽度", "轮宽", "厚度",
                  "高度", "长度", "总长", "全长", "基准直径", "节圆直径", "分度圆直径",
                  "齿数", "槽数", "槽型", "带型", "型号", "模数", "升程", "跨距",
                  "环槽数", "线径", "壁厚", "销孔", "自由高度", "螺纹规格"))
    # ⚠️ 必须在**原始文本**上一次性定好切点，不能逐个 replace。
    #    逐个 replace 时，"自由高度" 先插入变成 "自由 高度150"，轮到 "高度" 时
    #    会在它中间再插一次 -> "自由 高度150"，长别名被从中间切开，弹簧自由高度
    #    直接读不到（实测退化成 46.77 的派生值）。
    #    改成"先按长度降序找出所有不重叠的匹配区间，再统一在区间起点插空格"。
    cuts = set()
    taken = []
    for w in sorted(words, key=len, reverse=True):
        start = 0
        while True:
            i = t.find(w, start)
            if i < 0:
                break
            span = (i, i + len(w))
            if any(not (span[1] <= a or span[0] >= b) for a, b in taken):
                start = i + 1          # 与更长的词重叠 -> 跳过这次匹配，让长的赢
                continue
            taken.append(span)
            cuts.add(i)
            start = i + len(w)
    if cuts:
        out = []
        for i, ch in enumerate(t):
            if i in cuts:
                out.append(" ")
            out.append(ch)
        t = "".join(out)
    # 计数写法前面补空格：数字 + 槽/齿（含"个"）
    t = re.sub(r"(?<=\d)\s*(个?\s*[槽齿])", r" \1", t)
    return re.sub(r"[ \t]{2,}", " ", t)


def _longest_valid_split(run: str, lo, hi):
    """把连写的数字串拆成"属于本参数的值" + "剩下的数字"。

    连写来源：多个参数段合并成一句时数字会粘在一起，例如
        「宽度40」+「4个槽」 → 「宽度404个槽」（实测 4 个槽因此丢失）。
    规则（升级文档 U2 第 3 条：粘连拆分按最长合法前缀切）：
      · 本参数取**最长的合法前缀**（落在 lo..hi 内，且不是"前缀+1位"也合法的那种切法）；
      · 剩余部分必须是非空数字串（否则说明没粘连，原值就该整取）。
    返回 (value, remain) 或 None。
    """
    if not run or len(run) < 2:
        return None
    for cut in range(len(run) - 1, 0, -1):
        head, tail = run[:cut], run[cut:]
        if not tail:
            continue
        try:
            v = float(head)
        except ValueError:
            continue
        if lo is not None and not (lo <= v <= hi):
            continue
        # 更长前缀也合法时优先更长（已被上一轮迭代覆盖），这里返回首个合法切法
        return v, tail
    return None


def _preceded_by_param_name(text: str, pos: int, names=None, self_alias: str = None) -> bool:
    """位置 pos 左边紧邻的是不是一个**参数名**（宽度/内径/外径/厚度/长度/齿宽…）。

    用于计数规则：数字紧跟在参数名后面时，它属于那个参数，不该被当成"3个槽"的 3。
    只在 pos 左边 6 个汉字以内判断，够覆盖"基准直径""节圆直径"这类长别名。
    names 可由调用方传入（零件的真实参数名集合），比内置表更准。
    """
    head = text[max(0, pos - 8):pos]
    if not head or head[-1].isspace() or head[-1].isdigit():
        return False
    for w in (names or _PARAM_NAMES):
        if not w or not head.endswith(w):
            continue
        # ⚠️ head 以**当前别名自己**结尾时不算"别的参数名"：
        #    「长度40」的 head 结尾正是"长度"，早先因此把合法匹配全拦掉
        #    （实测长度/厚度读不到，是这一轮最隐蔽的一个坑）。
        if self_alias and (w == self_alias or self_alias.endswith(w)):
            continue
        return True
    return False


# 参数名（含简称），用于上面那个判定
_PARAM_NAMES = ("基准直径", "节圆直径", "分度圆直径", "齿顶圆", "螺纹规格", "公称直径",
                "宽度", "轮宽", "内径", "外径", "内孔", "孔径", "轴径", "小径", "大径",
                "厚度", "高度", "长度", "总长", "全长", "长", "厚", "宽", "高",
                "模数", "齿数", "槽数", "节距", "升程", "跨距", "行程", "基圆直径",
                "线径", "壁厚", "直径", "半径")


def cn_count(text: str, words, lo=1, hi=20, names=None, is_free=None):
    """中文/阿拉伯数字计数：「双槽」「3个槽」「四槽」。返回 (n, span, raw)。

    实现要点（踩过坑）：**从量词位置往前找紧邻的数字**，不写"数字 + \\s* + 量词"的正则。
    后者因为 `\\s*` 允许跨空格，会把「宽度30 槽数3」匹配成 '度30 槽'，
    于把宽度 30 当成槽数（实测就是这么错的）。
    规则：量词左边只允许空白与可选的"个"，再左边必须是数字，且数字左边不能还是数字。
    """
    for w in words:
        for m in re.finditer(re.escape(w), text):
            i = m.start()
            j = i
            while j > 0 and (text[j - 1].isspace() or text[j - 1] == "\u0001"):
                j -= 1
            if j > 0 and text[j - 1] == "个":
                j -= 1
                while j > 0 and (text[j - 1].isspace() or text[j - 1] == "\u0001"):
                    j -= 1
            k = j
            while k > 0 and text[k - 1].isdigit():
                k -= 1
            if k < j:                         # 紧邻的是阿拉伯数字
                if k > 0 and text[k - 1].isdigit():
                    continue                  # 数字左边还有数字，说明只取到一段，弃用
                run = text[k:j]
                # ⚠️ 区间一律用 [k, i)，**不要**再向左吸空格或遮罩。
                #    早先为了"把分隔空白算进来"而让起点穿过空格与占位符，
                #    结果「节圆直径20 20 齿」里起点穿过了整段遮罩落到 4，
                #    区间 (4,15) 与之相交 -> 明明空闲的 20 被判成已占用，
                #    齿数整条读不到（退化成按直径反推的 12）。
                #    占用判定交给 is_free 自己按"遮罩"判断（见 Extractor._free_w）。
                if _preceded_by_param_name(text, k, names, self_alias=w):
                    # 数字紧前是**别的参数名** -> 这个数属于那个参数。
                    # 但连写时它可能同时含"本计数"（宽度40 + 4个槽 → 404），
                    # 这时按"剩下的那位"当计数（升级文档 U2 第 3 条：粘连拆分）。
                    if len(run) >= 2 and run.isdigit():
                        tail = int(run[-1])
                        if lo <= tail <= hi and (is_free is None or is_free(k, i)):
                            return tail, (j - 1, i), text[j - 1:i + len(w)]
                    continue
                if not run.isdigit():
                    continue              # 空 run / 带遮罩的 run 一律作废
                n = int(run)
                if lo <= n <= hi and (is_free is None or is_free(k, i)):
                    return n, (k, i), text[k:i + len(w)]
                continue
            # 紧邻的不是阿拉伯数字：再试"中文数字 + 量词"（双槽/四槽）
            if j > 0 and text[j - 1] in _CN_DIGITS:
                n = _CN_DIGITS[text[j - 1]]
                if lo <= n <= hi and (is_free is None or is_free(j - 1, i)):
                    return n, (j - 1, i), text[j - 1:i + len(w)]
    return None


def find_code(text: str, candidates, not_before_letter=True):
    """读字母/数字代号（槽型 A/SPZ、齿型 XL/5M…）。

    实现是"候选表拼成一个正则、按长度降序交替、取**最左边**的完整词"：
        候选按长度降序交替 -> 同一位置自然取最长（XXH|XH、XL|L）；
        最左优先 -> 只认第一个出现的代号，不会让文本后面"凑巧出现的单字母"胜出。
    早先的实现是逐个候选 finditer 后 `hits[-1]`，等于**优先挑位置最靠后**的命中，
    「齿数20 XL型」里靠后的单字母 Z 压过了正确的 XL（同步带轮齿型被读成 Z）。
    也不做"前缀排除"：那会误杀「XL型」里的 L（因为 ZL 并不是候选，拼接无意义）。

    不写一个"猜型号"的大正则：实测那样会从「V带轮」里抓出 V、从「SPC带轮」里抓出 'SPC带'。
    """
    cands = sorted(set(c for c in candidates), key=len, reverse=True)
    if not cands:
        return None
    pat = "(?:" + "|".join(re.escape(c) for c in cands) + ")"
    for m in re.finditer(pat, text, re.I):
        i, j = m.start(), m.end()
        if not_before_letter and i > 0 and text[i - 1].isascii() and text[i - 1].isalpha():
            continue
        # 前面紧邻阿拉伯数字也不算（否则「214M型」会从中间咬出 14M）
        if i > 0 and text[i - 1].isascii() and text[i - 1].isdigit():
            continue
        if j < len(text) and text[j].isascii() and text[j].isalnum():
            continue
        cand = m.group(0).upper()
        return cand, (i, j), cand
    return None


# ---------------------------------------------------------------- 抽取器
class Extractor(object):
    """按 Spec 从一段文本里抽参数表。

    规则顺序（命中即占用 span，后续规则看不到这段文本）：
        1) 计数词（"3个槽"/"双槽"）
        2) 中文别名（"内径25"/"轮宽40"）
        3) 符号（"d25 D100 n3"）—— 只在空格分隔的"符号区"里认
        4) 字母代号（槽型/齿型）
        5) 后置写法（"25的内径"）
    之后由调用方补 STANDARD/DERIVED/DEFAULT。
    """

    def __init__(self, spec: Spec, text: str, work: str = None):
        self.spec = spec
        self.text = text or ""
        # 先算出"工作文本"（可能已由调用方用 add_boundaries 补过边界），再建参数表。
        self._names_cache = None
        self.work = self.text if work is None else work
        self.tab = ParamTable(self.text, self.work)

    def _names(self):
        """本零件所有参数别名（用于"数字紧跟在参数名后面"的判定）。"""
        if self._names_cache is None:
            out = set(_PARAM_NAMES)
            for al in (self.spec.aliases or {}).values():
                out.update(al)
            self._names_cache = out
        return self._names_cache

    def _free_w(self, i, j):
        """work 坐标区间是否未被占用。

        ⚠️ 判据是"**这段里有没有遮罩**"，不是"和已占用区间做坐标换算后是否相交"。
        为什么不能用坐标换算：占位符 `\\u0001` 是**等长**的，一段遮罩的起止映射回原文
        会得到一个**比原占用区间更宽**的区间。实测「节圆直径20 20 齿」里
        齿的计数区间 [12,15) 换算成原文是 (4,15)，与 节圆直径 占用的 (5,11) 相交，
        于是**明明空闲的 20 被判成已占用**，齿数整条读不到（退化成按直径反推的 12）。
        直接看遮罩既准确又不依赖坐标算术。
        """
        blank = self.tab.blanked_text()
        a = max(0, int(i))
        b = min(len(blank), max(a + 1, int(j)))
        return "\u0001" not in blank[a:b]

    def _text_for_rules(self):
        """规则所用的文本：**挖过遮罩的工作文本**。

        ⚠️ 必须用挖过遮罩的版本。用未挖的版本时，别名会跨过已被别的参数占用的数字
        （实测「直径200 孔径45」里"孔径"咬住了 200 的后一位，孔径被读成 4）。
        索引与工作文本一一对应（挖遮罩是等长替换），因此 span 可直接映射回原文。
        """
        return self.tab.blanked_text()

    # ---- 规则 1：计数词
    def _rule_counts(self):
        for key, words in (self.spec.count_words or {}).items():
            if self.tab.has(key):
                continue
            lo, hi = self.spec.ranges.get(key, (1, 99))
            got = cn_count(self._text_for_rules(), words, lo=lo, hi=hi,
                           names=self._names(), is_free=self._free_w)
            if got:
                n, span, raw = got
                self.tab.set(key, n, unit="个", span=span, raw=raw)
                self.tab.claim(*self.tab.to_orig_span(span))

    # ---- 规则 2：中文别名
    #   ⚠️ 别名按"长度从长到短"排序：短别名"槽"绝不能在长别名"槽数/槽型"之前抢先匹配
    #      （实测过「槽数3」被"槽"拿走，槽数被读成别的数）。
    def _rule_aliases(self):
        items = []
        for key, aliases in (self.spec.aliases or {}).items():
            for a in aliases:
                items.append((-len(a), key, a))
        items.sort()
        for _, key, a in items:
            if self.tab.has(key):
                continue
            lo, hi = self.spec.ranges.get(key, (None, None))
            got = find_alias_value(self._text_for_rules(), a, lo=lo, hi=hi,
                                   names=self._names(), is_free=self._free_w,
                                   ctx=self.work)
            if got:
                v, span, raw = got
                self.tab.set(key, v, span=span, raw=raw)
                self.tab.claim(*self.tab.to_orig_span(span))

    # ---- 规则 3：符号（要求独立成词）
    def _rule_symbols(self):
        for key, sym in (self.spec.symbols or {}).items():
            if self.tab.has(key):
                continue
            lo, hi = self.spec.ranges.get(key, (None, None))
            got = find_symbol_value(self._text_for_rules(), sym, lo=lo, hi=hi,
                                    is_free=self._free_w)
            if got:
                v, span, raw = got
                self.tab.set(key, v, span=span, raw=raw)
                self.tab.claim(*self.tab.to_orig_span(span))

    # ---- 规则 4：字母代号（顺带归一化：SPB→B、SPA→A…，与标准表一致）
    def _rule_codes(self):
        for key, cands in (self.spec.codes or {}).items():
            if self.tab.has(key):
                continue
            got = find_code(self._text_for_rules(), cands)
            if got:
                code, span, raw = got
                norm = code
                try:
                    from .std_pulley import normalize_type
                    norm = normalize_type(code) or code
                except Exception:
                    pass
                self.tab.set(key, norm, unit="", span=span, raw=raw)
                self.tab.claim(*self.tab.to_orig_span(span))

    def run(self) -> ParamTable:
        """规则顺序**很重要**（踩过坑，写在最前面提醒后来人）：

        1) 中文别名 —— 先跑。它占用的是"别名+数字"整段，信息量最大、最明确；
        2) 计数词（"3个槽"/"双槽"）；
        3) 字母代号（槽型/齿型，"A型"/"XL型"）；
        4) 符号（"d25 D100 n3"）。

        为什么别名必须排在计数词之前：计数词先跑时，「槽数5」里的 5 会先被占走，
        于是前面那句「孔径45」被挖成 "孔径4"，孔径被读成 4（实测就是这么错的）。

        为什么**代号必须排在符号之前**：带轮的槽型键就叫 `t`，而尺寸符号里有 `d/D/B/n`。
        「带轮 d25 D100 B30 n3 A型」里符号规则会先把 `D100` 的 D 当成槽型，
        于是真正的 "A型" 再也写不进去（实测槽型被读成 D × 1，轮缘宽度跟着算成 113）。
        "带型/齿型"这类用户明确写出来的代号，优先级必须高于裸字母尺寸符号。
        """
        self._rule_aliases()
        self._rule_counts()
        self._rule_codes()
        self._rule_symbols()
        return self.tab


# ---------------------------------------------------------------- 上层工具
def apply_standard(table: ParamTable, key: str, fn: Callable[[ParamTable], Optional[Value]]):
    """用标准表/公式补一个参数；fn 返回 Value 或 None。"""
    if table.has(key):
        return
    v = fn(table)
    if v is not None:
        table.items[key] = v


def check_consistency(table: ParamTable, rules) -> List[str]:
    """一致性检查：rules 为 [(描述, 判定函数), ...]，返回失败说明列表。

    例如：内孔必须小于外径；齿数必须是正整数；槽数不能超过轮缘能容纳的数量。
    """
    bad = []
    for desc, fn in rules:
        try:
            if not fn(table):
                bad.append(desc)
        except Exception as e:
            bad.append("%s（检查异常 %s）" % (desc, type(e).__name__))
    return bad
