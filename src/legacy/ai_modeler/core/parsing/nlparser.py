# -*- coding: utf-8 -*-
"""自然语言 → 特征树 解析引擎。

规则驱动、完全离线可用 (无需 API Key)。支持中文/英文混合描述, 例如:

    画一个 100×60×20 的长方体, 四角圆角 R5, 在中心打一个直径 10 的通孔
    直径 30 长 50 的圆柱, 沿 X 方向阵列 4 个, 间距 40
    外径 120 内径 40 厚 20 的法兰, 均布 6 个直径 10 的螺栓孔, 分度圆 90
    叶盘 外径 200 厚 30 叶片数 24 叶展 40
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from ai_modeler.core.model.docmodel import ADD, CUT, Document, Feature

# --------------------------------------------------------------------------
# 文本规范化
# --------------------------------------------------------------------------

_FW = {
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4', '５': '5', '６': '6',
    '７': '7', '８': '8', '９': '9',
    '．': '.', '，': ',', '；': ';', '：': ':', '（': '(', '）': ')',
    '／': '/', '－': '-', 'ｘ': 'x', 'Ｘ': 'X', '×': 'x', '＊': '*',
    '、': ',', '。': '.', '　': ' ',
    # 直径符号的各种写法统统归一到 ⌀。
    # ⚠️ 'Ø'(U+00D8 拉丁大写 O 带斜杠) 是最常见的一个, 早先漏了它 ——
    # 于是「外径Ø25」整段读不出数字, 零件库模板以为自己没拿到尺寸, 就套用了
    # 典型值(法兰 160×20 内孔60), 用户看到的就是"生成了一个 160 的大圆盘"。
    'Ø': '⌀', 'ø': '⌀', 'Φ': '⌀', 'φ': '⌀', 'ϕ': '⌀', '∅': '⌀', '⌀': '⌀',
    'Ф': '⌀', 'ф': '⌀',
}

_UNIT_SCALE = {
    'mm': 1.0, '毫米': 1.0, 'cm': 10.0, '厘米': 10.0, 'dm': 100.0, '分米': 100.0,
    'm': 1000.0, '米': 1000.0, 'in': 25.4, '英寸': 25.4,
}

_UNIT_RE = r'(?:毫米|厘米|分米|英寸|mm|cm|dm|in|m|米)?'
_UNIT_C = r'(毫米|厘米|分米|英寸|mm|cm|dm|in|m|米)?'
_NUM_RE = r'(\d+(?:\.\d+)?)'


def normalize(text: str) -> str:
    # ⚠️ 换行先保护起来: 图纸式提示词是**一行一项**("内孔 Ø20.0" / "外径 Ø25.0"…),
    #    如果把换行当成空格再按"中文与数字之间不留空格"消掉, 就会把相邻两行粘成
    #    「内孔⌀20.0切槽Ra0.4…壁厚2.5mm外径」, 标签和数值全部错位 —— 实测就是
    #    这么把一条缸筒描述解析成 Ø160 默认法兰 + 一堆重复零件的。
    s = ''.join(_FW.get(ch, ch) for ch in (text or ''))
    s = s.replace('\r\n', '\n').replace('\r', '\n').replace('\n', _NL)
    s = s.replace('⌀', '⌀')
    # 让 "直径 10 mm 的孔" 这类带空格的写法粘合起来
    s = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=\d)', '', s)
    s = re.sub(r'(?<=\d)\s+(?=(?:mm|cm|dm|in|m|毫米|厘米|分米|英寸)\b)', '', s)
    s = re.sub(r'(?<=\d)\s*(?=(?:mm|cm|dm|in|米|毫米|厘米|分米|英寸))(?:mm|cm|dm|in|米|毫米|厘米|分米|英寸)',
               lambda m: m.group(0).strip(), s)
    s = re.sub(r'(?<=\d)\s+(?=[\u4e00-\u9fff])', '', s)
    s = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[RrCc]\s*\d)', '', s)
    s = re.sub(r'(?<=[RrCc])\s+(?=\d)', '', s)
    # "沿 X 方向" / "绕 Y 轴" 这类写法里的单个轴字母, 不能因为空格被切开
    s = re.sub(r'(?<=[\u4e00-\u9fff])\s*([XYZxyz])\s*(?=[\u4e00-\u9fff])',
               lambda m: m.group(1).upper(), s)
    return s


_SPLIT_RE = re.compile(r'\x01|[\s,;.()\[\]{}]+|然后|接着|之后|而后|其次|并且|同时|以及|再把|再|随后')
_CONNECT = re.compile(r'^(然后|接着|之后|而后|其次|并且|同时|以及|再|随后|请|帮我|麻烦|给我|把|将|一个|一张|个)+')


_DEC_DOT = '\x00'      # 小数点的临时替身, 见 split_clauses
_NL = '\x01'           # 换行的临时替身: 图纸式提示词里换行就是一项, 不能当空格吃掉

# 中文数字: 「两个槽」「三处均布」这类写法很常见
_CN_NUM = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def split_clauses(text: str) -> List[str]:
    """按标点/连接词断句。

    注意: _SPLIT_RE 里的 '.' 是句号, 但小数点的字符也是 '.',
    直接切会把 "8.74" 拆成 "8" 和 "74", 导致所有带小数的尺寸全部失效。
    所以先把"数字中间的点"换成占位符, 切完再换回来。
    """
    text = re.sub(r'(?<=\d)\.(?=\d)', _DEC_DOT, text or '')
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p and p.strip()]
    out = []
    for p in parts:
        p = _CONNECT.sub('', p).strip().replace(_DEC_DOT, '.')
        if p:
            out.append(p)
    return out


def _scale_unit(unit: Optional[str]) -> float:
    if not unit:
        return 1.0
    return _UNIT_SCALE.get(unit, 1.0)


def _to_float(m) -> Optional[float]:
    try:
        return float(m.group(1)) * _scale_unit(m.group(2) if m.lastindex and m.lastindex >= 2 else None)
    except Exception:
        return None


# --------------------------------------------------------------------------
# 尺寸抽取
# --------------------------------------------------------------------------


class DimTable(object):
    """通用形状取值的**出处表**（U1 第一段：取值即记出处）。

    为什么不做成"把 extract_dims 重写进 ParamTable"：
    那个函数同时被十几处调用（孔位/阵列/圆角/裸尺寸兜底），一次性重写
    风险极高、且失败时几何会**静默错位**。这里的做法是：
        **值的来源只有一处**（原正则），出处在同一段代码里顺手记下 ——
    于是"值"和"出处"不可能不一致，也不改变任何既有行为。
    """

    def __init__(self, text: str = ""):
        self.text = text or ""
        self.items: Dict[str, object] = {}

    def note(self, name, value, span=(-1, -1), provenance=None, raw=""):
        try:
            from ai_modeler.core import params as _P
            prov = provenance or _P.EXPLICIT
            self.items[name] = _P.Value(value, "mm", tuple(span), prov, "", raw)
        except Exception:
            self.items[name] = None
        return value

    def provenance_map(self) -> Dict[str, str]:
        return {k: v.provenance for k, v in self.items.items() if v is not None}


class Dims(object):
    def __init__(self):
        self.raw: List[float] = []
        self.length = None
        self.width = None
        self.height = None
        self.diameter = None
        self.inner_diameter = None
        self.radius = None
        self.count = None
        self.spacing = None
        self.angle = None
        self.pcd = None
        self.corner = None
        self.depth = None
        self.wall = None          # 壁厚 (管件/缸筒: 外径-内径)/2
        self.teeth = None
        self.module = None
        self.pair = None
        self.triple = None
        self.xy = None
        self.margin = None
        self.blades = None
        self.span = None
        #: U1：本对象每个字段的出处（字段名 -> params.Value）。
        #: 由 `extract_dims` 在取值**同一次匹配**里填好，两者不可能漂移。
        self.table = DimTable()

    def first(self, *names):
        for n in names:
            v = getattr(self, n, None)
            if v is not None:
                return v
        return None

    def has_any(self) -> bool:
        return bool(self.raw) or any(
            getattr(self, k) is not None for k in
            ('length', 'width', 'height', 'diameter', 'inner_diameter', 'radius', 'count'))

    # ---- U1：出处查询（供审计/界面/AI 通道使用） ----
    def provenance_of(self, name):
        """某个字段的出处（'explicit' 等）；没记录返回 None。"""
        v = self.table.items.get(name)
        return getattr(v, "provenance", None) if v is not None else None

    def span_of(self, name):
        v = self.table.items.get(name)
        return getattr(v, "span", (-1, -1)) if v is not None else (-1, -1)

    def provenance_map(self) -> Dict[str, str]:
        return self.table.provenance_map()


def _lbl(pat: str) -> str:
    """标签 + (可选)直径符号/R + 数字(+单位)。

    中间那个可选符号很重要: 图纸写法最常是「外径Ø25」而不是「外径25」,
    早先的写法只允许 '为/是/=/:', 于是 'Ø' 一挡就整条读不出来。
    """
    return (pat + r'\s*(?:为|是|=|:|约)?\s*(?:[⌀Rr])?\s*' + _NUM_RE + r'\s*' + _UNIT_C)


def extract_dims(text: str) -> Dims:
    d = Dims()
    t = text
    T = d.table                                    # U1：出处表（与取值同源）
    T.text = text or ""

    # 连乘分隔符：x、*、以及中文图纸最常用的 **×（U+00D7）** 和全角 Ｘ。
    # ⚠️ 早先只写了 [x*]，于是「底板 200×150×12」这类**中文乘号**写法
    #    整条读不出三元组（d.triple=None），只能退化成 raw 里三个孤立数字，
    #    后面的形状匹配全部落到兜底分支。实测「200×150×12」triple=None，
    #    而「100x60x20」正常 —— 差别就在这个符号。
    _MUL = r'[x*\u00d7\u2715\uFF58]'
    m = re.search(r'(\d+(?:\.\d+)?)\s*' + _MUL + r'\s*(\d+(?:\.\d+)?)\s*' + _MUL +
                  r'\s*(\d+(?:\.\d+)?)\s*(' + _UNIT_RE + r')', t)
    if m:
        sc = _scale_unit(m.group(4))
        d.triple = (float(m.group(1)) * sc, float(m.group(2)) * sc, float(m.group(3)) * sc)
        T.note('length', d.triple[0], (m.start(1), m.end(1)), raw=m.group(0))
        T.note('width', d.triple[1], (m.start(2), m.end(2)), raw=m.group(0))
        T.note('height', d.triple[2], (m.start(3), m.end(3)), raw=m.group(0))
    else:
        m2 = re.search(r'(\d+(?:\.\d+)?)\s*' + _MUL + r'\s*(\d+(?:\.\d+)?)\s*(' + _UNIT_RE + r')', t)
        if m2:
            sc = _scale_unit(m2.group(3))
            d.pair = (float(m2.group(1)) * sc, float(m2.group(2)) * sc)
            T.note('pair', d.pair, (m2.start(1), m2.end(2)), raw=m2.group(0))

    def grab(pat, attr, conv=float):
        m = re.search(_lbl(pat), t)
        if m:
            v = conv(float(m.group(1)) * _scale_unit(m.group(2)))
            setattr(d, attr, v)
            T.note(attr, v, (m.start(1), m.end(1)), raw=m.group(0))
            return v
        return None

    grab(r'(?:底径|底部直径|大端直径|大端径)', 'diameter')
    grab(r'(?:顶径|顶部直径|小端直径|小端径)', 'inner_diameter')
    grab(r'(?:外径|直径|大径)', 'diameter')
    grab(r'(?:内径|孔径|小径)', 'inner_diameter')
    # 「内孔/中心孔/轴孔/缸孔」也是内径的说法 —— 不认它们的话, 内孔那一段
    # 会被当成"切除动作", 整个零件库模板都不生效(实测: 联轴器/缸筒/垫圈全中招)
    if d.inner_diameter is None:
        m = re.search(_lbl(r'(?:内孔|中心孔|轴孔|缸孔|中孔|通孔直径)'), t)
        if m:
            d.inner_diameter = float(m.group(1)) * _scale_unit(m.group(2))
            T.note('inner_diameter', d.inner_diameter,
                   (m.start(1), m.end(1)), raw=m.group(0))
    if d.diameter is None:
        # ⚠️ 这里**不能**允许 R 前缀: 「外圆R3」是圆角半径, 不是直径 —— 早先把它抓成
        #    直径 3, 于是同一句里的「均布8个Ø14通孔」被切成了 Ø3 的孔(A30 就这么错的)。
        m = re.search(r'(?:齿顶圆|外圆|轮缘外径|筒体外径|头径)\s*(?:为|是|=|:|约)?\s*'
                      r'[⌀]?\s*' + _NUM_RE + r'\s*' + _UNIT_C, t)
        if m:
            d.diameter = float(m.group(1)) * _scale_unit(m.group(2))
            T.note('diameter', d.diameter, (m.start(1), m.end(1)), raw=m.group(0))
    grab(r'半径', 'radius')
    m = re.search(r'(?<![A-Za-z])[Rr]\s*(?:角)?\s*(?:为|是|=|:)?\s*' + _NUM_RE + r'\s*' + _UNIT_C, t)
    if m and d.radius is None:
        d.radius = float(m.group(1)) * _scale_unit(m.group(2))
        T.note('radius', d.radius, (m.start(1), m.end(1)), raw=m.group(0))
    m = re.search(r'(?<![A-Za-z])(?:⌀|D)\s*(?:为|是|=|:)?\s*' + _NUM_RE + r'\s*' + _UNIT_C, t)
    if m and d.diameter is None:
        d.diameter = float(m.group(1)) * _scale_unit(m.group(2))
        T.note('diameter', d.diameter, (m.start(1), m.end(1)), raw=m.group(0))

    grab(r'(?:长度|长边|总长|全长|长)', 'length')
    grab(r'(?:宽度|宽边|宽)', 'width')
    # 「壁厚2.5」不能当成高度! 早先 '厚' 会命中 "壁厚2.5" 里的 "厚",
    # 于是缸筒的壁厚被当成了零件高度(建出来 2.5 mm 高的怪东西)。
    grab(r'(?:壁厚|管壁厚|缸筒壁厚)', 'wall')
    grab(r'(?:高度|厚度|(?<!壁)厚|高)', 'height')
    grab(r'(?:孔深|深度|深)', 'depth')
    grab(r'(?:间距|间隔|步距|距离)', 'spacing')
    grab(r'(?:分度圆|节圆|分布圆|中心圆)(?:直径)?', 'pcd')
    grab(r'(?:模数)', 'module')

    # 中文后置标签: 「33×4.0mm厚」= 厚4.0、「20mm深」= 深20
    # (图纸式描述里非常常见, 数字在前、量名在后)
    def grab_post(pat, attr):
        if getattr(d, attr, None) is not None:
            return None
        m = re.search(_NUM_RE + r'\s*' + _UNIT_C + r'\s*(?:' + pat + r')', t)
        if m:
            v = float(m.group(1)) * _scale_unit(m.group(2))
            setattr(d, attr, v)
            T.note(attr, v, (m.start(1), m.end(1)), raw=m.group(0))
            return v
        return None

    grab_post(r'厚|厚度', 'height')
    grab_post(r'长|长度', 'length')
    grab_post(r'宽|宽度', 'width')
    grab_post(r'深|深度', 'depth')

    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:度|°|deg)', t)
    if m:
        d.angle = float(m.group(1))
        T.note('angle', d.angle, (m.start(1), m.end(1)), raw=m.group(0))

    m = re.search(r'(?:齿数|齿数为)\s*(?:为|是|=|:)?\s*(\d+)', t)
    if m:
        d.teeth = int(m.group(1))
        T.note('teeth', d.teeth, (m.start(1), m.end(1)), raw=m.group(0))

    m = re.search(r'(?:叶片数量|叶片数|叶数|片数)\s*(?:为|是|=|:)?\s*(\d+)', t)
    if m:
        d.blades = int(m.group(1))
        T.note('blades', d.blades, (m.start(1), m.end(1)), raw=m.group(0))

    grab(r'(?:叶展|展长|叶高|叶片长度|叶片高度)', 'span')
    grab(r'(?:内缩|边距|距边|离边|留边)', 'margin')

    m = re.search(r'(\d+)\s*(?:个|条|处|颗|枚|只|组)', t)
    if m:
        d.count = int(m.group(1))
        T.note('count', d.count, (m.start(1), m.end(1)), raw=m.group(0))
    else:
        # ⚠️ 中文数字也要认: 「铣两个槽」「三处均布」很常见, 只认阿拉伯数字会漏掉
        m = re.search(r'([一二两三四五六七八九十])\s*(?:个|条|处|颗|枚|只|组|道)', t)
        if m:
            d.count = _CN_NUM.get(m.group(1))
            T.note('count', d.count, (m.start(1), m.end(1)), raw=m.group(0))

    m = re.search(r'(?:圆角|倒圆(?:角)?)\s*(?:半径)?\s*(?:为|是|=|:)?\s*[Rr]?\s*' + _NUM_RE + r'\s*' + _UNIT_C, t)
    if m:
        d.corner = float(m.group(1)) * _scale_unit(m.group(2))
        T.note('corner', d.corner, (m.start(1), m.end(1)), raw=m.group(0))
    else:
        m = re.search(r'(?:倒角)\s*(?:为|是|=|:)?\s*[Cc]?\s*' + _NUM_RE + r'\s*' + _UNIT_C, t)
        if m:
            d.corner = float(m.group(1)) * _scale_unit(m.group(2))
            T.note('corner', d.corner, (m.start(1), m.end(1)), raw=m.group(0))

    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*' + _UNIT_C, t):
        try:
            d.raw.append(float(m.group(1)) * _scale_unit(m.group(2)))
        except Exception:
            pass

    m = re.search(r'[(\[]\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*[)\]]', t)
    if m:
        d.xy = (float(m.group(1)), float(m.group(2)))
        T.note('xy', d.xy, (m.start(1), m.end(2)), raw=m.group(0))
    else:
        mx = re.search(r'[Xx]\s*=\s*(-?\d+(?:\.\d+)?)', t)
        my = re.search(r'[Yy]\s*=\s*(-?\d+(?:\.\d+)?)', t)
        if mx and my:
            d.xy = (float(mx.group(1)), float(my.group(1)))
            T.note('xy', d.xy, (mx.start(1), my.end(1)), raw='%s %s' % (mx.group(0), my.group(0)))
    return d


def detect_axis(text: str) -> Optional[str]:
    if re.search(r'沿\s*[Xx]|[Xx]\s*方向|[Xx]轴|水平方向', text):
        return 'X'
    if re.search(r'沿\s*[Yy]|[Yy]\s*方向|[Yy]轴', text):
        return 'Y'
    if re.search(r'沿\s*[Zz]|[Zz]\s*方向|[Zz]轴|竖直|垂直方向', text):
        return 'Z'
    return None


# --------------------------------------------------------------------------
# 解析结果
# --------------------------------------------------------------------------


class ParseResult(object):
    def __init__(self):
        self.doc = Document('Part1')
        self.steps: List[str] = []
        self.warnings: List[str] = []
        self.ok = True
        self.recognized = 0
        self.message = ''
        self.used_templates: List[str] = []
        # 「需要确认」清单: 关键参数取自典型值、或描述里存在冲突时填进来。
        # AI/自动化通道看到它就应该**先问再建**(而不是照着典型值建一个不对的东西)。
        self.questions: List[str] = []
        # 哪些参数是"猜"的(来自零件库典型值), 让用户一眼看到依据
        self.assumed: List[str] = []

    def summary(self) -> str:
        if self.recognized == 0:
            return '未识别出任何建模指令'
        s = '识别到 %d 条建模指令' % self.recognized
        if self.used_templates:
            s += ', 套用模板: ' + '、'.join(self.used_templates)
        if self.assumed:
            s += '\n  其中这些参数是**推定**的(不是描述里给的): ' + '；'.join(self.assumed)
        if self.questions:
            s += '\n  ⚠ 需要你确认(%d 项)后再定稿:\n' % len(self.questions)
            for i, q in enumerate(self.questions, 1):
                s += '    %d) %s\n' % (i, q)
        return s


# --------------------------------------------------------------------------
# 词表
# --------------------------------------------------------------------------

# 能力开关 —— 迭代评测时逐项打开, 用来量化"学会了什么"
CAPS = {'partlib': True, 'synonyms': True}


def set_caps(**kw):
    CAPS.update(kw)
    return dict(CAPS)


KW_BOX = ('长方体', '立方体', '正方体', '方块', '矩形块', '矩形体', '方体', '方形块',
          '底板', '安装板', '垫板', '盖板', '基板',
          # 板料/箱体类说法(实测用户最常用): "铝板 150×100×8"、"箱体 260×180×90"
          '铝板', '钢板', '铁板', '铜板', '平板', '板料', '板材', '面板', '隔板', '板',
          '箱体', '方箱', '机箱', '壳体', '机座', '基座',
          'box', 'cuboid', 'block')
KW_CYL = ('圆柱', '圆柱体', '柱体', '圆棒', '圆钢', '圆轴', '凸台', '圆凸台', '凸缘',
          '圆盘', '盘体', 'cylinder')
KW_CONE = ('圆锥', '圆台', '锥形', '锥体', '锥台', 'cone', 'frustum')
KW_SPHERE = ('球体', '圆球', '球', 'sphere', 'ball')
KW_GEAR = ('齿轮', '齿坯', 'gear')
KW_HOLE = ('孔', '钻孔', '打孔', '开孔', '穿孔', '沉台', '沉坑', 'hole', 'bore')
KW_POCKET = ('凹槽', '挖槽', '铣槽', '方槽', '键槽', '槽', '内腔', '型腔', '开口', '开口槽',
             'pocket', 'slot')
KW_EXTRUDE = ('拉伸', '挤出', '拉出', '拉伸体', 'extrude', 'pad')
KW_REVOLVE = ('回转', '旋转体', '车削', '旋转成型', '旋成', 'revolve', 'lathe')
KW_FILLET = ('圆角', '倒圆', '倒圆角', '圆滑', 'fillet')
KW_CHAMFER = ('倒角', '切角', 'chamfer')
KW_PATTERN = ('阵列', '排列', '均布', '复制', '分布', 'pattern', 'array')
KW_SKETCH = ('草图', '轮廓', '截面', '剖面', '矩形', '长方形', '正方', '圆形', '圆片', '圆板', 'sketch', 'profile')
KW_CUT = ('切除', '挖掉', '去掉', '减去', '掏空', '挖空', '去除', '铣掉', '钻', '打', '开')
KW_SYM = ('对称', '居中', '中心对称')
KW_HOLLOW = ('空心', '中空', '抽壳', '管', '套筒', '筒', '环')

TEMPLATES = ('叶盘', '整体叶盘', '叶轮', 'blisk', '叶片', 'blade', '法兰', 'flange',
             '阶梯轴', '台阶轴', '套筒', '圆管', '钢管', '管件')


_STRIP_WORDS = re.compile(r'(?:叶片数量|叶片数|叶数|片数|齿数|孔数|个数|数量|模数|分度圆|节圆|分布圆|中心圆|排数|直径|半径)')
_STRIP_NUMS = re.compile(r'\d+(?:\.\d+)?\s*(?:mm|cm|dm|in|m|毫米|厘米|分米|英寸)?')

_NEW_FEATURE_KW = (KW_BOX + KW_CYL + KW_CONE + KW_SPHERE + KW_GEAR + KW_HOLE + KW_POCKET
                   + KW_EXTRUDE + KW_REVOLVE + KW_FILLET + KW_CHAMFER + KW_PATTERN + KW_SKETCH
                   + ('叶盘', '整体叶盘', '叶轮', 'blisk', '叶片', 'blade', '法兰', 'flange',
                      '阶梯轴', '台阶轴', '套筒', '圆管', '钢管'))


def _has_feature_kw(c: str) -> bool:
    c2 = _STRIP_WORDS.sub('', c)
    c2 = _STRIP_NUMS.sub('', c2)
    return _any(c2, _NEW_FEATURE_KW)


def merge_clauses(clauses):
    """把「模数3」「厚20」「内缩12」这类纯参数片段并回前一个特征片段。"""
    out = []
    for c in clauses:
        if out and not _has_feature_kw(c) and (extract_dims(c).has_any() or len(c) <= 4):
            # 两边都是数字时要留个空格, 否则 "轴承10" + "30" 会粘成 "轴承1030"
            sep = ' ' if (out[-1][-1:].isdigit() and c[:1].isdigit()) else ''
            out[-1] = out[-1] + sep + c
        else:
            out.append(c)
    # 再正向合并一次: 「刹车盘 外径280 厚度22」会被切成 ['刹车盘', '外径280厚度22'],
    # 只带名字、不带数字的片段要和后面那个带数字的片段粘回去。
    merged = []
    i = 0
    while i < len(out):
        c = out[i]
        if (not extract_dims(c).has_any() and i + 1 < len(out)
                and extract_dims(out[i + 1]).has_any()):
            merged.append(c + out[i + 1])
            i += 2
        else:
            merged.append(c)
            i += 1
    return merged


# 空格连写的长句里, 这些词标志着"基础形状说完了, 接下来是修饰/切除要求"
_SPLIT_MARKS = ('四角', '均布', '中心', '内缩', '对称', '圆角', '倒角',
                '凹槽', '键槽', '挖槽', '铣槽', '方槽', '槽',
                '沿', '阵列', '打', '开', '钻', '挖', '切除')

# 这些词里含切分标记字（"槽数"含"槽"），但它们是**参数名**而不是切除动作。
# 不屏蔽的话「多槽带轮内径25外径100宽度30 槽数3」会被切成
# 「…宽度30」+「槽数3」，零件库因缺"槽数"而整条识别失败（实测 V 带轮建不出来）。
_SPLIT_PROTECT = ('多槽带轮', '多楔带轮', '同步带轮', '齿槽', '槽数', '槽型', '槽宽',
                  '槽距', '槽深', '齿数', '齿型', '齿宽',
                  # 「3槽」「双槽」「四槽」（槽数写在零件名前，别在中间下刀）
                  '双槽', '单槽', '三槽', '四槽', '五槽', '六槽', '八槽', '十槽')


def _deoverlap(head: str, tail: str) -> str:
    """拼接两段文字时去掉尾部/首部的重叠（含单个数字的重叠）。

    例: head="…宽度40", tail="40 2个槽 Z型" → "…宽度40 2个槽 Z型"
        而不是 "…宽度4040 2个槽 Z型"（那会把宽度读成 4040）。
    """
    if not head or not tail:
        return head + tail
    # 1) 先按较长重叠（最多 6 个字）试
    for ln in range(min(6, len(head), len(tail)), 0, -1):
        if head.endswith(tail[:ln]):
            return head + tail[ln:]
    # 2) 再试"尾部数字与首部数字重叠"（宽度40 + 40 …）
    m1 = re.search(r'(\d+)$', head)
    m2 = re.match(r'(\d+)', tail)
    if m1 and m2:
        a, b = m1.group(1), m2.group(1)
        for ln in range(min(len(a), len(b)), 0, -1):
            if a.endswith(b[:ln]):
                return head + tail[ln:]
    return head + tail


def _split_head_tail(clause: str):
    """把「基础形状 + 修饰/切除要求」连写成一句的话切开。

    例: 底板200×150×12四角各打一个直径8的通孔内缩12
        -> ('底板200×150×12', '四角各打一个直径8的通孔内缩12')
       四角圆角R8在中心打一个直径20的通孔
        -> ('四角圆角R8', '在中心打一个直径20的通孔')

    标记词常常就出现在句首(如"四角圆角R8"), 所以要**从左往右找第一个
    切完两边都说得通的位置**, 而不是简单取最早出现的标记。
    含标记字的参数名（槽数/齿数…）先等长屏蔽, 避免在词中间下刀。
    """
    protected = clause
    for word in _SPLIT_PROTECT:
        if word in protected:
            protected = protected.replace(word, '\u0001' * len(word))
    # 「3槽」「12槽」：数字+槽 也是槽数写法，同样不能在这里下刀
    protected = re.sub(r'\d+\s*槽', lambda m: '\u0001' * len(m.group(0)), protected)
    positions = set()
    for mk in _SPLIT_MARKS:
        start = 0
        while True:
            i = protected.find(mk, start)
            if i < 0:
                break
            positions.add(i)
            start = i + len(mk)
    for i in sorted(positions):
        if i <= 0 or i >= len(clause) - 2:
            continue
        head, tail = clause[:i], clause[i:]
        if any(ch.isdigit() for ch in head):
            return head, tail
    return '', ''


def parse_clauses(text: str):
    # ⚠️ 先按换行切成"行", 每行内部再做连写合并 —— 绝不能让合并跨行,
    #    否则图纸式规格表(一行一项)又会被粘成一句, 标签和数值全错位。
    out = []
    for line in (text or '').split(_NL):
        out.extend(merge_clauses(split_clauses(line)))
    return out


# 切除/修饰关键词, 用来从句尾摘出"附加要求"(例如齿轮后面的 中心孔直径15)
_TAIL_KEYWORDS = ('中心孔', '通孔', '盲孔', '沉孔', '螺栓孔', '销孔', '油孔', '螺纹孔',
                  '凹槽', '键槽', '圆角', '倒角', '孔', '槽')
_TAIL_PREFIX = ('中心', '均布', '四角', '内缩', '对称', '沿', '打', '开')


def _tail_phrases(clause: str, covered: str):
    """摘出句子里**所有** "模板不管" 的切除/修饰短语(可能有多段)。

    例: 「直齿圆柱齿轮模数2齿数30齿宽20中心孔直径15圆角R2」
        -> ['中心孔直径15', '圆角R2']
    只取这些短语本身, 不把无关参数(轮毂外径36…)带进去 —— 否则通用规则会把 36
    当成孔径(实测踩过这个坑)。
    """
    hits = []
    for kw in _TAIL_KEYWORDS:
        if kw in covered:          # 模板自己就管这个词(如法兰的"孔径")
            continue
        start = 0
        while True:
            i = clause.find(kw, start)
            if i < 0:
                break
            s = i
            for pre in _TAIL_PREFIX:   # 往前吃"中心/均布/四角…"
                p = s - len(pre)
                if p >= 0 and clause[p:s] == pre:
                    s = p
                    break
            hits.append((s, i + len(kw)))
            start = i + len(kw)
    if not hits:
        return []
    hits.sort()
    merged = []
    for s, e in hits:              # 合并重叠(如"中心孔"里还含"孔")
        if merged and s < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    for idx, (s, _e) in enumerate(merged):
        nxt = merged[idx + 1][0] if idx + 1 < len(merged) else len(clause)
        piece = clause[s:nxt]
        # ⚠️ 还要在**下一个参数名**处截断: 「孔Ø46总长140左端台阶Ø58长25」里的
        #    "总长/台阶/法兰"是零件自身的参数, 不是这一段切除要求的一部分 ——
        #    不截断的话它会被当成一个新零件再建一遍(A47 多出一个 Ø46×140 的圆柱)。
        m = re.search(r'(总长|全长|外径|内径|内孔|壁厚|厚度|台阶|轮毂|键槽|倒角|材料|'
                      r'热处理|表面|公差|硬度|淬火|Ra)', piece)
        if m and m.start() > 0:
            piece = piece[:m.start()]
        piece = piece.strip(' ,，、;；')
        if piece:
            out.append(piece)
    return out


_DIM_NOISE = re.compile(
    r'(毫米|厘米|分米|英寸|mm|cm|dm|in|米|长度|宽度|高度|厚度|直径|半径'
    r'|长|宽|高|厚|径|的|一个|个|是|为|和|与|及|以及|约|左右|大概)')

_NON_DIM_CHARS = re.compile(r'[^0-9a-zA-Z\s,.\-_+*/x×=()（）、]')


def _looks_like_bare_dims(c: str) -> bool:
    """整句是不是"基本只剩尺寸"?

    "100x60x20"      -> True
    "长100 宽60 高20" -> True
    "M8 内六角螺栓"    -> False  (还有"内六角螺栓"这种实词)
    """
    r = _STRIP_NUMS.sub('', c)
    r = _DIM_NOISE.sub('', r)
    r = re.sub(r'[\s,.\-_+*/x×=()（）]', '', r)
    return len(r) <= 1


def _any(text: str, kws) -> bool:
    low = text.lower()
    for k in kws:
        if k.isascii():
            if re.search(r'(?<![a-z])' + re.escape(k) + r'(?![a-z])', low):
                return True
        elif k in text:
            return True
    return False


def _fmt(v) -> str:
    try:
        return ('%.4g' % float(v))
    except Exception:
        return str(v)


# --------------------------------------------------------------------------
# 主解析器
# --------------------------------------------------------------------------


class Parser(object):
    def __init__(self):
        self.res = ParseResult()
        self.pending_profile = None      # 待拉伸的草图轮廓
        self.pending_note = ''
        self.axis = 'Z'
        self.template_used = None
        self.handled = set()             # 已经处理过的子句(防止递归打转)
        self.depth = 0                   # 当前递归深度
        self._no_stack = False           # 本句是否"不许堆叠"(说了同心/套在…)

    # ---------------- 对外接口 ----------------
    def parse(self, text: str, doc: Optional[Document] = None) -> ParseResult:
        if doc is not None:
            self.res.doc = doc
        self.res.doc.source_prompt = text
        norm = normalize(text)
        clauses = parse_clauses(norm)
        norm_flat = norm.replace(_NL, ' ')
        self._multi = (_NL in norm)      # 多行 = 图纸式规格表(一行一项)
        if not clauses:
            self.res.ok = False
            self.res.message = '输入为空。'
            return self.res
        # 逐段处理; 遇到"零件名 + 参数分散在后面几段"的长句时, 先把纯尺寸段并回来。
        # 例: 「缸筒 总长180, 筒体外径Ø63 内孔Ø50, 右端法兰Ø80 厚10」——
        #     第一段只有"总长180", 真正的外径/内孔在第二段。不合并的话
        #     零件库只能靠典型值(35×35), 建出来的缸筒完全不是那个尺寸。
        i = 0
        seen_consumed = set()
        visits = {}
        while i < len(clauses):
            # 安全阀: 只有当"同一个下标被反复处理"时才算卡住（正常循环每个下标只来一次）。
            visits[i] = visits.get(i, 0) + 1
            if visits[i] > 2:
                self.res.warnings.append('解析时检测到无法收敛的合并, 已停止合并'
                                         '（剩余片段: %s）' % ' ｜ '.join(clauses[i:i + 3]))
                break
            _i_before = i
            # ⚠️ 中文常把参数写在形状词**前面**: 「半径 27.5mm, 高 84mm 的圆柱」。
            # 早先这种写法会先被当成裸尺寸建一个长方体, 再把"的圆柱"按默认值建个 Ø50×50,
            # 结果整件东西完全不对(基准 A05)。这里把"参数段 + 紧随其后的形状段"并成一句。
            if i + 1 < len(clauses):
                nxt = clauses[i + 1].lstrip('的')
                if (self._is_param_line(clauses[i]) or self._is_dim_clause(clauses[i])) \
                        and _any(nxt, KW_CYL + KW_BOX + KW_CONE + KW_SPHERE + KW_GEAR):
                    merged = clauses[i] + nxt
                    self.res.steps.append('参数在前、形状在后, 已合并: %s' % merged)
                    self.handle(merged, norm_flat)
                    i += 2
                    continue
            # 零件库的"零件名 + 参数项"常被切分器拆成两段（例如
            # 「多槽带轮 内径25 外径100 宽度30 槽数3」→「…宽度30」+「槽数3」，
            # 「V带轮 … 槽数8 D型」→「…槽数8」+「D型」），零件名只在第一段里。
            # 这里在当前段后面最多再吞 2 段，逐个组合试零件库识别：能建成带轮
            # （不是套典型值硬凑）才合并，避免把后面的独立要求吃进来。
            if not getattr(self, '_no_cross_merge', False):
                try:
                    from ai_modeler.parts import partlib as _pl
                    if _pl.find_part(clauses[i]):
                        merged_ok = False
                        for extra in (1, 2):
                            if i + extra >= len(clauses):
                                break
                            joined = ''.join(clauses[i:i + extra + 1])
                            # 两段之间常有重叠（"…宽度40" + "40 2个槽…" → "宽度4040…"），
                            # 直接把重叠部分去掉，否则数字会被粘成 4040 这种怪值。
                            joined = _deoverlap(clauses[i], ''.join(clauses[i + 1:i + extra + 1]))
                            got = _pl.build_features_ex(joined)
                            if not got:
                                continue
                            cat2, feats2, _bb2 = got[:3]
                            if cat2 not in ('多槽带轮', '同步带轮'):
                                continue
                            if not any(ft.kind in ('vbelt_pulley', 'timing_pulley')
                                       for ft in feats2):
                                continue
                            self.res.steps.append(
                                '零件参数跨段合并: %s' % ' ｜ '.join(clauses[i:i + extra + 1]))
                            # 合并后的这一句直接交给 handle, 并置位禁止再次合并,
                            # 否则 handle 会从头再走一遍切分/合并, 形成死循环。
                            self._no_cross_merge = True
                            try:
                                self.handle(joined, norm_flat)
                            finally:
                                self._no_cross_merge = False
                            i += extra + 1
                            merged_ok = True
                            break
                        # ⚠️ 这里必须用显式标志: 早先用 for...else 判断, 而循环里的
                        #    break（i+extra 越界）会**跳过 else**, 于是 extra 残留为 1、
                        #    主循环 i 不前进 —— 每个零件名都会把解析器卡成死循环。
                        if merged_ok:
                            continue
                except Exception:
                    pass
            merged, nxt = self._merge_dim_clauses(clauses, i)
            for k in getattr(self, '_merged_consumed', set()):
                seen_consumed.add(k)
            if i not in seen_consumed:
                self.handle(merged, norm_flat)
            i = nxt
            if i <= _i_before:            # 兜底: 保证主循环一定前进
                i = _i_before + 1
        if self.pending_profile is not None:
            self.res.warnings.append('草图已经画好了, 但还缺一句「拉伸 XX 毫米」—— '
                                     '没有它就不会生成实体。')
        if self.res.recognized == 0:
            self.res.ok = False
            self.res.message = ('没有从描述中识别出建模指令。\n'
                                '可试试: 「画一个 100×60×20 的长方体, 中心打直径 10 的通孔」')
        elif not self.res.doc.features:
            self.res.ok = False
            self.res.message = ('只识别到草图, 还没有生成实体。\n'
                                '草图的后面要再补一句拉伸, 例如:\n'
                                '「100×50 毫米的矩形草图, 然后拉伸 20 毫米」')
        return self.res

    # ---------------- 单句处理 ----------------
    def _part_tails(self, clause: str, cat: str):
        """零件模板匹配成功后, 摘出同一句里模板不负责的附加要求(可能有几段)。

        例: 「直齿圆柱齿轮 模数2 齿数30 齿宽20 中心孔直径15」—— 齿轮模板只管
        模数/齿数/齿宽, 后面的"中心孔直径15"要再交给通用规则, 否则会被整句吞掉
        (这就是"建出来没有中心孔"的原因)。
        """
        try:
            from ai_modeler.parts import partlib
        except Exception:
            return []
        spec = partlib.PARTS.get(cat) or {}
        words = []
        for al in (spec.get('params') or {}).values():
            words.extend(al)
        words.extend(spec.get('aliases') or [])
        return _tail_phrases(clause, ''.join(words))

    @staticmethod
    def _is_param_line(c: str) -> bool:
        """这一段是不是"标签+数值"的参数行(图纸式规格表里最常见)?

        例: 「内孔 Ø20.0」「外径 Ø25.0」「壁厚 2.5 mm」「模数3」「齿数24」「总长180」。
        判据: 以参数名开头 + 含数字 + **不含**形状/动作词。
        """
        return bool(re.match(r'^\s*[（(]?\s*(?:缸筒壁厚|管壁厚|壁厚|外径|内径|内孔|孔径|中心孔|轴孔|轮毂|厚度|厚|'
                             r'长度|总长|全长|长|高度|高|宽度|宽|模数|齿数|齿宽|分度圆|节圆|螺距|'
                             r'线径|圈数|有效圈数|头数|行程|跨距|拐数|缸数|对边|头高|升程|直径|半径|'
                             r'半径|外圆|内圆|孔深|沉孔|倒角|圆角|材料|热处理|表面|公差|数量|个数)',
                             c or '')) and bool(re.search(r'\d', c or ''))

    @staticmethod
    def _is_annotation_line(c: str) -> bool:
        """这一段是"工艺/表面/定位"说明, 不是形状也不是切除:

        例: 「切槽 Ra 0.4」「平台网纹(交叉角45°±5°)」「过渡圆角R0.8」
            「顶面法兰定位」「材料 45#钢」「热处理 HRC40」「表面发黑」「未注公差±0.1」。
        它们不该打断"零件名 + 后面几行参数"的合并, 也不该被当成新零件。
        """
        return bool(re.search(r'(网纹|滚花|Ra|粗糙度|公差|±|材料|硬度|热处理|淬火|调质|发黑|'
                              r'镀|阳极|喷砂|喷漆|去毛刺|未注|定位|基准|技术要求|说明|备注|'
                              r'切槽|退刀槽|过渡圆角|倒角|圆角)', c or ''))

    def _pattern_is_inherent(self, c: str) -> bool:
        """这一段说的"均布"是零件自带的结构, 不需要再阵列一次?

        例: 「四个轴颈相位90°均布」(十字轴自带)、「齿沿圆周均布」(齿轮本来就是整圈齿)。
        判据: 上一个特征是复合件, 且这一句没有给出真正的阵列参数。
        """
        last = self.last()
        if last is None:
            return False
        if last.kind not in ('cross_joint', 'rotor', 'gear', 'helical_gear',
                             'bevel_gear', 'sprocket', 'impeller', 'crankshaft',
                             'camshaft', 'scroll_disc', 'flywheel', 'conrod'):
            return False
        if re.search(r'(阵列|复制|镜像|沿.{0,4}方向)', c):
            return False
        return bool(re.search(r'(相位|均布|圆周|一圈)', c))

    @staticmethod
    def _looks_like_plain_cylinder(c: str, d: Dims) -> bool:
        """这一段其实只是在描述一个圆柱吗(而不是真的要做回转体)?

        判据: 给了外径/半径 + 高度, 且**没有**轮廓/草图/截面/圆弧这类回转特征描述。
        用于拦住「回转底座 Ø160 高35」这类"零件名里带回转"的说法。
        """
        has_size = bool(d.diameter or d.radius) and bool(d.height or d.length)
        if not has_size:
            return False
        if re.search(r'(轮廓|草图|截面|剖面|圆弧|母线|扫掠|旋成|绕|轴心线)', c):
            return False
        # ⚠️ 「回转体 半径40 高60, 回转360度」是**真的回转体**, 不能当成圆柱;
        #    只有"零件名里带回转"(回转底座/回转台…)才按圆柱做。
        if re.search(r'(回转体|旋转体|车削|回转\s*[\d.]+|回转成|回转\s*\d+\s*度)', c):
            return False
        return True

    def _merge_dim_clauses(self, clauses, i):
        """把紧跟其后的"纯尺寸段"并进当前段(只在**确实更准**时才并)。

        返回 (要处理的文本, 下一段的下标)。
        """
        c = clauses[i]
        merged, j = c, i + 1
        try:
            from ai_modeler.parts import partlib
        except Exception:
            return c, i + 1
        base = partlib.model_params(c)
        base_def = len(base.get('defaulted') or []) if base else 0
        # 单行描述保持**原来的保守行为**: 只把紧跟其后的"纯尺寸段"并进来。
        # 多行(图纸式规格表)才启用下面那套"逐项收参数"的逻辑 —— 否则会改变
        # 已经调好的单行用例表现(实测 100 条基准会从 0.95 掉到 0.89)。
        if not getattr(self, '_multi', False):
            self._merged_consumed = set()
            self._is_merged = False
            while j < len(clauses) and j <= i + 3:
                nxt = clauses[j]
                if not self._is_dim_clause(nxt):
                    break
                if re.search(r'\d+\s*(?:个|条|处|颗|枚|只|组)', nxt):
                    break
                cand_text = merged + nxt
                cand = partlib.model_params(cand_text)
                if cand and len(cand.get('defaulted') or []) < base_def:
                    self.res.steps.append('把后面一段参数并进零件: %s' % nxt)
                    merged, base_def = cand_text, len(cand.get('defaulted') or [])
                    self._is_merged = True
                    j += 1
                    continue
                break
            return merged, j
        # 只有"零件名/形状那种段"才值得往后面收参数; 参数行自己不用收
        if not (base or _has_feature_kw(c) or partlib.find_part(c)):
            return c, i + 1
        consumed = set()
        while j < len(clauses) and j <= i + 8:
            nxt = clauses[j]
            note = self._is_annotation_line(nxt)
            mine = self._is_dim_clause(nxt) or self._is_param_line(nxt)
            # 说明性的一段(切槽/网纹/圆角/定位/材料…)既可以并进来当参数, 也不打断
            # 合并; 真正的"新形状/新零件"才停
            if not note and (not mine) and (_has_feature_kw(nxt) or partlib.find_part(nxt)):
                break
            if not (mine or note):
                break
            if re.search(r'\d+\s*(?:个|条|处|颗|枚|只|组)', nxt):
                break                      # "均布6个Ø14"是孔阵特征, 别并进来
            cand_text = merged + nxt
            cand = partlib.model_params(cand_text) if (base or partlib.find_part(merged)) else None
            if mine and (cand is None or len(cand.get('defaulted') or []) > base_def):
                break
            self.res.steps.append('把后面一项并进零件: %s' % nxt)
            merged = cand_text
            if cand is not None:
                base_def = len(cand.get('defaulted') or [])
            if mine:
                consumed.add(j)
            j += 1
        self._merged_consumed = consumed
        self._is_merged = bool(consumed) or merged != c
        return merged, j

    def _note_assumptions(self, cat: str, notes) -> None:
        """把"按典型值推定"的参数变成**需要确认的问题**。

        规范要求(零幻觉 + 先问再建): 参数不是描述里给的, 就不能当成用户的意思默默建下去。
        这里只对**结构性参数**追问(外形/长度/齿数/模数/对边/头高…), 像"倒角半径"
        这类不影响主体的不追问, 免得把提问变成噪音。
        """
        struct_words = ('D=', 'd=', 'L=', 'b=', 'H=', 'B=', 'm=', 'z=', 's=', 'k=',
                        'R=', 'n=', '对边', '头高')
        for n in (notes or []):
            if '典型值' not in n and '推定' not in n:
                continue
            for chunk in re.split(r'[、,，]', n):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if any(w in chunk for w in struct_words):
                    self.res.assumed.append(chunk)
        if self.res.assumed:
            self.res.questions.append(
                '这些参数描述里没给, 我按零件库典型值/国标比例先填了: %s '
                '—— 请确认或直接给我数值(例如「齿宽20」)。'
                % '、'.join(self.res.assumed))

    def handle(self, c: str, full: str, depth: int = 0):
        """处理一个子句。

        ⚠️ 这里必须保证**严格收敛**: 零件库匹配成功后会从句子里再摘"附加要求"
        递归处理(如齿轮后面的"中心孔直径15"), 但如果摘出来的那段**又匹配到同一个
        零件库词条**, 就会自己调自己并且永不缩小 —— 实测「缸筒…法兰Ø33…」这类
        长句直接把递归打爆(RecursionError: maximum recursion depth exceeded)。
        所以三重保险: 深度上限 + 子句必须变短 + 同一段不重复处理。
        """
        key = (c or '').strip()
        # 「同心/居中/套在/穿过/贯穿」等说法表示**不要**堆叠, 而是同轴重叠;
        # 另外描述里给了"总长/全长"时也不堆叠 —— 用户已经声明了总长, 各段都应该
        # 落在这个总长之内(实测「缸筒 总长180 …, 右端法兰Ø80 厚10」堆叠后会变 190)。
        self._no_stack = bool(
            # ⚠️ 只有"同轴重叠"的意思才不堆叠: 「居中/中间」是**位置**描述(顶面居中一个凸台
            #    仍然要堆在顶面上), 早先把它也算进来, 于是 B09 的凸台没叠上去, 总高 25≠43。
            re.search(r'(同心|同轴|套在|穿过|贯穿|重叠|在一起)', c or '')
            # 「轮毂」是齿轮/带轮上跟齿同宽的凸台, 默认跟主体同心同高, 不能往上堆
            # (早先会把它叠在齿宽之上: 齿宽40 的斜齿轮被堆成 Z=60, 基准 B17/B22 失分)
            or re.search(r'(轮毂|齿毂|轴毂)', c or '')
            or re.search(r'(总长|全长)', full or '')
            or re.search(r'(总长|全长)', c or ''))
        if depth > 8:
            self.res.warnings.append('描述嵌套太深, 这一段已忽略: %s' % key[:40])
            return
        if key and key in self.handled:
            return
        if key:
            self.handled.add(key)
        self.depth = depth
        d = extract_dims(c)
        ax = detect_axis(c)
        if ax:
            self.axis = ax

        # 0) 标准零件库 —— 从《嘉立创FA机械设计手册》学到的汽车/机械基础零件
        if CAPS.get('partlib'):
            hit = None
            notes = []
            try:
                from ai_modeler.parts import partlib
                r4 = partlib.build_features_ex(c)
                if r4:
                    hit = r4[:3]
                    notes = list(r4[3] or [])
            except Exception:
                hit = None
            if hit:
                cat, feats, _bb = hit
                # 顺序建模: 前面已经有实体了, 这个零件(如端面法兰)就默认堆在它顶面上
                has_body = any(f.op == ADD and f.enabled for f in self.res.doc.features)
                for ft in feats:
                    if ft.op == ADD and has_body and not self._no_stack:
                        p = dict(ft.params or {})
                        c0 = p.get('center', (0.0, 0.0, 0.0))
                        if p.get('stack') is None and abs(float(c0[2])) < 1e-9:
                            p['stack'] = True
                            ft.params = p
                    self.res.doc.add(ft)
                self.res.recognized += 1
                self.res.steps.append('零件库识别: %s (来自机械设计手册)' % cat)
                # 带轮这类"按标准生成"的零件会把"提示词 → 特征"对照表带上来，
                # 逐条写进步骤里 —— 用户问的"这句提示词到底对应什么特征"就看这里。
                for ft in feats:
                    exp = (ft.params or {}).get('explain')
                    if not exp:
                        continue
                    self.res.steps.append('—— 提示词 → 生成特征 ——')
                    for item in exp:
                        try:
                            kw, what, why = item
                        except Exception:
                            continue
                        self.res.steps.append('  %s  →  %s（依据：%s）' % (kw, what, why))
                    self.res.steps.append('—— 对照表结束 ——')
                for ft in feats:
                    for w in ((ft.params or {}).get('std_warnings') or []):
                        if w not in self.res.warnings:
                            self.res.warnings.append(w)
                self.res.warnings.extend(notes)
                self.res.doc.invalidate()
                # 同一句里可能还挂着模板不负责的切除要求(如齿轮后面的"中心孔直径15")
                for tail in self._part_tails(c, cat):
                    if len(tail.strip()) >= len(key):      # 必须变短, 否则会打转
                        continue
                    self.res.steps.append('同句附加要求: %s' % tail)
                    self.handle(tail, full, depth + 1)
                # 关键参数是"推定"的 -> 生成澄清问题(AI 通道会先问再建)
                self._note_assumptions(cat, notes)
                return

        # 0.5) 空格连写的长句先切开: "底板200×150×12四角各打一个直径8的通孔内缩12"
        #      (零件库已经先试过了, 这里只处理它不管的情况)
        #      ⚠️ 由"多行规格表合并"得来的子句**不能**再切: 它是"零件名+若干参数项"
        #      拼起来的, 切分器会在词中间下刀(实测把"切槽"切成"切|槽"),
        #      于是凭空多出凹槽、还把零件又匹配一遍。
        head, tail = ('', '') if getattr(self, '_is_merged', False) else _split_head_tail(c)
        if head and tail:
            self.res.steps.append('连写句拆分: %s ｜ %s' % (head, tail))
            self.handle(head, full, depth + 1)
            self.handle(tail, full, depth + 1)
            return

        # 1) 复合零件模板
        if self.try_template(c, d, full):
            return

        # 2) 圆角/倒角 (修饰已有特征)
        if self.is_corner_clause(c):
            if self.try_corner(c, d):
                return
            self.res.warnings.append('未取得圆角/倒角数值, 已忽略: %s' % c)
            return

        # 2.5) 孔位修饰 (内缩 / 分度圆 / 均布)
        if self.try_modifier(c, d):
            return

        # 3) 孔
        #    ⚠️ 先排除"只是在报尺寸"的片段: 「筒体外径Ø63内孔Ø50」里有"孔"字,
        #    但它说的是零件的内径参数, 不是"去打一个孔"。早先会凭空多切一个
        #    Ø63 的孔出来(实测缸筒 A43 的孔全乱了)。
        #    另外: 含"键槽/凹槽"的片段归凹槽管 —— 「孔壁开宽14 深5 的键槽」里的
        #    14 是槽宽不是孔径(实测被切成了 Ø14 的孔, B07)。
        keyway = bool(re.search(r'(键槽|凹槽|铣槽|方槽)', c))
        if _any(c, KW_HOLE) and not self._is_dim_clause(c) and not keyway:
            if self.make_hole(c, d, full):
                return

        # 4) 凹槽
        if _any(c, KW_POCKET):
            if self.make_pocket(c, d):
                return

        # 4.5) 只说了"几个"+动作、没给尺寸的碎段(如「顶面铣两个」):
        #      个数要留给**下一个**切除要求用, 不能丢(实测「铣两个槽」只切了一个)。
        if (d.count and d.count > 1 and not (d.length or d.width or d.pair or d.diameter)
                and re.search(r'(铣|挖|开|切|钻|打|槽|孔)', c)):
            self.pending_count = int(d.count)
            self.res.steps.append('记下数量: ×%d(给下一个切除用)' % int(d.count))
            return

        # 5) 阵列
        #    ⚠️ 复合件(十字轴/转子/齿轮…)的"四个轴颈相位90°均布"是**零件自带的结构**,
        #    不是"再做一次阵列": 早先会真的阵列一遍, 把 120×120×25 变成 120×120×85(B37)。
        if _any(c, KW_PATTERN) and not self._pattern_is_inherent(c):
            if self.try_pattern(c, d):
                return

        # 6) 回转
        #    ⚠️ 「回转底座 Ø160 高35」里的"回转"只是**零件名**, 不是"做回转体"这个动作。
        #    真给出外径+高度的, 应该按圆柱做(实测 B11 被做成了回转体, 外形全错)。
        if _any(c, KW_REVOLVE) and not self._looks_like_plain_cylinder(c, d):
            if self.make_revolve(c, d):
                return

        # 7) 拉伸
        if _any(c, KW_EXTRUDE):
            if self.make_extrude(c, d):
                return

        # 8) 基本体
        #    「回转底座 Ø160 高35」这种"没有形状词、只给了 Ø + 高"的说法一律按**圆柱**:
        #    带 Ø 的尺寸做成方的是错的(早先会掉到裸尺寸兜底建个长方体)。
        if self._looks_like_plain_cylinder(c, d):
            self.make_cylinder(c, d)
            return
        if _any(c, KW_BOX):
            self.make_box(c, d)
            return
        if _any(c, KW_GEAR):
            self.make_gear(c, d)
            return
        if _any(c, KW_CONE):
            self.make_cone(c, d)
            return
        if _any(c, KW_CYL):
            self.make_cylinder(c, d)
            return
        if _any(c, KW_SPHERE):
            self.make_sphere(c, d)
            return

        # 9) 草图 / 轮廓描述
        if _any(c, KW_SKETCH) and self.make_sketch(c, d):
            return
        if self.pending_profile is not None and d.has_any() and self.make_sketch(c, d):
            return

        # 10) 裸尺寸兜底 → 长方体。
        #     只有"整句基本只剩尺寸"时才兜底; 否则 "M8 内六角螺栓" 这种
        #     会被误当成尺寸, 默默建出一个毫无意义的方块。
        if d.has_any():
            starters = ('画', '做', '建', '生成', '创建', '设计', '来')
            has_starter = any(s in c for s in starters)
            if _looks_like_bare_dims(c) or has_starter:
                self.res.warnings.append('片段「%s」仅描述了尺寸, 已按长方体处理。' % c)
                self.make_box(c, d, force=True)
                return
            self.res.warnings.append('不认识这个说法: %s' % c)
            return

        self.res.warnings.append('未能识别片段: %s' % c)

    @staticmethod
    def is_corner_clause(c: str) -> bool:
        if _any(c, KW_FILLET) or _any(c, KW_CHAMFER):
            return True
        return bool(re.match(r'^[RrCc]\s*\d', c.strip()))

    @staticmethod
    def _is_dim_clause(c: str) -> bool:
        """这一段是不是"只是在报零件尺寸"(不是要做切除动作)?

        例如「筒体外径Ø63内孔Ø50」「总长180」「壁厚2.5」。
        判据: 出现 外径/内径/内孔/壁厚/总长/全长/外圆 这类**参数名**,
        并且没有 打/钻/开/铣/挖/通孔/盲孔/沉孔/均布/四角 这些**动作词**。
        """
        if not re.search(r'(外径|内径|内孔|中孔|壁厚|总长|全长|外圆|筒体|孔径|轴孔)', c):
            return False
        if re.search(r'(打|钻|开|铣|挖|切|通孔|盲孔|沉孔|扩孔|均布|四角|阵列|各一个|各打)', c):
            return False
        return True

    # ---------------- 孔位修饰 ----------------
    def try_modifier(self, c, d: Dims) -> bool:
        # 含"孔/槽"的片段应由 make_hole / make_pocket 处理
        if _any(c, KW_HOLE) or _any(c, KW_POCKET):
            return False
        last = self.last()
        if last is None:
            return False
        if (last.kind == 'revolve' and d.angle is not None
                and re.search(r'(回转|旋转|旋成|车削)', c)
                and not re.search(r'(半径|直径|直径)', c)):
            last.params['angle'] = float(d.angle)
            self.res.steps.append('修饰: 回转角度改为 %s°' % _fmt(d.angle))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        if re.search(r'(内缩|边距|距边|离边|留边)', c) and last.kind == 'hole':
            v = d.margin if d.margin is not None else (d.raw[0] if d.raw else None)
            if v is None:
                return False
            last.params['margin'] = float(v)
            self.res.steps.append('修饰: 孔内缩 %s mm' % _fmt(v))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        if (re.search(r'(分度圆|节圆|分布圆|中心圆(?!周))', c)
                and last.kind == 'hole'
                and not re.search(r'(阵列|排列|均布|复制)', c)):
            v = d.pcd if d.pcd is not None else (d.raw[0] if d.raw else None)
            if v is None:
                return False
            # 如果上一个孔已经是"孔阵"(pcd)才盖在它身上; 否则这是**下一个**孔的分度圆
            # (「分度圆Ø100 上均布8个Ø12」被切断时就会这样), 暂存起来给下一个孔用。
            if last.params.get('pos') == 'pcd':
                last.params['pcd'] = float(v)
                if not last.params.get('count'):
                    last.params['count'] = int(d.count or 4)
            else:
                self.pending_pcd = float(v)
            self.res.steps.append('修饰: 分度圆 ⌀%s' % _fmt(v))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        if ('均布' in c or '等分' in c) and last.kind == 'hole' and d.count:
            if last.params.get('pos') in ('pcd', 'corners'):
                last.params['count'] = int(d.count)
                last.params['pos'] = 'pcd'
            else:
                self.pending_count = int(d.count)     # 留给下一个孔用得着
            self.res.steps.append('修饰: 均布 ×%d' % int(d.count))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        return False

    # ---------------- 辅助 ----------------
    def add(self, feat: Feature, desc: str):
        # 顺序建模: 已经有一个实体了, 又描述了一个**新实体**(凸台/盘/轴段),
        # 那就默认把它堆在前一个的顶面上, 而不是同心重叠在 z=0
        # (实测「缸筒 Ø25/Ø20 长40, 端面法兰 Ø33 厚4」原本建出 40 长的筒 +
        #  一个陷在里面的法兰, 总高还是 40; 堆叠后才是 44 的带法兰缸筒)。
        if feat.op == ADD and not self._no_stack:
            if any(f.op == ADD and f.enabled for f in self.res.doc.features):
                p = dict(feat.params or {})
                # 已经自带 z 的(台阶轴/曲轴那些"一段一段往上摆"的模板)不要再叠一次
                c = p.get('center', (0.0, 0.0, 0.0))
                if p.get('stack') is None and abs(float(c[2])) < 1e-9:
                    p['stack'] = True
                    feat.params = p
        self.res.doc.add(feat)
        self.res.recognized += 1
        self.res.steps.append(desc)

    def last(self) -> Optional[Feature]:
        for f in reversed(self.res.doc.features):
            return f
        return None

    def pick3(self, d: Dims, defaults=(100.0, 60.0, 20.0)):
        """取长宽高。"""
        l = d.length
        w = d.width
        h = d.height
        if d.triple:
            l = l if l is not None else d.triple[0]
            w = w if w is not None else d.triple[1]
            h = h if h is not None else d.triple[2]
        elif d.pair:
            l = l if l is not None else d.pair[0]
            w = w if w is not None else d.pair[1]
        if l is None and h is not None and d.raw:
            pass
        nums = list(d.raw)
        for name in ('l', 'w', 'h'):
            pass
        vals = []
        for cur in (l, w, h):
            vals.append(cur)
        used = [v for v in vals if v is not None]
        it = iter([n for n in nums if n not in used])
        out = []
        for i, v in enumerate(vals):
            if v is None:
                try:
                    v = next(it)
                except StopIteration:
                    v = defaults[i]
            out.append(float(v))
        return out[0], out[1], out[2]

    # ---------------- 基本体 ----------------
    @staticmethod
    def _attach_dim_prov(f, d, mapping):
        """U1：把 Dims 里记下的出处搬到特征上。

        mapping: {特征参数名: 字段名 或 (字段名, ...)}；按顺序取第一个有出处的字段。
        值本身还是由原逻辑算出来的（`radius` = `diameter`/2 这类换算**不改出处**：
        这个数仍然是用户那句话给的，只是量纲换了一下）。
        """
        if f is None or d is None:
            return
        prov = {}
        for key, spec in (mapping or {}).items():
            names = spec if isinstance(spec, (tuple, list)) else (spec,)
            for n in names:
                try:
                    pv = d.provenance_of(n)
                except Exception:
                    pv = None
                if pv:
                    prov[key] = pv
                    break
        f.set_provenance(prov)

    def make_box(self, c, d: Dims, force=False):
        l, w, h = self.pick3(c and d or d)
        corner = d.corner if d.corner is not None else 0.0
        mode = 'chamfer' if _any(c, KW_CHAMFER) else 'fillet'
        f = Feature('box', {'length': l, 'width': w, 'height': h,
                            'corner': corner, 'corner_mode': mode,
                            'center': (0.0, 0.0, 0.0), 'base': True, 'axis': self.axis})
        self._attach_dim_prov(f, d, {'length': ('length', 'pair', 'triple'),
                                     'width': ('width', 'pair', 'triple'),
                                     'height': ('height', 'triple', 'pair'),
                                     'corner': ('corner',)})
        # 圆角没写时取的是默认 0.0 —— 这是 default，不是"凭空产生"。
        # 不标的话审计会把它算成"追不出处的尺寸"，掩盖真正的问题。
        if d.corner is None:
            f.set_provenance({'corner': 'default'})
        desc = '长方体 %s×%s×%s mm' % (_fmt(l), _fmt(w), _fmt(h))
        if corner:
            desc += ' (%s R%s)' % ('倒角' if mode == 'chamfer' else '圆角', _fmt(corner))
        self.add(f, desc)

    def make_cylinder(self, c, d: Dims):
        r = d.radius
        dia = d.diameter
        h = d.height
        if r is None and dia is not None:
            r = dia / 2.0
        if r is None and d.triple:
            r = d.triple[0] / 2.0
        if r is None and d.pair:
            r = d.pair[0] / 2.0
        if h is None and d.triple:
            h = d.triple[2]
        elif h is None and d.pair:
            h = d.pair[1]
        nums = list(d.raw)
        if r is None and nums:
            r = nums[0] / 2.0 if (dia is not None or '⌀' in c) else nums.pop(0) / 2.0
        if r is None:
            r = 25.0
        if h is None:
            rest = [n for n in d.raw if abs(n - r * 2) > 1e-6 and abs(n - r) > 1e-6]
            h = rest[0] if rest else 50.0
        r2 = None
        if d.inner_diameter and d.diameter and '圆台' in c:
            r2 = d.inner_diameter / 2.0
        f = Feature('cylinder', {'radius': r, 'height': h, 'radius2': r2,
                                 'center': (0.0, 0.0, 0.0), 'base': True,
                                 'axis': self.axis, 'segments': 48})
        self._attach_dim_prov(f, d, {'radius': ('radius', 'diameter', 'pair', 'triple'),
                                     'height': ('height', 'triple', 'pair'),
                                     'radius2': ('inner_diameter',)})
        self.add(f, '圆柱 ⌀%s × %s mm' % (_fmt(r * 2), _fmt(h)))

    def make_cone(self, c, d: Dims):
        r1 = d.radius or (d.diameter / 2.0 if d.diameter else 25.0)
        r2 = (d.inner_diameter / 2.0) if d.inner_diameter else 0.0
        h = d.height or (d.raw[-1] if d.raw else 50.0)
        f = Feature('cylinder', {'radius': r1, 'radius2': r2, 'height': h,
                                 'center': (0.0, 0.0, 0.0), 'base': True,
                                 'axis': self.axis, 'segments': 48})
        self.add(f, '圆台 R%s→R%s × %s mm' % (_fmt(r1), _fmt(r2), _fmt(h)))

    def make_sphere(self, c, d: Dims):
        r = d.radius or (d.diameter / 2.0 if d.diameter else None)
        if r is None and d.raw:
            r = d.raw[0] / 2.0
        r = r or 25.0
        f = Feature('sphere', {'radius': r, 'center': (0.0, 0.0, 0.0),
                               'segments': 32, 'rings': 16})
        self.add(f, '球体 R%s mm' % _fmt(r))

    def make_gear(self, c, d: Dims):
        teeth = d.teeth or d.count or 20
        module = d.module
        if module is None:
            dia = d.diameter or d.outer_diameter if hasattr(d, 'outer_diameter') else d.diameter
            if dia:
                module = dia / (teeth + 2.0)
        module = module or 2.0
        h = d.height or 10.0
        f = Feature('gear', {'teeth': int(teeth), 'module': module, 'height': h,
                             'center': (0.0, 0.0, 0.0), 'base': True, 'axis': self.axis})
        self.add(f, '齿轮 模数%s × %d 齿, 厚 %s mm' % (_fmt(module), int(teeth), _fmt(h)))
        if d.inner_diameter:
            self.add(Feature('hole', {'diameter': d.inner_diameter, 'through': True,
                                      'pos': 'center', 'axis': self.axis}, CUT),
                     '中心孔 ⌀%s' % _fmt(d.inner_diameter))

    # ---------------- 草图 / 拉伸 ----------------
    def make_sketch(self, c, d: Dims) -> bool:
        if not d.has_any():
            return False
        cc = c.replace('分度圆', '分度○').replace('节圆', '节○')
        cc = cc.replace('中心圆', '中心○').replace('分布圆', '分布○')
        is_circle = ('圆形' in cc) or bool(re.search(r'圆(?!角|弧|柱|锥|球|台|环|管|盘|钢|棒|孔|周|心)', cc))
        if is_circle:
            r = d.radius or (d.diameter / 2.0 if d.diameter else (d.raw[0] / 2.0 if d.raw else None))
            if r is None:
                return False
            self.pending_profile = ('circle', r)
            self.pending_note = '圆形草图 ⌀%s' % _fmt(r * 2)
            self.res.steps.append('建立草图: ' + self.pending_note)
            self.res.recognized += 1
            return True
        if d.triple:
            l, w = d.triple[0], d.triple[1]
        elif d.pair:
            l, w = d.pair
        elif d.length and d.width:
            l, w = d.length, d.width
        elif len(d.raw) >= 2:
            l, w = d.raw[0], d.raw[1]
        else:
            return False
        corner = d.corner or 0.0
        mode = 'chamfer' if _any(c, KW_CHAMFER) else 'fillet'
        self.pending_profile = ('rect', l, w, corner, mode)
        self.pending_note = '矩形草图 %s×%s mm%s' % (
            _fmt(l), _fmt(w), (' (R%s)' % _fmt(corner)) if corner else '')
        self.res.steps.append('建立草图: ' + self.pending_note)
        self.res.recognized += 1
        return True

    def make_extrude(self, c, d: Dims) -> bool:
        h = d.height
        if h is None and d.triple:
            h = d.triple[2]
        if h is None and d.raw:
            h = d.raw[-1]
        if self.pending_profile is None:
            # 没有草图 → 根据描述自建轮廓
            if '圆' in c:
                r = d.radius or (d.diameter / 2.0 if d.diameter else (d.raw[0] / 2.0 if d.raw else 25.0))
                prof = [tuple(p) for p in _circle_profile(r)]
                h = h or 50.0
            else:
                l, w = (d.pair or d.triple[:2] if d.triple else (d.pair or (d.length or 100.0, d.width or 60.0)))
                prof = [(-l / 2.0, -w / 2.0), (l / 2.0, -w / 2.0), (l / 2.0, w / 2.0), (-l / 2.0, w / 2.0)]
                h = h or 20.0
            f = Feature('prism', {'profile': prof, 'height': h,
                                  'center': (0, 0, 0), 'axis': self.axis})
            self.add(f, '轮廓拉伸 %s mm' % _fmt(h))
            return True
        kind = self.pending_profile[0]
        if kind == 'circle':
            r = self.pending_profile[1]
            prof = [tuple(p) for p in _circle_profile(r)]
            h = h or r * 2.0
            note = self.pending_note
        else:
            _, l, w, corner, mode = self.pending_profile
            prof = [tuple(p) for p in _rect_profile(l, w, corner, mode)]
            h = h or min(l, w)
            note = self.pending_note
        f = Feature('prism', {'profile': prof, 'height': h,
                              'center': (0, 0, 0), 'axis': self.axis})
        self.add(f, '拉伸: %s → 高 %s mm' % (note, _fmt(h)))
        self.pending_profile = None
        return True

    def make_revolve(self, c, d: Dims) -> bool:
        ang = d.angle or 360.0
        if d.diameter or d.radius:
            r = d.radius or d.diameter / 2.0
            prof = [(0.0, 0.0), (r, 0.0), (r, d.height or 20.0), (0.0, d.height or 20.0)]
        else:
            h = d.height or 40.0
            r = d.raw[0] / 2.0 if d.raw else 20.0
            prof = [(0.0, 0.0), (r, 0.0), (r, h), (0.0, h)]
        f = Feature('revolve', {'profile': prof, 'angle': ang,
                                'center': (0, 0, 0), 'axis': 'Z', 'segments': 48})
        self.add(f, '回转体 %s°' % _fmt(ang))
        return True

    # ---------------- 切除类 ----------------
    def _diameters_in(self, c: str):
        """把片段里所有 ⌀/直径 数值按出现顺序列出来(沉孔要两个)。

        ⚠️ 必须排除"分度圆/节圆/分布圆/中心圆"后面那个数: 它是孔**分布圆**直径,
        不是孔径。实测「均布6个⌀9通孔带⌀15沉孔深5分度圆⌀110」把 110 当成了沉孔,
        切出一个比外径还大的洞(A32/B08 就是这么失分的)。
        """
        t = re.sub(r'(?:分度圆|节圆|分布圆|中心圆|PCD)\s*[⌀Ø]?\s*\d+(?:\.\d+)?',
                   ' ', c, flags=re.I)
        out = []
        for m in re.finditer(r'[⌀Ø](?:为|是|=|:)?\s*(\d+(?:\.\d+)?)', t):
            out.append(float(m.group(1)))
        for m in re.finditer(r'(?:直径|孔径|内径)\s*(?:为|是|=|:)?\s*(\d+(?:\.\d+)?)', t):
            v = float(m.group(1))
            if v not in out:
                out.append(v)
        return out

    def _dup_hole(self, dia, pos, depth=None):
        """要加的孔是不是"已经有了"?

        典型场景: 零件库件自带中心孔, 描述里又写了「中心孔Ø20」; 或者一句话里
        同一个孔被说了两遍。不合并的话特征树里会出现两个一模一样的孔,
        操作数虚高, 用户看到的是"怎么打了两次"。
        """
        for f in self.res.doc.features:
            if f.op != CUT or f.kind != 'hole':
                # 零件库件自带的孔(齿轮/带轮/转子等的 bore_d)也算"已有"
                bp = (f.params or {}).get('bore_d')
                if not bp:
                    continue
                if dia > 0 and abs(float(bp) - dia) / max(1e-9, dia) <= 0.03:
                    return True
                continue
            p = f.params or {}
            d0 = float(p.get('diameter', 0.0) or 0.0)
            if d0 <= 0 or dia <= 0:
                continue
            if abs(d0 - dia) / max(1e-9, dia) <= 0.03 and p.get('pos', 'center') == pos:
                return True
        return False

    def make_hole(self, c: str, d: Dims, full: str) -> bool:
        if not self.res.doc.features and '孔' not in full:
            return False
        dia = d.diameter or d.inner_diameter
        if dia is None:
            if d.radius:
                dia = d.radius * 2
            elif d.raw:
                dia = d.raw[0]
        if dia is None:
            dia = 10.0
        pos = 'center'
        # ⚠️ 判据顺序: **先认"中心孔"**, 再看"分度圆均布"。
        #    长句里「中心孔Ø28 … 分度圆Ø100 上均布8个Ø12」常被并成一段, 早先先判 pcd,
        #    于是中心孔被标成"分度圆上4个", 真的切了 4 个(基准里 B02/B16/B22/B24/B25/
        #    B34/B35 全栽在这)。四角/坐标优先, 然后是明确的"中心孔"。
        #    另外「四角Ø11通孔 … 顶面沉孔Ø18深6」里的沉孔**没说位置**, 应跟着上一组
        #    四角孔走(B08: 四个角都要沉孔, 只切一个就少了一半)。
        on_circle = bool(re.search(r'(均布|分度圆|节圆|分布圆|中心圆|PCD|圆周)', c))
        prev = self.last()
        # 只有**沉孔/沉台/扩孔**这种"跟着已有孔走"的加工才继承上一组孔的位置与数量,
        # 普通"孔"不继承(否则盘类用例会被带偏: 实测一度把总分从 0.991 拉到 0.983)。
        inherit = (prev is not None and prev.kind == 'hole'
                   and bool(re.search(r'(沉孔|沉台|扩孔|锪平|埋头)', c))
                   and not re.search(r'(四角|均布|分度圆|节圆|坐标|@)', c))
        if '四角' in c or '四个角' in c or '各角' in c:
            pos = 'corners'
        elif d.xy:
            pos = 'coord'
        elif re.search(r'(中心孔|中心打|中心钻|中心通孔|中心盲孔)', c):
            pos = 'center'
        elif on_circle and d.count and d.count > 1:
            pos = 'pcd'
        elif inherit and prev.params.get('pos') in ('pcd', 'corners'):
            pos = prev.params['pos']
            if not d.count:
                d.count = prev.params.get('count')
            if not d.pcd:
                d.pcd = prev.params.get('pcd')
        elif re.search(r'(中心|中间|正中)', c):
            pos = 'center'
        through = ('通孔' in c) or ('通' in c and '孔' in c) or (d.depth is None)
        if '盲孔' in c or d.depth is not None:
            through = False

        # 沉孔/扩孔: 「Ø6.6通孔带Ø11沉孔深5」= 一个通孔 + 一个盲的沉孔
        # 沉台/沉坑: 「顶面中心Ø100 深12 的圆形沉台」= 一个大直径的盲孔(不是通孔)
        sink = ('沉孔' in c) or ('扩孔' in c)
        if sink or ('沉台' in c) or ('沉坑' in c):
            sizes = self._diameters_in(c)
            if sink and len(sizes) >= 2:
                small, big = min(sizes), max(sizes)
                ok = False
                if not self._dup_hole(small, pos):
                    ok = self._add_hole(small, True, pos, d, c) or ok
                if not self._dup_hole(big, pos):
                    ok = self._add_hole(big, False, pos, d, c, depth=d.depth or 5.0) or ok
                if ok:
                    return True
            # 沉台: 只有一个直径 -> 直接按"大直径盲孔"切
            if sizes and (d.depth or 0) > 0:
                dia2 = max(sizes)
                if not self._dup_hole(dia2, pos):
                    if self._add_hole(dia2, False, pos, d, c, depth=d.depth):
                        return True

        if self._dup_hole(dia, pos):
            self.res.warnings.append('孔 ⌀%s 与已有的孔重复（同一位置同一尺寸），已合并。'
                                     % _fmt(dia))
            return True
        return self._add_hole(dia, through, pos, d, c)

    def _add_hole(self, dia, through, pos, d: Dims, c: str, depth=None) -> bool:
        # 从上一段暂存下来的"分度圆/均布"参数, 归到这一段新建的孔上
        if getattr(self, 'pending_pcd', None) and not d.pcd:
            d.pcd = self.pending_pcd
            self.pending_pcd = None
        if getattr(self, 'pending_count', None) and not d.count:
            d.count = self.pending_count
            self.pending_count = None
            if pos == 'center':
                pos = 'pcd'
        params = {'diameter': dia, 'through': through, 'axis': (detect_axis(c) or 'Z'),
                  'pos': pos, 'segments': 24}
        if not through:
            params['depth'] = float(depth if depth is not None else (d.depth or 5.0))
        if pos == 'coord' and d.xy:
            params['x'], params['y'] = d.xy
        if pos == 'pcd':
            params['count'] = int(d.count or 4)
            # 「分度圆Ø100 上均布8个Ø12」常被切成两段: "分度圆Ø100上" 和 "均布8个Ø12…",
            # 前一段的修饰会先被处理, 所以把没归属的分度圆暂存下来, 给**下一个**孔用
            # (早先会盖到上一个中心孔上, 于是孔阵的分度圆全成了默认 60)。
            params['pcd'] = d.pcd or getattr(self, 'pending_pcd', None) or 60.0
            if getattr(self, 'pending_pcd', None):
                self.pending_pcd = None
        if pos == 'corners':
            params['margin'] = d.margin if d.margin is not None else (d.spacing or 0.0)
        f = Feature('hole', params, CUT)
        # U1：孔的直径/数量/分度圆/内缩 都要能追到出处
        self._attach_dim_prov(f, d, {'diameter': ('inner_diameter', 'diameter', 'pair', 'triple'),
                                     'depth': ('depth',),
                                     'count': ('count',),
                                     'pcd': ('pcd',),
                                     'margin': ('margin', 'spacing'),
                                     'x': ('xy',), 'y': ('xy',)})
        self.add(f, '孔 ⌀%s %s%s' % (
            _fmt(dia),
            {'center': '@中心', 'corners': '×4 四角',
             'coord': '@(%s,%s)' % (_fmt(params.get('x', 0)), _fmt(params.get('y', 0))),
             'pcd': '×%d 均布' % int(d.count or 4)}[pos],
            '' if through else ' 盲孔深%s' % _fmt(params.get('depth'))))
        return True

    def make_pocket(self, c, d: Dims) -> bool:
        if not self.res.doc.features:
            return False
        # 「顶面中心Ø50 深22 的圆形凹槽」「圆形沉台」是**圆的**, 该按圆形盲切做,
        # 不能按方槽(早先一律切方槽, 实测 B05 的圆凹槽被切成了 20×20 的方坑)。
        if re.search(r'(圆形|圆的|圆头|圆凹|圆沉)', c) and (d.diameter or d.radius):
            dia = d.diameter or (d.radius * 2 if d.radius else None)
            depth = d.depth or (d.height or 5.0)
            if dia:
                f = Feature('hole', {'diameter': dia, 'through': False, 'depth': depth,
                                     'axis': detect_axis(c) or 'Z', 'pos': 'center',
                                     'segments': 48}, CUT)
                self.add(f, '圆形凹槽 ⌀%s 深 %s mm' % (_fmt(dia), _fmt(depth)))
                return True
        l = d.length or d.pair[0] if d.pair else (d.length or 20.0)
        w = d.width or (d.pair[1] if d.pair else 20.0)
        depth = d.depth or (d.height or 5.0)
        # 「铣两个 60×40 深10 的槽」: 个数要真的展开成 N 个切除。描述没给间距时
        # 按**沿长度方向等距、间距 1.5×槽长、整体居中**排布(与用户确认过的口径),
        # 并把用的间距写进说明, 不猜得无声无息。
        n = int(d.count or 1)
        if n <= 1 and getattr(self, 'pending_count', None):
            n = int(self.pending_count)          # 上一条碎段留下的"×N"
            self.pending_count = None
        if n > 1:
            pitch = float(l) * 1.5
            total = pitch * (n - 1)
            x0 = -total / 2.0
            for i in range(n):
                self.add(Feature('pocket', {'length': l, 'width': w, 'depth': depth,
                                            'corner': 0.0, 'x': x0 + i * pitch,
                                            'axis': detect_axis(c) or 'Z'}, CUT),
                         '凹槽 %d/%d %s×%s 深 %s (间距 %s, 居中)'
                         % (i + 1, n, _fmt(l), _fmt(w), _fmt(depth), _fmt(pitch)))
            self.res.warnings.append(
                '「%s」没写槽间距, 已按"沿长度等距、间距 1.5×槽长、整体居中"排布(间距 %s mm)，'
                '如需精确位置请在描述里给间距或坐标。' % (c, _fmt(pitch)))
            return True
        f = Feature('pocket', {'length': l, 'width': w, 'depth': depth,
                               'corner': 0.0, 'axis': detect_axis(c) or 'Z'}, CUT)
        self.add(f, '凹槽 %s×%s 深 %s mm' % (_fmt(l), _fmt(w), _fmt(depth)))
        return True

    # ---------------- 修饰 ----------------
    def try_corner(self, c, d: Dims) -> bool:
        is_fillet = _any(c, KW_FILLET)
        is_chamfer = _any(c, KW_CHAMFER)
        m = re.search(r'(?<![A-Za-z])[Rr]\s*(?:角)?\s*(?:为|是|=|:)?\s*(\d+(?:\.\d+)?)', c)
        if not is_fillet and not is_chamfer:
            if not (m and not _any(c, KW_CYL + KW_SPHERE + KW_BOX + KW_CONE)):
                return False
        base = _any(c, KW_BOX) or _any(c, KW_CYL) or _any(c, KW_GEAR)
        if base and self.last() is not None:
            v = d.corner
            if v is None and m:
                v = float(m.group(1))
            if v is None:
                return False
            f = self.last()
            f.params['corner'] = v
            f.params['corner_mode'] = 'chamfer' if is_chamfer else 'fillet'
            self.res.steps.append('修饰: %s %s mm' % ('倒角' if is_chamfer else '圆角', _fmt(v)))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        f = self.last()
        if f is None:
            return False
        if _any(c, KW_BOX) or f.kind in ('box', 'gear', 'prism'):
            v = d.corner if d.corner is not None else (float(m.group(1)) if m else None)
            if v is None:
                return False
            f.params['corner'] = v
            f.params['corner_mode'] = 'chamfer' if is_chamfer else 'fillet'
            self.res.steps.append('修饰: %s %s mm' % ('倒角' if is_chamfer else '圆角', _fmt(v)))
            self.res.recognized += 1
            self.res.doc.invalidate()
            return True
        self.res.warnings.append('圆角/倒角目前仅支持长方体、齿轮与轮廓拉伸特征, 已忽略。')
        return False

    def try_pattern(self, c, d: Dims) -> bool:
        if not self.res.doc.features:
            return False
        count = d.count or 4
        circ = ('圆周' in c or '环形' in c or '一圈' in c or '围绕' in c or '绕' in c)
        axis = detect_axis(c) or 'Z'
        # 阵列必须继承源特征的操作: 阵列"孔"仍然是切除, 阵列"叶片"才是合并
        src = self.last()
        src_op = src.op if src is not None else ADD
        # 只有放样体(叶片)这种互相不重叠的薄壳才用组合模式提速
        compound = bool(src is not None and src.kind == 'loft')
        if circ:
            params = {'mode': 'circular', 'count': count, 'axis': axis,
                      'angle': d.angle or 360.0,
                      'full': (d.angle is None or abs((d.angle or 360) - 360) < 1e-6),
                      'center': (0.0, 0.0, 0.0), 'compound': compound}
            desc = '圆周阵列 ×%d' % count
        else:
            step = d.spacing or 20.0
            vec = {'X': (step, 0.0, 0.0), 'Y': (0.0, step, 0.0), 'Z': (0.0, 0.0, step)}[axis]
            params = {'mode': 'linear', 'count': count, 'axis': axis, 'step': vec,
                      'compound': compound}
            desc = '线性阵列 ×%d, 沿 %s 方向间距 %s mm' % (count, axis, _fmt(step))
        self.add(Feature('pattern', params, src_op), desc)
        return True

    # ---------------- 模板零件 ----------------
    def try_template(self, c, d: Dims, full: str) -> bool:
        low = c.lower()
        name, fn = None, None
        if '整体叶盘' in c or '叶盘' in c or '叶轮' in c or 'blisk' in low:
            name, fn = '叶盘', self.tpl_blisk
        elif '叶片' in c or 'blade' in low:
            name, fn = '叶片', self.tpl_blade
        elif '法兰' in c or 'flange' in low:
            name, fn = '法兰', self.tpl_flange
        elif '阶梯轴' in c or '台阶轴' in c:
            name, fn = '阶梯轴', self.tpl_shaft
        elif '套筒' in c or '圆管' in c or '钢管' in c:
            name, fn = '套筒/圆管', self.tpl_tube
        if fn is None:
            return False
        ok = fn(c, d)
        if ok:
            self.template_used = name
        return ok

    def tpl_blisk(self, c, d: Dims) -> bool:
        R = (d.diameter or 200.0) / 2.0
        t = d.height or 30.0
        n = int(d.blades or d.teeth or d.count or 20)
        span = d.span or d.length or (R * 0.35)
        if span > R:
            span = R * 0.35
        self.add(Feature('cylinder', {'radius': R, 'height': t, 'center': (0, 0, 0),
                                      'base': False, 'axis': 'Z', 'segments': 48}),
                 '轮盘: 圆柱 ⌀%s × %s mm' % (_fmt(R * 2), _fmt(t)))
        blade = Feature('loft', {'span': span, 'root_chord': max(4.0, t * 0.85),
                                 'tip_chord': max(2.0, t * 0.5), 'thickness': 0.10,
                                 'camber': 0.06, 'twist': 22.0, 'sweep': 3.0,
                                 'sections': 6, 'points': 22, 'compound': True,
                                 'rotate': ('Y', 90.0),
                                 'translate': (R * 0.90, 0.0, 0.0)})
        self.add(blade, '叶片: 放样体, 展长 %s mm' % _fmt(span))
        self.add(Feature('pattern', {'mode': 'circular', 'count': n, 'axis': 'Z',
                                     'angle': 360.0, 'full': True,
                                     'center': (0.0, 0.0, 0.0), 'compound': True}),
                 '叶盘: 叶片圆周阵列 ×%d' % n)
        self.res.used_templates.append('叶盘')
        return True

    def tpl_blade(self, c, d: Dims) -> bool:
        span = d.height or d.length or (d.raw[-1] if d.raw else 80.0)
        chord = d.pair[0] if d.pair else (d.width or 40.0)
        tip = d.pair[1] if d.pair else chord * 0.5
        self.add(Feature('loft', {'span': span, 'root_chord': chord, 'tip_chord': tip,
                                  'thickness': 0.12, 'camber': 0.05, 'twist': 18.0,
                                  'sweep': 0.0, 'sections': 8, 'points': 26}),
                 '叶片: 放样体 展长 %s mm, 弦长 %s→%s mm' % (_fmt(span), _fmt(chord), _fmt(tip)))
        self.res.used_templates.append('叶片')
        return True

    def tpl_flange(self, c, d: Dims) -> bool:
        D = d.diameter or 120.0
        t = d.height or 20.0
        hole_d = d.inner_diameter
        self.add(Feature('cylinder', {'radius': D / 2.0, 'height': t, 'center': (0, 0, 0),
                                      'base': False, 'axis': 'Z', 'segments': 64}),
                 '法兰盘体: 圆柱 ⌀%s × %s mm' % (_fmt(D), _fmt(t)))
        if hole_d:
            self.add(Feature('hole', {'diameter': hole_d, 'through': True, 'pos': 'center',
                                      'axis': 'Z', 'segments': 24}, CUT),
                     '中心孔 ⌀%s' % _fmt(hole_d))
        n = d.count
        bolt_d = d.radius * 2 if d.radius else None
        if n and n >= 2:
            bd = bolt_d or max(6.0, D * 0.08)
            pcd = d.pcd or (D + (hole_d or D * 0.3)) / 2.0
            self.add(Feature('hole', {'diameter': bd, 'through': True, 'pos': 'pcd',
                                      'count': int(n), 'pcd': pcd, 'axis': 'Z',
                                      'segments': 24}, CUT),
                     '螺栓孔: ⌀%s ×%d, 分度圆 ⌀%s' % (_fmt(bd), int(n), _fmt(pcd)))
        self.res.used_templates.append('法兰')
        return True

    def tpl_shaft(self, c, d: Dims) -> bool:
        t = c.replace('×', 'x').replace('*', 'x')
        # ① 图纸写法:「Ø60×40, Ø48×60, Ø36×50」= 每段 直径×长度
        pairs = [(float(a), float(b)) for a, b in
                 re.findall(r'[⌀Ø]\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)', t)]
        if not pairs:
            # ② 口语写法:「直径40 长60, 直径30 长50」
            pairs = re.findall(r'(?:直径|⌀)\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:长度|长)\s*(\d+(?:\.\d+)?)', t)
        if not pairs:
            dia_all = [float(x) for x in re.findall(r'(?:直径|⌀)\s*(\d+(?:\.\d+)?)', t)]
            len_all = [float(x) for x in re.findall(r'(?:长度|长)\s*(\d+(?:\.\d+)?)', t)]
            pairs = list(zip(dia_all, len_all))
        if not pairs and d.triple:
            pairs = [(d.triple[0], d.triple[1])]
        if not pairs:
            pairs = [(30.0, 50.0), (20.0, 40.0), (30.0, 30.0)]
        z = 0.0
        for i, (dia, ln) in enumerate(pairs):
            dia, ln = float(dia), float(ln)
            self.add(Feature('cylinder', {'radius': dia / 2.0, 'height': ln,
                                          'center': (0.0, 0.0, z), 'base': True,
                                          'axis': 'Z', 'segments': 48}),
                     '轴段%d: ⌀%s × %s mm' % (i + 1, _fmt(dia), _fmt(ln)))
            z += ln
        self.res.used_templates.append('阶梯轴')
        return True

    def tpl_tube(self, c, d: Dims) -> bool:
        D = d.diameter or 60.0
        t = d.inner_diameter or (d.radius * 2 if d.radius else D * 0.6)
        L = d.height or d.length or 80.0
        self.add(Feature('cylinder', {'radius': D / 2.0, 'height': L, 'center': (0, 0, 0),
                                      'base': False, 'axis': 'Z', 'segments': 64}),
                 '管体: 圆柱 ⌀%s × %s mm' % (_fmt(D), _fmt(L)))
        self.add(Feature('hole', {'diameter': t, 'through': True, 'pos': 'center',
                                  'axis': 'Z', 'segments': 32}, CUT),
                 '内孔 ⌀%s' % _fmt(t))
        self.res.used_templates.append('套筒/圆管')
        return True


def _circle_profile(r, n=48):
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def _rect_profile(l, w, corner=0.0, mode='fillet'):
    from ai_modeler.core import kernel as K
    return K.profile_rect(l, w, corner, mode)


# --------------------------------------------------------------------------
# 便捷入口
# --------------------------------------------------------------------------


def parse(text: str, doc: Optional[Document] = None) -> ParseResult:
    return Parser().parse(text, doc)


EXAMPLES = [
    '画一个 100×60×20 的长方体',
    '100×20 毫米的矩形草图, 然后拉伸 20 毫米',
    '画一个 120×80×15 的长方体, 四角圆角 R8, 在中心打一个直径 20 的通孔',
    '直径 60 高 120 的圆柱, 中心打一个直径 20 的通孔',
    '底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12',
    '法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120',
    '阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40',
    '叶盘 外径 220 厚 30 叶片数 24 叶展 45',
    '齿轮 模数 3 齿数 24 厚 20, 中心孔直径 20',
    '套筒 外径 80 内径 50 长 100',
]
