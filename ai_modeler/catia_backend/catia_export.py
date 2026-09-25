# -*- coding: utf-8 -*-
"""CATIA V5 导出模块。

把特征树转换成 CATIA V5 可执行的东西, 有两种产物:

1. build_catia_features(doc)
   把特征树展开(阵列会被展开成重复特征)成一张扁平的、CATIA 友好的特征表。
   刻意只使用 CATIA 最稳定的几个 API: 草图(Sketch) + 凸台(Pad) + 凹槽(Pocket) + 旋转体(Shaft),
   并且会自动根据体积变化纠正切除方向, 因此对 CATIA 版本的依赖降到最低。

2. export_catia_script(doc, path)
   生成一段可在装有 CATIA V5 + pywin32 的机器上直接运行的 Python 自动化脚本。

注意: 限于 Z 轴方向的特征(本程序的自然语言解析器生成的特征默认全部是 Z 轴)。
      X/Y 轴特征会被记录为"跳过"并写入脚本注释。
"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import math
import os
import time
from typing import Dict, List

from ai_modeler.core import kernel as K
from ai_modeler.parts import std_pulley as SP


def _entry(**kw) -> Dict:
    e = {'kind': 'rect_pad', 'plane': 'XY', 'cx': 0.0, 'cy': 0.0, 'z': 0.0, 'rot': 0.0,
         'l': 0.0, 'w': 0.0, 'r': 0.0, 'h': 0.0, 'depth': 0.0, 'through': False,
         'corner': 0.0, 'profile': [], 'name': '', 'note': ''}
    e.update(kw)
    return e


def _circle_profile(r, n=48):
    return [[round(x, 4), round(y, 4)] for (x, y) in K.profile_circle(r, n)]


# --------------------------------------------------------------------------
#  回转体（圆台 / 球 / 自定义回转轮廓）
#
#  为什么不用 CATIA 的旋转体(Shaft): 实测通过 API 调 AddNewShaft 之后, 紧跟的
#  Part.Update 会让自动化连接直接中断(日志停在那一行, CATIA 端报"方法 Update 失败")。
#  所以这里改成"多层圆盘"近似 —— 只用凸台, 和车床车出来的一样, 稳定可用。
# --------------------------------------------------------------------------

def _radius_at(pts, y):
    """闭合轮廓在高度 y 处的最大半径(取外侧交点)。"""
    best = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if abs(y2 - y1) < 1e-9:
            continue
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        if lo - 1e-9 <= y <= hi + 1e-9:
            t = (y - y1) / (y2 - y1)
            x = x1 + t * (x2 - x1)
            if x > best:
                best = x
    return max(best, 0.0)


def _revolve_pads(profile, cx, cy, z0, name, note, steps=18):
    """把回转轮廓近似成若干层圆盘凸台。"""
    pts = [(float(a), float(b)) for (a, b) in (profile or [])]
    if len(pts) < 3:
        return []
    ys = [y for _r, y in pts]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-6:
        return []
    n = max(6, min(int(steps), 60))
    out = []
    for i in range(n):
        y1 = lo + (hi - lo) * i / n
        y2 = lo + (hi - lo) * (i + 1) / n
        r = _radius_at(pts, (y1 + y2) / 2.0)
        if r <= 1e-6:
            continue
        out.append(_entry(kind='circle_pad', cx=cx, cy=cy, z=z0 + y1, r=r,
                          h=(y2 - y1), name=name, note=note))
    return out

#
#  这些零件（三角转子 / 齿轮 / 叶轮 / 曲轴 …）在几何内核里大多是
#  "二维轮廓沿 Z 拉伸 (+ 中心孔)" 或 "若干圆柱组合", 因此可以映射到
#  CATIA 最稳的四个 API: 草图 + 凸台(Pad) + 凹槽(Pocket) + 旋转体(Shaft)。
#  能精确映射的精确映射, 只能近似的会在结果里明确写清楚哪一部分被简化。
# --------------------------------------------------------------------------

def _feature_bbox(f):
    """单独构建这一个特征, 取它自己的包围盒 (xmin,ymin,zmin,xmax,ymax,zmax)。"""
    try:
        polys = f.build({'tag': 0})
        return K.bbox(polys) if polys else None
    except Exception:
        return None


def _feature_bboxes(doc):
    """按 Document.build 的上下文顺序逐个特征算包围盒。

    Feature.build(ctx) 需要 ctx['bbox']（此前已累积的实体范围），
    孔/凹槽/复合件都靠它定位，所以不能只传 {'tag': i}。
    这里只累积所有实体（不做布尔运算）—— 包围盒与真实结果一致。
    """
    out = {}
    acc = []
    for i, f in enumerate(doc.features):
        if not f.enabled or f.kind == 'pattern':
            continue
        ctx = {'tag': i, 'bbox': K.bbox(acc) if acc else None}
        try:
            solids = [s for s in f.build(ctx) if s]
        except Exception:
            solids = []
        flat = []
        for s in solids:
            flat.extend(s if isinstance(s, list) else [s])
        if not flat:
            continue
        bb = K.bbox(flat)
        if bb:
            out[i] = bb
        acc.extend(flat)
    return out


def _fit_profile(prof, bb, anchor=(0.0, 0.0), target=(0.0, 0.0)):
    """把标称轮廓等比缩放, 并让**轮廓自身的原点(轴线)**落到 target 上。

    返回相对坐标(由调用方把 cx/cy 设成 target), 即:
        relative = p * s + (anchor - target)

    为什么要按"轮廓原点"对齐, 而不是"轮廓包围盒中心"对齐:
        三角转子的真实几何中心(质心, 也就是轴线)在原点, 但它的包围盒中心在
        (0, 5.316) —— Reuleaux 三角形的包围盒本来就不对称。早先按包围盒中心
        对齐, 于是转子的中心孔被切在了 (0, 5.316), **偏轴 5.3 mm**。真机导出的
        STL 证实了这一点: 孔壁顶点确实绕 (0, 5.32) 分布。凡是"剖面自成一体、
        轴线在剖面原点"的零件(转子/齿轮/凸轮/连杆), 都应该按原点对齐。
        (等比缩放仍按包围盒尺寸算, 保证外形大小和内核一致。)
    """
    pts = [(float(a), float(b)) for (a, b) in (prof or [])]
    if len(pts) < 3 or not bb:
        return pts
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    pw, ph = max(xs) - min(xs), max(ys) - min(ys)
    tw, th = bb[3] - bb[0], bb[4] - bb[1]
    if pw < 1e-9 or ph < 1e-9 or tw < 1e-9 or th < 1e-9:
        return pts
    s = ((tw / pw) + (th / ph)) / 2.0          # 等比, 避免轮廓变形
    ox, oy = anchor[0] - target[0], anchor[1] - target[1]
    return [(round(x * s + ox, 4), round(y * s + oy, 4)) for (x, y) in pts]


def _compound_entries(f, k, p, bb, warnings):
    """把零件库复合件展开成 CATIA 特征条目。"""
    made = []
    if not bb:
        warnings.append('特征「%s」几何为空, 已跳过。' % f.describe())
        return made
    x0, y0, z0, x1, y1, z1 = bb
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0      # 包围盒中心: 只用于放置轮廓
    c0 = p.get('center') or (0.0, 0.0, 0.0)
    ax, ay = float(c0[0]), float(c0[1])            # 零件轴心: 孔/回转体都该在这里
    h = max(z1 - z0, 0.1)
    rx, ry = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    r_out = max(rx, ry)
    nm, note = f.name, f.describe()

    def prof_pad(profile, height=None, z=None):
        made.append(_entry(kind='poly_pad', cx=cx, cy=cy, z=(z0 if z is None else z),
                           h=(h if height is None else height),
                           profile=_fit_profile(profile, bb, anchor=(ax, ay),
                                                target=(cx, cy)), name=nm, note=note))

    def circ(kind, radius, height=None, z=None, dx=0.0, dy=0.0, name=None):
        # 注意是 ax/ay(轴心), 不是 cx/cy(包围盒中心): 转子这类不对称零件的
        # 包围盒中心并不在轴线上, 用错地方孔就会偏心(实测偏 5.3 mm)。
        if kind.endswith('_cut'):
            # 切除: 草图放在材料上方, 向下切穿
            # (实测 CATIA 的凸台朝 +Z 长、凹槽朝 -Z 切, 方向相反)
            made.append(_entry(kind=kind, cx=ax + dx, cy=ay + dy,
                               z=(z1 + 1.0 if z is None else z),
                               r=radius, depth=(h if height is None else height) + 2.0,
                               name=(name or nm), note=note))
        else:
            made.append(_entry(kind=kind, cx=ax + dx, cy=ay + dy,
                               z=(z0 if z is None else z),
                               r=radius, h=(h if height is None else height),
                               name=(name or nm), note=note))

    if k == 'rotor':                                    # 三角转子: 精确
        prof_pad(K.profile_rotor(float(p.get('radius', 60.0)), int(p.get('n', 48))))
        bore = float(p.get('bore_d', 0.0) or 0.0)
        if bore > 0:
            circ('circle_cut', bore / 2.0, name=nm)

    elif k == 'helical_gear':                           # 斜齿轮: 齿形精确, 螺旋角省略
        prof_pad(K.profile_gear(int(p.get('teeth', 24)), float(p.get('module', 3.0))))
        bore = float(p.get('bore_d', 0.0) or 0.0)
        if bore > 0:
            circ('circle_cut', bore / 2.0, name=nm)
        warnings.append('斜齿轮「%s」导出为直齿（螺旋角 %.1f° 需在 CATIA 里用螺旋扫掠补）'
                        % (note, float(p.get('helix_deg', 0.0))))

    elif k == 'bevel_gear':                             # 锥齿轮: 齿形近似, 锥度省略
        prof_pad(K.profile_gear(int(p.get('teeth', 20)), float(p.get('module', 4.0))))
        bore = float(p.get('bore_d', 0.0) or 0.0)
        if bore > 0:
            circ('circle_cut', bore / 2.0, name=nm)
        warnings.append('锥齿轮「%s」按外径等比近似为直齿圆柱（锥面/齿向需在 CATIA 里调整）' % note)

    elif k == 'spoked_wheel':                           # 辐条轮: 轮缘 + 轮毂（辐条省略）
        circ('circle_pad', r_out, name=nm)
        circ('circle_cut', max(r_out - float(p.get('rim_w', 6.0)), 0.5),
             height=h + 2.0, name=nm)
        circ('circle_pad', float(p.get('hub_r', 15.0)), name=nm)
        warnings.append('辐条轮「%s」导出为 轮缘 + 轮毂（%d 根辐条需在 CATIA 里补）'
                        % (note, int(p.get('spokes', 5))))

    elif k == 'vbelt_pulley':
        # 盘体按标准外径做圆凸台, 再逐个把**标准 V 形槽**当多边形凹槽切出来
        # （槽剖面来自 GB/T 10412—2002, 见 ai_modeler/std_pulley.py）。
        # 不用 revolve 轮廓近似: 分层圆盘会把 V 槽抹平成"近似光轮"。
        circ('circle_pad', r_out, name=nm)
        groove_type = p.get('groove_type')
        if not groove_type:
            circ_cut_r = float(p.get('groove_depth') or 0.0)
            warnings.append('带轮「%s」导出为光轮（%d 条 V 型槽需在 CATIA 里开）'
                            % (note, int(p.get('grooves', 3))))
            if circ_cut_r:
                warnings.append('  参考: 原槽深约 %g mm' % circ_cut_r)
        else:
            try:
                s = SP.spec(float(p.get('pitch_d', 80.0)), int(p.get('grooves', 3)),
                            groove_type, float(p.get('height', 0.0)) or None,
                            bore_d=float(p.get('bore_d', 0.0) or 0.0))
            except Exception:
                s = None
            if not s:
                warnings.append('带轮「%s」的槽剖面参数不可用, 已导出为光轮' % note)
            else:
                # ⚠️ 这里**故意不再伪造 V 槽的切除特征**（实测与审计双重确认）：
                #   V 形槽是**绕轴的回转切除**，而本导出通道只有"草图 + 凸台/凹槽"
                #   两种线性操作，原理上做不出环形槽。旧代码硬塞了 3 个 poly_cut，
                #   其剖面用的是 (半径, 轴向) 两个坐标，却放在水平面上当线性凹槽用，
                #   而且**没设 depth**（切除类导出读 depth）→ depth=0。
                #   后果有两层：① 在 CATIA 里根本不切（出来是个光轮）；
                #   ② 0 深特征还会让整棵 Part 报错（与螺钉零深凹槽同一类故障）。
                #   与其发一个做不出效果、还可能把 Part 搞坏的假特征，不如
                #   出一块**准确的光轮盘体**，并把标准槽参数写清楚让用户回转切除。
                warnings.append(
                    '带轮「%s」导出为**光轮盘体**（外径与宽度准确）。%d 条 V 形槽是'
                    '回转切除，本通道只有"草图+凸台/凹槽"两种线性操作，做不出环形槽，'
                    '因此不伪造 —— 请在 CATIA 里用「旋转槽(Groove)」按下列标准参数补：'
                    '标准 %s、基准宽度 bd=%g、槽深 hf=%g、槽顶高 ha=%g、'
                    '槽间距 e=%g、槽角 φ=%g°。'
                    % (note, s['grooves'], s['standard'], s['base_width'],
                       s['hf'], s['ha'], s['pitch_e'], s['angle']))

    elif k == 'timing_pulley':
        # 同步带轮: 盘体按节圆直径做圆凸台; 轮齿是绕圆周的小凸台,
        # CATIA 里用一根草图画齿(或阵列)最快, 这里如实说明并给出标准参数。
        circ('circle_pad', float(p.get('pitch_d', 80.0)) / 2.0, name=nm)
        try:
            info = SP.timing_pitch(str(p.get('tooth_type', 'XL')))
        except Exception:
            info = None
        teeth = int(p.get('teeth', 0) or 0)
        bore = float(p.get('bore_d', 0.0) or 0.0)
        if bore > 0:
            circ('circle_cut', bore / 2.0, name=nm)
        if info:
            warnings.append('同步带轮「%s」导出盘体（节圆 ⌀%g, 内孔 ⌀%g）; '
                            '%d 个 %s 型轮齿（节距 %g、齿高 %g、齿顶宽 %g）'
                            '请在 CATIA 里用一根齿形草图做圆周阵列补上'
                            % (note, float(p.get('pitch_d', 80.0)), bore, teeth,
                               info['type'], info['pitch'], info['tooth_h'], info['tooth_tip']))
        else:
            warnings.append('同步带轮「%s」导出为光轮（齿形参数不可用）' % note)

    elif k == 'piston':                                 # 活塞: 回转体近似
        circ('circle_pad', r_out, name=nm)
        warnings.append('活塞「%s」导出为圆柱体（销孔/活塞环槽需在 CATIA 里补）' % note)

    elif k == 'camshaft':                               # 凸轮轴: 轴身 + 各凸轮
        circ('circle_pad', float(p.get('base_d', 30.0)) / 2.0, name=nm)
        lobes = max(1, int(p.get('lobes', 4)))
        cam_prof = K.profile_cam(float(p.get('base_d', 30.0)) / 2.0,
                                 float(p.get('lift', 8.0)),
                                 float(p.get('lobe_deg', 0.0) or 120.0))
        for i in range(lobes):
            zc = z0 + h * (i + 1) / (lobes + 1)
            made.append(_entry(kind='poly_pad', cx=ax, cy=ay, z=zc, h=h / (lobes + 1.5),
                               profile=cam_prof, name=nm, note=note))
        warnings.append('凸轮轴「%s」的凸轮相位为等分布置, 相位角请在 CATIA 里核对' % note)

    elif k == 'crankshaft':                             # 曲轴: 轴线沿 X，曲拐沿轴向排列
        # 网格内核的曲轴沿 X 轴；默认 circle_pad 沿 Z 轴，会让主轴与曲拐错位。
        # Plane X 对应 CATIA 的 YZ 草图平面，偏移和凸台长度都沿全局 X 轴。
        center = tuple(p.get('center', (0.0, 0.0, 0.0)))
        center_y, center_z = float(center[1]), float(center[2])
        main_d = float(p.get('main_d', 50.0))
        pin_d = float(p.get('pin_d', 42.0))
        stroke = float(p.get('stroke', 86.0))
        throws = max(1, int(p.get('throws', 4)))
        shaft_len = max(0.1, x1 - x0)
        spacing = shaft_len / throws
        web_w = min(max(2.0, main_d * 0.28), spacing * 0.25)
        pin_len = spacing * 0.42
        web_r = max(main_d / 2.0, stroke / 2.0 + pin_d / 2.0 + 1e-4)
        # 相邻圆盘之间必须留**远大于 CATIA 几何容差(0.001mm)**的搭接。
        # 旧值 1e-3 恰好等于容差: 真机 V5-6R2023 上第 12~17 个凸台开始
        # "方法 Update 失败", 之后每个操作都连锁失败(2026-09-26 实测日志
        #  ai_modeler_push_*.log: ok=11 fail=14)。改 0.5mm 实测 25/25 成功。
        join_overlap = min(0.5, web_w * 0.25, pin_len * 0.25)

        def axial_pad(start, end, radius, y):
            if end - start <= 1e-9:
                return
            made.append(_entry(kind='circle_pad', plane='X', cx=y,
                               cy=center_z, z=start, r=radius, h=end - start,
                               name=nm, note=note))

        cursor = x0
        for i in range(throws):
            x_mid = x0 + spacing * (i + 0.5)
            pin_left = x_mid - pin_len / 2.0
            pin_right = x_mid + pin_len / 2.0
            web_left = pin_left - web_w
            web_right = pin_right + web_w
            pin_y = center_y + (stroke / 2.0 if i % 2 == 0 else -stroke / 2.0)
            # 网格中每个曲拐有两片圆盘曲柄臂。保留其轴向位置和中心，
            # 使 CATIA 近似模型与预览对齐并保持连通。
            main_start = cursor - (join_overlap if i else 0.0)
            axial_pad(main_start, web_left + join_overlap,
                      main_d / 2.0, center_y)
            axial_pad(web_left, pin_left, web_r, center_y)
            axial_pad(pin_left - join_overlap, pin_right + join_overlap,
                      pin_d / 2.0, pin_y)
            axial_pad(pin_right, web_right, web_r, center_y)
            cursor = web_right
        axial_pad(cursor - join_overlap, x1, main_d / 2.0, center_y)
        warnings.append('曲轴「%s」按 X 轴主轴颈、%d 组交替偏置连杆颈和圆盘曲柄臂近似；'
                        '曲柄臂外形已简化' % (note, throws))

    elif k == 'impeller':                               # 叶轮: 轮毂 + 轮盘（叶片省略）
        circ('circle_pad', float(p.get('hub_d', 30.0)) / 2.0, name=nm)
        circ('circle_pad', r_out, name=nm)
        warnings.append('叶轮「%s」只导出了轮毂 + 轮盘（%d 片扭转叶片需用 CATIA 的扫掠做）'
                        % (note, int(p.get('blades', 9))))

    elif k == 'conrod':                                 # 连杆: 大小头 + 杆身
        bl = float(p.get('length', 120.0))
        circ('circle_pad', float(p.get('big_d', 48.0)) / 2.0, name=nm)
        circ('circle_pad', float(p.get('small_d', 24.0)) / 2.0,
             dx=0.0, dy=bl / 2.0, name=nm)
        sw = float(p.get('shank_w', 0) or (float(p.get('small_d', 24.0)) * 0.8))
        made.append(_entry(kind='rect_pad', cx=ax, cy=ay + bl / 4.0, z=z0, l=sw,
                           w=bl / 2.0, h=h, name=nm, note=note))
        for d, dy, tag in ((float(p.get('big_bore', 40.0)), 0.0, '大头'),
                           (float(p.get('small_bore', 18.0)), bl / 2.0, '小头')):
            if d > 0:
                made.append(_entry(kind='circle_cut', cx=ax, cy=ay + dy, z=z1 + 1.0,
                                   r=d / 2.0, depth=h + 2.0, name=nm, note=note))
        warnings.append('连杆「%s」按 大头 + 小头 + 直杆身 近似（杆身过渡需在 CATIA 里修）' % note)

    elif k == 'scroll_disc':                            # 涡旋盘: 圆盘近似
        circ('circle_pad', r_out, name=nm)
        warnings.append('涡旋盘「%s」只导出了底盘（%.1f 圈螺旋壁需用 CATIA 的螺旋/扫掠做）'
                        % (note, float(p.get('turns', 2.5))))

    elif k == 'cross_joint':                            # 万向节十字轴: 单轴近似
        circ('circle_pad', float(p.get('journal_d', 20.0)) / 2.0, name=nm)
        warnings.append('万向节「%s」只导出了 Z 向轴（另一根横轴需在 CATIA 里补）' % note)

    elif k == 'worm':                                   # 蜗杆: 光轴近似
        circ('circle_pad', float(p.get('radius', 20.0)), name=nm)
        warnings.append('蜗杆「%s」导出为光轴（螺旋齿需用 CATIA 的螺旋扫掠做）' % note)

    else:
        return []

    if made:
        warnings.append('零件库件「%s」已展开为 %d 个 CATIA 操作。' % (note, len(made)))
    return made


# 这些 kind 由 _compound_entries 处理 (零件库复合件)
COMPOUND_KINDS = ('rotor', 'helical_gear', 'bevel_gear', 'spoked_wheel', 'vbelt_pulley',
                  'timing_pulley',
                  'piston', 'camshaft', 'crankshaft', 'impeller', 'conrod',
                  'scroll_disc', 'cross_joint', 'worm')



def _apply_pattern(entries, pf, warnings):
    """把一组特征按阵列参数复制展开。"""
    p = pf.params
    count = max(1, int(p.get('count', 1)))
    out = []
    if p.get('mode', 'linear') == 'circular':
        total = float(p.get('angle', 360.0))
        full = bool(p.get('full', abs(total - 360.0) < 1e-6))
        step = total / count if full else total / max(1, count - 1)
        pcx, pcy, _pcz = p.get('center', (0.0, 0.0, 0.0))
        for i in range(count):
            a = math.radians(step * i)
            ca, sa = math.cos(a), math.sin(a)
            for e in entries:
                n = dict(e)
                dx, dy = e['cx'] - pcx, e['cy'] - pcy
                n['cx'] = round(pcx + dx * ca - dy * sa, 4)
                n['cy'] = round(pcy + dx * sa + dy * ca, 4)
                n['rot'] = round(e.get('rot', 0.0) + step * i, 4)
                out.append(n)
    else:
        dx, dy, dz = p.get('step', (0.0, 0.0, 0.0))
        for i in range(count):
            for e in entries:
                n = dict(e)
                n['cx'] = round(e['cx'] + dx * i, 4)
                n['cy'] = round(e['cy'] + dy * i, 4)
                n['z'] = round(e['z'] + dz * i, 4)
                out.append(n)
    warnings.append('阵列「%s」已展开为 %d 组显式特征 (避免依赖版本相关的阵列 API)'
                    % (pf.describe(), len(out)))
    return out


def build_catia_features(doc):
    """把特征树展开成 CATIA 特征表。返回 (特征表, 提示信息)。

    **轴线支持**：圆柱/孔可以放在 X / Y / Z 任意轴上（记录里带轴字母，
    宏会挑对应的基准面 PlaneYZ / PlaneZX / PlaneXY 做草图支撑面）。
    其它类型（长方体/轮廓拉伸）目前只支持 Z 轴，非 Z 轴会跳过并给出提示。

    为什么要做成"能放任意轴"：吊环的环体轴线是**垂直于螺杆**的（实测
    `GB825_88_M16` 环体是 ⌀63、绕 Y 轴的圆柱）。旧版对非 Z 轴一律
    「跳过 + 提示」，于是发到 CATIA 只剩一根螺杆 —— 用户看到的就是
    "模型不完整"，与零深凹槽那次是同一类故障（看到的东西和描述不一致）。
    """
    warnings: List[str] = []
    feats: List[Dict] = []
    by_index: Dict[int, List[Dict]] = {}
    # ⚠ 导出只需要**特征表**，网格只是拿来量包围盒。网格建不出来（多孔件 BSP
    #   碎片化撞面数上限，实测 EGT215 报"模型过于复杂 (64803 个面)"）
    #   **不应该让整条导出失败** —— 否则一个零件会因为"预览网格太复杂"
    #   连 CATIA 都发不过去。这里放宽上限，再兜一层异常，退化成没有包围盒继续导出。
    try:
        body = doc.build(max_faces=400000)
    except Exception as e:
        body = []
        warnings.append('预览网格未建出（%s），导出继续；依赖包围盒的孔位/'
                        '切除深度可能不准。' % str(e)[:60])
    need_bbox = any(f.kind in COMPOUND_KINDS for f in doc.features if f.enabled)
    try:
        bboxes = _feature_bboxes(doc) if need_bbox else {}
    except Exception:
        bboxes = {}

    #: 轴 → (面内 u 下标, 面内 v 下标, 轴向 w 下标)
    #  PlaneYZ 的两轴是 (Y, Z)、法向 X; PlaneZX 的两轴是 (Z, X)、法向 Y;
    #  PlaneXY 的两轴是 (X, Y)、法向 Z。
    _AXMAP = {'Z': (0, 1, 2), 'X': (1, 2, 0), 'Y': (2, 0, 1)}
    #: 这些特征类型支持任意轴（圆形草图，或自带轴向映射的分支）。
    #  ⚠ `extrude_holes` 必须列进来：型材的拉伸轴是 Y，不在白名单里会被整条跳过
    #    —— 实测 267 个型材的导出特征表**全空**（CATIA 里什么都建不出来），
    #    通道检查从 523/523 掉到 256/523。
    _AXIS_FREE = ('cylinder', 'hole', 'extrude_holes')

    for i, f in enumerate(doc.features):
        if not f.enabled:
            continue
        p = f.params
        is_cut = (f.op == 'cut')
        axis = (p.get('axis') or 'Z').upper()
        if axis not in _AXMAP:
            warnings.append('特征「%s」的轴「%s」无法识别，已跳过。'
                            % (f.describe(), axis))
            continue
        if axis != 'Z' and f.kind not in _AXIS_FREE:
            warnings.append('特征「%s」使用 %s 轴, 该类型（%s）目前只支持 Z 轴, '
                            '已跳过。' % (f.describe(), axis, f.kind))
            continue
        ui, vi, wi = _AXMAP[axis]

        made: List[Dict] = []
        k = f.kind

        if k == 'box':
            c = p.get('center', (0, 0, 0))
            h = float(p.get('height', 10))
            base = bool(p.get('base', True))
            z0 = float(c[2]) if base else float(c[2]) - h / 2.0
            l = float(p.get('length', 10))
            w = float(p.get('width', 10))
            corner = float(p.get('corner', 0) or 0)
            if corner > 1e-9:
                # 带圆角: 用内核算好的圆角矩形轮廓, 而不是 4 条直线的矩形
                # (否则 CATIA 里会是尖角, 与助手预览不一致且没有任何提示)
                prof = [[round(x, 4), round(y, 4)] for (x, y) in
                        K.profile_rect(l, w, corner, p.get('corner_mode', 'fillet'))]
                made.append(_entry(kind='poly_pad', cx=float(c[0]), cy=float(c[1]), z=z0,
                                   h=h, profile=prof, corner=corner,
                                   name=f.name, note=f.describe()))
            else:
                made.append(_entry(kind='rect_pad', cx=float(c[0]), cy=float(c[1]), z=z0,
                                   l=l, w=w, h=h, corner=corner,
                                   name=f.name, note=f.describe()))

        elif k == 'cylinder':
            c = p.get('center', (0, 0, 0))
            h = float(p.get('height', 10))
            base = bool(p.get('base', True))
            # 轴的平移量取 w 分量, 面内位置取 u/v 分量; 轴为 Z 时与旧行为一致
            off = float(c[wi]) if base else float(c[wi]) - h / 2.0
            r1 = float(p.get('radius', 5))
            r2 = p.get('radius2')
            r2 = r1 if r2 is None else float(r2)
            if abs(r1 - r2) < 1e-9:
                made.append(_entry(kind='circle_pad', plane=axis,
                                   cx=float(c[ui]), cy=float(c[vi]), z=off,
                                   r=r1, h=h, name=f.name, note=f.describe()))
            else:
                if axis != 'Z':
                    warnings.append('圆台「%s」在 %s 轴上暂不支持, 已跳过。'
                                    % (f.describe(), axis))
                    continue
                made = _revolve_pads([[0.0, 0.0], [r1, 0.0], [r2, h], [0.0, h]],
                                     float(c[0]), float(c[1]), off,
                                     f.name, f.describe())
                warnings.append('圆台「%s」用 %d 层圆盘近似(旋转体 API 在这套 CATIA 里不可用)。'
                                % (f.describe(), len(made)))

        elif k == 'sphere':
            c = p.get('center', (0, 0, 0))
            r = float(p.get('radius', 5))
            half = [[round(r * math.sin(math.pi * t / 20), 4),
                     round(-r * math.cos(math.pi * t / 20), 4)] for t in range(21)]
            half = [[0.0, -r]] + half + [[0.0, r]]
            made = _revolve_pads(half, float(c[0]), float(c[1]), float(c[2]),
                                 f.name, f.describe(), steps=24)
            warnings.append('球体「%s」用 %d 层圆盘近似(旋转体 API 在这套 CATIA 里不可用)。'
                            % (f.describe(), len(made)))

        elif k == 'extrude_holes':
            # 直写网格件（等截面拉伸 + 直孔）导出成 CATIA 能表达的形式：
            #   **1 个矩形凸台 + 每个孔一个圆凹槽**。
            # 注意 CATIA 侧用的是它自己的 C++ 内核，几百个凹槽是常规操作，
            # 不像我们的纯 Python 布尔那样有孔数上限。
            sa = float(p.get('sec_a', 80)); sb = float(p.get('sec_b', 80))
            ln = float(p.get('length', 100))
            ax = (p.get('axis') or 'Z').upper()
            ui2, vi2, wi2 = _AXMAP[ax]
            made.append(_entry(kind='rect_pad', plane=ax, cx=0.0, cy=0.0,
                               z=float(p.get('base', 0.0)), l=sa, w=sb, h=ln,
                               name=f.name, note=f.describe()))
            nm_hole = 0
            for h in (p.get('holes') or []):
                hd = float(h.get('d') or 0.0)
                if hd <= 0:
                    continue
                hax = (h.get('axis') or 'Z').upper()
                hm = _AXMAP[hax]
                hai = "XYZ".index(hax)
                # 孔位换算到"以零件中心为原点"的建坐标系。
                # ⚠ 两套面内轴序**不一样**，必须显式换一次：
                #   · 提取器给出的 (u, v) 是**下标升序**的另一两轴
                #     （Y 向孔 → u=X, v=Z）；
                #   · CATIA 的草图平面按 _AXMAP 排（Y 向孔 → PlaneZX = (Z, X)）。
                #   不换的话 Y 向孔的位置会沿对角线镜像（实测型材孔位全错）。
                rmin, rsiz = p.get('ref_min'), p.get('ref_size')
                u = float(h.get('u') or 0.0)
                v = float(h.get('v') or 0.0)
                oth = [q for q in range(3) if q != hai]
                if rmin and rsiz:
                    u = u - (float(rmin[oth[0]]) + float(rsiz[oth[0]]) / 2.0)
                    v = v - (float(rmin[oth[1]]) + float(rsiz[oth[1]]) / 2.0)
                by_letter = {"XYZ"[oth[0]]: u, "XYZ"[oth[1]]: v}
                u = by_letter["XYZ"[hm[0]]]
                v = by_letter["XYZ"[hm[1]]]
                # 通孔：沿孔轴贯穿整个零件。
                # ⚠ dims 的键要用**轴字母**：`_AXMAP` 给的是下标，
                #   直接拿下标当键会 KeyError（实测 'X' 找不到）。
                dims = {'X': sa, 'Y': sb, 'Z': ln}      # 截面两轴 + 拉伸轴
                dims[ax] = ln
                dims["XYZ"[ui2]] = sa
                dims["XYZ"[vi2]] = sb
                made.append(_entry(kind='circle_cut', plane=hax, cx=u, cy=v,
                                   z=dims[hax] / 2.0 + 2.0, r=hd / 2.0,
                                   depth=dims[hax] + 4.0, through=True,
                                   name='实测孔 ⌀%g' % hd, note=''))
                nm_hole += 1
            warnings.append('等截面拉伸件「%s」按 **1 个凸台 + %d 个实测孔** 导出 '
                            '(直写网格件; 孔位/孔径来自 STEP 实测)。'
                            % (f.describe(), nm_hole))

        elif k == 'prism':
            c = p.get('center', (0, 0, 0))
            prof = [[round(float(a), 4), round(float(b), 4)] for (a, b) in p.get('profile', [])]
            if len(prof) >= 3:
                made.append(_entry(kind='poly_pad', cx=float(c[0]), cy=float(c[1]),
                                   z=float(c[2]), h=float(p.get('height', 10)),
                                   profile=prof, name=f.name, note=f.describe()))

        elif k == 'hex_bolt':
            # 六角头螺栓 → CATIA 里对应两个特征: 六边形草图 Pad(头) + 圆形草图 Pad(杆)。
            # 对边 s → 外接圆半径 R = s/√3 (对边是两平行面距, 不能直接用 s/2)。
            d = float(p.get('shank_d', 12.0))
            L = float(p.get('length', 80.0))
            s = float(p.get('across_flats', 1.5 * d))
            kk = float(p.get('head_h', 0.7 * d))
            c = tuple(p.get('center', (0, 0, 0)))
            R = s / math.sqrt(3.0)
            hexp = [[round(R * math.cos(math.radians(60.0 * i)), 4),
                     round(R * math.sin(math.radians(60.0 * i)), 4)] for i in range(6)]
            made.append(_entry(kind='poly_pad', cx=float(c[0]), cy=float(c[1]),
                               z=float(c[2]), h=kk, profile=hexp,
                               name=f.name, note='六角头(对边%g)' % s))
            made.append(_entry(kind='circle_pad', cx=float(c[0]), cy=float(c[1]),
                               z=float(c[2]) + kk, r=d / 2.0, h=L,
                               name=f.name, note='光杆 Ø%g×%g(螺纹按装饰表示)' % (d, L)))

        elif k == 'gear':
            teeth = int(p.get('teeth', 20))
            mod = float(p.get('module', 2.0))
            h = float(p.get('height', 10))
            c = tuple(p.get('center', (0, 0, 0)))
            base = bool(p.get('base', True))
            # 内核里 base=False 时齿轮是关于中心对称的(z 从 -h/2 到 +h/2)。
            # 这里必须跟着算 z0, 否则 CATIA 里的齿轮会整体抬高 h/2, 之后按包围盒
            # 算出来的"通孔"就切不穿(实测: 顶部残留 9mm, 看起来像没有中心孔)。
            z0 = float(c[2]) if base else float(c[2]) - h / 2.0
            prof = [[round(x, 4), round(y, 4)] for (x, y) in K.profile_gear(teeth, mod)]
            made.append(_entry(kind='poly_pad', cx=float(c[0]), cy=float(c[1]), z=z0, h=h,
                               profile=prof, name=f.name, note=f.describe()))

        elif k == 'revolve':
            prof = [[round(float(a), 4), round(float(b), 4)] for (a, b) in p.get('profile', [])]
            if len(prof) >= 3:
                made = _revolve_pads(prof, 0.0, 0.0, 0.0, f.name, f.describe(), steps=24)
                warnings.append('回转体「%s」用 %d 层圆盘近似(旋转体 API 在这套 CATIA 里不可用)。'
                                % (f.describe(), len(made)))

        elif k == 'real_solid':
            # 实测 B-Rep 复刻件：自由曲面/型腔无法用"凸台 + 圆孔"的特征表表达。
            # 如实提示改走 STEP 导入，**不假装能建**（宁可少建也不糊弄）。
            warnings.append(
                '零件「%s」是**按实测 B-Rep 复刻**的（型腔/筋/台阶为自由曲面），'
                'CATIA 特征表无法表达：请用「导出 STEP」把实测几何导进 CATIA。'
                % f.describe())

        elif k == 'hole':
            dia = float(p.get('diameter', 10))
            r = dia / 2.0
            pm = p.get('pos', 'center')
            through = bool(p.get('through', True)) or not p.get('depth')
            depth = float(p.get('depth', 0) or 0)
            bb = K.bbox(body)
            # 沿**孔自己的轴**取范围（X/Y 轴孔要用对应的分量）
            if bb:
                lo, hi = float(bb[wi]), float(bb[wi + 3])
            else:
                lo, hi = 0.0, 0.0
            if pm == 'corners' and bb:
                m = float(p.get('margin', 0) or 0) + r
                u1, u2 = float(bb[ui]), float(bb[ui + 3])
                v1, v2 = float(bb[vi]), float(bb[vi + 3])
                positions = [(u1 + m, v1 + m), (u2 - m, v1 + m),
                             (u2 - m, v2 - m), (u1 + m, v2 - m)]
            elif pm == 'pcd':
                n = max(2, int(p.get('count', 4)))
                pr = float(p.get('pcd', 60.0)) / 2.0
                positions = [(pr * math.cos(2 * math.pi * i / n), pr * math.sin(2 * math.pi * i / n))
                             for i in range(n)]
            else:
                # Document hole coordinates use the two remaining global axes in
                # ascending XYZ order: X-axis -> (Y,Z), Y-axis -> (X,Z),
                # Z-axis -> (X,Y). CATIA sketch planes use _AXMAP order:
                # PlaneYZ -> (Y,Z), PlaneZX -> (Z,X), PlaneXY -> (X,Y).
                if axis == 'Z':
                    positions = [(float(p.get('x', 0.0)), float(p.get('y', 0.0)))]
                elif axis == 'X':
                    positions = [(float(p.get('x', 0.0)), float(p.get('y', 0.0)))]
                else:
                    positions = [(float(p.get('y', 0.0)), float(p.get('x', 0.0)))]
            for (x, y) in positions:
                if through:
                    # CATIA 的凹槽朝 -轴切(实测: 凸台朝 +轴长、凹槽朝 -轴切),
                    # 所以切割草图要放在材料"上方", 向下切穿。
                    made.append(_entry(kind='circle_cut', plane=axis, cx=x, cy=y,
                                       z=hi + 2.0, r=r,
                                       depth=(hi - lo) + 4.0, through=True,
                                       name=f.name, note=f.describe()))
                else:
                    # 盲孔从顶面往下钻
                    made.append(_entry(kind='circle_cut', plane=axis, cx=x, cy=y,
                                       z=hi + 0.5, r=r,
                                       depth=depth + 0.5, name=f.name,
                                       note=f.describe()))

        elif k == 'pocket':
            bb = K.bbox(body)
            lo, hi = (bb[2], bb[5]) if bb else (0.0, 50.0)
            c1, c2 = ((bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0) if bb else (0.0, 0.0)
            depth = float(p.get('depth', 5.0))
            corner = float(p.get('corner', 0) or 0)
            # 同上: 草图放顶面之上, 向下切 depth; 带圆角时用圆角轮廓
            if corner > 1e-9:
                prof = [[round(x, 4), round(y, 4)] for (x, y) in
                        K.profile_rect(float(p.get('length', 20)), float(p.get('width', 20)),
                                       corner, p.get('corner_mode', 'fillet'))]
                made.append(_entry(kind='poly_cut', cx=float(p.get('x', c1)),
                                   cy=float(p.get('y', c2)), z=hi + 0.5,
                                   depth=depth + 0.5, profile=prof, corner=corner,
                                   name=f.name, note=f.describe()))
            else:
                made.append(_entry(kind='rect_cut', cx=float(p.get('x', c1)),
                                   cy=float(p.get('y', c2)), z=hi + 0.5,
                                   l=float(p.get('length', 20)), w=float(p.get('width', 20)),
                                   depth=depth + 0.5, corner=corner,
                                   name=f.name, note=f.describe()))

        elif k == 'loft':
            warnings.append('放样体「%s」在 CATIA 中需要多截面曲面(Loft)功能, '
                            '已改用根部截面拉伸近似, 请在 CATIA 中手动调整。' % f.describe())
            chord = float(p.get('root_chord', 30))
            span = float(p.get('span', 60))
            prof2d = K.profile_airfoil(chord, float(p.get('thickness', 0.12)),
                                       float(p.get('camber', 0.05)), 0.4, 20)
            rot = p.get('rotate')
            t = tuple(p.get('translate', (0, 0, 0)))
            prof = []
            for (x, y) in prof2d:
                xx, yy = x - chord / 2.0, y
                if rot:
                    a = math.radians(rot[1])
                    xx, yy = xx * math.cos(a), xx * math.sin(a)
                prof.append([round(xx + t[0], 4), round(yy + t[1], 4)])
            made.append(_entry(kind='poly_pad', cx=0.0, cy=0.0, z=float(t[2]),
                               h=span, profile=prof, name=f.name, note=f.describe()))

        elif k in COMPOUND_KINDS:
            made = _compound_entries(f, k, p, bboxes.get(i), warnings)

        elif k == 'pattern':
            src = by_index.get(max([j for j in by_index if j < i], default=-1))
            if not src:
                warnings.append('阵列「%s」找不到源特征, 已跳过。' % f.describe())
                continue
            made = _apply_pattern(src, f, warnings)

        else:
            if k == 'helix':
                warnings.append('弹簧/螺旋「%s」用 CATIA 的草图+凸台表达不了, 已跳过; '
                                '可在 CATIA 里用「螺旋 + 扫掠」重建, 或改用「文件 → 导出 STL」再导入。'
                                % f.describe())
            else:
                warnings.append('特征类型「%s」(%s) 无法用 CATIA 的草图/凸台/凹槽/旋转体表达, 已跳过; '
                                '可改用「文件 → 导出 STL」再导入 CATIA。' % (f.describe(), k))
            continue

        if made:
            for e in made:
                if is_cut and not e['kind'].endswith('_cut'):
                    e['kind'] = e['kind'].replace('_pad', '_cut')
                    # 凸台是"从草图向上长", 凹槽是"从草图向下切" —— 转成切除时
                    # 把草图挪到原来的顶面, 这样切掉的范围才和原来一致
                    e['z'] = e['z'] + e['h']
                    # ⚠️ 必须同时把拉伸高度搬到 `depth` 上。
                    #    切除类导出读的是 `depth`(见 format_vbs_table:
                    #    `hv = depth if kind.endswith('_cut') else h`),
                    #    只改 kind/z 而不设 depth, 导出的就是**零深凹槽**。
                    #    实测后果(用户现场): CATIA 里 AddNewPocket(0) 之后
                    #    Part.Update 抛「方法 Update 失败」, Part 进入报错状态,
                    #    接着**所有** Sketches.Add 全部失败(报「缺少对象」),
                    #    最后只看得到草图和前两个凸台 —— 整条模型废掉。
                    e['depth'] = e['h']
            # 兜底校验: 任何非正的拉伸/切除厚度都不许发给 CATIA。
            # 零深特征在 CATIA 里不是"没效果", 而是"整棵 Part 报错", 必须拦住。
            fixed = 0
            for e in made:
                is_cut_e = str(e['kind']).endswith('_cut')
                key = 'depth' if is_cut_e else 'h'
                if float(e.get(key) or 0.0) <= 1e-9:
                    e[key] = 0.1
                    fixed += 1
            if fixed:
                warnings.append('特征「%s」的厚度算出来是 0，已按 0.1 mm 兜底 —— '
                                'CATIA 里零深凸台/凹槽会让整个零件报错，不能这样发过去。'
                                % f.describe())
            feats.extend(made)
            if f.kind != 'pattern':
                by_index[i] = made

    feats, dropped = _dedupe_cuts(feats)
    if dropped:
        warnings.append('已合并 %d 个重复切除(同一位置同一尺寸被描述了两遍, '
                        '例如零件库件自带中心孔 + 描述里又写了「中心孔直径x」)。' % dropped)
    return feats, warnings


def _dedupe_cuts(feats):
    """去掉"同一个孔被描述了两遍"的重复圆形切除。

    为什么需要: 零件库件(如三角转子)自带中心孔, 而描述里的「中心孔24」还会被
    通用规则再解析成一个孔特征 -> 同一个孔切成两次, 操作数虚高(实测 3 个操作)。
    齿轮没这个问题, 是因为它的参数表里没有「中心孔」这个别名, 只有通用规则生效。

    判据: **同一个平面**上, 半径相同, 且两个圆心相距不超过半径的一半 ——
    这种"近到重叠"的两个同径孔在机械上就是同一个孔(重复描述)。
    实测那两个的圆心是 转子绘制中心(0, 5.32) 和 (0,0), 相距 5.32 < 半径 12 的一半。
    保留先出现的那一个: 零件库件按自身几何中心放孔, 比通用规则的 (0,0) 更准。
    **半径不同的一律保留**: 沉孔那种"同中心、大小两个直径"必须都在。

    ⚠ 两条**必须**守住的边界（都踩过）:
      · 平面必须相同。`(cx, cy)` 只是"草图平面内的两个坐标", 不同轴的孔
        完全可能落在同一个 (cx, cy) 上 —— 型材的 X 向孔 (Y, Z) 与 Z 向孔
        (X, Y) 就有这种巧合。实测 EGT001 SW80x088 的 13 个实测孔被这条规则
        **吃掉 3 个 Z 向孔**（导出只剩 10 个），CATIA 里就少钻 3 个孔。
      · 距离阈值不能宽到"同径孔阵"。型材 30 mm 孔阵、⌀11 孔，旧阈值
        `max(1.0, r)`=5.5 mm 已经把不该合的合了；改成 0.5r（转子那例 5.32 < 6
        仍然合并，孔阵 15 mm 间距不会误合）。
    """
    kept, dropped = [], 0
    for e in feats:
        if e['kind'] == 'circle_cut':
            dup = False
            for k in kept:
                if k['kind'] != 'circle_cut' or abs(k['r'] - e['r']) > 1e-6:
                    continue
                if (k.get('plane') or 'Z') != (e.get('plane') or 'Z'):
                    continue
                if math.hypot(k['cx'] - e['cx'], k['cy'] - e['cy']) <= max(0.3, 0.5 * e['r']):
                    dup = True
                    break
            if dup:
                dropped += 1
                continue
        kept.append(e)
    return kept, dropped


# --------------------------------------------------------------------------
# 生成可执行的 CATIA 自动化脚本
# --------------------------------------------------------------------------

_TPL_DIR = os.path.dirname(os.path.abspath(__file__))


def _read_tpl(name: str) -> str:
    with open(os.path.join(_TPL_DIR, name), 'r', encoding='utf-8') as f:
        return f.read()


def render_script(doc, features, warnings, out_catpart=None) -> str:
    name = doc.name or 'Part1'
    out_catpart = out_catpart or (os.path.splitext('catia_%s.py' % name)[0] + '.CATPart')

    head = []
    head.append('# -*- coding: utf-8 -*-')
    head.append('"""由「AI 自动建模助手」生成的 CATIA V5 自动化脚本。')
    head.append('')
    head.append('运行前提: 本机已安装 CATIA V5 与 pywin32  (pip install pywin32)')
    head.append('运行方式: python ' + os.path.basename('catia_build.py'))
    head.append('生成时间: ' + time.strftime('%Y-%m-%d %H:%M:%S'))
    head.append('')
    head.append('原始需求: ' + (doc.source_prompt or '(无)'))
    head.append('')
    head.append('特征树 (%d 项 -> 展开为 %d 个 CATIA 操作):' % (len(doc.features), len(features)))
    for i, f in enumerate(doc.features, 1):
        head.append('    %2d. [%s] %s' % (i, '切除' if f.op == 'cut' else '合并', f.describe()))
    if warnings:
        head.append('')
        head.append('需要注意的地方:')
        for w in warnings:
            head.append('    ! ' + w)
    head.append('"""')
    head.append('')
    head.append('PART_NAME = %r' % name)
    head.append('OUTPUT_CATPart = %r' % out_catpart)
    head.append('')

    body = ['# ---------------------------------------------------------------------------',
            '# 特征表 (由自然语言解析结果展开而来)',
            '# ---------------------------------------------------------------------------',
            'FEATURES = [']
    for e in features:
        d = dict(e)
        d['profile'] = [[round(a, 4), round(b, 4)] for (a, b) in (d.get('profile') or [])]
        body.append('    ' + json.dumps(d, ensure_ascii=False) + ',')
    body.append(']')
    body.append('')

    return '\n'.join(head) + _read_tpl('catia_tpl_helpers.txt') + '\n\n' + \
        '\n'.join(body) + '\n' + _read_tpl('catia_tpl_main.txt')


def export_catia_script(doc, path: str) -> str:
    features, warnings = build_catia_features(doc)
    out_catpart = os.path.splitext(path)[0] + '.CATPart'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(render_script(doc, features, warnings, out_catpart))
    return path


def format_vbs_table(features) -> str:
    """把特征表格式化成 VBScript 可以直接 ExecuteGlobal 的字面量。

    记录格式(竖线分隔, 共 11 段):
        kind|cx|cy|z|n1|n2|n3|n4|name|profile|axis
    profile 为 "x1,y1;x2,y2;..." (无轮廓时留空)
    第 11 段是**轴字母** X/Y/Z —— 宏据此挑基准面（PlaneYZ/PlaneZX/PlaneXY）。
    只有 10 段的老表仍能跑：宏缺省按 Z 轴处理。
    """
    out = ['FEAT_COUNT = %d' % len(features)]
    for i, e in enumerate(features):
        prof = ';'.join('%g,%g' % (a, b) for (a, b) in (e.get('profile') or []))
        name = (e.get('name') or '').replace('|', '/').replace('"', "'")
        kind = str(e['kind'])
        # 拉伸类用 h, 切除类用 depth
        hv = e.get('depth', 0.0) if kind.endswith('_cut') else e.get('h', 0.0)
        rec = '|'.join([kind, '%g' % e['cx'], '%g' % e['cy'], '%g' % e['z'],
                        '%g' % e.get('l', 0), '%g' % e.get('w', 0),
                        '%g' % e.get('r', 0), '%g' % hv,
                        name, prof, _axis_of(e)])
        out.append('FEAT_%d = "%s"' % (i, rec))
    return '\n'.join(out) + '\n'


def _axis_of(e) -> str:
    """从特征条目里取**轴字母**。

    `plane` 字段历史上存的是平面名（'XY'），新代码直接存轴字母（'X'/'Y'/'Z'），
    两种都要认；否则老条目会被误判成别的轴。
    """
    pl = str((e or {}).get('plane') or 'Z').upper()
    if pl in ('X', 'Y', 'Z'):
        return pl
    return {'XY': 'Z', 'YZ': 'X', 'ZX': 'Y'}.get(pl, 'Z')


def export_catia_vbs(doc, path: str) -> str:
    """导出 VBScript 特征字面量表, 供 CATIA 宏 (CATScript) 读取。"""
    features, warnings = build_catia_features(doc)
    text = format_vbs_table(features)
    # VBScript 的 FileSystemObject 以 Unicode 读取最稳, 因此写 UTF-16LE + BOM
    with open(path, 'w', encoding='utf-16') as f:
        f.write(text)
        f.write("' 生成时间: " + time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
        f.write("' 原始需求: " + (doc.source_prompt or '') + '\n')
        for w in warnings:
            f.write("' 注意: " + w + '\n')
    return path


def export_catia_json(doc, path: str) -> str:
    """导出给 CATIA 插件(宏)使用的特征表 JSON。"""
    features, warnings = build_catia_features(doc)
    data = {'part_name': doc.name or 'Part1',
            'source_prompt': doc.source_prompt,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'features': features,
            'warnings': warnings}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------
# 直推通道: 生成独立 VBScript, 用 cscript + COM 直接建到 CATIA 里
# (不依赖 pywin32; 与 .CATScript 宏共用同一份构建核心)
# --------------------------------------------------------------------------

_PUSH_HEAD = r"""' ===========================================================================
'  AI Modeler  ->  CATIA V5   直接建模脚本
'  由「AI 自动建模助手」生成, 运行方式:  cscript //nologo "本文件"
'  不需要 pywin32: 通过 COM (GetObject) 附着到正在运行的 CATIA, 没有就启动它。
'  生成时间: @@TIME@@
'  原始需求: @@PROMPT@@
' ===========================================================================

Const PART_NAME   = @@PART_NAME@@
Const OUT_CATPART = @@OUT_CATPART@@
Const START_CMD   = @@START_CMD@@
Const LOG_PATH    = @@LOG_PATH@@
Const WAIT_SEC    = @@WAIT_SEC@@
Dim POST_BUILD_NOTICE
POST_BUILD_NOTICE = @@POST_BUILD_NOTICE@@

Dim CATIA, gPart, gBody, gSF, gHB, gFso, gLogPath

@@RECORDS@@

Main


' 过程日志: 万一某一步卡住, 打开这个文件就能看到停在哪
Sub Note(msg)
    On Error Resume Next
    Dim ts
    If Len(LOG_PATH) = 0 Then Exit Sub
    Set ts = gFso.OpenTextFile(LOG_PATH, 8, True)
    ts.WriteLine Time & "  " & msg
    ts.Close
    Err.Clear
End Sub


Sub Main()
    Dim i, total, okN, failN, failMsg, saved, doc
    Set gFso = CreateObject("Scripting.FileSystemObject")
    Set gHB = Nothing
    gLogPath = LOG_PATH
    Note "---- push start, features=" & FEAT_COUNT

    ' 先接上正在运行的 CATIA; 没有就启动它 (首次约 30-60 秒)
    If Not AttachOrStart(False) Then
        Note "no running CATIA, launching ..."
        If Not AttachOrStart(True) Then
            Note "CATIA not available"
            WScript.Echo "ERROR CATIA_NOT_AVAILABLE"
            WScript.Quit 3
        End If
    End If
    Note "attached to running CATIA"
    On Error Resume Next
    Err.Clear
    Note "  documents=" & CATIA.Documents.Count
    Err.Clear
    On Error GoTo 0

    On Error Resume Next
    CATIA.DisplayFileAlerts = False
    Err.Clear
    On Error GoTo 0

    ' 新建零件文档; CATIA 若刚好被关掉, 重新启动并重试
    Dim attempt, made
    made = False
    For attempt = 1 To 3
        On Error Resume Next
        Err.Clear
        Set doc = CATIA.Documents.Add("Part")
        If Err.Number = 0 And Not doc Is Nothing Then
            made = True
            Err.Clear
            On Error GoTo 0
            Exit For
        End If
        Note "create part attempt " & attempt & " failed: " & Err.Description
        Err.Clear
        On Error GoTo 0
        If attempt = 1 Then
            If AttachOrStart(True) Then Note "CATIA relaunched, retrying"
        End If
        WScript.Sleep 1500
    Next
    If Not made Then
        Note "cannot create part"
        WScript.Echo "ERROR CANNOT_CREATE_PART"
        WScript.Quit 4
    End If
    Note "new document: " & doc.Name

    Set gPart = doc.Part
    On Error Resume Next
    gPart.Name = PART_NAME
    Err.Clear
    On Error GoTo 0
    Set gBody = MainBodyOf(gPart)
    Set gSF = gPart.ShapeFactory
    Note "body: " & gBody.Name

    total = FEAT_COUNT
    okN = 0
    failN = 0
    failMsg = ""
    For i = 0 To total - 1
        Note "  [" & (i + 1) & "/" & total & "] " & Split(RECS(i), "|")(8)
        On Error Resume Next
        Err.Clear
        BuildOne RECS(i)
        If Err.Number <> 0 Then
            failN = failN + 1
            failMsg = failMsg & "  - " & Split(RECS(i), "|")(8) & " : " & Err.Description & vbCrLf
            Note "      FAILED: " & Err.Description
            Err.Clear
        Else
            okN = okN + 1
            Note "      ok"
        End If
        On Error GoTo 0
    Next

    On Error Resume Next
    gPart.Update
    Err.Clear
    On Error GoTo 0
    Note "features done: ok=" & okN & " fail=" & failN

    saved = ""
    If Len(OUT_CATPART) > 0 Then
        On Error Resume Next
        Err.Clear
        If gFso.FileExists(OUT_CATPART) Then gFso.DeleteFile OUT_CATPART, True
        doc.SaveAs OUT_CATPART
        If Err.Number = 0 Then
            saved = OUT_CATPART
            Note "saved: " & OUT_CATPART
        Else
            saved = "SAVE_FAILED " & Err.Number & " " & Err.Description
            Note "save failed (" & Err.Number & "): " & Err.Description
            Err.Clear
        End If
        On Error GoTo 0
    End If

    ' ---- show the result: bring CATIA to the front and fit the view ----
    ' (asked for explicitly: after building, the model must be visible)
    On Error Resume Next
    Err.Clear
    CATIA.Visible = True
    Err.Clear
    doc.Activate
    Err.Clear
    Dim vw
    Set vw = CATIA.ActiveWindow.ActiveViewer
    If IsObject(vw) Then vw.Reframe
    Err.Clear
    CreateObject("WScript.Shell").AppActivate "CATIA V5"
    Err.Clear
    On Error GoTo 0
    Note "shown: activated + reframe"

    If failN = 0 And Len(POST_BUILD_NOTICE) > 0 Then
        On Error Resume Next
        MsgBox POST_BUILD_NOTICE, vbInformation, "AI Modeler - CATIA 建模说明"
        Err.Clear
        On Error GoTo 0
    End If

    WScript.Echo "OK " & okN & " / " & total
    WScript.Echo "PART " & gPart.Name
    WScript.Echo "DOC " & doc.Name
    If failN > 0 Then WScript.Echo "FAILED " & failN & vbCrLf & failMsg
    If Left(saved, 12) = "SAVE_FAILED " Then
        WScript.Echo saved
    ElseIf Len(saved) > 0 Then
        WScript.Echo "SAVED " & saved
    Else
        ' 不自动保存: 由用户在 CATIA 里手动保存
        WScript.Echo "NOT_SAVED"
    End If
    If failN > 0 Then WScript.Quit 1
    WScript.Quit 0
End Sub


Function CatiaUsable()
    ' 能真正读到 Documents.Count 才算接上了 (GetObject 有时会返回不可用的引用)
    Dim n
    On Error Resume Next
    Err.Clear
    n = CATIA.Documents.Count
    If Err.Number = 0 Then
        CatiaUsable = True
    Else
        CatiaUsable = False
    End If
    Err.Clear
    On Error GoTo 0
End Function


Function AttachOrStart(forceStart)
    Dim sh, waits
    If Not forceStart Then
        On Error Resume Next
        Err.Clear
        Set CATIA = GetObject(, "CATIA.Application")
        If Err.Number = 0 Then
            If CatiaUsable() Then
                Err.Clear
                On Error GoTo 0
                AttachOrStart = True
                Exit Function
            End If
        End If
        Err.Clear
        On Error GoTo 0
    End If

    If Len(START_CMD) = 0 Then
        AttachOrStart = False
        Exit Function
    End If

    On Error Resume Next
    Set sh = CreateObject("WScript.Shell")
    sh.Run START_CMD, 1, False
    Err.Clear
    On Error GoTo 0

    waits = 0
    Do While waits < WAIT_SEC
        On Error Resume Next
        Err.Clear
        Set CATIA = GetObject(, "CATIA.Application")
        If Err.Number = 0 Then
            If CatiaUsable() Then
                Err.Clear
                On Error GoTo 0
                AttachOrStart = True
                Exit Function
            End If
        End If
        Err.Clear
        On Error GoTo 0
        WScript.Sleep 1000
        waits = waits + 1
    Loop
    AttachOrStart = False
End Function


@@CORE@@
"""


def _core_text() -> str:
    """共享的 CATIA 构建核心 (宏与直推脚本同一份, 保证行为一致)。"""
    root = str(PROJECT_ROOT)
    with open(os.path.join(root, 'catia_plugin', 'catia_build_core.vbs.txt'),
              'r', encoding='utf-8') as f:
        return f.read()


def _vbs_literal(value):
    """Encode text as a VBScript string expression without executing user text."""
    lines = str(value or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return ' & vbCrLf & '.join('"%s"' % line.replace('"', '""') for line in lines)


def _vbs_comment_text(value):
    """Keep untrusted text on one comment/record line in the generated script."""
    return ' '.join(str(value or '').replace('|', '/').split())


def render_push_vbs(doc, features, warnings, out_catpart='', start_cmd='', wait_sec=180,
                    log_path='', post_build_notice='') -> str:
    """生成可被 cscript 直接运行的独立 VBScript (UTF-16, 中文提示可读)。"""
    name = doc.name or 'Part1'
    prompt = _vbs_comment_text(doc.source_prompt or '(无)')
    recs = []
    for e in features:
        prof = ';'.join('%g,%g' % (a, b) for (a, b) in (e.get('profile') or []))
        nm = _vbs_comment_text(e.get('name') or '').replace('"', "'")
        kind = str(e['kind'])
        hv = e.get('depth', 0.0) if kind.endswith('_cut') else e.get('h', 0.0)
        rec = '|'.join([kind, '%g' % e['cx'], '%g' % e['cy'], '%g' % e['z'],
                        '%g' % e.get('l', 0), '%g' % e.get('w', 0),
                        '%g' % e.get('r', 0), '%g' % hv, nm, prof, _axis_of(e)])
        recs.append(rec.replace('"', "'"))
    lines = ['Dim RECS', 'FEAT_COUNT = %d' % len(recs)]
    if recs:
        body = ', _\n    '.join('"%s"' % r for r in recs)
        lines.append('RECS = Array( _\n    %s)' % body)
    else:
        lines.append('RECS = Array()')

    text = _PUSH_HEAD
    text = text.replace('@@RECORDS@@', '\n'.join(lines))
    text = text.replace('@@CORE@@', _core_text())
    text = text.replace('@@TIME@@', time.strftime('%Y-%m-%d %H:%M:%S'))
    text = text.replace('@@PROMPT@@', prompt.replace('\n', ' '))
    text = text.replace('@@PART_NAME@@', _vbs_literal(name))
    text = text.replace('@@OUT_CATPART@@', _vbs_literal(out_catpart))
    text = text.replace('@@START_CMD@@', _vbs_literal(start_cmd))
    text = text.replace('@@LOG_PATH@@', _vbs_literal(log_path))
    text = text.replace('@@POST_BUILD_NOTICE@@', _vbs_literal(post_build_notice))
    text = text.replace('@@WAIT_SEC@@', str(int(wait_sec)))
    return text


def export_push_vbs(doc, path: str, out_catpart='', start_cmd='', wait_sec=180,
                    log_path='', post_build_notice=''):
    """导出「直接建到 CATIA」的脚本; 返回 (路径, 特征数, 提示列表)。"""
    features, warnings = build_catia_features(doc)
    text = render_push_vbs(doc, features, warnings, out_catpart, start_cmd, wait_sec,
                           log_path, post_build_notice)
    with open(path, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(text)
    return path, len(features), warnings
