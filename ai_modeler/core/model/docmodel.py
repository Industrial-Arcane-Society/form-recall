# -*- coding: utf-8 -*-
"""模型文档 —— 特征树 (Feature Tree)。

一个 Document 由若干 Feature 顺序组成, 每个 Feature 产生一个实体,
按 op 做并集(add)或差集(cut), 最终得到零件。
"""
from __future__ import annotations

import copy
import math
import time
from typing import Callable, Dict, List, Optional

from ai_modeler.core import kernel as K


# --------------------------------------------------------------------------
# 矩阵工具
# --------------------------------------------------------------------------


def mat_identity():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def mat_rot(axis: str, deg: float):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    a = (axis or 'Z').upper()
    if a == 'X':
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if a == 'Y':
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def mat_apply(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2])


def transform_polys(polys, m=None, t=(0.0, 0.0, 0.0)):
    m = m or mat_identity()
    out = []
    for p in polys:
        verts = []
        for (pos, nor) in p.vertices:
            np_ = K.v_add(mat_apply(m, pos), t)
            nn = K.v_norm(mat_apply(m, nor))
            verts.append((np_, nn))
        n2 = K.v_norm(mat_apply(m, p.plane.normal))
        out.append(K.Polygon(verts, K.Plane(n2, K.v_dot(n2, verts[0][0])), p.tag))
    return out


# --------------------------------------------------------------------------
# 特征
# --------------------------------------------------------------------------

ADD, CUT = 'add', 'cut'

# 单次构建的时间与规模上限, 超过就主动中止而不是死等
DEFAULT_TIME_BUDGET = 30.0
DEFAULT_MAX_FACES = 60000


def _bbox_overlap(a, b, margin: float = 1e-6) -> bool:
    """两个包围盒是否相交。不相交时布尔运算可以直接免掉。"""
    return not (a[3] < b[0] - margin or b[3] < a[0] - margin or
                a[4] < b[1] - margin or b[4] < a[1] - margin or
                a[5] < b[2] - margin or b[5] < a[2] - margin)


class BuildAborted(Exception):
    """构建被主动中止 (超时 / 太复杂 / 用户取消)。"""

    def __init__(self, reason: str, hint: str = '', elapsed: float = 0.0):
        Exception.__init__(self, reason)
        self.reason = reason
        self.hint = hint
        self.elapsed = elapsed

KIND_LABELS = {
    'box': '长方体',
    'cylinder': '圆柱/圆台',
    'socket_head_bolt': '内六角圆柱头螺钉',
    'sphere': '球体',
    'prism': '轮廓拉伸',
    'revolve': '回转体',
    'gear': '齿轮',
    'loft': '放样体',
    'hole': '孔',
    'pocket': '凹槽',
    'pattern': '阵列',
    'helix': '螺旋体',
    'spoked_wheel': '辐条轮',
    'vbelt_pulley': 'V带轮',
    'timing_pulley': '同步带轮',
    'worm': '蜗杆',
    'helical_gear': '斜齿轮',
    'bevel_gear': '锥齿轮',
    'crankshaft': '曲轴',
    'camshaft': '凸轮轴',
    'impeller': '叶轮',
    'piston': '活塞',
    'conrod': '连杆',
    'cross_joint': '万向节',
    'scroll_disc': '涡旋盘',
    'rotor': '三角转子',
    'shell': '抽壳',
}

OP_LABELS = {ADD: '合并', CUT: '切除'}


class Feature(object):
    """单个建模特征。"""

    def __init__(self, kind: str, params: Dict = None, op: str = ADD,
                 name: str = '', note: str = '', feature_id: str = None,
                 relations: List[Dict] = None):
        self.kind = kind
        self.params: Dict = dict(params or {})
        self.op = op
        self.name = name or KIND_LABELS.get(kind, kind)
        self.note = note
        # CADPlan identity/relations survive conversion into the executable
        # Document so repeated features can resolve their declared source.
        self.feature_id = str(feature_id) if feature_id else None
        self.relations = copy.deepcopy(relations or [])
        self.enabled = True
        #: U1：每个数值参数的**出处**（参数名 -> 'explicit'/'standard'/'derived'/
        #: 'default'/'asked'）。空 dict 表示"这个特征的数字不是来自参数表"
        #: （例如几何内核自己算的派生量），审计工具会把它算作"追不出处"。
        self.provenance: Dict[str, str] = {}

    # ---- 出处（U1：构建期只绑定值，出处必须一路带下来） ----
    def set_provenance(self, mapping: Dict[str, str]) -> 'Feature':
        if mapping:
            self.provenance.update(mapping)
        return self

    def provenance_of(self, key: str):
        return self.provenance.get(key)

    # ---- 序列化 ----
    def to_dict(self) -> Dict:
        return {'kind': self.kind, 'params': copy.deepcopy(self.params),
                'op': self.op, 'name': self.name, 'note': self.note,
                'feature_id': self.feature_id,
                'relations': copy.deepcopy(self.relations),
                'enabled': self.enabled,
                'provenance': dict(self.provenance)}

    @staticmethod
    def from_dict(d: Dict) -> 'Feature':
        f = Feature(d.get('kind', 'box'), d.get('params', {}),
                    d.get('op', ADD), d.get('name', ''), d.get('note', ''),
                    d.get('feature_id'), d.get('relations'))
        f.enabled = bool(d.get('enabled', True))
        f.provenance = dict(d.get('provenance') or {})
        return f

    @property
    def compound(self) -> bool:
        return bool(self.params.get('compound'))

    def describe(self) -> str:
        p = self.params
        k = self.kind
        f1 = lambda v: ('%.4g' % float(v))
        if k == 'box':
            s = '长方体 %s × %s × %s mm' % (f1(p.get('length', 0)), f1(p.get('width', 0)), f1(p.get('height', 0)))
            if p.get('corner'):
                s += ' (%s %s)' % ('圆角' if p.get('corner_mode', 'fillet') == 'fillet' else '倒角', f1(p['corner']))
        elif k == 'cylinder':
            r1 = float(p.get('radius', 0) or 0)
            r2 = p.get('radius2')
            r2 = r1 if r2 is None else float(r2)
            if abs(r1 - r2) > 1e-9:
                s = '圆台 R%s→R%s × %s mm' % (f1(r1), f1(r2), f1(p.get('height', 0)))
            else:
                s = '圆柱 ⌀%s × %s mm' % (f1(r1 * 2), f1(p.get('height', 0)))
        elif k == 'sphere':
            s = '球体 R%s mm' % f1(p.get('radius', 0))
        elif k == 'socket_head_bolt':
            s = ('内六角螺钉 M%s × %s mm (p=%s, 头径%s, 头高%s)' % (
                f1(p.get('shank_d', 0)), f1(p.get('length', 0)),
                f1(p.get('thread_pitch', 0)),
                f1(p.get('head_d', 0)), f1(p.get('head_h', 0))))
        elif k == 'extrude_holes':
            s = ('等截面拉伸 %s×%s×%s + %d 个直孔（直写网格，不做布尔）'
                 % (f1(p.get('sec_a', 0)), f1(p.get('sec_b', 0)),
                    f1(p.get('length', 0)), len(p.get('holes') or [])))
        elif k == 'prism':
            s = '轮廓拉伸 %s mm (%d 点轮廓)' % (f1(p.get('height', 0)), len(p.get('profile', [])))
        elif k == 'revolve':
            s = '回转体 %s° (%d 点轮廓)' % (f1(p.get('angle', 360)), len(p.get('profile', [])))
        elif k == 'loft':
            s = '放样叶片 展长 %s mm, 弦长 %s→%s mm' % (
                f1(p.get('span', 0)), f1(p.get('root_chord', 0)), f1(p.get('tip_chord', 0)))
        elif k == 'gear':
            s = '齿轮 模数%s × %d 齿' % (f1(p.get('module', 1)), int(p.get('teeth', 20)))
        elif k == 'hole':
            s = '⌀%s %s' % (f1(p.get('diameter', 0)), '通孔' if p.get('through', True) else '盲孔深%s' % f1(p.get('depth', 0)))
            pm = p.get('pos', 'center')
            if pm == 'corners':
                s += ' ×4 (四角)'
            elif pm == 'pcd':
                s += ' ×%d (分度圆 ⌀%s)' % (int(p.get('count', 4)), f1(p.get('pcd', 0)))
            elif p.get('x') or p.get('y'):
                s += ' @(%s, %s)' % (f1(p.get('x', 0)), f1(p.get('y', 0)))
            else:
                s += ' @中心'
        elif k == 'pocket':
            s = '凹槽 %s×%s 深 %s' % (f1(p.get('length', 0)), f1(p.get('width', 0)), f1(p.get('depth', 0)))
        elif k == 'helix':
            s = '螺旋体 R%s 线径%s × %s mm, %s 圈' % (
                f1(p.get('radius', 0)), f1(float(p.get('wire_r', 0)) * 2),
                f1(p.get('height', 0)), f1(p.get('turns', 0)))
        elif k == 'spoked_wheel':
            s = '辐条轮 ⌀%s × %s mm, %d 根辐条' % (
                f1(float(p.get('outer_r', 0)) * 2), f1(p.get('height', 0)),
                int(p.get('spokes', 0)))
        elif k == 'vbelt_pulley':
            s = '带轮 ⌀%s × %s mm, %d 槽' % (
                f1(p.get('pitch_d', 0)), f1(p.get('height', 0)),
                int(p.get('grooves', 0)))
        elif k == 'pattern':
            if p.get('mode') == 'circular':
                s = '圆周阵列 ×%d' % int(p.get('count', 1))
            else:
                s = '线性阵列 ×%d 间距 %s mm' % (int(p.get('count', 1)), f1(p.get('step', (0, 0, 0))[0]))
        else:
            s = k
        return s

    # ---- 几何 ----
    def build(self, ctx: Dict) -> List[List[K.Polygon]]:
        """返回该特征产生的若干实体 (每个为多边形列表)。"""
        p = self.params
        k = self.kind
        tag = ctx.get('tag', 0)

        if k == 'box':
            c = tuple(p.get('center', (0, 0, 0)))
            h = float(p.get('height', 10))
            axis = p.get('axis', 'Z')
            base = bool(p.get('base', True))
            prof = K.profile_rect(float(p.get('length', 10)), float(p.get('width', 10)),
                                  float(p.get('corner', 0) or 0),
                                  p.get('corner_mode', 'fillet'))
            # base=True: 从 center 起沿轴正向拉伸 h; base=False: 关于 center 对称
            return [K.extrude_profile(prof, h, c, axis, tag, symmetric=not base, z0=0.0)]

        if k == 'cylinder':
            c = tuple(p.get('center', (0, 0, 0)))
            h = float(p.get('height', 10))
            axis = p.get('axis', 'Z')
            base = bool(p.get('base', True))
            return [K.make_cylinder(float(p.get('radius', 5)), h, c, tag,
                                    int(p.get('segments', 48)), axis,
                                    symmetric=not base, z0=0.0,
                                    radius2=p.get('radius2'))]

        if k == 'sphere':
            return [K.make_sphere(float(p.get('radius', 5)),
                                  tuple(p.get('center', (0, 0, 0))), tag,
                                  int(p.get('segments', 32)), int(p.get('rings', 16)))]

        if k == 'extrude_holes':
            # B 计划（直写网格）：等截面拉伸 + 若干直通孔，**不做任何布尔运算**。
            # 为什么需要：纯 Python BSP-CSG 正确做多孔的上限约 96 个孔
            # （实测 192 孔体积就错 2%），而型材真实有 545 个孔。
            # 这条路径 545 孔 0.5 秒建完、体积误差 0.36%、网格封闭。
            from ai_modeler.core import directmesh as _DM
            # ⚠ 必须包成 `[实体]`：构建管线约定 `build()` 返回"实体列表"、
            #   每个实体是"多边形列表"。直接返回多边形列表会让 solids[0] 变成
            #   单个多边形 → `'Polygon' object is not iterable`（实测踩到）。
            return [_DM.extrude_with_holes(
                float(p.get('sec_a', 80)), float(p.get('sec_b', 80)),
                float(p.get('length', 100)), axis=p.get('axis', 'Z'),
                base=float(p.get('base', 0.0)), holes=p.get('holes') or [],
                seg=int(p.get('segments', 24)), tag=tag,
                ref_min=p.get('ref_min'), ref_size=p.get('ref_size'))]

        if k == 'real_solid':
            # 实测 B-Rep 实体：直接建"真实零件的网格"（外轮廓 = 真实型腔/筋/台阶）。
            # 详见 `ai_modeler/parts/realsolid.py` 的说明（为什么标准件不走这条路）。
            from ai_modeler.parts import realsolid as _RS
            return [_RS.polys(str(p.get('model') or p.get('code') or ''), tag)]

        if k == 'prism':
            prof = [(float(a), float(b)) for (a, b) in p.get('profile', [])]
            if len(prof) < 3:
                return []
            return [K.extrude_profile(prof, float(p.get('height', 10)),
                                      tuple(p.get('center', (0, 0, 0))),
                                      p.get('axis', 'Z'), tag)]

        if k == 'revolve':
            prof = [(float(a), float(b)) for (a, b) in p.get('profile', [])]
            if len(prof) < 3:
                return []
            return [K.make_revolve(prof, float(p.get('angle', 360)),
                                   tuple(p.get('center', (0, 0, 0))), tag,
                                   int(p.get('segments', 48)), p.get('axis', 'Z'))]

        if k == 'gear':
            prof = K.profile_gear(int(p.get('teeth', 20)), float(p.get('module', 2.0)))
            base = bool(p.get('base', True))
            return [K.extrude_profile(prof, float(p.get('height', 10)),
                                      tuple(p.get('center', (0, 0, 0))),
                                      p.get('axis', 'Z'), tag,
                                      symmetric=not base, z0=0.0)]

        if k == 'loft':
            secs = self._loft_sections()
            if len(secs) < 2:
                return []
            polys = K.make_loft(secs, tag)
            rot = p.get('rotate')
            m = mat_rot(rot[0], rot[1]) if rot else None
            t = tuple(p.get('translate', (0.0, 0.0, 0.0)))
            if m is not None or any(abs(v) > 1e-12 for v in t):
                polys = transform_polys(polys, m, t)
            return [polys]

        if k == 'hole':
            return self._build_holes(ctx, tag)

        if k == 'pocket':
            return self._build_pockets(ctx, tag)

        if k == 'helix':
            return [K.make_helix(float(p.get('radius', 10.0)),
                                 float(p.get('wire_r', 1.5)),
                                 float(p.get('height', 40.0)),
                                 float(p.get('turns', 8.0)),
                                 tuple(p.get('center', (0, 0, 0))), tag,
                                 int(p.get('seg_per_turn', 14)),
                                 p.get('axis', 'Z'),
                                 float(p.get('taper', 0.0)),
                                 int(p.get('wire_seg', 8)),
                                float(p.get('taper_start', 0.0)))]

        if k == 'spoked_wheel':
            return [K.make_spoked_wheel(float(p.get('outer_r', 60.0)),
                                        float(p.get('rim_w', 6.0)),
                                        float(p.get('hub_r', 15.0)),
                                        float(p.get('height', 8.0)),
                                        int(p.get('spokes', 5)),
                                        p.get('spoke_w'),
                                        tuple(p.get('center', (0, 0, 0))), tag,
                                        int(p.get('segments', 48)))]

        if k == 'vbelt_pulley':
            # 新参数（groove_type / outer_d / bore_d）走标准剖面；旧参数退回近似
            if p.get('groove_type') or p.get('outer_d'):
                return [K.make_vbelt_pulley(float(p.get('pitch_d', 80.0)),
                                            int(p.get('grooves', 3)),
                                            float(p.get('height', 40.0)),
                                            tuple(p.get('center', (0, 0, 0))), tag,
                                            None,
                                            int(p.get('segments', 64)),
                                            float(p.get('bore_d', 0.0) or 0.0),
                                            p.get('groove_type'))]
            return [K.make_vbelt_pulley(float(p.get('pitch_d', 80.0)),
                                        int(p.get('grooves', 3)),
                                        float(p.get('height', 40.0)),
                                        tuple(p.get('center', (0, 0, 0))), tag,
                                        p.get('groove_depth'),
                                        int(p.get('segments', 48)))]

        if k == 'timing_pulley':
            return [K.make_timing_pulley(float(p.get('pitch_d', 80.0)),
                                         int(p.get('teeth', 16)),
                                         float(p.get('height', 10.0)),
                                         str(p.get('tooth_type', 'XL')),
                                         float(p.get('bore_d', 0.0) or 0.0),
                                         tuple(p.get('center', (0, 0, 0))), tag,
                                         int(p.get('segments', 64)))]

        if k == 'worm':
            # 轴身 + 螺旋齿一次性输出为同一个网格。
            # 螺旋管如果走布尔并集, BSP 拆分量是平方级增长, 一条蜗杆能算十几分钟;
            # 这里直接网格拼接: 外形与包围盒完全一致, 速度快上千倍。
            cen = tuple(p.get('center', (0, 0, 0)))
            cen2 = tuple(p.get('center2', cen))
            shaft = K.make_cylinder(float(p.get('radius', 20.0)),
                                    float(p.get('height', 60.0)), cen, tag,
                                    int(p.get('segments', 48)))
            thread = K.make_helix(float(p.get('thread_r', 15.0)),
                                  float(p.get('wire_r', 3.0)),
                                  float(p.get('height2', p.get('height', 60.0))),
                                  float(p.get('turns', 2.0)), cen2, tag,
                                  int(p.get('seg_per_turn', 14)), 'Z', 0.0,
                                  int(p.get('wire_seg', 8)))
            return [shaft + thread]

        if k == 'helical_gear':
            # 斜齿轮: 内核里"齿廓扭转"的布尔并集极慢(实测 25~36 秒, 齿多时直接
            # 爆到 27 万个面), 所以按**直齿近似** —— 齿数、齿顶圆(包围盒)、
            # 中心孔都仍然正确, 只是少了螺旋角。CATIA 导出本来也是这么近似的。
            z = max(6, int(p.get('teeth', 24)))
            m = float(p.get('module', 3.0))
            h = float(p.get('height', 25.0))
            cen = tuple(p.get('center', (0, 0, 0)))
            prof = K.profile_gear(z, m)
            bore_r = float(p.get('bore_d', 0.0) or 0.0) / 2.0
            if bore_r > 0:
                # 有中心孔: 孔直接画进棱柱(在齿廓上做布尔差集很慢)
                return [K.make_twisted_tube(prof, K.profile_circle(bore_r, 40), h, 0.0, cen,
                                            tag, max(6, min(int(p.get('segments', 20)), z)),
                                            'Z')]
            # 没有中心孔时**不要**用带内环的棱柱: 退化内环会把轮廓算出界,
            # 实测齿宽 45 的斜齿轮被算成 Z=85、Y 也涨到 116(B22/B17)。
            return [K.extrude_profile(prof, h, cen, 'Z', tag, symmetric=True, z0=0.0)]

        if k == 'bevel_gear':
            return [K.make_bevel_gear(int(p.get('teeth', 20)),
                                      float(p.get('module', 4.0)),
                                      float(p.get('height', 22.0)),
                                      float(p.get('cone_deg', 45.0)),
                                      float(p.get('bore_d', 0.0)),
                                      tuple(p.get('center', (0, 0, 0))), tag,
                                      int(p.get('segments', 12)))]

        if k == 'hex_bolt':
            # 六角头螺栓: 头(六棱柱) + 杆；标准螺距可生成近似外螺旋牙型。
            return [K.make_hex_bolt(float(p.get('shank_d', 12.0)),
                                    float(p.get('length', 80.0)),
                                    float(p.get('across_flats', 18.0)),
                                    float(p.get('head_h', 7.5)),
                                    tuple(p.get('center', (0, 0, 0))), tag,
                                    int(p.get('segments', 48)),
                                    thread_pitch=float(p.get('thread_pitch', 0.0)))]

        if k == 'socket_head_bolt':
            # 内六角圆柱头螺钉: 圆柱头 + 杆 + 头端内六角孔（ISO 4762）
            return [K.make_socket_head_bolt(float(p.get('shank_d', 10.0)),
                                            float(p.get('length', 30.0)),
                                            float(p.get('head_d', 16.0)),
                                            float(p.get('head_h', 10.0)),
                                            float(p.get('hex_s', 8.0)),
                                            float(p.get('hex_t', 5.0)),
                                            tuple(p.get('center', (0, 0, 0))), tag,
                                            int(p.get('segments', 48)),
                                            thread_pitch=float(p.get('thread_pitch', 0.0)))]

        if k == 'crankshaft':
            return [K.make_crankshaft(float(p.get('main_d', 50.0)),
                                      float(p.get('pin_d', 42.0)),
                                      float(p.get('stroke', 86.0)),
                                      int(p.get('throws', 4)),
                                      tuple(p.get('center', (0, 0, 0))), tag,
                                      int(p.get('segments', 32)),
                                      p.get('length'))]

        if k == 'camshaft':
            return [K.make_camshaft(float(p.get('base_d', 30.0)),
                                    float(p.get('lift', 8.0)),
                                    int(p.get('lobes', 4)),
                                    float(p.get('length', 320.0)),
                                    p.get('journal_d'),
                                    tuple(p.get('center', (0, 0, 0))), tag,
                                    int(p.get('segments', 24)),
                                    float(p.get('lobe_deg', 0.0)))]

        if k == 'impeller':
            return [K.make_impeller(float(p.get('hub_d', 30.0)),
                                    float(p.get('tip_d', 120.0)),
                                    float(p.get('height', 40.0)),
                                    int(p.get('blades', 9)),
                                    float(p.get('twist_deg', 55.0)),
                                    tuple(p.get('center', (0, 0, 0))), tag,
                                    int(p.get('segments', 12)),
                                    p.get('blade_t'))]

        if k == 'piston':
            return [K.make_piston(float(p.get('diameter', 80.0)),
                                  float(p.get('height', 70.0)),
                                  float(p.get('pin_d', 22.0)),
                                  int(p.get('grooves', 3)),
                                  tuple(p.get('center', (0, 0, 0))), tag,
                                  int(p.get('segments', 48)))]

        if k == 'conrod':
            return [K.make_conrod(float(p.get('length', 120.0)),
                                  float(p.get('big_d', 48.0)),
                                  float(p.get('big_bore', 40.0)),
                                  float(p.get('small_d', 24.0)),
                                  float(p.get('small_bore', 18.0)),
                                  p.get('shank_w'),
                                  tuple(p.get('center', (0, 0, 0))), tag,
                                  int(p.get('segments', 32)),
                                  p.get('thick'))]

        if k == 'cross_joint':
            return [K.make_cross_joint(float(p.get('span', 80.0)),
                                       float(p.get('journal_d', 20.0)),
                                       tuple(p.get('center', (0, 0, 0))), tag,
                                       int(p.get('segments', 24)))]

        if k == 'scroll_disc':
            return [K.make_scroll_disc(float(p.get('outer_d', 100.0)),
                                       float(p.get('height', 30.0)),
                                       float(p.get('wall_t', 4.0)),
                                       float(p.get('turns', 2.5)),
                                       tuple(p.get('center', (0, 0, 0))), tag,
                                       int(p.get('segments', 120)))]

        if k == 'rotor':
            prof = K.profile_rotor(float(p.get('radius', 60.0)), int(p.get('n', 48)))
            polys = K.make_twisted_prism(prof, float(p.get('height', 40.0)), 0.0,
                                         tuple(p.get('center', (0, 0, 0))), tag, 2)
            bore = float(p.get('bore_d', 0.0))
            if bore > 0:
                polys = K.csg_subtract(polys, K.make_cylinder(
                    bore / 2.0, float(p.get('height', 40.0)) * 3.0,
                    tuple(p.get('center', (0, 0, 0))), tag, 32, 'Z', symmetric=True))
            return [polys]

        if k == 'pattern':
            return self._build_pattern(ctx)

        return []

    # ---- 叶片放样 ----
    def _loft_sections(self):
        p = self.params
        nsec = max(2, int(p.get('sections', 8)))
        span = float(p.get('span', 60.0))
        rc = float(p.get('root_chord', 30.0))
        tc = float(p.get('tip_chord', rc * 0.55))
        th = float(p.get('thickness', 0.12))
        cam = float(p.get('camber', 0.05))
        camp = float(p.get('camber_pos', 0.4))
        twist = float(p.get('twist', 0.0))
        sweep = float(p.get('sweep', 0.0))
        npts = int(p.get('points', 26))
        secs = []
        for i in range(nsec + 1):
            u = i / float(nsec)
            chord = rc + (tc - rc) * u
            prof = K.profile_airfoil(chord, th, cam, camp, npts)
            ang = math.radians(twist * u)
            ca, sa = math.cos(ang), math.sin(ang)
            shift = sweep * u
            z = span * u
            sec = []
            for (x, y) in prof:
                xx = x - chord / 2.0 + shift
                sec.append((xx * ca - y * sa, xx * sa + y * ca, z))
            secs.append(sec)
        return secs

    # ---- 孔 ----
    def _build_holes(self, ctx: Dict, tag: int):
        p = self.params
        axis = (p.get('axis') or 'Z').upper()
        bb = ctx.get('bbox')
        r = max(1e-3, float(p.get('diameter', 10)) / 2.0)
        seg = int(p.get('segments', 24))
        pm = p.get('pos', 'center')

        positions = []
        if pm == 'corners':
            if bb:
                margin = float(p.get('margin', 0) or 0) + r
                lo1, hi1, lo2, hi2 = _plane_extent(bb, axis)
                positions = [(lo1 + margin, lo2 + margin), (hi1 - margin, lo2 + margin),
                             (hi1 - margin, hi2 - margin), (lo1 + margin, hi2 - margin)]
            else:
                positions = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
        elif pm == 'pcd':
            n = max(2, int(p.get('count', 4)))
            pcd = float(p.get('pcd', 60.0)) / 2.0
            phase = math.radians(float(p.get('phase', 0.0)))
            positions = [(pcd * math.cos(2 * math.pi * i / n + phase),
                          pcd * math.sin(2 * math.pi * i / n + phase)) for i in range(n)]
        elif p.get('x') is not None or p.get('y') is not None:
            positions = [(float(p.get('x', 0.0)), float(p.get('y', 0.0)))]
        else:
            if bb:
                c1, c2 = _plane_center(bb, axis)
                positions = [(c1, c2)]
            else:
                positions = [(0.0, 0.0)]

        through = bool(p.get('through', True)) or not p.get('depth')
        if bb:
            lo, hi = _axis_range(bb, axis)
        else:
            lo, hi = -50.0, 50.0

        solids = []
        for (a, b) in positions:
            if through:
                length = (hi - lo) + 20.0
                start = lo - 10.0
            else:
                depth = float(p.get('depth', 5.0))
                depth_tolerance = max(1e-6, (hi - lo) * 1e-8)
                if depth <= 0 or depth >= (hi - lo) - depth_tolerance:
                    raise ValueError('盲孔深度必须大于零且小于实体轴向厚度；不能静默改为通孔。')
                length = depth + 1.0
                start = hi - depth
            center = _place(a, b, axis, start)
            solids.append(K.make_cylinder(r, length, center, tag, seg, axis, False, 0.0))
        return solids

    # ---- 槽 ----
    def _build_pockets(self, ctx: Dict, tag: int):
        p = self.params
        axis = (p.get('axis') or 'Z').upper()
        bb = ctx.get('bbox')
        depth = float(p.get('depth', 5.0))
        if bb:
            lo, hi = _axis_range(bb, axis)
            c1, c2 = _plane_center(bb, axis)
        else:
            lo, hi, c1, c2 = -50.0, 50.0, 0.0, 0.0
        c1 = float(p.get('x', c1))
        c2 = float(p.get('y', c2))
        depth_tolerance = max(1e-6, (hi - lo) * 1e-8)
        if depth <= 0 or depth >= (hi - lo) - depth_tolerance:
            raise ValueError('盲槽深度必须大于零且小于实体轴向厚度；贯穿切除请使用通孔特征。')
        side = str(p.get('side', 'positive')).lower()
        if side not in {'positive', 'negative'}:
            raise ValueError('pocket.side 必须为 positive 或 negative。')
        prof = K.profile_rect(float(p.get('length', 20)), float(p.get('width', 20)),
                              float(p.get('corner', 0) or 0), p.get('corner_mode', 'fillet'))
        epsilon = max(1e-5, min(1e-3, depth * 1e-6))
        if side == 'positive':
            center, z0 = _place(c1, c2, axis, hi), -depth
        else:
            center, z0 = _place(c1, c2, axis, lo), -epsilon
        # Keep the requested floor exact; extend the cutter only beyond the
        # exterior face to satisfy the Boolean tolerance.
        return [K.extrude_profile(prof, depth + epsilon, center, axis, tag, False, z0)]

    # ---- 阵列 ----
    def _build_pattern(self, ctx: Dict):
        p = self.params
        target = ctx.get('target_polys')
        if not target:
            return []
        count = max(1, int(p.get('count', 2)))
        mode = p.get('mode', 'linear')
        out = []
        if mode == 'circular':
            axis = (p.get('axis') or 'Z').upper()
            total = float(p.get('angle', 360.0))
            full = bool(p.get('full', abs(total - 360.0) < 1e-6))
            step = total / count if full else total / max(1, count - 1)
            cx, cy, cz = p.get('center', (0.0, 0.0, 0.0))
            pivot = (float(cx), float(cy), float(cz))
            for i in range(count):
                m = mat_rot(axis, step * i)
                t = K.v_sub(pivot, mat_apply(m, pivot))
                out.append(transform_polys(target, m, t))
        else:
            dx, dy, dz = p.get('step', (10.0, 0.0, 0.0))
            for i in range(count):
                out.append(transform_polys(target, None, (dx * i, dy * i, dz * i)))
        return out


# --------------------------------------------------------------------------
# 坐标系辅助
# --------------------------------------------------------------------------


def _shift_center(c, axis, d):
    a = (axis or 'Z').upper()
    if a == 'X':
        return (c[0] + d, c[1], c[2])
    if a == 'Y':
        return (c[0], c[1] + d, c[2])
    return (c[0], c[1], c[2] + d)


def _axis_range(bb, axis):
    a = (axis or 'Z').upper()
    if a == 'X':
        return bb[0], bb[3]
    if a == 'Y':
        return bb[1], bb[4]
    return bb[2], bb[5]


def _plane_extent(bb, axis):
    """返回与 axis 垂直平面内的两个方向的最小/最大值 (c1lo, c1hi, c2lo, c2hi)。"""
    a = (axis or 'Z').upper()
    if a == 'X':
        return bb[1], bb[4], bb[2], bb[5]
    if a == 'Y':
        return bb[0], bb[3], bb[2], bb[5]
    return bb[0], bb[3], bb[1], bb[4]


def _plane_center(bb, axis):
    a = (axis or 'Z').upper()
    if a == 'X':
        return (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0
    if a == 'Y':
        return (bb[0] + bb[3]) / 2.0, (bb[2] + bb[5]) / 2.0
    return (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0


def _place(a, b, axis, along):
    """把平面坐标 (a, b) 映射到垂直于 axis 的平面上, along 为轴向坐标。"""
    x = (axis or 'Z').upper()
    if x == 'X':
        return (along, a, b)
    if x == 'Y':
        return (a, along, b)
    return (a, b, along)


# --------------------------------------------------------------------------
# 文档
# --------------------------------------------------------------------------


class Document(object):
    """特征树文档。"""

    def __init__(self, name: str = 'Part1'):
        self.name = name
        self.features: List[Feature] = []
        self._cache: Optional[List[K.Polygon]] = None
        self._brep_cache = None
        self._brep_cache_config = None
        self._brep_report: Optional[Dict] = None
        self.last_log: List[str] = []
        self.version = 1
        self.source_prompt = ''
        self.max_faces = DEFAULT_MAX_FACES

    # ---- 基本操作 ----
    def clear(self):
        self.features = []
        self._cache = None
        self._brep_cache = None
        self._brep_cache_config = None
        self._brep_report = None
        self.last_log = []

    @property
    def feature_count(self) -> int:
        return len(self.features)

    def add(self, feature: Feature) -> Feature:
        self.features.append(feature)
        self.invalidate()
        return feature

    def remove(self, index: int):
        if 0 <= index < len(self.features):
            self.features.pop(index)
            self.invalidate()

    def move(self, index: int, delta: int):
        j = index + delta
        if 0 <= index < len(self.features) and 0 <= j < len(self.features):
            self.features[index], self.features[j] = self.features[j], self.features[index]
            self.invalidate()

    def update_params(self, index: int, params: Dict):
        if 0 <= index < len(self.features):
            self.features[index].params.update(params)
            self.invalidate()

    # ---- 构建 ----
    def invalidate(self):
        self._cache = None
        self._brep_cache = None
        self._brep_cache_config = None
        self._brep_report = None

    def build_brep(self, tolerance: float = 1e-5,
                   max_mesh_faces: int = 120000,
                   self_intersection_face_limit: Optional[int] = None):
        """Build the authoritative local solid with OpenCASCADE.

        CADPlan previews use this path and deliberately do not fall back to the
        legacy BSP mesh kernel.  Callers that need compatibility mesh-only
        behavior must use :meth:`build` explicitly.
        """
        from ai_modeler.core.geometry import occ_kernel
        face_limit = (occ_kernel.SELF_INTERSECTION_FACE_LIMIT
                      if self_intersection_face_limit is None
                      else int(self_intersection_face_limit))
        cache_config = (float(tolerance), int(max_mesh_faces), face_limit)
        if self._brep_cache is None or self._brep_cache_config != cache_config:
            shape, report = occ_kernel.build_document(
                self, tolerance=tolerance, max_mesh_faces=max_mesh_faces,
                self_intersection_face_limit=face_limit)
            self._brep_cache = shape
            self._brep_cache_config = cache_config
            self._brep_report = report
        return self._brep_cache

    @property
    def brep_report(self) -> Optional[Dict]:
        return copy.deepcopy(self._brep_report)

    def _check(self, t0, deadline, cancel, body, label):
        """时间 / 规模 / 取消 三重检查, 避免界面看起来"卡死"。"""
        if cancel is not None and cancel.is_set():
            raise BuildAborted('已取消计算', '', time.time() - t0)
        el = time.time() - t0
        if deadline is not None and time.time() > deadline:
            raise BuildAborted(
                '计算超时 (%.0f 秒)' % el,
                '这个模型的特征太多或者布尔运算太复杂。可以试试:\n'
                '  · 减少阵列数量\n'
                '  · 把「圆周分段」从 48 调到 24\n'
                '  · 检查尺寸是否自相矛盾 (例如孔比零件还大、\n'
                '    螺栓孔分度圆直径小于孔径导致孔互相重叠)', el)
        if len(body) > self.max_faces:
            raise BuildAborted(
                '模型过于复杂 (%d 个面)' % len(body),
                '布尔运算产生的面片数已经超过上限 %d。可以试试:\n'
                '  · 减少特征数量或阵列数量\n'
                '  · 降低圆周分段数\n'
                '  · 避免让切除体互相重叠或与零件表面完全贴合' % self.max_faces,
                el)

    def build(self, progress: Callable[[str, float], None] = None,
              deadline: Optional[float] = None, cancel=None,
              max_faces: Optional[int] = None) -> List[K.Polygon]:
        """重建模型。

        deadline  秒; 超过则抛 BuildAborted (None = 不限时)
        cancel    threading.Event; 置位则抛 BuildAborted
        max_faces 面片数上限
        """
        if self._cache is not None and deadline is None and cancel is None:
            return self._cache
        self.max_faces = int(max_faces or DEFAULT_MAX_FACES)
        t0 = time.time()
        body: List[K.Polygon] = []
        #: A′ 计划：把实体维护成一棵 **BSP 累加器**，切除**就地**做。
        #  旧做法每切一刀都要 `csg_subtract(body, s)`：拿全部面片重建一次 BSP 树、
        #  再对整个实体做两次取反、并把每张面片对着刀具的每个平面切一遍。
        #  实测 545 个孔的型材因此要几十秒，192 个孔时体积误差到 2%（碎片越切越碎）。
        #  新做法只碰"与刀具包围盒相交"的那几片面，且不重建树
        #  （等价性/体积/封闭性判据见 `tools/check_bsp_local.py`，10/10 通过）。
        root = None
        dirty = False
        log: List[str] = []
        solids_by_index: Dict[int, List[K.Polygon]] = {}
        active = [f for f in self.features if f.enabled]
        total = max(1, len(active))
        done = 0

        def _sync_body():
            """把累加器里的几何取回 `body`（需要面片列表时才做，避免每刀都走一遍）。"""
            nonlocal body, dirty
            if dirty and root is not None:
                body = K.node_all_polygons(root)
                dirty = False

        for i, f in enumerate(self.features):
            if not f.enabled:
                continue
            done += 1
            if progress:
                el = time.time() - t0
                progress('正在生成: %s  (已用 %.1fs)' % (f.describe(), el),
                         done / float(total))
            self._check(t0, deadline, cancel, body, f.describe())
            _sync_body()
            ctx = {'tag': i, 'bbox': K.bbox(body) if body else None}
            # 顺序建模的"堆叠"语义: 用户说「缸筒 …长40, 端面法兰 Ø33 厚4」时,
            # 法兰是**装在前一个零件的端面上**, 不是和它同心重叠在 z=0。
            # 所以带 stack 标记的实体特征, 默认从当前模型的顶面往上长。
            if f.op == ADD and (f.params or {}).get('stack') and ctx.get('bbox'):
                top = float(ctx['bbox'][5])
                p2 = dict(f.params)
                c = p2.get('center', (0.0, 0.0, 0.0))
                p2['center'] = (float(c[0]), float(c[1]), float(c[2]) + top)
                p2['base'] = True
                f = Feature(f.kind, p2, f.op, f.name, f.note,
                            f.feature_id, f.relations)
            if f.kind == 'pattern':
                for j in range(i - 1, -1, -1):
                    if j in solids_by_index:
                        ctx['target_polys'] = solids_by_index[j]
                        break
                if not ctx.get('target_polys'):
                    log.append('[跳过] %s —— 找不到阵列源特征' % f.describe())
                    continue
            solids = [s for s in f.build(ctx) if s]
            if not solids:
                log.append('[跳过] %s —— 未产生几何' % f.describe())
                continue
            solids_by_index[i] = list(solids[0])
            for s in solids:
                if not body:
                    # ⚠ **不要**在这里就建 BSP 累加器：`solid_begin` 会克隆全部面片
                    #   并建一棵完整的 BSP 树。直写网格件（型材 545 个孔 = 5.4 万面）
                    #   根本没有切除，提前建树纯属浪费 —— 实测把 0.5 s 拖到 55 s。
                    #   累加器改成**用到才建**（见下面的 CUT 分支）。
                    body = list(s)
                    root, dirty = None, False
                    log.append('[基准] %s → %d 面' % (f.describe(), len(body)))
                elif f.compound:
                    body.extend(s)
                    root, dirty = None, False
                    log.append('[组合] %s → %d 面' % (f.describe(), len(body)))
                else:
                    bb_body = K.bbox(body)
                    bb_s = K.bbox(s)
                    if bb_body and bb_s and not _bbox_overlap(bb_body, bb_s):
                        # 包围盒都不相交, 布尔运算可以直接跳过 —— 省掉最贵的一步
                        if f.op == CUT:
                            log.append('[跳过] %s —— 与零件不相交' % f.describe())
                        else:
                            body.extend(s)
                            root, dirty = None, False
                            log.append('[并排] %s —— 与零件不相交, 直接合并 → %d 面'
                                       % (f.describe(), len(body)))
                    elif f.op == CUT:
                        if root is None:
                            root = K.solid_begin(body)
                        K.solid_cut(root, s)        # 局部切除（就地改 BSP 累加器）
                        dirty = True
                        _sync_body()
                        log.append('[切除] %s → %d 面' % (f.describe(), len(body)))
                    else:
                        body = K.csg_union(body, s)
                        root, dirty = None, False
                        log.append('[合并] %s → %d 面' % (f.describe(), len(body)))
                self._check(t0, deadline, cancel, body, f.describe())
        _sync_body()
        self._check(t0, deadline, cancel, body, '收尾')
        self._cache = body
        self.last_log = log
        if progress:
            progress('完成', 1.0)
        return body

    # ---- 统计 ----
    def stats(self) -> Dict:
        body = self.build()
        bb = K.bbox(body)
        return {
            'faces': len(body),
            'triangles': len(K.polygons_to_triangles(body)),
            'bbox': bb,
            'size': None if not bb else (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]),
            'volume': K.volume_of(body) if body else 0.0,
            'area': K.surface_area(body) if body else 0.0,
        }

    # ---- 序列化 ----
    def to_dict(self) -> Dict:
        return {'format': 'ai-modeler', 'version': self.version, 'name': self.name,
                'source_prompt': self.source_prompt,
                'features': [f.to_dict() for f in self.features]}

    @staticmethod
    def from_dict(d: Dict) -> 'Document':
        doc = Document(d.get('name', 'Part1'))
        doc.features = [Feature.from_dict(x) for x in d.get('features', [])]
        doc.source_prompt = d.get('source_prompt', '')
        return doc
