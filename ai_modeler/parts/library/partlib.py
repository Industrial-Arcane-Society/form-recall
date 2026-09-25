# -*- coding: utf-8 -*-
r"""汽车基础零件库 —— 让程序"学会"标准零件的参数与形状。
知识来源: kb\auto_parts.json
          (由 tools\build_kb.py 从「嘉立创FA机械设计手册」 https://www.jlc-jdgf.com/mcbook/ 学习而来)

每个零件类别登记: 别名 / 参数中文名 / 参数英文名 / 建模方式 / 外形尺寸算法。
自然语言解析器遇到这些零件名时, 直接按这里的定义建模, 命中率远高于通用规则。"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import math
import os
import re
from typing import Dict, List, Optional

from ai_modeler.core import kernel as K
from ai_modeler.core.model.docmodel import ADD, CUT, Feature
from ai_modeler.parts import reallib as RL
from ai_modeler.parts import realparts as RP

ROOT = str(PROJECT_ROOT)
KB_PATH = os.path.join(ROOT, 'kb', 'auto_parts.json')

# ---------------------------------------------------------------------------
# 零件定义: 名字 -> 参数别名 / 建模函数 / 外形尺寸
# ---------------------------------------------------------------------------
# params: 英文参数名 -> 中文别名列表 (解析时按这些词找数)
# build : (p, Feature, ADD, CUT) -> 特征列表
# bbox  : (p) -> (X, Y, Z) 期望外形尺寸; None 表示不校验该项
def _ring(p):
    """圆环类零件: 外径 D 内径 d 厚度 B。
    厚度键名各零件不统一 (轴承 B / 蝶形弹簧 h / 挡圈 s), 这里统一兜底,
    否则 KeyError 会被上层吞掉, 零件直接"消失"或退化成通用圆柱。"""
    tk = p.get('b', p.get('B', p.get('h', p.get('s', p.get('T', p.get('H',
         p.get('W', p.get('m', 3.0))))))))
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': tk,
                                 'center': (0, 0, 0), 'base': False,
                                 'segments': 64}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 48}, CUT)] if p.get('d') else [])


def _disc(p):
    """盘类: 外径 D + 厚度(制动盘用 T, 法兰用 b), 中心孔 d,
       若给 K(分度圆)+n(孔数) 则加均布螺栓孔。"""
    tk = 'T' if 'T' in p else ('b' if 'b' in p else 'h')
    fs = [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p[tk],
                               'center': (0, 0, 0), 'base': False, 'segments': 64})]
    if p.get('d'):
        fs.append(Feature('hole', {'diameter': p['d'], 'through': True,
                                   'pos': 'center', 'segments': 48}, CUT))
    if p.get('K') and p.get('n'):
        fs.append(Feature('hole', {'diameter': p.get('dh', 10.0), 'through': True,
                                   'pos': 'pcd', 'count': int(p['n']), 'pcd': p['K'],
                                   'segments': 24}, CUT))
    return fs


def _flange(p):
    return _disc(p)


def _gear(p):
    return [Feature('gear', {'teeth': int(p['z']), 'module': p['m'],
                             'height': p['b'], 'center': (0, 0, 0), 'base': False})]


def _shaft(p):
    return [Feature('cylinder', {'radius': p['d'] / 2.0, 'height': p['L'],
                                 'center': (0, 0, 0), 'base': False})]


def _washer(p):
    tk = p.get('h', p.get('s', p.get('B', 2.0)))
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': tk,
                                 'center': (0, 0, 0), 'base': False, 'segments': 64}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 48}, CUT)] if p.get('d') else [])


def _oring(p):
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p.get('W', 2.0),
                                 'center': (0, 0, 0), 'base': False, 'segments': 64}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 48}, CUT)] if p.get('d') else [])


def _bushing(p):
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p['L'],
                                 'center': (0, 0, 0), 'base': False, 'segments': 64}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 48}, CUT)] if p.get('d') else [])


def _coupling(p):
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p['L'],
                                 'center': (0, 0, 0), 'base': False, 'segments': 48}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 32}, CUT)] if p.get('d') else [])


def _bolt(p):
    """六角头螺栓: 六棱柱头 + 光杆。

    参数口径(与用户确认过):
      · 描述给了「对边 s / 头高 k」就**用描述值**;
      · 没给才按 GB/T 5782 比例推定 s≈1.5d、k≈0.7d, 并在提示里写明来源(不静默);
      · 螺纹不做真实牙型 —— 真实牙型在 CSG 内核里会指数级增加面片, 工程上通常用
        "光杆 + 装饰螺纹/注释"表示; CATIA 侧同样只建光杆(可自行加装饰螺纹)。
    """
    d = float(p['d'])
    L = float(p['L'])
    s = float(p.get('s') or round(1.5 * d, 1))
    k = float(p.get('k') or round(0.7 * d, 1))
    return [Feature('hex_bolt', {'shank_d': d, 'length': L, 'across_flats': s,
                                 'head_h': k, 'center': (0, 0, 0),
                                 'segments': 48})]


def _nut(p):
    return [Feature('cylinder', {'radius': p.get('s', p['D']) / 2.0,
                                 'height': p.get('m', p.get('h', 8.0)),
                                 'center': (0, 0, 0), 'base': False, 'segments': 6}),
            Feature('hole', {'diameter': p['D'] if 'D' in p else p['d'],
                             'through': True, 'pos': 'center', 'segments': 32}, CUT)]


# 类别 -> 定义
PARTS: Dict[str, dict] = {
    '深沟球轴承': dict(
        aliases=['深沟球轴承', '球轴承', '深沟球', '轴承'],
        params={'d': ['内径', '轴径', '孔径'], 'D': ['外径'], 'B': ['宽度', '厚', '宽'],
                'code': ['代号', '轴承代号']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['B']), note='外圈简化模型: 圆柱 D×B 挖通孔 d'),
    '圆锥滚子轴承': dict(
        aliases=['圆锥滚子轴承', '锥轴承'],
        params={'d': ['内径'], 'D': ['外径'], 'T': ['装配高', '总宽', 'T']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['T']), note='简化为圆环'),
    '推力球轴承': dict(
        aliases=['推力球轴承', '推力轴承', '平面轴承'],
        params={'d': ['内径'], 'D': ['外径'], 'H': ['高度', '厚']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['H']), note='简化为圆环'),
    '直齿圆柱齿轮': dict(
        aliases=['直齿圆柱齿轮', '直齿轮', '圆柱齿轮', '齿轮'],
        params={'m': ['模数'], 'z': ['齿数'], 'b': ['齿宽', '宽度', '厚']},
        build=_gear, bbox=lambda p: ((p['m'] * (p['z'] + 2)) * 1.0, (p['m'] * (p['z'] + 2)) * 1.0, p['b']), note='按模数齿数生成真实齿形'),
    '同步带轮': dict(
        # 注意: 这里有 "带轮" 这个短别名, find_part 会用它兜住只写"带轮"的情况。
        # 同步带轮走标准节距齿形, V 带轮走 GB/T 10412 轮槽剖面。
        aliases=['同步带轮', '同步轮', '圆弧齿带轮', '周节制带轮', '带轮'],
        params={'d': ['内径', '轴径', '孔径'], 'D': ['节圆直径', '外径', '直径'],
                'B': ['宽度', '厚', '轮宽'], 'z': ['齿数'], 't': ['齿型', '型号']},
        # 't' 在这个零件里是**齿型代号**（不走参数表抽数值，走 codes 匹配 XL/T5/5M）
        code_keys=('t',),
        # 用 lambda 延迟查找: 函数定义在本文件靠后的位置
        build=lambda p: globals()['_timing_pulley'](p),
        bbox=lambda p: (p['D'], p['D'], p['B']),
        note='按标准节距生成同步带轮齿形'),
    '链轮': dict(
        aliases=['链轮'],
        params={'d': ['内径', '轴径'], 'D': ['外径'], 'B': ['宽度', '厚']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['B']), note='简化为圆环'),
    '制动盘': dict(
        aliases=['制动盘', '刹车盘', '刹车碟', '制动碟'],
        params={'D': ['外径', '直径'], 'T': ['厚度', '厚'], 'd': ['中心孔', '内径'], 'K': ['分度圆', '节圆', 'PCD'], 'n': ['孔数', '螺栓孔数'], 'dh': ['螺栓孔直径', '孔径']},
        build=_disc, bbox=lambda p: (p['D'], p['D'], p['T']), note='盘体 + 中心孔 + 均布螺栓孔'),
    '法兰': dict(
        aliases=['法兰', '法兰盘', '端盖', '闷盖', '透盖', '盖盘', '轴承端盖'],
        params={'D': ['外径', '直径'], 'd': ['内径', '孔径'], 'b': ['厚度', '厚'], 'K': ['分度圆', '节圆', 'PCD'], 'n': ['孔数', '螺栓孔数'], 'dh': ['螺栓孔直径', '孔径']},
        build=_flange, bbox=lambda p: (p['D'], p['D'], p['b']), note='盘体 + 中心孔 + 均布螺栓孔'),
    '垫圈': dict(
        aliases=['垫圈', '平垫', '垫片'],
        params={'d': ['内径'], 'D': ['外径'], 'h': ['厚度', '厚']},
        build=_washer, bbox=lambda p: (p['D'], p['D'], p['h']), note='圆环'),
    'O形圈': dict(
        aliases=['O形圈', 'o形圈', '密封圈', 'O型圈'],
        params={'d': ['内径'], 'D': ['外径'], 'W': ['线径', '截面', '厚']},
        build=_oring, bbox=lambda p: (p['D'], p['D'], p.get('W', 2.0)), note='圆环(未做圆截面)'),
    '油封': dict(
        aliases=['油封', '骨架油封'],
        params={'d': ['内径'], 'D': ['外径'], 'b': ['宽度', '厚']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['b']), note='简化为圆环'),
    '轴套': dict(
        aliases=['轴套', '衬套', '套筒', '隔套', '缸筒', '缸套', '油缸筒', '液压缸筒',
                 '空心轴', '空心轴套', '管轴', '圆管', '钢管', '导管'],
        params={'d': ['内径'], 'D': ['外径'], 'L': ['长度', '长', '高']},
        build=_bushing, bbox=lambda p: (p['D'], p['D'], p['L']), note='圆柱挖通孔'),
    '联轴器': dict(
        aliases=['联轴器'],
        params={'d': ['内径', '轴孔直径'], 'D': ['外径'], 'L': ['长度', '长']},
        build=_coupling, bbox=lambda p: (p['D'], p['D'], p['L']), note='简化为套筒'),
    '螺栓': dict(
        aliases=['螺栓', '螺钉', '螺丝', '六角头螺栓', '六角螺栓'],
        params={'d': ['螺纹规格', '公称直径', '直径', 'M'], 'L': ['长度', '长'],
                's': ['对边宽度', '对边', 's'], 'k': ['头高', '头部高度', 'k']},
        build=_bolt,
        # 外形: 对边 s × 对边 s × (杆长 + 头高)。对边没给就按 GB/T 5782 的 s≈1.5d
        bbox=lambda p: (p.get('s') or 1.5 * p['d'], p.get('s') or 1.5 * p['d'],
                        p['L'] + (p.get('k') or 0.7 * p['d'])),
        note='六棱柱头 + 光杆(螺纹按装饰表示)'),
    '螺母': dict(
        aliases=['螺母', '螺帽'],
        params={'D': ['螺纹规格', '公称直径', '直径', 'M'], 's': ['对边宽度', '对边', 's'], 'm': ['厚度', '厚', 'm']},
        build=_nut, bbox=lambda p: (p.get('s', p['D'] * 1.6), p.get('s', p['D'] * 1.6), p.get('m', p['D'] * 0.8)), note='六角柱挖通孔'),
    '轴': dict(
        aliases=['光轴', '圆柱轴', '实心轴', '轴'],
        params={'d': ['直径', '轴径'], 'L': ['长度', '长']},
        build=_shaft, bbox=lambda p: (p['d'], p['d'], p['L']), note='实心圆柱'),
}




# ---------------------------------------------------------------------------
# 中等难度零件 (来自机械设计手册的开源参数 + 参数化建模)
# ---------------------------------------------------------------------------

def _prof_bbox(prof, h):
    xs = [p[0] for p in prof]
    ys = [p[1] for p in prof]
    return (max(xs) - min(xs), max(ys) - min(ys), h)


def _spring_params(p, taper=0.0, taper_start=0.0):
    """算弹簧的螺旋参数 (供 _spring / _taper_spring 共用)。
    两处校正:
    1) make_helix 的轴向范围是 [-wire_r, height+wire_r], 直接用 H 会多出两个线径;
       所以螺旋段缩掉 2*wire_r 并把起点抬到 wire_r, 总高才正好是 H;
    2) 钢丝截面是 wire_seg 边的多边形(内接), 按内接半径摆的话外径会偏小,
       这里按外接半径定义, 保证最大外径正好落在 D 上。"""
    # 截面边数越多, 内接/外接半径差越小, 外径越准 (16 边时最大偏差 3.8%)
    wire_seg = 24
    wire_r = max(0.2, p.get('d', 2.0) / 2.0) / math.cos(math.pi / wire_seg)
    H = float(p['H'])
    return {'radius': max(0.5, p['D'] / 2.0 - wire_r),
            'wire_r': wire_r,
            'height': max(1.0, H - 2.0 * wire_r),
            'turns': p.get('n', 8.0),
            'center': (0, 0, -H / 2.0 + wire_r),
            'seg_per_turn': 16, 'wire_seg': wire_seg, 'taper': taper,
            'taper_start': taper_start}


def _spring(p):
    """螺旋弹簧。
    make_helix 的轴向范围是 [-wire_r, height + wire_r], 直接用 H 会让总高多出
    两个线径。这里把螺旋段缩短 2*wire_r, 再把起点抬一个 wire_r, 总高才正好等于 H。"""
    return [Feature('helix', _spring_params(p, p.get('taper', 0.0)))]


def _taper_spring(p):
    """锥形弹簧: 螺旋半径沿轴线线性收缩, 轴向补偿同 _spring。"""
    # taper_start=1/圈数: 第一圈保持圆柱, 之后才收缩 —— 这样"最大外径"在整
    # 第一圈上都能量到, X / Y 两个方向的包围盒才都等于标称外径。
    return [Feature('helix', _spring_params(p, 0.5, 1.0 / max(1.0, p.get('n', 8.0))))]


def _spline_shaft(p):
    z = int(p.get('z', 8))
    # 齿顶圆以"大径"为准; 输入把大小径写反时钳住小径, 否则齿会戳到大径外面
    ro = p['D'] / 2.0
    ri = min(p['d'] / 2.0, ro * 0.92)
    prof = K.profile_spline(ri, max(0.5, ro - ri), z)
    return [Feature('prism', {'profile': [[round(a, 4), round(b, 4)] for (a, b) in prof],
                              'height': p['L'], 'center': (0, 0, 0), 'axis': 'Z'})]


def _cam(p):
    base_r = p['d'] / 2.0
    lift = max(0.5, (p['D'] - p['d']) / 2.0)
    prof = K.profile_cam(base_r, lift, p.get('angle', 120.0))
    return [Feature('prism', {'profile': [[round(a, 4), round(b, 4)] for (a, b) in prof],
                              'height': p['b'], 'center': (0, 0, 0), 'axis': 'Z'})]


def _handwheel(p):
    return [Feature('spoked_wheel', {'outer_r': p['D'] / 2.0,
                                     'rim_w': max(3.0, p['D'] * 0.06),
                                     'hub_r': max(5.0, p.get('d', p['D'] * 0.2) / 2.0),
                                     'height': p['H'],
                                     'spokes': int(p.get('z', 5)),
                                     'center': (0, 0, 0)})]


def _pulley_common(p, kind: str):
    """带轮的通用参数整理：内径/宽度/槽数/槽型。

    除参数名外还认两种中文口径：
      · 「双槽带轮」「四槽V带轮」—— 槽数写在零件名前（双/三/四/五/六…）；
      · 「3槽」—— 阿拉伯数字直接跟"槽"。
    """
    bore = p.get('d') or p.get('d0') or 0.0
    width = p.get('B') or p.get('L') or p.get('b') or 0.0
    n = p.get('n') or p.get('z') or 0
    gtype = p.get('t') or p.get('gt')
    if not n:
        n = _cn_groove_count(p.get('__text') or '')
    return float(bore or 0.0), float(width or 0.0), int(n or 0), (str(gtype) if gtype else None)


_CN_DIGITS = {'一': 1, '两': 2, '二': 2, '双': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _cn_groove_count(text: str, claimed=()):
    """从「双槽」「四槽」「3槽」「3个槽」里读出槽数。读不到返回 0。

    claimed 是"已经被别的参数吃掉"的字符串（如宽度读到的 "宽度30"）：
    先把它们抠掉再找，否则「宽度303个槽」这种没空格的连写会把 303 当成槽数。
    """
    if not text:
        return 0
    t = text
    for c in claimed or ():
        if c:
            t = t.replace(c, ' ')
    # 前面不能是数字、后面不能紧跟数字: 「宽度402个槽」里的 "0" 不能算作槽数的开头
    m = re.search(r'(?:^|[^0-9])([0-9]{1,2})(?!\d)\s*(?:个)?\s*槽', t)
    if m:
        return int(m.group(1))
    m = re.search(r'([一二两双三四五六七八九十])\s*(?:个)?\s*槽', t)
    if m:
        return _CN_DIGITS.get(m.group(1), 0)
    return 0


def _vbelt(p):
    """V 带轮 / 多槽带轮：按 GB/T 10412—2002 的轮槽剖面生成真正的 V 形槽。

    轮槽尺寸、槽角、外径、轮缘宽度全部取自国家标准（见 ai_modeler/std_pulley.py）：
    外径 da = 基准直径 dd + 2·ha，槽在基准线上下分别深 ha / hf，槽间距 e 决定轮缘最小宽度。
    """
    from ai_modeler.parts import std_pulley
    bore, width, n, gtype = _pulley_common(p, 'v')
    dd = next((x for x in (p.get('D'), p.get('dd'), p.get('dp')) if x), 100.0)
    # 参数表路径下 width 可能已被标准层放大过；raw_width 让提示语显示用户真正写的那个数
    _raw = p.get('__user_width')
    s = std_pulley.spec(float(dd), int(n or 3), gtype, width or None, bore_d=bore,
                        raw_width=_raw)
    fs = [Feature('vbelt_pulley', {
        'pitch_d': s['pitch_d'], 'grooves': s['grooves'], 'height': s['width'],
        'groove_type': s['gtype'], 'outer_d': s['outer_d'],
        'bore_d': s['bore_d'], 'center': (0, 0, 0),
        # 把"提示词 → 特征"的对照表随特征带上，解析器会逐条显示在"步骤/日志"里
        'explain': std_pulley.explain(s),
        'std_warnings': list(s.get('warnings') or [])},
        ADD, name='%s型V带轮 %d槽' % (s['gtype'], s['grooves']))]
    return fs


def _timing_pulley(p):
    """同步带轮：按周节制（GB/T 11361）/ 圆弧齿（JB/T 7512.2）的节距生成真实轮齿。"""
    from ai_modeler.parts import std_pulley as SP
    import math as _m
    bore, width, _n, gtype = _pulley_common(p, 't')
    dd = float(p.get('D') or p.get('dp') or p.get('dd') or 80.0)
    tooth = SP.timing_pitch(gtype or 'XL')
    z = int(p.get('z') or p.get('teeth') or 0) or max(10, int(round(dd * 3.14159 / tooth['pitch'])))
    if not width:
        width = SP.timing_width(gtype or 'XL')
    warn = []
    # ---- 齿距自洽性（U3）：齿的切向宽度是**常数**（= 标准齿顶宽），而齿距
    # p = π·dd/z 随直径变化。判据要取**齿顶圆处**的切向齿距：
    #     齿顶处的切向齿距 = 2π·r_tip / z
    # 若它小于齿顶宽，相邻齿在齿顶处必然互相重叠 —— 网格里齿形就是错的。
    # 实测这种轮子 10 齿能数出 20 个径向凸起、自相关估出 3/4/8/12 各种谐波值，
    # 任何"齿数测量"都不可能读对，所以必须明确告知用户输入自相矛盾。
    # ⚠️ 不要写成 `p/2 < tip/2`：那是拿节圆处的齿距去比齿顶宽，判据偏松。
    z_eff = max(6, z)
    r_tip_chk = dd / 2.0 + tooth['tooth_h'] * 0.45
    gap_tip = 2.0 * _m.pi * r_tip_chk / z_eff
    if gap_tip < tooth['tooth_tip']:
        min_dd = z_eff * tooth['tooth_tip'] / (2.0 * _m.pi) * 2.0 - 2.0 * tooth['tooth_h'] * 0.45
        warn.append(
            "%d 齿 %s 型同步带轮：齿顶处的切向齿距 %.2f mm 小于齿顶宽 %.2f mm，"
            "相邻齿会互相重叠（输入自相矛盾，齿形不可用）。按 %d 齿算节圆直径至少需 "
            "⌀%.2f mm；或按 ⌀%g mm 改用 %d 齿。"
            % (z, (gtype or 'XL').upper(), gap_tip, tooth['tooth_tip'],
               z, max(dd, min_dd), dd,
               max(6, int(_m.floor(2.0 * _m.pi * r_tip_chk / tooth['tooth_tip'])))))
    params = {
        'pitch_d': dd, 'teeth': z, 'height': float(width),
        'tooth_type': (gtype or 'XL').upper(), 'bore_d': float(bore or 0.0),
        'center': (0, 0, 0),
        # "提示词 → 生成特征"对照表，解析器会逐条写进步骤日志
        'explain': SP.explain_timing(dd, z, gtype)}
    if warn:
        params['std_warnings'] = warn
    return [Feature('timing_pulley', params, ADD,
                    name='%s型同步带轮 %d齿' % ((gtype or 'XL').upper(), z))]


def _worm(p):
    """蜗杆: 用螺旋扫掠近似 (分度圆直径 d, 外径 D, 长度 L)。"""
    d, D, L = float(p['d']), float(p['D']), float(p['L'])
    # 齿高不能超过轴向长度的 1/6, 否则短粗蜗杆的螺旋会鼓出两端
    wire_r = max(0.5, min((D - d) / 4.0, L / 6.0))
    thread_r = D / 2.0 - wire_r          # 齿顶正好落在 D/2
    turns = max(2.0, min(20.0, L / max(1.0, d * 0.8)))
    # make_helix 的轴向范围是 [-wire_r, height+wire_r], 圆柱是从 center 往 +Z 长,
    # 所以螺旋段要缩掉两端各 2*wire_r, 再把起点抬到 +wire_r, 整体才正好落在 [0, L]。
    th = max(1.0, L - 4.0 * wire_r)
    # 轴身与螺旋齿合成一个特征输出, 避免"圆柱 并 螺旋"这种极慢的布尔并集。
    return [Feature('worm', {'radius': min(d / 2.0, thread_r - wire_r),
                             'height': L,
                             'thread_r': thread_r,
                             'wire_r': wire_r,
                             'height2': th,
                             'turns': turns,
                             'segments': 48, 'seg_per_turn': 16, 'wire_seg': 8,
                             'center': (0.0, 0.0, -L / 2.0),
                             'center2': (0.0, 0.0, 2.0 * wire_r - L / 2.0)},
                ADD, name='蜗杆')]


def _brake_drum(p):
    """制动盘 外筒 + 端面法兰盘。"""
    R = p['D'] / 2.0
    r = p['d'] / 2.0
    return [Feature('cylinder', {'radius': R, 'height': p['H'],
                                 'center': (0, 0, 0), 'base': False, 'segments': 64}),
            Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                             'segments': 48}, CUT),
            Feature('hole', {'diameter': max(2.0, (R - r) * 0.5), 'through': True,
                             'pos': 'pcd', 'count': 5,
                             'pcd': (R + r) * 0.85, 'segments': 24}, CUT)]


def _bearing_seat(p):
    """轴承座(简化为带凸缘的环): 外径 D, 内孔 d, 高 H。"""
    return [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p['H'],
                                 'center': (0, 0, 0), 'base': False, 'segments': 64}),
            ] + ([Feature('hole', {'diameter': p['d'], 'through': True, 'pos': 'center',
                                   'segments': 48}, CUT)] if p.get('d') else [])


def _key(p):
    """平键：长方体。

    ⚠️ 这里的 box 参数用**局部轴**语义，不是通用 box 的"全局 X/Y/Z"语义：
        length->沿 X = 键宽 b、width->沿 Y = 键高 h、height->沿 Z = 键长 L。
    也就是"键躺在平面上、长度沿 Z"。这是既有约定，被两处测试钉住：
        tools/make_complex_testlist.py：平键 宽8 高7 长40 -> bbox (8, 7, 40)
        tools/bench_v2.py            ：平键 宽10 高8 长40 -> x=10 y=8 z=40
    不要为了"键名看起来更顺"去改映射 —— 那会把上面两处既有期望全部推翻。
    """
    return [Feature('box', {'length': p['b'], 'width': p['h'], 'height': p['L'],
                            'center': (0, 0, 0), 'base': False})]


MEDIUM_PARTS = {
    '螺旋弹簧': dict(
        aliases=['圆柱螺旋压缩弹簧', '压缩弹簧', '螺旋弹簧', '弹簧'],
        params={'d': ['线径', '材料直径', '丝径'], 'D': ['外径', '弹簧外径'], 'H': ['自由高度', '高度', '长'], 'n': ['圈数', '有效圈数']},
        build=_spring, bbox=lambda p: (p['D'], p['D'], p['H']), note='圆形截面沿螺旋线扫掠'),
    '锥形弹簧': dict(
        aliases=['锥形弹簧', '圆锥螺旋弹簧'],
        params={'d': ['线径', '材料直径'], 'D': ['外径'], 'H': ['高度', '自由高度'], 'n': ['圈数']},
        build=_taper_spring, bbox=lambda p: (p['D'], p['D'], p['H']), note='螺旋半径线性收缩'),
    '碟形弹簧': dict(
        aliases=['碟形弹簧', '蝶形弹簧'],
        params={'d': ['内径'], 'D': ['外径'], 'h': ['厚度', '厚']},
        build=_ring, bbox=lambda p: (p['D'], p['D'], p['h']), note='简化为圆环'),
    '花键轴': dict(
        aliases=['矩形花键', '渐开线花键', '花键轴', '花键'],
        params={'d': ['小径', '内径'], 'D': ['大径', '外径'], 'L': ['长度', '长'], 'z': ['齿数']},
        build=_spline_shaft, bbox=lambda p: (p['D'], p['D'], p['L']), note='真实花键齿形'),
    '盘形凸轮': dict(
        aliases=['盘形凸轮', '凸轮'],
        params={'d': ['基圆直径', '基圆'], 'D': ['外径', '最大直径'], 'b': ['厚度', '宽度', '厚']},
        build=_cam, bbox=lambda p: _prof_bbox(K.profile_cam(p['d'] / 2.0, max(0.5, (p['D'] - p['d']) / 2.0), p.get('angle', 120.0)), p['b']), note='基圆 + 简谐升程轮廓'),
    '辐条手轮': dict(
        aliases=['方向盘', '手轮', '辐条轮'],
        params={'d': ['内径', '孔径'], 'D': ['外径'], 'H': ['厚度', '厚'], 'z': ['辐条数', '轮辐数']},
        build=_handwheel, bbox=lambda p: (p['D'], p['D'], p['H']), note='外圈 + 轮毂 + N 根辐条'),
    '多槽带轮': dict(
        # 提示词别名: 用户写 "V带轮 / 皮带轮 / 多楔带轮 / 槽数3 的带轮" 都要命中这一条
        aliases=['多槽带轮', '多楔带轮', 'V带轮', 'V型带轮', '皮带轮', 'V带',
                 '槽轮', '塔轮', '窄V带轮'],
        params={'d': ['内径', '内孔', '轴径', '孔径'], 'D': ['基准直径', '节圆直径', '外径', '直径'],
                'B': ['宽度', '轮宽', '厚'], 'n': ['槽数', '槽', '双槽', '单槽', '三槽',
                                                '四槽', '五槽', '六槽', '八槽', '十槽'],
                't': ['槽型', '带型', '型号']},
        # 't' 在这个零件里是**槽型代号**（A/B/SPZ…，走 codes 匹配而非抽数值）
        code_keys=('t',),
        build=lambda p: globals()['_vbelt'](p),
        bbox=lambda p: (p['D'], p['D'], p['B']),
        note='按 GB/T 10412 轮槽剖面生成真实 V 形槽 + 轮毂 + 辐板'),
    '蜗杆': dict(
        aliases=['蜗杆'],
        params={'d': ['分度圆直径', '中径', '内径'], 'D': ['外径', '齿顶圆直径'], 'L': ['长度', '长']},
        build=_worm, bbox=lambda p: (p['D'], p['D'], p['L']), note='圆柱 + 螺旋螺纹近似'),
    '制动鼓': dict(
        aliases=['制动鼓', '刹车鼓'],
        params={'D': ['外径', '直径'], 'd': ['内径'], 'H': ['高度', '宽', '厚']},
        build=_brake_drum, bbox=lambda p: (p['D'], p['D'], p['H']), note='外筒 + 中心孔 + 5 个螺栓孔'),
    '轴承座': dict(
        aliases=['轴承座', '轴承套'],
        params={'d': ['内径', '孔径'], 'D': ['外径'], 'H': ['高度', '长度', '长']},
        build=_bearing_seat, bbox=lambda p: (p['D'], p['D'], p['H']), note='带内孔的套座'),
    '平键': dict(
        aliases=['平键', '键'],
        params={'b': ['宽度', '宽'], 'h': ['高度', '厚'], 'L': ['长度', '长']},
        build=_key, bbox=lambda p: (p['b'], p['h'], p['L']), note='长方体'),
    '离合器摩擦片': dict(
        aliases=['离合器摩擦片', '摩擦片', '离合器片'],
        params={'d': ['内径'], 'D': ['外径'], 'h': ['厚度', '厚']},
        build=_washer, bbox=lambda p: (p['D'], p['D'], p['h']), note='圆环片'),
    '挡圈': dict(
        aliases=['孔用弹性挡圈', '轴用弹性挡圈', '挡圈', '卡簧'],
        params={'d': ['内径', '孔径'], 'D': ['外径'], 's': ['厚度', '厚']},
        build=_washer, bbox=lambda p: (p['D'], p['D'], p['s']), note='开口圆环简化为整环'),
}

# ==========================================================================
# 高难度 / 极高难度汽车零件
#   参数与几何同样是"参数化定义直接算", 与自然语言解析器无关。
# ==========================================================================

def _crank_bbox(p):
    # 描述里显式写了总长/全长就照它走(用 'L' 作为"总长"的参数名, 见零件表)
    L, R = K.crankshaft_size(p['d'], p['dp'], p['S'], int(p.get('n', 4)),
                             p.get('L'))
    return (L, 2.0 * R, 2.0 * R)


def _conrod_bbox(p):
    L, D = K.conrod_size(p['L'], p['D'], p['d'])
    return (L, D, D)


def _rotor_bbox(p):
    return _prof_bbox(K.profile_rotor(p['R'], 48), p['H'])


def _helical_gear(p):
    return [Feature('helical_gear', {'teeth': int(p['z']), 'module': p['m'],
                                     'height': p['b'],
                                     'helix_deg': p.get('beta', 18.0),
                                     'bore_d': p.get('dh', 0.0),
                                     'center': (0, 0, 0), 'segments': 20})]


def _bevel_gear(p):
    return [Feature('bevel_gear', {'teeth': int(p['z']), 'module': p['m'],
                                   'height': p['b'],
                                   'cone_deg': p.get('delta', 45.0),
                                   'bore_d': p.get('dh', 0.0),
                                   'center': (0, 0, 0), 'segments': 12})]


def _crankshaft(p):
    return [Feature('crankshaft', {'main_d': p['d'], 'pin_d': p['dp'],
                                   'stroke': p['S'],
                                   'throws': int(p.get('n', 4)),
                                   'length': p.get('L'),
                                   'center': (0, 0, 0), 'segments': 32})]


def _camshaft(p):
    return [Feature('camshaft', {'base_d': p['d'], 'lift': p['h'],
                                 'lobes': int(p.get('n', 4)), 'length': p['L'],
                                 'journal_d': p.get('dj'),
                                 'center': (0, 0, 0), 'segments': 24})]


def _impeller(p):
    return [Feature('impeller', {'hub_d': p['d'], 'tip_d': p['D'],
                                 'height': p['H'], 'blades': int(p.get('n', 9)),
                                 'twist_deg': p.get('beta', 55.0),
                                 'center': (0, 0, 0), 'segments': 12})]


def _piston(p):
    return [Feature('piston', {'diameter': p['D'], 'height': p['H'],
                               'pin_d': p['d'], 'grooves': int(p.get('n', 3)),
                               'center': (0, 0, 0), 'segments': 48})]


def _conrod(p):
    # ⚠️ 'dh'/'dh2' 是可缺参数, 必须用 .get —— 直接 p['dh'] 会在用户没写大头孔径时
    # 抛 KeyError, 而调用方(nlparser)是 `except Exception: hit = None` 吞掉的,
    # 表现就是"零件库静默失效, 退回通用规则建出个圆柱"(实测 B28/B29 就是这样)。
    big_d = float(p.get('D', 48.0))
    small_d = float(p.get('d', 24.0))
    return [Feature('conrod', {'length': p['L'], 'big_d': big_d,
                               'big_bore': float(p.get('dh', big_d * 0.72)),
                               'small_d': small_d,
                               'small_bore': float(p.get('dh2', small_d * 0.72)),
                               'shank_w': float(p.get('thick', small_d * 0.8)),
                               'thick': p.get('thick'),
                               'center': (0, 0, 0), 'segments': 32})]


def _cross_joint(p):
    return [Feature('cross_joint', {'span': p['L'], 'journal_d': p['d'],
                                    'center': (0, 0, 0), 'segments': 24})]


def _scroll(p):
    return [Feature('scroll_disc', {'outer_d': p['D'], 'height': p['H'],
                                    'wall_t': p['t'], 'turns': p.get('n', 2.5),
                                    'center': (0, 0, 0), 'segments': 120})]


def _wankel(p):
    return [Feature('rotor', {'radius': p['R'], 'height': p['H'],
                              'bore_d': p.get('d', 0.0),
                              'center': (0, 0, 0), 'n': 48})]


def _flywheel(p):
    tk = 'h'
    fs = [Feature('cylinder', {'radius': p['D'] / 2.0, 'height': p[tk],
                               'center': (0, 0, 0), 'base': False, 'segments': 64})]
    if p.get('d'):
        fs.append(Feature('hole', {'diameter': p['d'], 'through': True,
                                   'pos': 'center', 'segments': 48}, CUT))
    if p.get('K') and p.get('n'):
        fs.append(Feature('hole', {'diameter': p.get('dh', 10.0), 'through': True,
                                   'pos': 'pcd', 'count': int(p['n']), 'pcd': p['K'],
                                   'segments': 24}, CUT))
    return fs


def _valve(p):
    r = p['d'] / 2.0
    R = p['D'] / 2.0
    return [Feature('cylinder', {'radius': r, 'height': p['L'],
                                 'center': (0, 0, 0), 'base': False, 'segments': 32}),
            Feature('cylinder', {'radius': R, 'height': max(1.0, p['L'] * 0.12),
                                 'center': (0, 0, 0), 'base': False, 'segments': 48})]


HARD_PARTS = {
    '斜齿圆柱齿轮': dict(
        aliases=['斜齿圆柱齿轮', '斜齿轮', '螺旋齿轮'],
        params={'m': ['模数'], 'z': ['齿数'], 'b': ['齿宽', '宽度', '厚度', '厚'], 'beta': ['螺旋角'], 'dh': ['中心孔', '内径', '孔径']},
        build=_helical_gear, bbox=lambda p: (p['m'] * (p['z'] + 2), p['m'] * (p['z'] + 2), p['b']), note='齿廓沿轴向扭转出螺旋角'),
    '直齿锥齿轮': dict(
        aliases=['直齿锥齿轮', '螺旋锥齿轮', '锥齿轮', '伞齿轮'],
        params={'m': ['模数'], 'z': ['齿数'], 'b': ['齿宽', '宽度', '厚度', '厚'], 'delta': ['节锥角', '锥角'], 'dh': ['中心孔', '内径', '孔径']},
        build=_bevel_gear, bbox=lambda p: (p['m'] * (p['z'] + 2), p['m'] * (p['z'] + 2), p['b']), note='齿廓沿节锥收缩'),
    '曲轴': dict(
        aliases=['发动机曲轴', '曲轴'],
        params={'d': ['主轴颈', '主轴颈直径'], 'dp': ['连杆颈', '连杆轴颈'], 'S': ['行程'],
                'n': ['拐数', '缸数'], 'L': ['总长', '全长', '长度']},
        build=_crankshaft, bbox=_crank_bbox, note='主轴颈 + 曲柄臂 + 偏心连杆颈'),
    '凸轮轴': dict(
        aliases=['凸轮轴'],
        params={'d': ['基圆直径', '基圆'], 'h': ['升程'], 'L': ['长度', '长'], 'n': ['凸轮数'], 'dj': ['轴颈']},
        build=_camshaft, bbox=lambda p: K.camshaft_size(p['d'], p['h'], int(p.get('n', 4)), p['L']), note='若干相位错开的凸轮'),
    '涡轮叶轮': dict(
        aliases=['涡轮叶轮', '压气机叶轮', '离心叶轮', '水泵叶轮', '叶轮'],
        params={'d': ['轮毂直径', '轮毂'], 'D': ['外径'], 'H': ['高度', '厚'], 'n': ['叶片数'], 'beta': ['扭转角']},
        build=_impeller, bbox=lambda p: (p['D'], p['D'], p['H']), note='后盘 + 扭曲叶片'),
    '活塞': dict(
        aliases=['发动机活塞', '活塞'],
        params={'D': ['外径', '直径'], 'H': ['高度', '高'], 'd': ['销孔', '活塞销孔'], 'n': ['环槽数']},
        build=_piston, bbox=lambda p: (p['D'], p['D'], p['H']), note='回转体 + 环槽 + 销孔'),
    '连杆': dict(
        aliases=['发动机连杆', '连杆'],
        params={'L': ['中心距', '长度', '长'], 'D': ['大头外径'], 'dh': ['大头孔径', '大头孔'], 'd': ['小头外径'], 'dh2': ['小头孔径', '小头孔'],
                'thick': ['杆厚', '厚度', '厚', '宽']},
        build=_conrod, bbox=_conrod_bbox, note='大头 + 小头 + 杆身'),
    '万向节十字轴': dict(
        aliases=['万向节十字轴', '十字轴', '万向节'],
        params={'L': ['跨距', '长度', '长'], 'd': ['轴颈直径', '轴颈']},
        build=_cross_joint, bbox=lambda p: (p['L'], p['L'], p['d']), note='中心块 + 四个轴颈'),
    '涡旋盘': dict(
        aliases=['涡旋盘', '涡旋压缩机'],
        params={'D': ['外径'], 'H': ['高度', '厚'], 't': ['壁厚'], 'n': ['圈数']},
        build=_scroll, bbox=lambda p: (p['D'], p['D'], p['H']), note='阿基米德螺线壁'),
    '三角转子': dict(
        aliases=['转子发动机转子', '三角转子'],
        params={'R': ['外接圆半径', '半径'], 'H': ['厚度', '厚'], 'd': ['中心孔']},
        build=_wankel, bbox=_rotor_bbox, note='圆角三角形 (Reuleaux)'),
    '飞轮': dict(
        aliases=['发动机飞轮', '飞轮'],
        params={'D': ['外径'], 'd': ['内径', '中心孔'], 'h': ['厚度', '厚'], 'K': ['分度圆'], 'n': ['孔数']},
        build=_flywheel, bbox=lambda p: (p['D'], p['D'], p['h']), note='盘体 + 中心孔 + 螺栓孔'),
    '气门': dict(
        aliases=['发动机气门', '气门'],
        params={'d': ['杆径', '杆部直径'], 'D': ['盘径', '头部直径'], 'L': ['总长', '长度', '长']},
        build=_valve, bbox=lambda p: (p['D'], p['D'], p['L']), note='杆部 + 头部盘'),
}

PARTS.update(HARD_PARTS)


PARTS.update(MEDIUM_PARTS)


# ---------------------------------------------------------------------------
# 焊装夹具件（依据真实夹具数据包 AI数据集/ 的 BOM 归纳，见 kb/fixture_gap.json）
#
# 为什么加这一类：插件原来 41 个类别全是通用机械件（轴承/齿轮/带轮/发动机件…），
# 而真实汽车焊装夹具里**占绝大多数的是"加工件"**——BOM 实测名称分布：
#   折弯件×14、焊接件×10、连接块×8、板×6、连接件×3、架体部件×2、转台部件×2、
#   焊接框架×2、焊接围栏×2、基板×2、安全销×2、配焊板条×2、支腿×2…
# 材料实测：Q235A×42、45#×10、6061×4；表面处理：喷漆 / 发蓝。
#
# ⚠️ 诚实边界：数据集**没有**这些加工件的可提取特征树（CATPart 二进制限制），
#    STEP 的包围盒又有坏值（装配型导出是局部坐标系并集，实测出现过 50 万 mm 的荒谬值）。
#    所以下面的参数是**按夹具件的一般构造给的合理默认与可选参数**，
#    不是从数据里量出来的真实尺寸。尺寸由用户按图纸输入。
# ---------------------------------------------------------------------------

def _connect_block(p):
    """连接块：长方体 + 可选中心孔。夹具上最常见的基础连接件。"""
    L = float(p['L'])
    W = float(p['W'])
    H = float(p.get('H') or max(10.0, min(L, W) * 0.4))
    fs = [Feature('box', {'length': L, 'width': W, 'height': H,
                          'center': (0, 0, 0), 'base': False, 'axis': 'Z'},
                  ADD, name='连接块')]
    dh = p.get('dh')
    if dh:
        fs.append(Feature('hole', {'diameter': float(dh), 'through': True,
                                   'pos': 'center', 'axis': 'Z', 'segments': 32},
                          CUT))
    return fs


def _base_plate(p):
    """基板 / 配焊板条：薄板 + 可选四角安装孔 + 可选中心孔。"""
    L = float(p['L'])
    W = float(p['W'])
    T = float(p.get('t') or max(6.0, min(L, W) * 0.06))
    fs = [Feature('box', {'length': L, 'width': W, 'height': T, 'corner': 0.0,
                          'corner_mode': 'fillet', 'center': (0, 0, 0),
                          'base': False, 'axis': 'Z'}, ADD, name='基板')]
    dh = p.get('dh')
    if dh and int(p.get('n') or 0) >= 2:
        fs.append(Feature('hole', {'diameter': float(dh), 'through': True,
                                   'pos': 'corners', 'axis': 'Z', 'segments': 32,
                                   'margin': float(p.get('margin') or 0.0)},
                          CUT))
    elif dh:
        fs.append(Feature('hole', {'diameter': float(dh), 'through': True,
                                   'pos': 'center', 'axis': 'Z', 'segments': 32},
                          CUT))
    return fs


def _support_leg(p):
    """支腿：圆柱（或方管，用 sides 切换）+ 可选中心孔。"""
    D = float(p['D'])
    H = float(p['H'])
    sides = int(p.get('sides') or 0)
    if sides >= 3:
        fs = [Feature('prism', {'profile': _regular_polygon(D / 2.0, sides),
                                'height': H, 'axis': 'Z', 'segments': sides,
                                'center': (0, 0, 0)}, ADD, name='支腿')]
    else:
        fs = [Feature('cylinder', {'radius': D / 2.0, 'height': H,
                                   'center': (0, 0, 0), 'base': False,
                                   'axis': 'Z', 'segments': 48}, ADD, name='支腿')]
    dh = p.get('dh')
    if dh:
        fs.append(Feature('hole', {'diameter': float(dh), 'through': True,
                                   'pos': 'center', 'axis': 'Z', 'segments': 32},
                          CUT))
    return fs


def _regular_polygon(r, n):
    import math as _m
    return [(_m.cos(2 * _m.pi * i / n) * r, _m.sin(2 * _m.pi * i / n) * r)
            for i in range(int(n))]


def _bent_part(p):
    """折弯件：L 形截面沿 Z 拉伸（板厚 t，两条边 L1 / L2）。"""
    L1 = float(p.get('L1') or p['L'])
    t = float(p.get('t') or max(2.0, min(L1, float(p.get('L2') or L1)) * 0.1))
    L2 = float(p.get('L2') or L1)
    H = float(p.get('H') or L1)
    prof = [(0.0, 0.0), (L1, 0.0), (L1, t), (t, t), (t, L2), (0.0, L2)]
    return [Feature('prism', {'profile': prof, 'height': H, 'axis': 'Z',
                              'segments': 1, 'center': (0, 0, 0)},
                    ADD, name='折弯件')]


def _joint_piece(p):
    """连接件：带台阶的矩形连接板（简化为长方体 + 可选两端孔）。"""
    L = float(p['L'])
    W = float(p['W'])
    t = float(p.get('t') or max(5.0, min(L, W) * 0.12))
    fs = [Feature('box', {'length': L, 'width': W, 'height': t,
                          'center': (0, 0, 0), 'base': False, 'axis': 'Z'},
                  ADD, name='连接件')]
    dh = p.get('dh')
    if dh and int(p.get('n') or 0) >= 2:
        fs.append(Feature('hole', {'diameter': float(dh), 'through': True,
                                   'pos': 'pcd', 'axis': 'Z', 'segments': 32,
                                   'pcd': float(p.get('pcd') or max(L, W) * 0.6),
                                   'count': int(p.get('n') or 2)}, CUT))
    return fs


def _socket_bolt(p):
    """内六角圆柱头螺钉（ISO 4762 / GB/T 70.1—2008）。

    尺寸来源优先级（**不静默**）：
      1. 用户明说的头径/头高/对边/六角深 -> 直接用；
      2. 没说的 -> 查 `kb/bolt_std.json` 标准表（按螺纹规格 M5/M6…）；
      3. 标准表也查不到 -> 按比例推定（并在 note 里说明）。

    关键价值：用户只写「内六角螺钉 M10x30」，头径 16、头高 10、对边 8、
    六角深 5 全部从标准表带出来 —— 不用逐个背标准值。
    """
    text = p.get('__text') or ''
    # 螺钉的行业写法 `M10x30`：`M` 别名能读到 d=10，但 **x 后面的长度没有别名可依**，
    # 于是 L 缺失、整条仍判"读不全"（实测 `内六角螺钉 M10x30` 就是这个原因失败的）。
    # 这里从原文把长度补出来 —— 只补**长度**这一个明确的量，不做别的推断。
    mt = re.search(r"[Mm]\s*\d+(?:\.\d+)?\s*[xX\u00d7*]\s*(\d+(?:\.\d+)?)", text)
    if mt:
        p = dict(p)
        p['L'] = float(mt.group(1))
    d = float(p['d'])
    L = float(p['L'])
    spec = None
    try:
        from ai_modeler.parts import std_bolt as SB
        spec = SB.spec_of(d) or SB.spec_of(p.get('spec') or '')
        # `M10x30` 这类**螺钉标准写法**：用户常常不写"螺纹规格"四个字，
        # 别名规则就把 d 读成了 10、L 却没读到（实测 `内六角螺钉 M10x30` 无特征）。
        # 这里从原文兜一次，长度也一并以原文为准。
        if spec is None or not spec.get('length'):
            got = SB.spec_from_text(p.get('__text') or '')
            if got:
                spec = got
                if got.get('length'):
                    L = float(got['length'])
    except Exception:
        spec = None
    dk = p.get('bolt_dk') or (spec or {}).get('head_d')
    k = p.get('bolt_head_k') or (spec or {}).get('head_h')
    s = p.get('bolt_hex_s') or (spec or {}).get('hex_s')
    t = p.get('bolt_hex_t') or (spec or {}).get('hex_t')
    if not all((dk, k, s, t)):
        # 标准表查不到且用户没给全 -> 按比例推定（上层能从 note 看出这是推定）
        dk = dk or round(1.6 * d, 1)
        k = k or round(d, 1)
        s = s or round(0.8 * d, 1)
        t = t or round(0.5 * d, 1)
    return [Feature('socket_head_bolt', {
        'shank_d': d, 'length': L, 'head_d': float(dk), 'head_h': float(k),
        'hex_s': float(s), 'hex_t': float(t), 'segments': 48,
        'center': (0, 0, 0)},
        ADD,
        name='内六角圆柱头螺钉 %s×%g'
             % ((spec or {}).get('spec') or ('M%g' % d), L))]


def _washer_thin(p):
    """弹垫 / 平垫圈（薄片环）：由 内径/外径/厚 定义。"""
    d = float(p['d'])
    D = float(p['D'])
    h = float(p.get('h') or max(0.5, (D - d) * 0.35))
    return [Feature('cylinder', {'radius': D / 2.0, 'height': h,
                                 'center': (0, 0, 0), 'base': False,
                                 'axis': 'Z', 'segments': 48}, ADD,
                    name='垫圈 ⌀%g' % D),
            Feature('hole', {'diameter': d, 'through': True, 'pos': 'center',
                             'axis': 'Z', 'segments': 48}, CUT)]


def _egt_profile(p):
    """EGT 型材（TUNKERS）：方管截面沿 Z 拉伸。

    来自真实型号数据：`EGT001 SW80x088` … `SW80x4078`（步长 30）、
    `EGT002 SW50x…`（步长 30）、`EGT006 D30x…`（步长 15）。
    **型号规律就是"截面代号 × 长度"** —— 所以按"截面 + 长度"参数化建模是对的。

    ⚠️ 诚实边界：SW80/SW50 的**真实截面**（内孔形状、齿纹、壁厚）是 TUNKERS
    厂商目录数据，我手上没有。这里按"方管"建：外形 = 截面尺寸（真实、可核验），
    壁厚按常规取（可改）。**不冒充**已还原真实齿纹截面。
    D30 系列按圆柱建（D=直径的常见含义），同样不冒充真实截面。
    """
    W = float(p.get('sec_w') or 80.0)
    L = float(p['L'])
    t = float(p.get('t') or max(2.0, W * 0.05))
    shape = (p.get('shape') or 'SW').upper()
    if shape.startswith('D') or p.get('D'):        # D30：圆柱型材
        D = float(p.get('D') or (W if W != 80.0 else 30.0))
        return [Feature('cylinder', {'radius': D / 2.0, 'height': L,
                                     'center': (0, 0, 0), 'base': False,
                                     'axis': 'Z', 'segments': 48}, ADD,
                        name='EGT 圆型材 ⌀%g×%g' % (D, L))]
    fs = [Feature('prism', {'profile': [(-W / 2, -W / 2), (W / 2, -W / 2),
                                        (W / 2, W / 2), (-W / 2, W / 2)],
                            'height': L, 'axis': 'Z', 'segments': 1,
                            'center': (0, 0, 0)}, ADD,
                  name='EGT 型材 %g×%g×%g' % (W, W, L))]
    inner = W - 2.0 * t
    if inner > 1.0:
        fs.append(Feature('prism', {
            'profile': [(-inner / 2, -inner / 2), (inner / 2, -inner / 2),
                        (inner / 2, inner / 2), (-inner / 2, inner / 2)],
            'height': L, 'axis': 'Z', 'segments': 1, 'center': (0, 0, 0)},
            CUT))
    return fs


def _spacer_pad(p):
    """垫片垫块：长×宽×厚 的平板（真实规格 50x20x5 / 50x50x5 / 65x20x5）。"""
    L = float(p['L'])
    W = float(p['W'])
    t = float(p.get('t') or 5.0)
    return [Feature('box', {'length': L, 'width': W, 'height': t,
                            'corner': 0.0, 'corner_mode': 'fillet',
                            'center': (0, 0, 0), 'base': False, 'axis': 'Z'},
                    ADD, name='垫片垫块 %g×%g×%g' % (L, W, t))]


def _locating_pin(p):
    """定位销（KMS-M01 系列）：阶梯圆柱 + 端部倒角。

    **尺寸来源优先级（这次是真实数据，不是猜的）**：
      1. 用户写了型号（如 `KMS-M01-P097-A20`）-> 查 `kb/pin_real_std.json`
         （**从 CATIA 导出的 STEP 实测得到**：定位段 ⌀9.7、台阶段 ⌀22、
          尾段 ⌀5.4、总长 62.308）；
      2. 用户直接给了直径/长度 -> 用用户的；
      3. 都没有 -> 按定位销通用构造推定（并保持可改）。

    实测到的编码规律（22 个规格，20 条完全符合）：
      · **P 码 ÷ 10 = 定位段直径**（P097→⌀9.7、P117→⌀11.7、P157→⌀15.7）
      · **A 码 = 长度档**（实测 A20→A30 恒定 +10.00 mm）
      · 台阶段 ⌀22、尾段 ⌀5.4 是该族的**恒定值**（P299 例外，它是 ⌀29.9）
    """
    real = None
    code = p.get('code') or ''
    try:
        from ai_modeler.parts import std_pin as SP
        cand = SP._norm(code) if code else ''
        if cand:
            real = SP.spec(cand)
            if real is None:
                # 型号写法不完整（如只写 P097-A20）时也试一次
                real = SP.spec(code)
    except Exception:
        real = None

    if real:
        d = float(real['d_loc'])
        L = float(real['length'])
        base_d = float(real['d_base'])
        tail_d = float(real.get('d_tail') or 5.4)
        base_h = max(3.0, L * 0.22)
        seg = [Feature('cylinder', {'radius': base_d / 2.0, 'height': base_h,
                                    'center': (0, 0, 0), 'base': False,
                                    'axis': 'Z', 'segments': 48}, ADD,
                       name='台阶段 ⌀%g×%.2f' % (base_d, base_h))]
        rest = L - base_h
        if rest > 1.0:
            seg.append(Feature('cylinder', {'radius': d / 2.0, 'height': rest,
                                            'center': (0, 0, base_h), 'base': True,
                                            'axis': 'Z', 'segments': 48}, ADD,
                               name='定位段 ⌀%g×%.2f' % (d, rest)))
        return seg

    # ---- 没有实测型号：按用户给的尺寸，或按通用构造推定 ----
    d = float(p['d'])
    L = float(p['L'])
    base_d = float(p.get('D') or round(min(d * 1.8, L * 0.9), 1))
    base_h = float(p.get('h') or max(3.0, min(L * 0.25, d * 1.2)))
    if base_h >= L:
        base_h = max(2.0, L * 0.2)
    return [Feature('cylinder', {'radius': base_d / 2.0, 'height': base_h,
                                 'center': (0, 0, 0), 'base': False,
                                 'axis': 'Z', 'segments': 48}, ADD,
                    name='定位销台阶段 ⌀%g×%g' % (base_d, base_h)),
            Feature('cylinder', {'radius': d / 2.0, 'height': L - base_h,
                                 'center': (0, 0, base_h), 'base': True,
                                 'axis': 'Z', 'segments': 48}, ADD,
                    name='定位销定位段 ⌀%g×%g' % (d, L - base_h))]


WELDMENT_PARTS = {
    # ⚠️ params 的方向必须是 **{'英文键': [中文别名...]}**，与既有 41 个类别一致
    #   （如 平键 {'b': ['宽度','宽'], 'h': ['高度','厚'], 'L': ['长度','长']}）。
    #   第一版我写成 {'L': ['长度','长']} —— 别名当键、键当别名，方向反了，
    #   于是「基板 长度300 宽度200」里的"宽度"根本抽不到，
    #   宽度退化成默认值 → 建出 300×300 的正方形板。实测抓出来的。
    '连接块': dict(
        aliases=['连接块'],
        params={'L': ['长度', '长'], 'W': ['宽度', '宽'],
                'H': ['高度', '高'], 'dh': ['孔径', '孔直径'], 'n': ['孔数']},
        build=_connect_block, bbox=lambda p: (p['L'], p['W'],
                                              p.get('H') or 20.0),
        note='长方体 + 可选中心孔（夹具基础连接件）'),
    '基板': dict(
        aliases=['基板', '底板', '配焊板条', '垫板'],
        params={'L': ['长度', '长'], 'W': ['宽度', '宽'], 't': ['厚度', '厚'],
                'dh': ['孔径', '安装孔径'], 'n': ['孔数'],
                'margin': ['内缩', '边距']},
        build=_base_plate, bbox=lambda p: (p['L'], p['W'], p.get('t') or 10.0),
        note='薄板 + 可选四角安装孔'),
    '支腿': dict(
        aliases=['支腿', '支撑腿', '立柱'],
        params={'D': ['直径', '外径'], 'H': ['高度', '长度', '高'],
                'dh': ['孔径', '孔直径'], 'sides': ['边数']},
        build=_support_leg, bbox=lambda p: (p['D'], p['D'], p['H']),
        note='圆柱支腿（sides≥3 时按正多边形方管）'),
    '折弯件': dict(
        aliases=['折弯件', '弯板', '角板'],
        params={'L1': ['长边', '长'], 'L2': ['短边', '另一边'],
                't': ['板厚', '厚度'], 'H': ['高度', '拉伸长度']},
        build=_bent_part, bbox=lambda p: (p['L1'], p.get('L2') or p['L1'],
                                          p.get('H') or p['L1']),
        note='L 形截面拉伸（钣金折弯件）'),
    '连接件': dict(
        aliases=['连接件', '连件'],
        params={'L': ['长度', '长'], 'W': ['宽度', '宽'], 't': ['厚度', '厚'],
                'dh': ['孔径', '孔直径'], 'n': ['孔数'],
                'pcd': ['分度圆', '节圆', '孔距']},
        build=_joint_piece, bbox=lambda p: (p['L'], p['W'], p.get('t') or 8.0),
        note='连接板 + 可选孔阵'),
}

PARTS.update(WELDMENT_PARTS)


# ---------------------------------------------------------------------------
# 内六角螺钉 / 垫圈（依据 kb/bolt_std.json，ISO 4762 / GB/T 70.1—2008）
# 来源：`零件参数\螺钉\MyBoltLibrary.xlsx` 实测 110 行全是"内六角螺栓-带弹垫"，
#       规格 M5(22)/M6(14)/M8(22)/M10(24)/M12(28)。
#       但 CATPart 逆向不出外形 —— 用标准表建，这才可核验。
# ---------------------------------------------------------------------------
BOLT_PARTS = {
    '内六角螺钉': dict(
        aliases=['内六角螺钉', '内六角圆柱头螺钉', '内六角螺栓', '杯头螺丝',
                 '内六角螺丝', '圆柱头螺钉'],
        # ⚠️ 头部尺寸键名故意用 bolt_ 前缀，避免与其它零件的 s/k 冲突：
        #    `s` 在螺栓里是"对边"、在挡圈里是"厚度"；`k` 是螺栓头高。
        #    键名冲突会让"必需参数"判定与别名抽取互相串味。
        params={'d': ['螺纹规格', '公称直径', '规格', '螺纹'], 'L': ['长度', '长'],
                'bolt_dk': ['头径', '头部直径'], 'bolt_head_k': ['头高', '头部高度'],
                'bolt_hex_s': ['对边', '对边宽度'], 'bolt_hex_t': ['六角深', '内六角深度']},
        build=_socket_bolt, bbox=lambda p: (
            float(p.get('bolt_dk') or 1.6 * float(p['d'])),
            float(p.get('bolt_dk') or 1.6 * float(p['d'])),
            float(p['L']) + float(p.get('bolt_head_k') or float(p['d']))),
        note='内六角圆柱头螺钉（按 ISO 4762 标准表补头径/头高/对边/六角深）'),
    '弹垫': dict(
        aliases=['弹垫', '弹簧垫圈', '弹性垫圈'],
        params={'d': ['内径', '孔径'], 'D': ['外径'], 'h': ['厚度', '厚']},
        build=_washer_thin, bbox=lambda p: (p['D'], p['D'],
                                            p.get('h') or 2.0),
        note='弹簧垫圈（薄片环）'),
}

PARTS.update(BOLT_PARTS)


# ---------------------------------------------------------------------------
# 型材 / 垫片垫块 / 定位销（依据 `零件参数` 实测的型号规律，见 kb/parts_param_catalog.json）
#   EGT001 SW80x088…x4078（步长 30）   133 个
#   EGT002 SW50x088…x4078（步长 30）   134 个
#   EGT006 D30x073…x373（步长 15）      22 个
#   垫片垫块 50x20x5 / 50x50x5 / 65x20x5
#   定位销 KMS-M01-P***-A15/20/30      22 个
# ---------------------------------------------------------------------------
PROFILE_PARTS = {
    'EGT型材': dict(
        aliases=['EGT型材', 'EGT', '型材', '铝型材', 'TUNKERS型材'],
        params={'L': ['长度', '长'], 'sec_w': ['截面', '截面尺寸', '边长', '宽度'],
                't': ['壁厚', '厚'], 'D': ['直径'],
                'shape': ['系列', '类型', '代号']},
        build=_egt_profile, bbox=lambda p: (p.get('sec_w') or 80.0,
                                            p.get('sec_w') or 80.0, p['L']),
        note='EGT/TUNKERS 型材：方管截面按截面尺寸+长度建；真实齿纹截面需厂商目录'),
    '垫片垫块': dict(
        aliases=['垫片垫块', '垫块', '垫片'],
        params={'L': ['长度', '长'], 'W': ['宽度', '宽'], 't': ['厚度', '厚']},
        build=_spacer_pad, bbox=lambda p: (p['L'], p['W'], p.get('t') or 5.0),
        note='平板垫片（真实规格 50×20×5 / 50×50×5 / 65×20×5）'),
    '定位销': dict(
        aliases=['定位销', '销钉', '圆柱销'],
        params={'d': ['直径', '销径', '定位直径'], 'L': ['长度', '长'],
                'D': ['台阶直径', '台径'], 'h': ['台阶高度', '台高'],
                # 型号（KMS-M01-P097-A20）：写了就查**实测表**
                # kb/pin_real_std.json（从 CATIA 导出的 STEP 量出来的）
                'code': ['型号', '代号']},
        build=_locating_pin, bbox=lambda p: (
            float(p.get('D') or float(p['d']) * 1.8),
            float(p.get('D') or float(p['d']) * 1.8), float(p['L'])),
        note='定位销：定位段 + 台阶段（KMS-M01 系列编码未解码，尺寸按图纸输入）'),
}

# ---------------------------------------------------------------------------
# 「写了型号就能补出来」的参数 —— **按零件声明**，不能进全局 OPTIONAL_PARAMS。
# 为什么不能全局：`d`/`L` 对绝大多数零件是**真必需**，全局放开会掩盖别处的缺失。
# 为什么必须声明：model_params 的"必需参数缺失"检查在调用 build **之前**跑，
#   不声明的话「定位销 KMS-M01-P097-A20」会因为"缺 d"直接返回 None，
#   型号等于白写（实测确认过）。
# ---------------------------------------------------------------------------
CODE_ONLY_PARAMS = {
    # 定位销：型号 -> kb/pin_real_std.json（CATIA 导出的 STEP 实测表）
    #          可补出 定位段直径 d、台阶段直径 D、总长 L
    '定位销': ('d', 'L', 'D', 'h'),
    # 深沟球轴承：代号 6204 -> kb/bearing_std.json 可补 d/D/B
    '深沟球轴承': ('d', 'D', 'B'),
    # 内六角螺钉：M10x30 -> kb/bolt_std.json 可补头径/头高/对边/六角深
    '内六角螺钉': ('bolt_dk', 'bolt_head_k', 'bolt_hex_s', 'bolt_hex_t'),
}

PARTS.update(PROFILE_PARTS)


# ---------------------------------------------------------------------------
# 真实型号族（本次迭代新增）—— 定义在 ai_modeler/parts/realparts.py
#
# 为什么单独一个模块：这些零件的尺寸**不来自别名解析，而来自 CATIA 导出 STEP 的
# 实测库**（kb/*_library.json）。把"真实几何驱动的零件"和"按别名解析的通用零件"
# 分开，是为了让"哪些零件是照着实测建的"一眼可查，也方便单独回归。
#
# 覆盖：内六角螺钉 / EGT型材 / EGT连接件 / 定位销 / 销座 / 支座 / 支座(铝) /
#       止块 / 分气块 / 阀岛 / 标牌 / 补高台 / 气缸连接块 / 垫片垫块 /
#       夹紧气缸 / 双导杆气缸 / 吊环 / 接近开关
#
# ⚠️ 与旧类的覆盖关系：`内六角螺钉`/`EGT型材`/`定位销`/`垫片垫块` 四类在
#    PROFILE_PARTS / BOLT_PARTS 里已有旧版定义，这里**用实测版覆盖**。
#    旧版是"按用户写的数建"，新版是"按实测数建"；用户显式写的数仍然优先
#    （见 model_params 里 real 覆盖的顺序）。
# ---------------------------------------------------------------------------
PARTS.update(RP.PARTS_EXT)

for _k, _v in RP.CODE_ONLY_EXT.items():
    _prev = CODE_ONLY_PARAMS.get(_k, ())
    CODE_ONLY_PARAMS[_k] = tuple(sorted(set(_prev) | set(_v)))



# ---------------------------------------------------------------------------
# 各类零件的典型参数集 (用于"零件库"面板一键填充, 以及中等难度零件的评测)
# ---------------------------------------------------------------------------
TYPICAL = {
    '深沟球轴承': {'d': 20, 'D': 47, 'B': 14},
    '圆锥滚子轴承': {'d': 25, 'D': 52, 'T': 16.25},
    '推力球轴承': {'d': 20, 'D': 35, 'H': 10},
    '直齿圆柱齿轮': {'m': 3, 'z': 24, 'b': 20},
    '同步带轮': {'d': 20, 'D': 80, 'B': 30},
    '链轮': {'d': 20, 'D': 100, 'B': 20},
    '多槽带轮': {'d': 25, 'D': 100, 'B': 30, 'n': 3},
    '制动盘': {'D': 280, 'T': 22, 'd': 64},
    '制动鼓': {'D': 300, 'd': 260, 'H': 80},
    '法兰': {'D': 160, 'd': 60, 'b': 20, 'K': 120, 'n': 6},
    '垫圈': {'d': 10, 'D': 20, 'h': 2},
    'O形圈': {'d': 20, 'D': 26, 'W': 3},
    '油封': {'d': 30, 'D': 47, 'b': 7},
    '轴套': {'d': 25, 'D': 35, 'L': 40},
    '联轴器': {'d': 25, 'D': 80, 'L': 60},
    '螺栓': {'d': 12, 'L': 50},
    '螺母': {'D': 12, 's': 19, 'm': 10},
    '螺旋弹簧': {'d': 2, 'D': 20, 'H': 50, 'n': 8},
    '锥形弹簧': {'d': 2, 'D': 24, 'H': 40, 'n': 6},
    '碟形弹簧': {'d': 20, 'D': 40, 'h': 3},
    '花键轴': {'d': 20, 'D': 25, 'L': 40, 'z': 8},
    '盘形凸轮': {'d': 40, 'D': 56, 'b': 10},
    '辐条手轮': {'D': 120, 'd': 20, 'H': 10, 'z': 5},
    '蜗杆': {'d': 32, 'D': 40, 'L': 60},
    '轴承座': {'d': 30, 'D': 60, 'H': 40},
    '平键': {'b': 8, 'h': 7, 'L': 40},
    '离合器摩擦片': {'d': 120, 'D': 200, 'h': 8},
    '挡圈': {'d': 20, 'D': 24, 's': 1.2},
    '圆柱销': {'d': 8, 'L': 40},
    '圆锥销': {'d': 8, 'L': 40},
    '胀紧套': {'d': 40, 'D': 62, 'L': 30},
    '管接头': {'d': 20, 'D': 32, 'L': 45},
    '斜齿圆柱齿轮': {'m': 3, 'z': 24, 'b': 25, 'beta': 18, 'dh': 20},
    '直齿锥齿轮': {'m': 4, 'z': 20, 'b': 22, 'delta': 45, 'dh': 15},
    '曲轴': {'d': 50, 'dp': 42, 'S': 86, 'n': 4},
    '凸轮轴': {'d': 30, 'h': 8, 'L': 320, 'n': 4, 'dj': 26},
    '涡轮叶轮': {'d': 30, 'D': 120, 'H': 40, 'n': 9, 'beta': 55},
    '活塞': {'D': 80, 'H': 70, 'd': 22, 'n': 3},
    '连杆': {'L': 120, 'D': 48, 'dh': 40, 'd': 24, 'dh2': 18},
    '万向节十字轴': {'L': 80, 'd': 20},
    '涡旋盘': {'D': 100, 'H': 30, 't': 4, 'n': 2.5},
    '三角转子': {'R': 60, 'H': 40, 'd': 24},
    '飞轮': {'D': 300, 'd': 40, 'h': 30, 'K': 250, 'n': 6},
    '气门': {'d': 8, 'D': 34, 'L': 105},
    '轴': {'d': 30, 'L': 120},
    '轴': {'d': 30, 'L': 120},      # 光轴: 覆盖自检要用
}

# ---------------------------------------------------------------------------
# 加载知识库统计 (用于报告"学到了多少")
# ---------------------------------------------------------------------------

_cache = None


def load_kb() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(KB_PATH, encoding='utf-8') as f:
                _cache = json.load(f)
        except Exception:
            _cache = {'count': 0, 'by_category': {}, 'parts': [],
                      'source': '(未找到 kb/auto_parts.json)'}
    return _cache


def stats() -> dict:
    kb = load_kb()
    out = {'source': kb.get('source', ''), 'count': kb.get('count', 0),
           'by_category': kb.get('by_category', {}),
           'templates': len(PARTS)}
    # 带轮的标准数据来自国家标准表（kb/pulley_std.json），一并报告出来，
    # 便于在界面/报告里说明"这个零件的尺寸是按哪个标准来的"。
    try:
        from ai_modeler.parts import std_pulley
        db = std_pulley.load_db()
        out['pulley_std'] = {
            'groove_types': sorted((db.get('v_belt_grooves') or {}).keys()),
            'standards': sorted({v.get('standard', '') for v in
                                 (db.get('v_belt_grooves') or {}).values()} - {''}),
            'selfcheck_ok': db.get('selfcheck_ok'),
            'sources': [s.get('url') for s in (db.get('sources') or [])],
        }
    except Exception:
        pass
    return out


def find_part(text: str):
    """在文本里找出最匹配的零件类别。

    规则(2026-09 改): **先看谁出现得早, 同样早才比谁的名字长**。
    为什么改: 长句里 零件名 后面常常还跟着别的零件词, 比如
        「液压缸缸筒 外径Ø25 内径Ø20 长40 法兰Ø33×4厚」
    这里主零件是"缸筒", "法兰"只是它的一个特征。老规则"取最长别名"会把这个
    法兰当成主零件, 于是整句被套上法兰的典型值(外径160/厚20/内孔60), 建出来
    是个 160 的大圆盘 —— 用户看到的就是"生成得明显不对"。

    带轮的消歧义(实测踩过): "带轮" 同时是"同步带轮"的别名, 于是
        「带轮 外径100 槽数3 A型」
    会被判成同步带轮, 建出来的是齿形轮 —— 用户要的明明是 V 带轮。
    因此这里按**信号词**先判一次:
        · 出现 "槽数/槽型/V带/多槽/多楔/皮带/V型" → V 带轮
        · 出现 "同步/齿数/节距齿型(XL/T5/5M…)" → 同步带轮

    真实型号优先(本次迭代新增): 文本里出现**库里真实存在的型号**
    （`KMS-M15-G14-H06`、`EGT001 SW80x088`、`M10x30`、`UCBH50OCSK0105`…）
    时，直接按真实库的类别判定 —— 真实型号是比别名更硬的证据。
    """
    try:
        _real = RL.lookup(text) if _real_on() else None
    except Exception:
        _real = None
    if _real and _real.get("category") in PARTS:
        _m = str(_real.get("model") or "")
        # 型号是"硬证据"：只要它在库里，就不该被更靠前的文字别名抢走。
        if _m and (re.search(re.escape(_m.split(" ")[0]), text, re.I)
                   or _m.lower() in text.lower()):
            return _real["category"]
    v_signal = re.search(r'(槽数|槽型|带型|V带|V型|多槽|多楔|皮带|窄V|'
                         r'([0-9一两二双三四五六七八九十])\s*个?\s*槽)', text, re.I)
    t_signal = re.search(r'(同步|齿数|齿型|周节制|圆弧齿)', text, re.I)
    if re.search(r'带轮|槽轮|塔轮', text):
        if not (v_signal or t_signal):
            # 只写型号或符号参数的写法（「带轮 d25 D100 n3 A型」）：
            # 出现 V 带槽型代号（Y/Z/A/B/C/D/E/SP*）就按 V 带轮处理
            v_signal = bool(_pulley_type(text, 'v'))
        if v_signal and not t_signal:
            return '多槽带轮'
        if t_signal and not v_signal:
            return '同步带轮'
        if v_signal and t_signal:
            return '多槽带轮'
    best, best_key = None, None
    for name, d in PARTS.items():
        for a in d['aliases']:
            i = text.find(a)
            if i < 0:
                continue
            # 单字别名(如"轴")只有在**不是更长词的一部分**时才算命中:
            #   「轴 直径30」要认; 「阶梯轴 …」不能认成光轴(那是台阶轴模板的活);
            #   「轴孔内键槽宽10」里的"轴"是"轴孔"的一部分, 更不能认(实测会凭空
            #   多建一根 Ø30×120 的光轴, 把整个联轴器模型搞乱)。
            if len(a) == 1 and i >= 0:
                prev = text[i - 1] if i > 0 else ''
                nxt = text[i + 1] if i + 1 < len(text) else ''
                if prev and '\u4e00' <= prev <= '\u9fff' and prev not in '个的了一画做来根段把将':
                    continue
                if nxt and '\u4e00' <= nxt <= '\u9fff' and \
                        not re.match(r'(直径|外径|内径|长|长度|的|是|为)', text[i + 1:]):
                    continue
            key = (i, -len(a))          # 位置优先, 同位置取更长的名字
            if best_key is None or key < best_key:
                best, best_key = name, key
    return best


# 这些参数"有就用, 没有也能建" —— 解析时不算必需
OPTIONAL_PARAMS = ('dh', 'W', 'K', 'n', 'beta', 'delta', 'dj', 'dh2', 'ring', 'alpha',
                   'thick',      # 杆厚之类"有就用, 没有也能建"的参数
                   'L',          # 曲轴的总长: 没写就按 行程×拐数 估算
                   's', 'k',     # 螺栓的对边/头高: 没写按 GB/T 5782 比例推定
                   'z',          # 齿轮齿数/同步带轮齿数: 多数零件会由直径反推
                   # 带轮的"槽型/带型"没写时, 按基准直径自动推荐（GB/T 10412 常用档），
                   # 因此不能算必需参数 —— 否则「多槽带轮 外径100 槽数3」会整条识别失败。
                   't', 'gt', 'type', 'tooth_type',
                   # 轴承代号（U5）：写了就直接查 GB/T 276 标准表补 d/D/B，
                   # 没写也可以只给内径/外径/宽度 —— 不能算必需，否则
                   # 「深沟球轴承 内径20 外径47 宽度14」会整条识别失败、
                   # 退化成通用球体（实测就是这么错的）。
                   'code',
                   # 焊装夹具件的**可选孔/方管参数**：缺了只是"不建孔"，不影响零件成立。
                   # 不加进来会整条 model_params 返回 None（实测「基板 长度300 宽度200
                   # 厚度16」明明抽到了 L/W，却因为缺 dh 而被判"读不全"直接失败）。
                   'dh', 'n', 'margin', 'sides', 'pcd',
                   # 内六角螺钉的头部尺寸：可以由 kb/bolt_std.json 标准表**按规格补齐**，
                   # 所以不是必需 —— 用户只写「内六角螺钉 M10x30」就够。
                   # （不列进来则 model_params 判定"缺 4 个必需参数"直接返回 None，
                   #   实测就是这么失败的。）
                   'bolt_dk', 'bolt_head_k', 'bolt_hex_s', 'bolt_hex_t',
                   # EGT 型材：不写"系列/代号"时按方管（SW）建；不写直径时按截面建。
                   # 这两个是**互斥的两种截面表达**，都不能算必需，否则互相把对方顶掉。
                   'shape', 'D', 'sec_w',
                   # 定位销：型号查实测表后 d/L/D 都能由表补出
                   'code',
                   # 定位销的台阶段：可以不写（按定位段直径与长度比例推定）
                   'h')

# 参数名 -> 通用维度字段(来自 nlparser.extract_dims)。
# 别名匹配失败时用它兜底, 这样「外径Ø25」「20mm厚」这些写法也能落到正确参数上。
_PARAM_SEMANTICS = {
    'D': ('diameter', 'od', 'outer_diameter'),
    'd': ('inner_diameter', 'bore', 'hole_diameter'),
    'L': ('length',),
    'H': ('length', 'height'),
    'b': ('height', 'thickness'),
    'h': ('height', 'thickness'),
    'T': ('height', 'thickness'),
    'W': ('height', 'thickness'),
    'm': ('height',),
    's': ('width',),
    'B': ('height', 'width'),
    'R': ('radius',),
    'r': ('radius',),
    'K': ('pcd',),
    'n': ('count',),
    'dh': ('hole_diameter',),
    'thick': ('height', 'width'),
}


def _alias_pattern(alias: str) -> str:
    """别名 + (可选 为/是/=/: 与 ⌀/R 前缀) + 数字。
    两个坑都踩过:
      · 「外径Ø25」—— 'Ø' 一挡, 整条读不出数字, 于是静默套用典型值;
      · 「壁厚2.5」—— '厚' 命中了里面的 "厚", 壁厚被当成零件高度。
    """
    a = re.escape(alias)
    if alias in ('厚', '厚度'):
        a = r'(?<!壁)' + a          # 壁厚 不算 厚度
    return (a + r'\s*(?:为|是|=|:|：)?\s*(?:[⌀Rr])?\s*' + r'(\d+(?:\.\d+)?)')


# 数字后面如果是这些字, 说明这个数字属于**下一个参数**, 不能算给当前参数。
# 例: 「5槽 Z型 内径12 基准直径59」里, "槽" 后面隔着空格逮到的 12 是内径的。
_NEXT_PARAM_HEADS = '内外基宽厚高长直齿螺中型节孔径轮槽带数度纹对头升跨壁圈拐'
_PARAM_PATTERN = (r'^(外径|内径|内孔|基准直径|基准|宽度|轮宽|厚度|齿数|槽数|槽型|孔径|轴径|'
                  r'高度|长度|带型|型号|模数|升程|跨距|壁厚|宽|厚|齿|槽|长|高|内|中|节)')


def _alias_value(text: str, alias: str):
    """按别名取一个数值, 并做边界校验（返回 float 或 None）。

    为什么要遍历所有出现位置: 简单 re.search 会在 "槽"(5槽) 后面隔着空格
    咬住下一个参数的数值（"…槽 Z型 内径12…" 把 12 读成槽数）。这里逐个出现位置
    试, 数字后面必须是"另一个参数名"或"句末/标点"才算数。
    """
    a = re.escape(alias)
    if alias in ('厚', '厚度'):
        a = r'(?<!壁)' + a
    for m in re.finditer(a + r'\s*(?:为|是|=|:|：)?\s*(?:[⌀Rr])?\s*(\d+(?:\.\d+)?)',
                         text):
        num = m.group(1)
        end = m.end()
        # 数字后面还有数字 -> 是更长数字的一部分, 说明正则把连写的两个参数串起来了
        # （「宽度402个槽」= 宽度40 + 2个槽），这个匹配作废, 继续找下一个。
        if end < len(text) and text[end].isdigit():
            continue
        rest = text[end:].lstrip()
        nxt = rest[:1]
        if nxt == '个':
            # 「2个槽」：只有当前别名本身就是槽/齿才认
            if not any(w in alias for w in ('槽', '齿')):
                continue
            rest = rest[1:].lstrip()
            nxt = rest[:1]
            if nxt in ('槽', '齿') and not any(w in alias for w in ('槽', '齿')):
                continue
        elif nxt and nxt in _NEXT_PARAM_HEADS and not re.match(_PARAM_PATTERN, rest):
            # 数字后面跟的既不是另一个参数名、也不是句末 -> 这个数字属于别处, 跳过
            continue
        try:
            return float(num)
        except ValueError:
            return None
    return None


def _num_before_alias(text: str, alias: str):
    """中文后置写法: 「4.0mm厚」= 厚4.0、「Ø33×4厚」。"""
    a = re.escape(alias)
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:毫米|mm)?\s*' + a, text)
    return float(m.group(1)) if m else None


# 有些参数的值是**字母代号**而不是数字：带轮槽型 A/B/SPZ、同步带轮型号 XL/5M。
# 这类参数用 _letter_value 读，否则「槽数4 A型」里的 A型 读不出来，
# 参数就成了"缺失"→ 整个零件库识别失败（实测 V 带轮因此建不出来）。
_LETTER_PARAMS = ('t', 'gt', 'type', 'tooth_type')
_LETTER_VALUE = r'([A-Za-z]{1,4}\d{0,3}(?:\.\d+)?)'


def _letter_value(text: str, aliases):
    """读字母代号参数: 「A型」「槽型A」「SPZ」「XL型」「5M」都认。"""
    for a in aliases:
        m = re.search(re.escape(a) + r'\s*(?:为|是|=|:|：)?\s*' + _LETTER_VALUE, text)
        if m:
            return m.group(1)
        m = re.search(_LETTER_VALUE + r'\s*型?\s*' + re.escape(a), text)
        if m:
            return m.group(1)
    return None


# ---------------- 带轮专用的"槽型/带型/型号"识别 ----------------
# 带类零件的型号是字母代号（A/B/C/SPZ/XL/5M…），用户写法极多：
#   「A型」「槽型A」「A带轮」「V带轮 槽型A」「同步带轮 XL型」「5M型」
# 通用别名正则只能认出"槽型A"这一种，实测另外几种全都漏，于是整条零件识别失败。
_PULLEY_TYPE_WORDS = ('v带轮', 'v型带轮', '多槽带轮', '多楔带轮', '皮带轮', '槽轮',
                      '塔轮', '窄v带轮', '同步带轮', '同步轮', '圆弧齿带轮', '带轮')
_V_GROOVE_TYPES = ('SPZ', 'SPA', 'SPB', 'SPC', 'Y', 'Z', 'A', 'B', 'C', 'D', 'E')
_TIMING_TYPES = ('XXH', 'XH', 'MXL', 'XL', 'L', 'H', 'T2.5', 'T5', 'T10', 'T20',
                 '14M', '8M', '5M', '3M')


def _pulley_type(text: str, kind: str = 'v'):
    """从描述里抽出带轮的槽型/带型代号。kind='v' 读 V 带槽型, 't' 读同步带型号。

    支持的写法（实测清单）:
      槽型A / 带型SPZ / 型号XL / 齿型XL / A型 / XL型 / 8M型 / 5M型
      A带轮 / SPB带轮 / 窄V带轮 SPB / 带轮 槽型A / 裸代号 "槽数3 A" / "槽数8D型"

    实现思路（换过三次写法, 这版最稳）:
      不写"一个大正则去猜型号", 而是**把表里的型号逐个当独立词去匹配** ——
      前后不允许接字母数字（中文相邻是允许的, 因为"槽数8D型"里 D 就贴着中文）。
      这样既不会从「V带轮」里抓出 V, 也不会把「槽数8」的 8 当成型号。
    """
    table = _V_GROOVE_TYPES if kind == 'v' else _TIMING_TYPES
    hits = []                                   # (位置, 型号)
    for cand in table:
        for m in re.finditer(re.escape(cand), text, re.I):
            i, j = m.start(), m.end()
            # 前面是**字母**才算"更长型号的一部分"（SPB 里的 B）。
            # 前面是数字不算 —— 「槽数8D型」里的 D 就是紧跟在槽数后面的型号。
            if i > 0 and text[i - 1].isascii() and text[i - 1].isalpha():
                continue
            # 后面接字母数字 -> 只有"数字+字母"型（8M）才容许
            if j < len(text) and text[j].isascii() and text[j].isalnum():
                if not re.match(r'^\d+[A-Za-z]+$', cand):
                    continue
            hits.append((i, cand.upper()))
    if not hits:
        return None
    # 越靠后越可能是"用户在末尾补的型号"（"…槽数8 D型"）；同位置取更长的
    hits.sort(key=lambda x: (x[0], len(x[1])))
    return hits[-1][1]


def _use_param_table() -> bool:
    """是否启用参数引用表（U2）。默认关：对拍全绿后再逐零件打开。"""
    import os as _os
    if _os.environ.get('PARAM_TABLE') == '1':
        return True
    if _os.environ.get('PARAM_TABLE') == '0':
        return False
    try:
        from ai_modeler.core import params as _p
        return bool(getattr(_p, 'USE_PARAM_TABLE', False))
    except Exception:
        return False


def model_params(text: str, extended: bool = True) -> Optional[dict]:
    """解析文本里该零件需要的参数, 返回 {参数名: 值}。缺关键参数返回 None。

    v1.4 起走**参数引用表**（升级文档 U2）：
      1) ai_modeler/core/params.py 抽出"用户写出来的数"（带 span 与出处）；
      2) ai_modeler/core/extract.py 补标准值 / 公式推导 / 典型值（各带出处）；
      3) 关键参数仍缺 -> 不猜，交给上层提问或给候选。
    旧实现保留为 `_model_params_legacy()`，仅在"新表没读全、旧逻辑读全了"时兜底，
    保证切换期不出现能力倒退（对拍见 reports/参数表对拍报告.md）。
    """
    cat = find_part(text)
    if not cat:
        return None
    d = PARTS[cat]
    # 参数引用表（U2）默认关闭：正在逐零件迁移，对拍未全绿前开关保持 False。
    # 打开方式：ai_modeler.params.USE_PARAM_TABLE = True（或用环境变量 PARAM_TABLE=1）。
    if not _use_param_table():
        return _model_params_legacy(text, extended=extended)
    try:
        from ai_modeler.core import extract as _ex
        spec = _ex.make_spec(cat, d)
        tab = _ex.extract(cat, spec, text)
        _ex.derive(cat, tab)
        need = [k for k in d['params'] if k not in OPTIONAL_PARAMS]
        _BORE_WORDS = ('内径', '孔径', '中心孔', '轴孔', '内孔', '缸孔', '通孔直径', '小径')
        BORE_KEYS = tuple(k for k, al in d['params'].items()
                          if any(_bw in a for a in al for _bw in _BORE_WORDS))
        notes = list(tab.notes)
        notes += _ex.fill_defaults(cat, tab, d, (TYPICAL or {}).get(cat) or {}, need)
        # 「写了型号就能补出」的参数不算缺失（按零件声明，见 CODE_ONLY_PARAMS）
        code_only = set(CODE_ONLY_PARAMS.get(cat, ()))
        missing = [k for k in need
                   if not tab.has(k) and k not in _ex.DERIVED_KEYS
                   and k not in BORE_KEYS and k not in code_only]
        if missing:
            legacy = _model_params_legacy(text, extended=extended)
            if legacy:
                legacy = dict(legacy)
                legacy['table'] = tab
                legacy['notes'] = list(legacy.get('notes') or []) + \
                    ['新参数表未读全（缺 %s），本条由旧解析兜底' % '、'.join(missing)]
                return legacy
            return None
        params = _ex.to_partlib_params(cat, tab, d)
        if params.get('D') and params.get('d') and float(params['d']) >= float(params['D']):
            params.pop('d', None)
        # ---- U4：用户的意图纠正（本地"微调"）
        # 在"用户明说的值 > 标准值 > 公式推导 > 典型值"这条链**之上**再叠一层：
        # 用户在「意图核对」面板里改过的参数，下次同类表述直接生效。
        # ⚠️ 只覆盖被纠正的那一个参数，其余一仍其旧 —— 纠正一个数不该把整条描述钉死。
        try:
            from ai_modeler.core import corrections as _COR
            fixes = _COR.lookup(cat, text)
            if fixes:
                for k, v in fixes.items():
                    if k not in d['params']:
                        continue
                    try:
                        tab.set(k, v, provenance='asked',
                                source='用户意图纠正')
                    except Exception:
                        pass
                    params[k] = v
                notes.append('已套用你的意图纠正：%s'
                             % '、'.join('%s=%s' % (k, v) for k, v in fixes.items()))
                try:
                    _COR.bump(text, cat)
                except Exception:
                    pass
        except Exception:
            pass
        defaulted = [k for k, v in tab.items.items() if v.provenance == 'default']
        params, _rp_notes = _overlay_real(cat, text, params, tab)
        notes.extend(_rp_notes)
        return {'category': cat, 'params': params, 'def': d,
                'defaulted': defaulted, 'notes': notes, 'table': tab}
    except Exception:
        return _model_params_legacy(text, extended=extended)


def _real_on():
    """真实型号库开关。

    默认开：这一轮迭代的全部价值就在这里（按实测几何建模）。
    设 `AI_MODELER_REALLIB=0` 可关掉，用于**同口径 A/B 回归**
    —— 关掉后行为与升级前一致，这样可以证明"没绿变红"不是靠猜。
    """
    v = os.environ.get('AI_MODELER_REALLIB', '')
    return v.strip().lower() not in ('0', 'off', 'false', 'no')


def _overlay_real(cat, text, params, tab=None):
    """把**实测真实型号库**的值覆盖到解析结果上。

    为什么这层要在最后（优先级最高）：
      用户这次的要求是"先让 AI 明白现实长什么样，再建模，不许凭空捏造"。
      一个真实存在的型号（`KMS-M15-G14-H06` / `EGT001 SW80x088` / `M10x30`）
      在 CATIA 导出的 STEP 里量出来的尺寸，比"别名解析 + 标准表 + 典型值"
      这条链上的任何一环都硬 —— 它是这个零件**实际的样子**。

    但**不覆盖用户明确写出的数**：用户写"EGT型材 SW80x100"时，100 是用户的
    意图，应该照办；实测库只在用户没写该参数时补位。

    返回 (params, notes)。
    """
    notes = []
    if not _real_on():
        return params, notes
    try:
        rec = RL.lookup(text)
    except Exception:
        rec = None
    if not rec or rec.get('category') != cat:
        return params, notes
    real = rec.get('real') or {}
    p = dict(params or {})
    p['__real'] = real
    p['__real_model'] = rec.get('model')
    p['__real_source'] = rec.get('source')
    written = set()
    try:
        if tab is not None:
            written = {k for k, v in tab.items.items()
                       if v.provenance in ('explicit', 'asked')}
    except Exception:
        written = set()
    applied = []
    for k, v in (rec.get('params') or {}).items():
        if k == '__real':
            continue                     # 上面已经放好
        if k.startswith('__'):
            # 内部键（`__env_sorted` 实测尺寸集合、`__nominal` 型号规格说明…）
            # **必须一起带过去**：建模器与验收都靠它们，丢了就会退回默认值
            # （实测踩过：EGT001 SW80x118 的期望外形退成 118×50×50）。
            p[k] = v
            continue
        if k in written and k != 'code':
            continue                     # 用户写了的，不动
        # `code` 一律以真实库为准：用户写的 `UCBH50OCSK0105` 与库里的
        # `UCBH50OCSK0105°` 是同一个型号，但建模器要靠库里的键去取真实几何。
        p[k] = v
        applied.append(k)
        if tab is not None:
            try:
                tab.set(k, v, provenance='explicit',
                        source='实测型号库 %s' % rec.get('model'))
            except Exception:
                pass
    if applied:
        notes.append('已按**CATIA 实测型号库**代入：%s（%s）'
                     % ('、'.join('%s=%s' % (k, p[k]) for k in applied[:8]),
                        rec.get('model')))
    for limit in (rec.get('limits') or []):
        notes.append('诚实边界：%s' % limit)
    return p, notes


def _model_params_legacy(text: str, extended: bool = True) -> Optional[dict]:
    """旧版参数解析（v1.3 及以前）。保留用于兜底与对拍；新代码请用 model_params()。"""
    import re as _re
    cat = find_part(text)
    if not cat:
        return None
    d = PARTS[cat]
    need = [k for k in d['params'] if k not in OPTIONAL_PARAMS]
    out = {}
    explicit = set()          # 从描述里**真读出来**的参数(区别于后来套的典型值)
    claimed = []              # 已被别名吃掉的原文片段（避免同一串数字被两个参数抢）
    for key, aliases in d['params'].items():
        for a in aliases:
            if key in _LETTER_PARAMS:
                # 字母代号参数（带轮槽型 A/B/SPZ、同步带轮型号 XL/5M）
                kind = 't' if cat in ('同步带轮', '同步轮') else 'v'
                lv = _pulley_type(text, kind) or _letter_value(text, aliases)
                if lv:
                    try:
                        from ai_modeler.parts.standard.std_pulley import normalize_type
                        lv = normalize_type(lv) or lv
                    except Exception:
                        pass
                    out[key] = str(lv).upper()
                    explicit.add(key)
                break
            if a == 'M':
                m = re.search(r'(?<![A-Za-z0-9])[Mm]\s*(\d+(?:\.\d+)?)(?![0-9])', text)
                if m:
                    out[key] = float(m.group(1))
                    explicit.add(key)
                    break
                continue
            # 中文口径的槽数: 「双槽带轮」「四槽V带轮」—— 中文数字写在零件名前面，
            # 别名正则只认数字，所以这里单独处理。
            if a in ('双槽', '单槽', '三槽', '四槽', '五槽', '六槽', '八槽', '十槽') \
                    and key in ('n', 'z'):
                cn = _cn_groove_count(text, claimed)
                if cn:
                    out[key] = float(cn)
                    explicit.add(key)
                    break
                continue
            val = _alias_value(text, a)
            if val is None and '槽' in a and key in ('n', 'z') and key not in out:
                # 兜底: 「宽度402个槽」= 宽度40 + 2个槽（连写时数字被粘在一起）。
                # 通用维度解析会把 402 当成一个数, 这里直接从连写串里取最后一位。
                for m3 in re.finditer(r'(\d+)\s*个?\s*([槽齿])', text):
                    run = m3.group(1)
                    if len(run) >= 2:
                        val = float(run[-1])
                        break
            if val is not None:
                out[key] = float(val)
                explicit.add(key)
                break
    # 2.5) 语义兜底: 别名读不出来的参数, 用通用维度词(外径/内孔/长/厚/壁厚)兜。
    #      为什么必须有这一步: 用户写「内孔Ø32」「齿顶圆Ø83」「总长140」时,
    #      如果该零件的别名表里没有这个词, 老代码会**静默套用典型值** ——
    #      联轴器长72 被套成 120、垫圈内孔20.5 被套成 10, 建出来完全不是那个东西。
    try:
        from ai_modeler.core.parsing.nlparser import extract_dims, normalize as _normalize
        dm = extract_dims(_normalize(text))
    except Exception:
        dm = None
    if dm is not None:
        # ⚠️ 连写串数保护: 「宽度402个槽」这种把"宽度40"和"2个槽"粘成 402 的情况,
        #    通用的 extract_dims 会把 402 当成宽度/数量。凡是紧跟在参数名后面的
        #    数字（＝别名本该读到的那个）, 一律**不许**再由语义兜底二次分配。
        dirty = set()
        for m2 in re.finditer(r'(?:宽度|基准直径|节圆直径|外径|内径|内孔|孔径|轴径|齿数|槽数|'
                              r'高度|长度|轮宽|厚度|模数|升程|跨距|壁厚)\s*(\d+(?:\.\d+)?)',
                              text):
            try:
                dirty.add(float(m2.group(1)))
            except ValueError:
                pass
        # 「法兰 Ø210×22」这种写法: 前面是外径, 后面是厚度 —— 盘类零件通用
        if getattr(dm, 'pair', None):
            p0, p1 = dm.pair
            if 'D' not in out and 'D' in d['params']:
                out['D'] = float(p0)
            for tk in ('b', 'h', 'T', 'B', 'W', 'm', 'L'):
                # 「M12×80」= 螺纹×长度、「法兰 Ø210×22」= 外径×厚度:
                # 第二个数给"厚度类"参数; 只有零件真有 L(长度)时才当长度
                if tk in d['params'] and tk not in out:
                    out[tk] = float(p1)
                    break
        # 一个测量值只能分配给**一个**参数: 早先把同一个"内径Ø48"同时填给了
        # d/dh/dh2, 于是连杆的小头外径也变成 48(实测 B28 的 D/d 全乱)。
        # 注意: 字母代号参数（带轮槽型 A/SPZ）不是数值, 不能参与数值去重。
        used_vals = set()
        for k, v in out.items():
            if k == 'D' or k in _LETTER_PARAMS:
                continue
            try:
                used_vals.add(float(v))
            except (TypeError, ValueError):
                continue
        for key, aliases in d['params'].items():
            if key in out:
                continue
            # 该参数的语义由它自己的中文别名决定:
            #   别名里有"外径/直径" -> 取通用 diameter; 有"内径/孔径" -> inner_diameter;
            #   有"长" -> length; 有"厚/宽/高" -> height/width; 有"半径" -> radius …
            # 这样「轴 Ø38」不会被当成内孔、「法兰 内孔Ø50」也不会被当成外径。
            sems = []
            joined = ''.join(aliases)
            # ⚠️ 顺序很重要: "轴孔直径"里也含"直径", 所以**先判内孔类**再判外径类,
            # 否则联轴器的 d 会被当成外径(实测 d=85 直接被一致性检查丢掉, 孔没了)
            bore_like = any(w in joined for w in ('内径', '孔径', '内孔', '中心孔',
                                                  '轴孔', '小径', '孔直径'))
            if bore_like:
                sems.append('inner_diameter')
            # ⚠️ **带"孔"字的参数绝不能兜到外径**: 「螺栓孔直径」里含"直径",
            #    于是把外径 Ø140 填给了 dh 螺栓孔径 —— 实测端盖 A32 就是这么错的。
            elif any(w in joined for w in ('外径', '直径', '大径', '外圆', '齿顶圆')):
                sems.append('diameter')
            if any(w in joined for w in ('半径',)):
                sems.append('radius')
            if any(w in joined for w in ('长度', '长', '跨距', '距离')):
                sems.append('length')
            if any(w in joined for w in ('高度', '厚度', '厚', '宽度', '宽')):
                sems += ['height', 'width']
            if any(w in joined for w in ('分度圆', '节圆', 'PCD')):
                sems.append('pcd')
            if any(w in joined for w in ('孔数', '个数', '齿数', '圈数', '头数', '拐数')):
                sems.append('count')
            if any(w in joined for w in ('模数',)):
                sems.append('module')
            if any(w in joined for w in ('升程',)):
                sems.append('span')
            sems += list(_PARAM_SEMANTICS.get(key, ()))
            for fld in sems:
                v = getattr(dm, fld, None)
                if not v:
                    continue
                if float(v) in dirty:
                    continue          # 这个数是"参数名后面直接跟着的", 不能二次分配
                # 外径/内径不能是同一个数: 「法兰 Ø140 …」只有一个 Ø 时,
                # 绝不能既填 D 又把这个 Ø 填给 d/dh(那会切出一个跟外径一样大的洞)
                if fld == 'diameter' and any(
                        abs(float(v) - float(out[k])) < 1e-9
                        for k in out if k not in ('d', 'dh', 'dh2')
                        and k not in _LETTER_PARAMS):
                    continue
                if fld == 'inner_diameter' and 'D' in out and float(v) >= float(out['D']):
                    continue
                if float(v) in used_vals:
                    continue          # 这个数已经给别的参数了, 不能再给一次
                out[key] = float(v)
                used_vals.add(float(v))
                break
        # 壁厚推导: 给了(外径 或 内径) + 壁厚, 就能算出另一个
        wall = getattr(dm, 'wall', None)
        if wall:
            if 'D' in out and 'd' not in out:
                out['d'] = max(1.0, out['D'] - 2.0 * wall)
            elif 'd' in out and 'D' not in out:
                out['D'] = out['d'] + 2.0 * wall
        # 内外径一致性检查放在函数末尾(套典型值之后)做, 否则刚丢掉的 d 会被填回来

    if extended:
        # 符号式: d20 D47 B14（前面不能紧跟字母/数字，否则 "n3" 会被 "B30" 这类串扰）
        for key in need:
            if key in out:
                continue
            m = re.search(r'(?<![A-Za-z0-9])' + re.escape(key) +
                          r'\s*(?:=|:|：)?\s*(\d+(?:\.\d+)?)', text)
            if m:
                out[key] = float(m.group(1))
                explicit.add(key)
        # 位置式: "轴承 20 47 14" —— 零件名在句首, 后面**只有裸数字**才认。
        # ⚠️ 早先只要"数字个数恰好等于缺的参数个数"就按顺序硬填, 于是
        #    「法兰⌀210x22」把 d(内径) 填成了 210 —— 建出一个内径=外径的怪盘。
        if not all(k in out for k in need):
            pos = -1
            for a in d['aliases']:
                i = text.find(a)
                if i >= 0 and (pos < 0 or i < pos):
                    pos = i
            if 0 <= pos <= 6:
                tail = text[pos:]
                for a in d['aliases']:
                    if a in tail:
                        tail = tail.split(a, 1)[1]
                        break
                if re.fullmatch(r'[\s,，、/0-9.]+', tail or ''):
                    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', tail)]
                    missing = [k for k in need if k not in out]
                    if len(nums) == len(missing):
                        for k, v in zip(missing, nums):
                            out[k] = v
                            explicit.add(k)

    # 关键参数齐全才算识别成功; 不全的用零件库典型值补上(并记下来提示用户),
    # 否则用户只写「直齿圆柱齿轮 模数2 齿数30」会整个识别不到。
    # ⚠️ 孔类参数例外: **绝不套典型值**。用户没写内孔时凭空给他挖一个 Ø60 的孔,
    #    比没有孔错得更离谱(实测法兰/端盖/缸筒全中招, 模型上莫名多个洞)。
    #    但要按零件判断: 法兰的 d 是内孔, 而 轴/螺栓/凸轮 的 d 是外径(必需)。
    _BORE_WORDS = ('内径', '孔径', '中心孔', '轴孔', '内孔', '缸孔', '通孔直径', '小径')
    BORE_KEYS = tuple(k for k, al in d['params'].items()
                      if any(_bw in a for a in al for _bw in _BORE_WORDS))
    defaulted = []
    # 把原始描述也带上：带轮的「双槽」「四槽」这类中文口径需要在 build 阶段读。
    if cat in ('多槽带轮', '同步带轮'):
        out['__text'] = text
    missing = [k for k in need if k not in out]
    if missing:
        typ = (TYPICAL or {}).get(cat) or {}
        for k in missing:
            if k in typ and k not in BORE_KEYS:
                out[k] = typ[k]
                defaulted.append('%s=%g' % (k, typ[k]))
        if [k for k in need if k not in out and k not in BORE_KEYS]:
            return None

    # 最后再做一次内外径一致性检查(必须放在套典型值**之后**, 否则刚丢掉的 d
    # 会被典型值又填回来: 实测「端面法兰Ø33厚4」被填上 Ø60 内孔, 比外径还大)。
    if 'D' in out and 'd' in out and float(out['d']) >= float(out['D']):
        if 'd' in explicit and 'D' not in explicit:
            out['D'] = out['d'] * 1.2
        else:
            out.pop('d', None)          # 内径比外径还大 -> 那个内径是套错的, 干脆不要孔
    # 六角头螺栓的"对边/头高"若没写, 按 GB/T 5782 比例推定 —— 要把来源写进提示,
    # 不能像以前那样悄悄用 d 当外形(那样建出来是个光圆柱, 用户一眼看出不对)。
    if cat == '螺栓':
        if not out.get('s'):
            out['s'] = round(1.5 * float(out.get('d') or 12), 1)
            defaulted.append('对边s=%.1f(按 GB/T 5782 的 s≈1.5d 推定)' % out['s'])
        if not out.get('k'):
            out['k'] = round(0.7 * float(out.get('d') or 12), 1)
            defaulted.append('头高k=%.1f(按 GB/T 5782 的 k≈0.7d 推定)' % out['k'])

    return {'category': cat, 'params': out, 'def': d, 'defaulted': defaulted}


# 出现这些词说明这一句是"切除"动作, 不是"造一个零件"。
# 例如 "均布6个直径12的螺栓孔" 里有"螺栓", 但它说的是孔, 不能当成6根螺栓。
CUT_WORDS = ('孔', '槽', '切除', '挖', '钻', '打穿', '通孔', '盲孔', '沉孔')

# 但这些是"零件的参数名", 不是切除动作 —— 例如刹车盘、法兰的 "中心孔24"
# 带"孔"字的零件名也要排除, 否则 "多槽带轮" 会被判成切除动作而整条失败。
CUT_EXCEPT = ('中心孔', '孔径', '内孔直径', '轴孔直径',
              # 「内孔/轴孔/缸孔/中孔」是零件的**内径参数名**, 不是切除动作。
              # 不排除它们的话, 「联轴器 外径Ø85 内孔Ø32 长72」这种最常见的写法
              # 会被整条判成"切除句", 零件库直接不生效, 于是套典型值建出 120 长的怪件。
              '内孔', '轴孔', '缸孔', '中孔', '筒体内径', '管孔',
              '槽数', '槽宽', '槽距', '槽深', '齿槽',
              # 「齿数」里有"槽"字的近亲写法也可能被漏判, 一并排除
              '齿数', '轮齿数', '带轮槽数',
              # 这些是零件的"参数名", 里面有"孔"字但不是切除动作
              '销孔', '孔数', '大头孔', '小头孔', '大头孔径', '小头孔径',
              '螺栓孔数', '油孔',
              # ⚠️ 带轮类零件名里带"槽"字: 不排除的话
              #    「双槽带轮 基准直径80 内径20」会被判成"切槽动作",
              #    零件库整条不生效（实测带轮全系列都建不出来）。
              '多槽带轮', '多楔带轮', 'V带轮', '皮带轮', '同步带轮',
              '双槽带轮', '单槽带轮', '槽轮', '塔轮', '带轮槽数', '轮槽')


def is_cut_clause(text: str) -> bool:
    """这一句是"切除动作"而不是"造一个零件"吗？

    ⚠️ 先做一次零件库识别: 只要这一句能匹配到已知零件（并且模型参数齐全），
       就一定是"造零件"而不是"切除" —— 否则「双槽带轮 基准直径80 内径20」
       会因为零件名里的"槽"字被判成切槽，零件库整条失效（实测就这么翻车的）。
    """
    try:
        if find_part(text) and model_params(text, extended=False):
            return False
    except Exception:
        pass
    t = text
    for e in CUT_EXCEPT:
        t = t.replace(e, '')
    return any(w in t for w in CUT_WORDS)


def _clamp_cuts(fs):
    """把中心切除特征钳到外轮廓之内。
    口语和随机参数里经常出现"内径 > 外径"的离谱数据 (例如 内径32.98 外径30.32),
    此时布尔差集会把整个实体切光, 后面取包围盒就会炸。这里统一留 4% 余量。

    ⚠ 取的是**最大**外轮廓, 不是最小 —— 这是实测踩出来的:
      旧版取 `min(所有 ADD 特征的直径)`。对"螺钉 + 平垫圈 + 弹垫圈"这种
      **多体装配式**零件, min 就是螺杆 ⌀5, 于是垫圈内孔 ⌀5.3/⌀5.1 被一起钳到
      5×0.96 = 4.8: 两个垫圈孔变成同一个直径, 既画错了垫圈, 又会被
      `_dedupe_cuts` 当成"重复切除"再合并掉一个。
      取 max 之后: 螺钉那类件最大外轮廓是平垫圈 ⌀10 → 限值 9.6, 内孔不再被动;
      而本函数想拦的"内径 32.98 > 外径 30.32"依旧被拦(最大外轮廓 30.32 → 29.1)。
    """
    outer = None
    for f in fs:
        if f.op != ADD:
            continue
        pr = f.params or {}
        cand = None
        if 'radius' in pr:
            cand = 2.0 * float(pr['radius'])
        elif 'length' in pr and 'width' in pr:
            cand = min(float(pr['length']), float(pr['width']))
        elif 'pitch_d' in pr:
            cand = float(pr['pitch_d'])
        if cand and cand > 0:
            outer = cand if outer is None else max(outer, cand)
    if not outer:
        return fs
    lim = outer * 0.96
    for f in fs:
        if f.op == ADD:
            continue
        pr = f.params or {}
        if pr.get('pos', 'center') != 'center':
            continue
        try:
            if pr.get('diameter') and float(pr['diameter']) >= lim:
                pr['diameter'] = lim
        except Exception:
            pass
    return fs


def _attach_provenance(fs, table, params):
    """把参数表的出处（显式/标准/推导/默认）贴到构建出来的特征上。

    这里只做**搬运**，不产生任何数值：特征的数值仍由 build() 从 params 里绑。
    对"由 params 直接换算来的"特征键做保守映射（如 `D` -> `radius`/`diameter`），
    认不出对应关系的键就留空 —— 宁可标成"追不出处"，也不乱标一个出处。
    """
    if not fs or table is None:
        return
    try:
        items = getattr(table, "items", {}) or {}
    except Exception:
        return
    if not items:
        return
    #: 特征键 -> 参数表键 的换算关系；同一参数可能出现在多个特征键上。
    #  ⚠️ partlib 把参数表的键**改过名**（内径 d -> bore_d、模数 m -> module、
    #    齿宽 b -> width、线径 d -> wire_r…），不把这些改名列进来，
    #    降级后的特征就追不到出处（实测：19 个真尺寸里大半是这一类）。
    alias = {
        # 直径/半径类
        "diameter": ("D", "d", "B", "H", "outer_d", "shank_d", "big_d", "journal_d"),
        "radius": ("D", "d", "outer_d", "R", "r"),
        "bore_d": ("d", "dh", "dk"),
        "outer_d": ("D",),
        "pitch_d": ("D",),
        "shank_d": ("d",),
        "big_d": ("D",),
        "journal_d": ("d",),
        "thread_d": ("d",),
        # 长度/高度/宽度类
        "height": ("B", "H", "L", "b", "t", "h", "thick"),
        "length": ("L", "b", "l"),
        "width": ("b", "B", "W"),
        "thick": ("t", "h"),
        "span": ("L",),
        # 计数/代号类
        "teeth": ("z",),
        "grooves": ("n",),
        "groove_type": ("t",),
        "tooth_type": ("t",),
        "module": ("m",),
        "spokes": ("z", "n"),
        "lobes": ("n",),
        "turns": ("n",),
        "across_flats": ("s",),
        "head_h": ("k",),
        "wire_r": ("d",),
        "wire_d": ("d",),
        "through": ("d",),
        "ring_w": ("W",),
        "dh": ("d",),
        "dh2": ("d",),
        "outer_r": ("D",),
    }
    #: 由**父子/公式**推出来、参数表里没有直接对应键的量。
    #  标 `derived` 而不是去追一个不存在的父键：值确实是算出来的，
    #  这正是 derived 这个出处的定义（如 rim_w 由轮缘/轮毂公式算、hub_r 由内孔推）。
    _derived_always = {"rim_w", "rim_t", "hub_r", "rim_r", "hub_d", "crown",
                       "depth", "face_w", "pitch_r", "tip_r", "root_r"}
    #: 纯几何/内核键，不该套出处（套了反而是错的）
    _not_dim = {"segments", "base", "center", "axis", "pos", "mode", "corner_mode",
                "enabled", "tag", "explain", "std_warnings", "name", "note",
                "seg_per_turn", "wire_seg", "taper", "taper_start", "op",
                # 螺旋线的采样/分段控制：不是用户量的尺寸，是内核参数
                "helix_deg", "twist", "wire_seg_n"}
    for f in fs:
        try:
            p = f.params or {}
        except Exception:
            continue
        for key in list(p.keys()):
            if key.startswith("__") or key in _not_dim:
                continue
            src = None
            if key in items:
                src = key
            else:
                for cand in alias.get(key, ()):  # 换算关系
                    if cand in items:
                        src = cand
                        break
            if src:
                try:
                    f.set_provenance({key: items[src].provenance})
                except Exception:
                    pass
            elif key in _derived_always:
                try:
                    f.set_provenance({key: "derived"})
                except Exception:
                    pass


def build_features_ex(text: str, extended: bool = None):
    """同 build_features, 但多返回一个提示列表 (例如哪些参数按典型值代入了)。
    返回 (类别, 特征列表, 期望外形尺寸, 提示列表), 失败返回 None。
    """
    if is_cut_clause(text):
        return None
    if extended is None:
        try:
            from ai_modeler.core.parsing.nlparser import CAPS
            extended = bool(CAPS.get('synonyms', True))
        except Exception:
            extended = True
    r = model_params(text, extended=extended)
    if not r:
        return None
    fs = _clamp_cuts(r['def']['build'](r['params']))
    # U1：把参数表的**出处**贴到特征上。
    # 构建期只负责把值绑进几何，值从哪来（用户写的/标准表查的/公式推的/典型值代的）
    # 必须一路带下去，否则下游无法区分"用户明说"和"程序猜的"——
    # 审计实测：不贴的话 93 个特征参数**一个都追不出处**。
    _attach_provenance(fs, r.get('table'), r['params'])
    try:
        bb = r['def']['bbox'](r['params'])
    except Exception:
        bb = None
    notes = []
    if r.get('defaulted'):
        notes.append('这些参数没写，已按零件库典型值代入：%s（可在右侧「参数」页改）'
                     % '、'.join(r['defaulted']))
    # U4：把"套用了你的意图纠正"也告诉用户 —— 静默改动比不改更糟
    notes.extend([n for n in (r.get('notes') or []) if '意图纠正' in n])
    return r['category'], fs, bb, notes


def build_features(text: str, extended: bool = None):
    """把自然语言直接变成特征列表。成功返回 (类别, 特征列表, 期望外形尺寸), 否则 None。"""
    r = build_features_ex(text, extended=extended)
    if not r:
        return None
    return r[0], r[1], r[2]
