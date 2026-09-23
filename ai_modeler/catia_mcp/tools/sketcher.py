# -*- coding: utf-8 -*-
r"""草图工具(11 个) —— 对应参考项目 catia-v5-mcp-server 的 sketcher 模块。

写这个模块时踩到/绕开的坑:
  · 桥是无状态的: 每次调用都是一个新 cscript 进程, 所以**每个**工具都要先在 CATIA 里
    重新定位"当前草图"(CurSketch): ① Part.InWorkObject(正在编辑的那个草图)
    ② 主实体里的最后一个草图 ③ 几何图形集里的最后一个草图。定位不到就明确报错。
  · 2D 几何一律用 Sketch.Factory2D, 但草图不在编辑状态时 Factory2D 是废的,
    所以 CurF2D 里先 OpenEdition 一次再取工厂。
  · Factory2D 没有 CreateArc: 圆弧就是"被裁的圆" —— CreateCircle(cx, cy, r, 起角, 终角),
    角度是 0..2π 的弧度(逆时针, 从 X 轴算)。
  · CreateSpline 要的是**由 Point2D 组成的数组**(不是坐标数组), 所以样条工具会先
    CreatePoint 再把点数组丢进去 —— 这些控制点同时也就是草图里的元素, 属正常现象。
  · 约束: Sketch.Constraints.AddMonoEltCst / AddBiEltCst, 元素先经
    Part.CreateReferenceFromObject 转 Reference; 数值再走 Constraint.Dimension.Value。
  · 中文只出现在 Fail 的说明里(脚本是 UTF-16, cscript 读得了); Out 的键与值都是 ASCII,
    给 AI 客户端看的说明文本全部在 Python 侧。
"""
from __future__ import annotations

import math

from ..bridge import run_vbs
from ._schema import A, I, N, S, arg_f, arg_i, arg_s, obj, vbs, vbs_num

NAME = 'sketcher'


# --------------------------------------------------------------------------
# VBScript 公共助手
# --------------------------------------------------------------------------

_H = r'''
' ===========================================================================
'  公共助手 (桥无状态, 每个工具调用都要重新定位对象)
' ===========================================================================
Sub HBAppend(o)
    On Error Resume Next
    Err.Clear
    If IsEmpty(gHB) Then Set gHB = Nothing
    If gHB Is Nothing Then Set gHB = Part().HybridBodies.Add()
    gHB.AppendHybridShape(o)
    Err.Clear
    On Error GoTo 0
End Sub

Function PlaneFor(nm, z)
    ' XY/YZ/ZX 三个基准面; z<>0 时在该面法向上做一张偏移平面
    Dim base, rf, off
    Set base = Part().OriginElements.PlaneXY
    If UCase(nm) = "YZ" Then Set base = Part().OriginElements.PlaneYZ
    If UCase(nm) = "ZX" Then Set base = Part().OriginElements.PlaneZX
    If Abs(z) < 0.000001 Then
        Set PlaneFor = base
        Exit Function
    End If
    Set rf = RefOf(base)
    Set off = HSF().AddNewPlaneOffset(rf, z, False)
    HBAppend off
    Part().Update
    Set PlaneFor = off
End Function

Function CurSketch()
    ' 当前草图: InWorkObject -> 主实体最后一个草图 -> 几何图形集里最后一个草图
    Dim o, b, hbs, hb, n, i, found
    Set CurSketch = Nothing
    Set found = Nothing
    On Error Resume Next
    Err.Clear
    Set o = Part().InWorkObject
    If Err.Number = 0 Then
        If TypeName(o) = "Sketch" Then Set found = o
    End If
    Err.Clear
    If found Is Nothing Then
        Set b = Body()
        n = b.Sketches.Count
        If Err.Number = 0 Then
            If n > 0 Then Set found = b.Sketches.Item(n)
        End If
        Err.Clear
    End If
    If found Is Nothing Then
        Set hbs = Part().HybridBodies
        n = hbs.Count
        If Err.Number = 0 Then
            For i = n To 1 Step -1
                Set hb = hbs.Item(i)
                If hb.HybridSketches.Count > 0 Then
                    Set found = hb.HybridSketches.Item(hb.HybridSketches.Count)
                    Exit For
                End If
            Next
        End If
        Err.Clear
    End If
    On Error GoTo 0
    Set CurSketch = found
End Function

Function CurF2D()
    ' 取当前草图的 Factory2D(顺带保证草图处于编辑状态)
    Dim skObj, f
    Set CurF2D = Nothing
    Set skObj = CurSketch()
    If skObj Is Nothing Then Exit Function
    On Error Resume Next
    Err.Clear
    skObj.OpenEdition
    Set f = skObj.Factory2D
    If Err.Number <> 0 Then Set f = Nothing
    Err.Clear
    On Error GoTo 0
    Set CurF2D = f
End Function
'''


# --------------------------------------------------------------------------
# 画图类工具共用的脚本骨架: 定位草图 -> On Error 包住创建 -> 回传元素数
# --------------------------------------------------------------------------

_DRAW = r'''Sub Main()
    Dim skObj, f2d, e, o%(locals)s
    Set skObj = CurSketch()
    If skObj Is Nothing Then Fail "%(tool)s: 找不到可编辑的草图, 请先调用 catia_create_sketch"
    Set f2d = CurF2D()
    If f2d Is Nothing Then Fail "%(tool)s: 取不到 Factory2D(草图无法进入编辑状态)"
%(pre)s
    On Error Resume Next
    Err.Clear
%(create)s
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "%(tool)s 不支持此操作: " & e
    End If
    Err.Clear
%(extra_out)s
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "sketch", skObj.Name
    Out "elements", skObj.GeometricElements.Count
    Out "constraints", skObj.Constraints.Count
End Sub
Main
'''


def _draw(tool: str, create: str, pre: str = '', extra_out: str = '', locals_: str = '') -> str:
    return _H + _DRAW % {'tool': tool, 'create': create, 'pre': pre,
                         'extra_out': extra_out, 'locals': locals_}


def _tail(v: dict) -> str:
    return '  当前草图 %s 元素数: %s, 约束数: %s' % (
        v.get('sketch') or '?', v.get('elements', '?'), v.get('constraints', '?'))


def _warn(v: dict) -> str:
    if v.get('update_err'):
        return '\n  ⚠ Part.Update 报错(几何可能没刷新): %s' % v['update_err']
    return ''


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

_CREATE_SKETCH = r'''Sub Main()
    Dim skObj, e
    On Error Resume Next
    Err.Clear
    Set skObj = Body().Sketches.Add(PlaneFor("%(plane)s", %(z)s))
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_create_sketch 不支持此操作: " & e
    End If
    skObj.OpenEdition
    Err.Clear
    On Error GoTo 0
    If Len(%(name)s) > 0 Then
        On Error Resume Next
        skObj.Name = %(name)s
        Err.Clear
        On Error GoTo 0
    End If
    On Error Resume Next
    Err.Clear
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "sketch", skObj.Name
    Out "elements", skObj.GeometricElements.Count
    Out "constraints", skObj.Constraints.Count
End Sub
Main
'''


def t_create_sketch(args):
    plane = (arg_s(args, 'plane', 'XY') or 'XY').strip().upper()
    if plane not in ('XY', 'YZ', 'ZX'):
        raise ValueError('plane 只能是 XY / YZ / ZX(或省略), 收到: %r' % plane)
    name = arg_s(args, 'name', '')
    z = arg_f(args, 'offset_z', 0.0)
    body = _H + _CREATE_SKETCH % {'plane': plane, 'z': vbs_num(z), 'name': vbs(name)}
    v = (run_vbs(body).get('vars') or {})
    ztxt = '在 %s 面法向偏移 %gmm 的平面上' % (plane, z) if abs(z) > 1e-9 else '在 %s 基准面上' % plane
    return ('已创建草图: %s, %s, 已 OpenEdition 进入编辑状态。\n'
            '  接下来用 catia_sketch_* 画几何, 画完调 catia_close_sketch 关闭并刷新。\n%s%s'
            % (v.get('sketch') or '(未取到)', ztxt, _tail(v), _warn(v)))


def t_close_sketch(args):
    body = _H + '''Sub Main()
    Dim skObj, e
    Set skObj = CurSketch()
    If skObj Is Nothing Then Fail "catia_close_sketch: 没有可关闭的草图"
    Out "sketch", skObj.Name
    Out "elements", skObj.GeometricElements.Count
    Out "constraints", skObj.Constraints.Count
    On Error Resume Next
    Err.Clear
    skObj.CloseEdition
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_close_sketch 不支持此操作: " & e
    End If
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
End Sub
Main
'''
    v = (run_vbs(body).get('vars') or {})
    return ('已关闭草图: %s(CloseEdition + Update)。\n'
            '  可以接着 catia_pad / catia_pocket 等基于这个草图做特征。\n%s%s'
            % (v.get('sketch') or '(未取到)', _tail(v), _warn(v)))


def t_sketch_line(args):
    x1, y1 = arg_f(args, 'x1'), arg_f(args, 'y1')
    x2, y2 = arg_f(args, 'x2'), arg_f(args, 'y2')
    ln = math.hypot(x2 - x1, y2 - y1)
    create = '    Set o = f2d.CreateLine(%s, %s, %s, %s)\n' % (
        vbs_num(x1), vbs_num(y1), vbs_num(x2), vbs_num(y2))
    v = (run_vbs(_draw('catia_sketch_line', create)).get('vars') or {})
    return ('已画直线: (%g, %g) -> (%g, %g), 长 %gmm。\n%s%s'
            % (x1, y1, x2, y2, ln, _tail(v), _warn(v)))


def _rect_lines(x0, y0, x1, y1) -> str:
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        out.append('    Set o = f2d.CreateLine(%s, %s, %s, %s)'
                   % (vbs_num(ax), vbs_num(ay), vbs_num(bx), vbs_num(by)))
    return '\n'.join(out) + '\n'


def t_sketch_rectangle(args):
    x1, y1 = arg_f(args, 'x1'), arg_f(args, 'y1')
    x2, y2 = arg_f(args, 'x2'), arg_f(args, 'y2')
    if abs(x2 - x1) < 1e-9 or abs(y2 - y1) < 1e-9:
        raise ValueError('两个角点不能重合/共线: (%g, %g) 与 (%g, %g)' % (x1, y1, x2, y2))
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    v = (run_vbs(_draw('catia_sketch_rectangle', _rect_lines(lo_x, lo_y, hi_x, hi_y)))
         .get('vars') or {})
    return ('已画矩形(4 条直线): 对角 (%g, %g)-(%g, %g), %g x %gmm, 中心 (%g, %g)。\n%s%s'
            % (x1, y1, x2, y2, hi_x - lo_x, hi_y - lo_y,
               (lo_x + hi_x) / 2.0, (lo_y + hi_y) / 2.0, _tail(v), _warn(v)))


def t_sketch_centered_rectangle(args):
    length = arg_f(args, 'length')
    width = arg_f(args, 'width')
    cx, cy = arg_f(args, 'cx'), arg_f(args, 'cy')
    if length <= 0 or width <= 0:
        raise ValueError('length / width 必须大于 0, 收到 %g x %g' % (length, width))
    lo_x, hi_x = cx - length / 2.0, cx + length / 2.0
    lo_y, hi_y = cy - width / 2.0, cy + width / 2.0
    v = (run_vbs(_draw('catia_sketch_centered_rectangle',
                       _rect_lines(lo_x, lo_y, hi_x, hi_y))).get('vars') or {})
    return ('已画中心矩形(4 条直线): %g x %gmm, 中心 (%g, %g), 角点 (%g, %g)-(%g, %g)。\n%s%s'
            % (length, width, cx, cy, lo_x, lo_y, hi_x, hi_y, _tail(v), _warn(v)))


def t_sketch_circle(args):
    cx, cy = arg_f(args, 'cx'), arg_f(args, 'cy')
    r = arg_f(args, 'radius')
    if r <= 0:
        raise ValueError('radius 必须大于 0, 收到 %g' % r)
    create = '    Set o = f2d.CreateClosedCircle(%s, %s, %s)\n' % (
        vbs_num(cx), vbs_num(cy), vbs_num(r))
    v = (run_vbs(_draw('catia_sketch_circle', create)).get('vars') or {})
    return ('已画整圆: 圆心 (%g, %g), 半径 %gmm(直径 %gmm)。\n%s%s'
            % (cx, cy, r, 2 * r, _tail(v), _warn(v)))


def t_sketch_arc(args):
    cx, cy = arg_f(args, 'cx'), arg_f(args, 'cy')
    r = arg_f(args, 'radius')
    a1, a2 = arg_f(args, 'start_angle'), arg_f(args, 'end_angle', 360.0)
    if r <= 0:
        raise ValueError('radius 必须大于 0, 收到 %g' % r)
    # Factory2D 没有 CreateArc: 圆弧 = CreateCircle(圆心, 半径, 起角, 终角), 弧度制
    create = '    Set o = f2d.CreateCircle(%s, %s, %s, %s, %s)\n' % (
        vbs_num(cx), vbs_num(cy), vbs_num(r),
        vbs_num(math.radians(a1)), vbs_num(math.radians(a2)))
    v = (run_vbs(_draw('catia_sketch_arc', create)).get('vars') or {})
    return ('已画圆弧: 圆心 (%g, %g), 半径 %gmm, 从 %g° 逆时针到 %g°。(API 用 CreateCircle + 弧度)\n%s%s'
            % (cx, cy, r, a1, a2, _tail(v), _warn(v)))


def t_sketch_spline(args):
    pts = args.get('points') or []
    if not isinstance(pts, (list, tuple)) or len(pts) < 2:
        raise ValueError('points 至少要有 2 个 [x, y] 点')
    coords = []
    for i, p in enumerate(pts):
        try:
            coords.append(float(p[0]))
            coords.append(float(p[1]))
        except (TypeError, IndexError, ValueError):
            raise ValueError('points[%d] 必须是 [x, y]: %r' % (i, p))
    arr = 'Array(%s)' % ', '.join(vbs_num(c) for c in coords)
    create = ('    c = %s\n'
              '    n = %d\n'
              '    ReDim pts2(n - 1)\n'
              '    For i = 0 To n - 1\n'
              '        Set pts2(i) = f2d.CreatePoint(c(i * 2), c(i * 2 + 1))\n'
              '    Next\n'
              '    Set o = f2d.CreateSpline(pts2)\n') % (arr, len(pts))
    body = _draw('catia_sketch_spline', create, locals_=', c, n, i, pts2')
    v = (run_vbs(body).get('vars') or {})
    shown = ', '.join('(%g, %g)' % (coords[i * 2], coords[i * 2 + 1])
                      for i in range(len(pts)))
    return ('已画 2D 样条: %d 个点 -> %s。\n'
            '  注意: CATIA 的 CreateSpline 要的是 Point2D 数组, 所以这些控制点同时也是草图里的元素。\n%s%s'
            % (len(pts), shown, _tail(v), _warn(v)))


def t_sketch_point(args):
    x, y = arg_f(args, 'x'), arg_f(args, 'y')
    create = '    Set o = f2d.CreatePoint(%s, %s)\n' % (vbs_num(x), vbs_num(y))
    v = (run_vbs(_draw('catia_sketch_point', create)).get('vars') or {})
    return ('已在草图里画点: (%g, %g)。\n%s%s' % (x, y, _tail(v), _warn(v)))


_CST_TYPES = {
    # CATConstraintType (来自 CATIA 类型库): 名称 -> (枚举值, 中文, 是否双元素)
    'length': (5, '长度(catCstTypeLength)', False),
    'distance': (1, '距离(catCstTypeDistance)', False),
    'radius': (14, '半径(catCstTypeRadius)', False),
    'angle': (6, '角度(catCstTypeAngle)', False),
    'coincidence': (2, '重合(catCstTypeOn)', True),
    'tangent': (4, '相切(catCstTypeTangency)', True),
}

_CONSTRAINT = r'''Sub Main()
    Dim skObj, cst, e, r1, r2, d, i1, i2, v
    Set skObj = CurSketch()
    If skObj Is Nothing Then Fail "catia_sketch_constraint: 找不到可编辑的草图"
    i1 = %(i1)d
    i2 = %(i2)d
    v = %(value)s
    If i1 < 1 Then Fail "catia_sketch_constraint: element_index 必须从 1 开始"
    If i1 > skObj.GeometricElements.Count Then Fail "catia_sketch_constraint: element_index 超出范围(草图元素只有 " & skObj.GeometricElements.Count & " 个)"
    On Error Resume Next
    Err.Clear
    Set r1 = RefOf(skObj.GeometricElements.Item(i1))
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_sketch_constraint 不支持此操作: " & e
    End If
    If i2 > 0 Then
        If i2 > skObj.GeometricElements.Count Then
            Err.Clear
            On Error GoTo 0
            Fail "catia_sketch_constraint: element_index2 超出范围"
        End If
        Set r2 = RefOf(skObj.GeometricElements.Item(i2))
        Set cst = skObj.Constraints.AddBiEltCst(%(ctype)d, r1, r2)
    Else
        Set cst = skObj.Constraints.AddMonoEltCst(%(ctype)d, r1)
    End If
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_sketch_constraint 不支持此操作: " & e
    End If
    Err.Clear
    Out "created", "1"
    If v <> 0 Then
        Set d = cst.Dimension
        If Err.Number = 0 Then
            If d Is Nothing Then
                Out "value_err", "Constraint.Dimension 为空"
            Else
                d.Value = v
                If Err.Number <> 0 Then Out "value_err", Err.Description
            End If
        Else
            Out "value_err", "取不到 Dimension: " & Err.Description
        End If
        Err.Clear
    End If
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "sketch", skObj.Name
    Out "elements", skObj.GeometricElements.Count
    Out "constraints", skObj.Constraints.Count
End Sub
Main
'''


def t_sketch_constraint(args):
    ctype = (arg_s(args, 'type', '') or '').strip().lower()
    if ctype not in _CST_TYPES:
        raise ValueError('type 只能是 %s, 收到: %r'
                         % (' / '.join(sorted(_CST_TYPES)), arg_s(args, 'type', '')))
    enum, zh, need_two = _CST_TYPES[ctype]
    i1 = arg_i(args, 'element_index', 1)
    i2 = arg_i(args, 'element_index2', 0)
    value = arg_f(args, 'value', 0.0)
    if need_two and i2 <= 0:
        # 重合/相切是双元素约束: 没给第二个元素就按 1 和 2 处理
        i2 = 2 if i1 == 1 else 1
    body = _H + _CONSTRAINT % {'i1': i1, 'i2': i2, 'ctype': enum, 'value': vbs_num(value)}
    v = (run_vbs(body).get('vars') or {})
    scope = ('元素 %d 与元素 %d' % (i1, i2)) if i2 > 0 else ('元素 %d' % i1)
    val_txt = ('并设置数值 %g' % value) if value else '未设数值'
    out = ['已创建约束: %s 作用在 %s 上, %s。' % (zh, scope, val_txt)]
    if v.get('value_err'):
        out.append('  ⚠ 约束已创建, 但数值没能写进去: %s' % v['value_err'])
    out.append(_tail(v))
    return '\n'.join(out) + _warn(v)


_GEO_TYPES = {
    0: '未知', 1: '轴(Axis2D)', 2: '点(Point2D)', 3: '直线(Line2D)', 4: '控制点(ControlPoint2D)',
    5: '圆/圆弧(Circle2D)', 6: '双曲线(Hyperbola2D)', 7: '抛物线(Parabola2D)',
    8: '椭圆(Ellipse2D)', 9: '样条(Spline2D)', 10: '点(3D)', 11: '直线(3D)', 12: '平面(3D)',
}


def t_sketch_get_geometry(args):
    body = _H + '''Sub Main()
    Dim skObj, i, o, n, t
    Set skObj = CurSketch()
    If skObj Is Nothing Then Fail "catia_sketch_get_geometry: 找不到草图"
    On Error Resume Next
    Err.Clear
    n = skObj.GeometricElements.Count
    If Err.Number <> 0 Then
        Dim e
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_sketch_get_geometry 不支持此操作: " & e
    End If
    Out "sketch", skObj.Name
    Out "count", n
    For i = 1 To n
        Set o = skObj.GeometricElements.Item(i)
        t = -1
        t = o.GeometricType
        If Err.Number <> 0 Then t = -1
        Err.Clear
        Out "el" & i, i & "|" & t & "|" & TypeName(o)
    Next
    Out "constraints", skObj.Constraints.Count
    Err.Clear
    On Error GoTo 0
End Sub
Main
'''
    v = (run_vbs(body).get('vars') or {})
    n = arg_i(v, 'count', 0)
    out = ['当前草图 %s: %d 个元素, 约束 %s 个 (序号就是 element_index 用的序号)'
           % (v.get('sketch') or '?', n, v.get('constraints', '?'))]
    for i in range(1, n + 1):
        raw = v.get('el%d' % i) or ''
        parts = raw.split('|')
        if len(parts) >= 3:
            try:
                gt = int(float(parts[1]))
            except ValueError:
                gt = -1
            zh = _GEO_TYPES.get(gt) or ('类型码 %s' % parts[1])
            out.append('  %s. %s  [%s]' % (parts[0], zh, parts[2]))
        else:
            out.append('  %d. %s' % (i, raw or '?'))
    if n == 0 and not v:
        out.append('  (干跑模式没有真的执行脚本, 所以取不到元素清单)')
    return '\n'.join(out)


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_create_sketch',
     '在指定支持平面(或 XY 面沿 Z 偏移的平面)上新建草图并进入编辑状态。之后用 '
     'catia_sketch_* 画几何, 画完用 catia_close_sketch 关闭。',
     obj({'plane': S('支持平面: XY / YZ / ZX, 默认 XY'),
          'offset_z': N('沿该平面法向的偏移(mm); 省略=0, 用 plane 指定的基准面本身'),
          'name': S('草图名(可留空, CATIA 自己编号)')},
         [], '新建草图'), t_create_sketch),

    ('catia_close_sketch',
     '关闭当前草图的编辑状态(CloseEdition)并刷新零件, 之后才能用它做拉伸/切除等特征。',
     obj({}), t_close_sketch),

    ('catia_sketch_line',
     '在当前草图里画一条直线(两点)。',
     obj({'x1': N('起点 X'), 'y1': N('起点 Y'), 'x2': N('终点 X'), 'y2': N('终点 Y')},
         ['x1', 'y1', 'x2', 'y2']), t_sketch_line),

    ('catia_sketch_rectangle',
     '在当前草图里画矩形: 给两个对角点, 内部用 4 条直线连成。',
     obj({'x1': N('角点 1 的 X'), 'y1': N('角点 1 的 Y'),
          'x2': N('角点 2 的 X'), 'y2': N('角点 2 的 Y')},
         ['x1', 'y1', 'x2', 'y2']), t_sketch_rectangle),

    ('catia_sketch_centered_rectangle',
     '在当前草图里画以 (cx, cy) 为中心的矩形(常用: 中心矩形 = 底板/凸台轮廓)。',
     obj({'length': N('X 方向长度(mm)'), 'width': N('Y 方向宽度(mm)'),
          'cx': N('中心 X, 默认 0'), 'cy': N('中心 Y, 默认 0')},
         ['length', 'width']), t_sketch_centered_rectangle),

    ('catia_sketch_circle',
     '在当前草图里画整圆(圆心 + 半径)。',
     obj({'cx': N('圆心 X'), 'cy': N('圆心 Y'), 'radius': N('半径(mm)')},
         ['cx', 'cy', 'radius']), t_sketch_circle),

    ('catia_sketch_arc',
     '在当前草图里画圆弧: 圆心 + 半径 + 起始/终止角(度, 逆时针, 从 X 轴算)。',
     obj({'cx': N('圆心 X'), 'cy': N('圆心 Y'), 'radius': N('半径(mm)'),
          'start_angle': N('起始角(度)'), 'end_angle': N('终止角(度), 默认 360')},
         ['cx', 'cy', 'radius', 'start_angle']), t_sketch_arc),

    ('catia_sketch_spline',
     '在当前草图里画 2D 样条(过给定点列)。',
     obj({'points': A('点列 [[x, y], ...], 至少 2 个', {'type': 'array', 'items': N('')})},
         ['points']), t_sketch_spline),

    ('catia_sketch_point',
     '在当前草图里画一个点(常用作定位/参考点)。',
     obj({'x': N('X'), 'y': N('Y')}, ['x', 'y']), t_sketch_point),

    ('catia_sketch_constraint',
     '给当前草图的元素加约束。type: length(长度) / distance(距离) / radius(半径) / '
     'angle(角度) / coincidence(重合) / tangent(相切); element_index 是元素序号(从 1 开始, '
     '用 catia_sketch_get_geometry 查), 重合/相切需要 element_index2。',
     obj({'type': S('约束类型: length / distance / radius / angle / coincidence / tangent'),
          'element_index': I('约束作用到的元素序号(从 1 开始, 默认 1)'),
          'element_index2': I('第二个元素序号(重合/相切用; 省略=自动取另一个元素)'),
          'value': N('约束数值(length/distance/radius 是 mm, angle 是度); 省略=只加几何约束')},
         ['type']), t_sketch_constraint),

    ('catia_sketch_get_geometry',
     '列出当前草图的元素个数与类型(以及约束个数), 同时给出后续工具要用的元素序号。',
     obj({}), t_sketch_get_geometry),
]
