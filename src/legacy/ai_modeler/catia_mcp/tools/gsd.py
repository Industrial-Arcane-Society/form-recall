# -*- coding: utf-8 -*-
r"""GSD(创成式外形设计)工具(24 个) —— 对应参考项目 catia-v5-mcp-server 的 gsd 模块。

写这个模块时踩到/绕开的坑:
  · 桥是无状态的: 每次调用都是新的 cscript 进程, 所以"当前几何图形集"不能存在 Python 里,
    而是存在 CATIA 文档里(CurGeoset): ① Part.InWorkObject 是哪个几何图形集就用哪个
    (catia_gsd_set_active_geoset 就是设它) ② 否则用最后一个几何图形集 ③ 都没有就新建一个。
  · 元素用名字或序号(所有几何图形集里从头数的 1 基序号)定位(GsdFind), 主实体里的草图也认,
    所以"投影到平面/按草图拉伸"这类可以混着用。
  · 新建的元素一律 geoset.AppendHybridShape(obj) 再 Part.Update。
  · CATIA 各版本/语言版本的接口并不齐: 凡是**不确定有没有**的接口(swing/loft/blend/join/
    trim/split/fill/offset/thick/close)都先老老实实尝试, 成功后继续; 全部尝试都失败就
    Out "unsupported", "1" + 原始错误, Python 侧回一句"该版本不支持, 请在 CATIA 里手动建",
    绝不假装成功。基本元素(点/线/面/圆/样条/拉伸/旋转/对称)失败则直接 Fail 带原始错误。
  · 几个真机文档核对过的签名(CAA V5 VB Help, 2020 版接口文档):
      AddNewPointCoord(x, y, z) / AddNewLinePtPt(ref1, ref2) / AddNewPlaneOffset(ref, off, False)
      AddNewPlane3Points(r1, r2, r3) / AddNewSpline() + spline.AddPoint ref
      AddNewCircleCtrRadWithAngles(center, support, geodesic, radius, a1, a2)  (a 是弧度)
      AddNewExtrude(profile, offset_debut, offset_fin, **HybridShapeDirection**)
      AddNewRevol(profile, offset_debut, offset_fin, axis)
      AddNewOffset(obj, offset, orientation, precision) / AddNewProject(el, support)
      AddNewIntersection(e1, e2) / AddNewJoin(e1, e2) + join.AddElement ref
      AddNewHybridSplit(e1, e2, orientation) / AddNewHybridTrim(e1, o1, e2, o2)
      AddNewSymmetry(obj, ref) / AddNewLoft() + AddSectionToLoft(ref, ori, Nothing) + AddGuide ref
      AddNewSweepExplicit(profile, guide) / AddNewFill() + fill.AddBound ref
      AddNewBlend() + SetCurve/SetSupport/SetOrientation
      ShapeFactory(不是 HSF).AddNewThickSurface / AddNewCloseSurface / AddNewVolume*
  · 中文只出现在 Fail/Out 的说明里(脚本是 UTF-16, cscript 读得了); 给 AI 客户端的说明文本、
    schema、返回文本全部在 Python 侧。
"""
from __future__ import annotations

import math

from ..bridge import run_vbs
from ._schema import A, I, N, S, arg_f, arg_i, arg_s, obj, vbs, vbs_num

NAME = 'gsd'


# --------------------------------------------------------------------------
# VBScript 公共助手
# --------------------------------------------------------------------------

_H = r'''
' ===========================================================================
'  公共助手 (桥无状态: 每次调用都要重新定位几何图形集与元素)
' ===========================================================================
Function CurGeoset()
    ' 当前几何图形集: InWorkObject 指着的那个 -> 最后一个 -> 新建一个
    Dim hbs, o, i, gs
    Set CurGeoset = Nothing
    Set gs = Nothing
    On Error Resume Next
    Err.Clear
    Set hbs = Part().HybridBodies
    If Err.Number = 0 Then
        If hbs.Count = 0 Then
            Set gs = hbs.Add()
        Else
            Set o = Part().InWorkObject
            If Err.Number = 0 Then
                For i = 1 To hbs.Count
                    If hbs.Item(i) Is o Then Set gs = o
                Next
            End If
            Err.Clear
            If gs Is Nothing Then Set gs = hbs.Item(hbs.Count)
        End If
    End If
    Err.Clear
    On Error GoTo 0
    Set CurGeoset = gs
End Function

Function GsdFind(key)
    ' 按名字或序号(所有几何图形集里从头数的 1 基序号)找元素; 主实体里的草图也认
    Dim hbs, hb, i, j, n, sn, o, k, idx
    Set GsdFind = Nothing
    If Len(key) = 0 Then Exit Function
    On Error Resume Next
    Err.Clear
    k = 0
    If IsNumeric(key) Then k = CLng(key)
    idx = 0
    Set hbs = Part().HybridBodies
    n = hbs.Count
    If Err.Number = 0 Then
        For i = 1 To n
            Set hb = hbs.Item(i)
            For j = 1 To hb.HybridShapes.Count
                Set o = hb.HybridShapes.Item(j)
                idx = idx + 1
                If k > 0 Then
                    If idx = k Then Set GsdFind = o
                Else
                    If LCase(o.Name) = LCase(key) Then Set GsdFind = o
                End If
                If Not (GsdFind Is Nothing) Then Exit Function
            Next
        Next
    End If
    Err.Clear
    If GsdFind Is Nothing Then
        For sn = 1 To Body().Sketches.Count
            Set o = Body().Sketches.Item(sn)
            If LCase(o.Name) = LCase(key) Then Set GsdFind = o
            If Not (GsdFind Is Nothing) Then Exit Function
        Next
    End If
    Err.Clear
    On Error GoTo 0
End Function

Function SupRef(key)
    ' 参考: XY/YZ/ZX 用基准面, 其它按元素名/序号找
    Dim o
    Set SupRef = Nothing
    Select Case UCase(key)
        Case "XY"
            Set SupRef = RefOf(Part().OriginElements.PlaneXY)
        Case "YZ"
            Set SupRef = RefOf(Part().OriginElements.PlaneYZ)
        Case "ZX"
            Set SupRef = RefOf(Part().OriginElements.PlaneZX)
        Case Else
            Set o = GsdFind(key)
            If Not (o Is Nothing) Then Set SupRef = RefOf(o)
    End Select
End Function

Function DirObj(key)
    ' 拉伸方向: X/Y/Z 用坐标轴; 其它按元素(直线)取 --> HybridShapeDirection
    Dim o
    Set DirObj = Nothing
    Select Case UCase(key)
        Case "X"
            Set DirObj = HSF().AddNewDirectionByCoord(1, 0, 0)
        Case "Y"
            Set DirObj = HSF().AddNewDirectionByCoord(0, 1, 0)
        Case "Z"
            Set DirObj = HSF().AddNewDirectionByCoord(0, 0, 1)
        Case Else
            Set o = GsdFind(key)
            If Not (o Is Nothing) Then Set DirObj = HSF().AddNewDirection(RefOf(o))
    End Select
End Function

Sub SupReport(gs)
    Out "geoset", gs.Name
    Out "geoset_count", gs.HybridShapes.Count
End Sub
'''


# --------------------------------------------------------------------------
# 公共脚本骨架
# --------------------------------------------------------------------------

_FAIL = '        Fail "%s 不支持此操作: " & e\n'
_UNSUP = ('        Out "unsupported", "1"\n'
          '        Out "err", "%s: " & e\n'
          '        SupReport gs\n'
          '        Exit Sub\n')
_APPEND = '    gs.AppendHybridShape o\n'

_MAIN = r'''Sub Main()
    Dim gs, o, e, hs%(locals)s
    Set hs = HSF()
    Set gs = CurGeoset()
    If gs Is Nothing Then Fail "%(tool)s: 找不到几何图形集, 请先用 catia_gsd_create_geoset 建一个"
%(pre)s
    On Error Resume Next
    Err.Clear
%(call)s
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
%(onerr)s
    End If
    Err.Clear
%(append)s
    If Err.Number <> 0 Then
        Out "append_err", Err.Description
        Err.Clear
    End If
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "created", o.Name
    SupReport gs
End Sub
Main
'''


def _body(tool: str, call: str, pre: str = '', locals_: str = '',
          unsupported: bool = False, append: str = None) -> str:
    return _H + _MAIN % {
        'tool': tool, 'locals': locals_, 'pre': pre,
        'call': call.rstrip('\n'),
        'onerr': (_UNSUP if unsupported else _FAIL) % tool,
        'append': _APPEND if append is None else append,
    }


# --------------------------------------------------------------------------
# Python 侧小工具
# --------------------------------------------------------------------------

def _tail(v: dict) -> str:
    return '  当前几何图形集 %s 元素数: %s' % (v.get('geoset') or '?',
                                        v.get('geoset_count', '?'))


def _warns(v: dict) -> list:
    out = []
    if v.get('append_err'):
        out.append('  ⚠ 元素已创建, 但没能加入几何图形集: %s' % v['append_err'])
    if v.get('update_err'):
        out.append('  ⚠ Part.Update 报错(可能没刷新/几何有错): %s' % v['update_err'])
    return out


def _done(what: str, v: dict, extra: str = '') -> str:
    lines = ['已%s: %s' % (what, v.get('created') or '(未取到)')]
    if extra:
        lines.append(extra)
    lines.extend(_warns(v))
    lines.append(_tail(v))
    return '\n'.join(lines)


def _unsupported(what: str, v: dict) -> str:
    err = v.get('err') or '(未取到原始错误)'
    return ('未能通过 API 创建%s —— 该 CATIA 版本不支持此接口, 请在 CATIA 里手动建。\n'
            '  原始说明: %s\n%s' % (what, err, _tail(v)))


def _res(what: str, v: dict, extra: str = '') -> str:
    if v.get('unsupported') == '1':
        return _unsupported(what, v)
    return _done(what, v, extra)


def _refs(pairs) -> str:
    """生成"取元素 -> Reference, 找不到就 Fail"的 VBS 片段。

    pairs: [(变量名, 名称/序号, Fail 前缀文字), ...]
    变量本身请在调用方的 pre 里 Dim(VBScript 里同一过程内重复 Dim 会报"名称重定义")。
    """
    lines = []
    for var, key, label in pairs:
        lines.append('    Set %s = SupRef(%s)' % (var, vbs(key)))
        lines.append('    If %s Is Nothing Then Fail %s & %s'
                     % (var, vbs(label), vbs(str(key))))
    return '\n'.join(lines) + '\n'


def _arr(values) -> str:
    return 'Array(%s)' % ', '.join(vbs(t) for t in values)


def _str_list(args, key, min_len=1):
    val = args.get(key)
    if not isinstance(val, (list, tuple)) or len(val) < min_len:
        raise ValueError('%s 至少要有 %d 个元素(元素名或序号)' % (key, min_len))
    return [str(x) for x in val]


def _xyz_list(args, key, min_len=2):
    val = args.get(key)
    if not isinstance(val, (list, tuple)) or len(val) < min_len:
        raise ValueError('%s 至少要有 %d 个 [x, y, z] 点' % (key, min_len))
    out = []
    for i, p in enumerate(val):
        try:
            out.append((float(p[0]), float(p[1]), float(p[2])))
        except (TypeError, IndexError, ValueError):
            raise ValueError('%s[%d] 必须是 [x, y, z]: %r' % (key, i, p))
    return out


# --------------------------------------------------------------------------
# 工具实现: 几何图形集的创建 / 激活 / 列表
# --------------------------------------------------------------------------

def t_create_geoset(args):
    name = arg_s(args, 'name')
    body = _H + '''Sub Main()
    Dim gs, e
    On Error Resume Next
    Err.Clear
    Set gs = Part().HybridBodies.Add()
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_gsd_create_geoset 不支持此操作: " & e
    End If
    On Error GoTo 0
    If Len(%s) > 0 Then
        On Error Resume Next
        gs.Name = %s
        Err.Clear
        On Error GoTo 0
    End If
    On Error Resume Next
    Err.Clear
    Part().InWorkObject = gs
    If Err.Number <> 0 Then
        Out "inwork_err", Err.Description
        Err.Clear
    End If
    Part().Update
    Err.Clear
    On Error GoTo 0
    Out "geoset", gs.Name
    Out "geoset_count", gs.HybridShapes.Count
    Out "geosets", Part().HybridBodies.Count
End Sub
Main
''' % (vbs(name), vbs(name))
    v = (run_vbs(body).get('vars') or {})
    out = ['已创建几何图形集: %s, 并设为当前(InWorkObject)。'
           % (v.get('geoset') or name or '(未取到)')]
    if v.get('inwork_err'):
        out.append('  ⚠ 没能把它设为当前: %s' % v['inwork_err'])
    out.append('  当前几何图形集 %s 元素数: %s; 零件里共 %s 个几何图形集'
               % (v.get('geoset') or '?', v.get('geoset_count', '?'), v.get('geosets', '?')))
    return '\n'.join(out)


def t_set_active_geoset(args):
    name = arg_s(args, 'name')
    if not name:
        raise ValueError('缺少参数 name(要激活的几何图形集名)')
    body = _H + '''Sub Main()
    Dim hbs, gs, i, e
    Set gs = Nothing
    Set hbs = Part().HybridBodies
    For i = 1 To hbs.Count
        If LCase(hbs.Item(i).Name) = LCase(%s) Then Set gs = hbs.Item(i)
    Next
    If gs Is Nothing Then Fail "catia_gsd_set_active_geoset: 找不到几何图形集 " & %s
    On Error Resume Next
    Err.Clear
    Part().InWorkObject = gs
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        On Error GoTo 0
        Fail "catia_gsd_set_active_geoset 不支持此操作: " & e
    End If
    Err.Clear
    On Error GoTo 0
    Out "geoset", gs.Name
    Out "geoset_count", gs.HybridShapes.Count
    Out "geosets", hbs.Count
End Sub
Main
''' % (vbs(name), vbs(name))
    v = (run_vbs(body).get('vars') or {})
    return ('已把几何图形集 %s 设为当前(新元素都会进它)。\n'
            '  当前几何图形集 %s 元素数: %s; 零件里共 %s 个几何图形集'
            % (name, v.get('geoset') or '?', v.get('geoset_count', '?'), v.get('geosets', '?')))


def t_list_elements(args):
    body = _H + '''Sub Main()
    Dim hbs, hb, o, i, j, n, total
    Set hbs = Part().HybridBodies
    n = hbs.Count
    total = 0
    Out "geosets", n
    Out "body_sketches", Body().Sketches.Count
    Out "body_shapes", Body().Shapes.Count
    For i = 1 To n
        Set hb = hbs.Item(i)
        Out "gs" & i, hb.Name & "|" & hb.HybridShapes.Count
        For j = 1 To hb.HybridShapes.Count
            Set o = hb.HybridShapes.Item(j)
            total = total + 1
            Out "el" & total, hb.Name & "|" & o.Name & "|" & TypeName(o)
        Next
    Next
    Out "total", total
End Sub
Main
'''
    v = (run_vbs(body).get('vars') or {})
    n = arg_i(v, 'geosets', 0)
    total = arg_i(v, 'total', 0)
    out = ['几何图形集 %d 个, 共 %d 个元素(序号就是其它工具可直接用的全局序号):'
           % (n, total)]
    for i in range(1, n + 1):
        raw = v.get('gs%d' % i) or '?|?'
        parts = raw.split('|')
        out.append('  [%s] %s 个元素' % (parts[0], parts[1] if len(parts) > 1 else '?'))
    for k in range(1, total + 1):
        parts = (v.get('el%d' % k) or '||').split('|')
        out.append('  %d. %s  (%s)' % (k, parts[1] if len(parts) > 1 else '?',
                                       parts[2] if len(parts) > 2 else '?'))
    if not v:
        out.append('  (干跑模式没有真的执行脚本, 所以取不到清单)')
    out.append('  主实体: 草图 %s 个, 特征 %s 个'
               % (v.get('body_sketches', '?'), v.get('body_shapes', '?')))
    return '\n'.join(out)


# --------------------------------------------------------------------------
# 工具实现: 点 / 线 / 面 / 样条 / 圆
# --------------------------------------------------------------------------

def t_point(args):
    x, y, z = arg_f(args, 'x'), arg_f(args, 'y'), arg_f(args, 'z')
    call = '    Set o = hs.AddNewPointCoord(%s, %s, %s)\n' % (
        vbs_num(x), vbs_num(y), vbs_num(z))
    v = (run_vbs(_body('catia_gsd_point', call)).get('vars') or {})
    return _done('在 (%g, %g, %g) 创建点' % (x, y, z), v)


def t_line(args):
    x1, y1, z1 = arg_f(args, 'x1'), arg_f(args, 'y1'), arg_f(args, 'z1')
    x2, y2, z2 = arg_f(args, 'x2'), arg_f(args, 'y2'), arg_f(args, 'z2')
    ln = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)
    pre = ('    Dim p1, p2\n'
           '    On Error Resume Next\n'
           '    Err.Clear\n'
           '    Set p1 = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    Set p2 = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    If Err.Number <> 0 Then\n'
           '        e = Err.Description\n'
           '        Err.Clear\n'
           '        On Error GoTo 0\n'
           '        Fail "catia_gsd_line 不支持此操作: " & e\n'
           '    End If\n'
           '    gs.AppendHybridShape p1\n'
           '    gs.AppendHybridShape p2\n'
           '    Err.Clear\n'
           '    On Error GoTo 0\n'
           '    Out "p1", p1.Name\n'
           '    Out "p2", p2.Name\n'
           ) % (vbs_num(x1), vbs_num(y1), vbs_num(z1),
                vbs_num(x2), vbs_num(y2), vbs_num(z2))
    call = '    Set o = hs.AddNewLinePtPt(RefOf(p1), RefOf(p2))\n'
    v = (run_vbs(_body('catia_gsd_line', call, pre=pre)).get('vars') or {})
    return _done('创建直线 (%g, %g, %g) -> (%g, %g, %g), 长 %gmm'
                 % (x1, y1, z1, x2, y2, z2, ln), v,
                 '  两个端点 %s / %s 也一并建好并进了同一个几何图形集。'
                 % (v.get('p1') or '?', v.get('p2') or '?'))


def t_plane_offset(args):
    reference = (arg_s(args, 'reference', 'XY') or 'XY').strip()
    offset = arg_f(args, 'offset')
    pre = ('    Dim rp\n'
           '    Set rp = SupRef(%s)\n'
           '    If rp Is Nothing Then Fail "catia_gsd_plane_offset: 找不到参考平面 " & %s\n'
           % (vbs(reference), vbs(reference)))
    call = '    Set o = hs.AddNewPlaneOffset(rp, %s, False)\n' % vbs_num(offset)
    v = (run_vbs(_body('catia_gsd_plane_offset', call, pre=pre)).get('vars') or {})
    return _done('创建偏移平面(%s 面偏移 %gmm)' % (reference, offset), v)


def t_plane_3points(args):
    pts = [(arg_f(args, 'x1'), arg_f(args, 'y1'), arg_f(args, 'z1')),
           (arg_f(args, 'x2'), arg_f(args, 'y2'), arg_f(args, 'z2')),
           (arg_f(args, 'x3'), arg_f(args, 'y3'), arg_f(args, 'z3'))]
    pre = ('    Dim p1, p2, p3\n'
           '    On Error Resume Next\n'
           '    Err.Clear\n'
           '    Set p1 = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    Set p2 = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    Set p3 = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    If Err.Number <> 0 Then\n'
           '        e = Err.Description\n'
           '        Err.Clear\n'
           '        On Error GoTo 0\n'
           '        Fail "catia_gsd_plane_3points 不支持此操作: " & e\n'
           '    End If\n'
           '    gs.AppendHybridShape p1\n'
           '    gs.AppendHybridShape p2\n'
           '    gs.AppendHybridShape p3\n'
           '    Err.Clear\n'
           '    On Error GoTo 0\n'
           ) % (vbs_num(pts[0][0]), vbs_num(pts[0][1]), vbs_num(pts[0][2]),
                vbs_num(pts[1][0]), vbs_num(pts[1][1]), vbs_num(pts[1][2]),
                vbs_num(pts[2][0]), vbs_num(pts[2][1]), vbs_num(pts[2][2]))
    call = '    Set o = hs.AddNewPlane3Points(RefOf(p1), RefOf(p2), RefOf(p3))\n'
    v = (run_vbs(_body('catia_gsd_plane_3points', call, pre=pre)).get('vars') or {})
    return _done('用过三点 %s 创建平面' % (' / '.join('(%g, %g, %g)' % p for p in pts)), v,
                 '  三个点也一并建好(平面的支撑点)。')


def t_spline(args):
    pts = _xyz_list(args, 'points', 2)
    coords = []
    for p in pts:
        coords.extend(p)
    arr = 'Array(%s)' % ', '.join(vbs_num(c) for c in coords)
    pre = ('    Dim c, i, n, pt\n'
           '    c = %s\n'
           '    n = %d\n'
           % (arr, len(pts)))
    call = ('    Set o = hs.AddNewSpline()\n'
            '    If Err.Number = 0 Then\n'
            '        For i = 0 To n - 1\n'
            '            Set pt = hs.AddNewPointCoord(c(i * 3), c(i * 3 + 1), c(i * 3 + 2))\n'
            '            If Err.Number <> 0 Then Exit For\n'
            '            gs.AppendHybridShape pt\n'
            '            o.AddPoint RefOf(pt)\n'
            '            If Err.Number <> 0 Then Exit For\n'
            '        Next\n'
            '    End If\n')
    v = (run_vbs(_body('catia_gsd_spline', call, pre=pre)).get('vars') or {})
    return _done('创建 3D 样条(%d 个点)' % len(pts), v,
                 '  点列: %s(每个点也一并建成了 Point)' % ', '.join('(%g, %g, %g)' % p for p in pts))


def t_circle(args):
    cx, cy, cz = arg_f(args, 'cx'), arg_f(args, 'cy'), arg_f(args, 'cz')
    r = arg_f(args, 'radius')
    support = (arg_s(args, 'support', 'XY') or 'XY').strip()
    off = arg_f(args, 'offset', 0.0)
    a1 = math.radians(arg_f(args, 'start_angle', 0.0))
    a2 = math.radians(arg_f(args, 'end_angle', 360.0))
    if r <= 0:
        raise ValueError('radius 必须大于 0, 收到 %g' % r)
    pre = ('    Dim pc, sr, pl\n'
           '    On Error Resume Next\n'
           '    Err.Clear\n'
           '    Set pc = hs.AddNewPointCoord(%s, %s, %s)\n'
           '    If Err.Number <> 0 Then\n'
           '        e = Err.Description\n'
           '        Err.Clear\n'
           '        On Error GoTo 0\n'
           '        Fail "catia_gsd_circle 不支持此操作: " & e\n'
           '    End If\n'
           '    gs.AppendHybridShape pc\n'
           '    Err.Clear\n'
           '    Set sr = SupRef(%s)\n'
           '    If Not (sr Is Nothing) Then\n'
           '        If Abs(%s) > 0.000001 Then\n'
           '            Set pl = hs.AddNewPlaneOffset(sr, %s, False)\n'
           '            If Err.Number = 0 Then\n'
           '                gs.AppendHybridShape pl\n'
           '                Set sr = RefOf(pl)\n'
           '            End If\n'
           '        End If\n'
           '    End If\n'
           '    Err.Clear\n'
           '    On Error GoTo 0\n'
           '    If sr Is Nothing Then Fail "catia_gsd_circle: 找不到支撑平面 " & %s\n'
           ) % (vbs_num(cx), vbs_num(cy), vbs_num(cz), vbs(support),
                vbs_num(off), vbs_num(off), vbs(support))
    call = ('    Set o = hs.AddNewCircleCtrRadWithAngles(RefOf(pc), sr, False, %s, %s, %s)\n'
            '    If Err.Number = 0 Then\n'
            '        Out "method", "AddNewCircleCtrRadWithAngles"\n'
            '    Else\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewCircleCtrRad(RefOf(pc), sr, False, %s)\n'
            '        If Err.Number = 0 Then Out "method", "AddNewCircleCtrRad"\n'
            '    End If\n'
            ) % (vbs_num(r), vbs_num(a1), vbs_num(a2), vbs_num(r))
    v = (run_vbs(_body('catia_gsd_circle', call, pre=pre)).get('vars') or {})
    extra = ('  圆心 (%g, %g, %g), 半径 %gmm, 支撑面 %s%s; 用的接口: %s'
             % (cx, cy, cz, r, support,
                ('(偏移 %gmm)' % off) if abs(off) > 1e-9 else '',
                v.get('method') or '(未取到)'))
    return _done('创建空间圆', v, extra)


# --------------------------------------------------------------------------
# 工具实现: 投影 / 相交 / 曲面
# --------------------------------------------------------------------------

def t_project(args):
    element = arg_s(args, 'element')
    support = arg_s(args, 'support')
    if not element:
        raise ValueError('缺少参数 element(要投影的元素名或序号)')
    if not support:
        raise ValueError('缺少参数 support(投影到哪个支撑面: XY/YZ/ZX 或平面元素名)')
    pre = '    Dim r1, r2\n' + _refs((('r1', element, 'catia_gsd_project: 找不到要投影的元素 '),
                                    ('r2', support, 'catia_gsd_project: 找不到支撑面 ')))
    call = '    Set o = hs.AddNewProject(r1, r2)\n'
    v = (run_vbs(_body('catia_gsd_project', call, pre=pre)).get('vars') or {})
    return _done('创建投影(%s -> %s)' % (element, support), v)


def t_intersection(args):
    e1 = arg_s(args, 'element1')
    e2 = arg_s(args, 'element2')
    if not e1 or not e2:
        raise ValueError('缺少参数 element1 / element2(两个要相交的元素名或序号)')
    pre = '    Dim r1, r2\n' + _refs((('r1', e1, 'catia_gsd_intersection: 找不到元素 '),
                                    ('r2', e2, 'catia_gsd_intersection: 找不到元素 ')))
    call = '    Set o = hs.AddNewIntersection(r1, r2)\n'
    v = (run_vbs(_body('catia_gsd_intersection', call, pre=pre)).get('vars') or {})
    return _done('创建相交(%s x %s)' % (e1, e2), v)


def t_multi_section_surface(args):
    sections = _str_list(args, 'sections', 2)
    guides = [str(x) for x in (args.get('guides') or [])]
    pre = ('    Dim sc, gd, i, r\n'
           '    sc = %s\n'
           '    gd = %s\n' % (_arr(sections), _arr(guides)))
    call = ('    Set o = hs.AddNewLoft()\n'
            '    If Err.Number = 0 Then\n'
            '        For i = 0 To UBound(sc)\n'
            '            Set r = SupRef(sc(i))\n'
            '            If r Is Nothing Then\n'
            '                Err.Raise 1001, "gsd", "找不到截面 " & sc(i)\n'
            '            Else\n'
            '                o.AddSectionToLoft r, 1, Nothing\n'
            '            End If\n'
            '            If Err.Number <> 0 Then Exit For\n'
            '        Next\n'
            '    End If\n'
            '    If Err.Number = 0 Then\n'
            '        For i = 0 To UBound(gd)\n'
            '            If Len(gd(i)) > 0 Then\n'
            '                Set r = SupRef(gd(i))\n'
            '                If r Is Nothing Then\n'
            '                    Err.Raise 1002, "gsd", "找不到引导线 " & gd(i)\n'
            '                Else\n'
            '                    o.AddGuide r\n'
            '                End If\n'
            '                If Err.Number <> 0 Then Exit For\n'
            '            End If\n'
            '        Next\n'
            '    End If\n')
    v = (run_vbs(_body('catia_gsd_multi_section_surface', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    extra = '  截面 %d 个%s' % (len(sections),
                             (', 引导线 %d 条' % len(guides)) if guides else '')
    return _res('多截面曲面(放样)', v, extra)


def t_sweep(args):
    profile = arg_s(args, 'profile')
    guide = arg_s(args, 'guide')
    if not profile or not guide:
        raise ValueError('缺少参数 profile / guide(轮廓与引导线, 都是元素名或序号)')
    pre = _refs((('r1', profile, 'catia_gsd_sweep: 找不到轮廓 '),
                 ('r2', guide, 'catia_gsd_sweep: 找不到引导线 ')))
    call = '    Set o = hs.AddNewSweepExplicit(r1, r2)\n'
    v = (run_vbs(_body('catia_gsd_sweep', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    return _res('扫掠曲面(轮廓 %s 沿引导线 %s)' % (profile, guide), v)


def t_extrude(args):
    profile = arg_s(args, 'profile')
    direction = arg_s(args, 'direction')
    limit = arg_f(args, 'limit')
    limit2 = arg_f(args, 'limit2', 0.0)
    if not profile:
        raise ValueError('缺少参数 profile(要拉伸的轮廓: 元素名或序号)')
    if not direction:
        raise ValueError('缺少参数 direction(方向: X/Y/Z, 或一条直线元素的名字)')
    pre = '    Dim r1, d\n' + _refs((('r1', profile, 'catia_gsd_extrude: 找不到轮廓 '),))
    pre += ('    On Error Resume Next\n'
            '    Err.Clear\n'
            '    Set d = DirObj(%s)\n'
            '    If Err.Number <> 0 Then\n'
            '        e = Err.Description\n'
            '        Err.Clear\n'
            '        On Error GoTo 0\n'
            '        Fail "catia_gsd_extrude 不支持此操作: " & e\n'
            '    End If\n'
            '    Err.Clear\n'
            '    On Error GoTo 0\n'
            '    If d Is Nothing Then Fail "catia_gsd_extrude: 找不到方向 " & %s\n'
            % (vbs(direction), vbs(direction)))
    call = ('    Set o = hs.AddNewExtrude(r1, %s, %s, d)\n'
            % (vbs_num(limit), vbs_num(limit2)))
    v = (run_vbs(_body('catia_gsd_extrude', call, pre=pre)).get('vars') or {})
    extra = ('  轮廓 %s, 方向 %s, 起止偏移 %g / %gmm(拉伸长度 %gmm)'
             % (profile, direction, limit, limit2, abs(limit - limit2)))
    return _done('创建拉伸曲面', v, extra)


def t_revolve(args):
    profile = arg_s(args, 'profile')
    axis = arg_s(args, 'axis')
    angle = arg_f(args, 'angle', 360.0)
    if not profile or not axis:
        raise ValueError('缺少参数 profile / axis(轮廓与旋转轴, 都是元素名或序号)')
    if angle == 0:
        raise ValueError('angle 不能是 0')
    pre = '    Dim r1, r2\n' + _refs((('r1', profile, 'catia_gsd_revolve: 找不到轮廓 '),
                                    ('r2', axis, 'catia_gsd_revolve: 找不到旋转轴 ')))
    call = ('    Set o = hs.AddNewRevol(r1, 0, %s, r2)\n'
            '    If Err.Number = 0 Then\n'
            '        Out "method", "AddNewRevol(profile, 0, angle, axis)"\n'
            '    Else\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewRevolve(r1, r2, 0, %s)\n'
            '        If Err.Number = 0 Then Out "method", "AddNewRevolve(profile, axis, 0, angle)"\n'
            '    End If\n') % (vbs_num(math.radians(angle)), vbs_num(math.radians(angle)))
    v = (run_vbs(_body('catia_gsd_revolve', call, pre=pre)).get('vars') or {})
    return _done('创建旋转曲面(轮廓 %s 绕 %s 转 %g°)' % (profile, axis, angle), v,
                 '  用的接口: %s' % (v.get('method') or '(未取到)'))


def t_fill(args):
    boundary = _str_list(args, 'boundary', 1)
    pre = ('    Dim bd, i, r\n'
           '    bd = %s\n' % _arr(boundary))
    call = ('    Set o = hs.AddNewFill()\n'
            '    If Err.Number = 0 Then\n'
            '        For i = 0 To UBound(bd)\n'
            '            Set r = SupRef(bd(i))\n'
            '            If r Is Nothing Then\n'
            '                Err.Raise 1004, "gsd", "找不到边界 " & bd(i)\n'
            '            Else\n'
            '                o.AddBound r\n'
            '            End If\n'
            '            If Err.Number <> 0 Then Exit For\n'
            '        Next\n'
            '    End If\n')
    v = (run_vbs(_body('catia_gsd_fill', call, pre=pre, unsupported=True)).get('vars') or {})
    return _res('填充曲面(边界 %d 条: %s)' % (len(boundary), ', '.join(boundary)), v)


def t_offset_surface(args):
    surface = arg_s(args, 'surface')
    offset = arg_f(args, 'offset')
    if not surface:
        raise ValueError('缺少参数 surface(要偏移的元素名或序号)')
    if offset == 0:
        raise ValueError('offset 不能是 0')
    pre = '    Dim r1\n' + _refs((('r1', surface, 'catia_gsd_offset_surface: 找不到元素 '),))
    call = ('    Set o = hs.AddNewOffset(r1, %s, False, 0.001)\n'
            '    If Err.Number <> 0 Then\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewOffset(r1, %s, False)\n'
            '    End If\n'
            '    If Err.Number <> 0 Then\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewOffset(r1, %s)\n'
            '    End If\n') % (vbs_num(offset), vbs_num(offset), vbs_num(offset))
    v = (run_vbs(_body('catia_gsd_offset_surface', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    return _res('偏移(%s, %gmm)' % (surface, offset), v)


def t_join(args):
    elements = _str_list(args, 'elements', 2)
    pre = ('    Dim el, i, r, r1, r2\n'
           '    el = %s\n'
           '    Set r1 = SupRef(el(0))\n'
           '    If r1 Is Nothing Then Fail "catia_gsd_join: 找不到元素 " & el(0)\n'
           '    Set r2 = SupRef(el(1))\n'
           '    If r2 Is Nothing Then Fail "catia_gsd_join: 找不到元素 " & el(1)\n'
           % _arr(elements))
    call = ('    Set o = hs.AddNewJoin(r1, r2)\n'
            '    If Err.Number = 0 Then\n'
            '        For i = 2 To UBound(el)\n'
            '            Set r = SupRef(el(i))\n'
            '            If r Is Nothing Then\n'
            '                Err.Raise 1005, "gsd", "找不到元素 " & el(i)\n'
            '            Else\n'
            '                o.AddElement r\n'
            '            End If\n'
            '            If Err.Number <> 0 Then Exit For\n'
            '        Next\n'
            '    End If\n')
    v = (run_vbs(_body('catia_gsd_join', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    return _res('接合 %d 个元素(%s)' % (len(elements), ', '.join(elements)), v)


def t_split(args):
    element = arg_s(args, 'element')
    cutter = arg_s(args, 'cutter')
    orient = arg_i(args, 'orientation', 1)
    if not element or not cutter:
        raise ValueError('缺少参数 element / cutter(被切元素与切割元素)')
    pre = '    Dim r1, r2\n' + _refs((('r1', element, 'catia_gsd_split: 找不到被切元素 '),
                                    ('r2', cutter, 'catia_gsd_split: 找不到切割元素 ')))
    call = ('    Set o = hs.AddNewHybridSplit(r1, r2, %d)\n'
            '    If Err.Number <> 0 Then\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewSplit(r1, r2)\n'
            '        If Err.Number = 0 Then Out "method", "AddNewSplit(element, cutter)"\n'
            '    Else\n'
            '        Out "method", "AddNewHybridSplit(element, cutter, orientation)"\n'
            '    End If\n') % orient
    v = (run_vbs(_body('catia_gsd_split', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    return _res('切割(%s 被 %s 切, orientation=%d)' % (element, cutter, orient), v,
                '  用的接口: %s(保留哪一侧不对的话, 把 orientation 换成 -1 再试)'
                % (v.get('method') or '(未取到)'))


def t_trim(args):
    e1 = arg_s(args, 'element1')
    e2 = arg_s(args, 'element2')
    if not e1 or not e2:
        raise ValueError('缺少参数 element1 / element2(两个互相修剪的元素)')
    pre = '    Dim r1, r2\n' + _refs((('r1', e1, 'catia_gsd_trim: 找不到元素 '),
                                    ('r2', e2, 'catia_gsd_trim: 找不到元素 ')))
    call = ('    Set o = hs.AddNewHybridTrim(r1, 1, r2, 1)\n'
            '    If Err.Number = 0 Then\n'
            '        Out "method", "AddNewHybridTrim(e1, 1, e2, 1)"\n'
            '    Else\n'
            '        Err.Clear\n'
            '        Set o = hs.AddNewTrim(r1, r2, 1, 1)\n'
            '        If Err.Number <> 0 Then\n'
            '            Err.Clear\n'
            '            Set o = hs.AddNewTrim(r1, r2)\n'
            '        End If\n'
            '        If Err.Number = 0 Then Out "method", "AddNewTrim"\n'
            '    End If\n')
    v = (run_vbs(_body('catia_gsd_trim', call, pre=pre,
                       unsupported=True)).get('vars') or {})
    return _res('修剪(%s 与 %s)' % (e1, e2), v,
                '  用的接口: %s' % (v.get('method') or '(未取到)'))


def t_symmetry(args):
    element = arg_s(args, 'element')
    reference = arg_s(args, 'reference')
    if not element or not reference:
        raise ValueError('缺少参数 element / reference(元素与对称参考: XY/YZ/ZX 或平面/直线元素)')
    pre = '    Dim r1, r2\n' + _refs((('r1', element, 'catia_gsd_symmetry: 找不到元素 '),
                                    ('r2', reference, 'catia_gsd_symmetry: 找不到参考 ')))
    call = '    Set o = hs.AddNewSymmetry(r1, r2)\n'
    v = (run_vbs(_body('catia_gsd_symmetry', call, pre=pre)).get('vars') or {})
    return _done('创建对称(%s 关于 %s)' % (element, reference), v)


def t_blend(args):
    c1 = arg_s(args, 'curve1')
    c2 = arg_s(args, 'curve2')
    s1 = arg_s(args, 'support1')
    s2 = arg_s(args, 'support2')
    if not c1 or not c2:
        raise ValueError('缺少参数 curve1 / curve2(两条要桥接的曲线)')
    pre = ('    Dim r1, r2, s1, s2, flipped\n'
           '    flipped = 0\n')
    pre += _refs((('r1', c1, 'catia_gsd_blend: 找不到曲线 '),
                  ('r2', c2, 'catia_gsd_blend: 找不到曲线 ')))
    pre += ('    Set s1 = Nothing\n'
            '    Set s2 = Nothing\n'
            '    If Len(%s) > 0 Then\n'
            '        Set s1 = SupRef(%s)\n'
            '        If s1 Is Nothing Then Fail "catia_gsd_blend: 找不到支撑面 " & %s\n'
            '    End If\n'
            '    If Len(%s) > 0 Then\n'
            '        Set s2 = SupRef(%s)\n'
            '        If s2 Is Nothing Then Fail "catia_gsd_blend: 找不到支撑面 " & %s\n'
            '    End If\n'
            % (vbs(s1), vbs(s1), vbs(s1), vbs(s2), vbs(s2), vbs(s2)))
    call = ('    Set o = hs.AddNewBlend()\n'
            '    If Err.Number = 0 Then\n'
            '        o.SetCurve 1, r1\n'
            '        o.SetCurve 2, r2\n'
            '        If Not (s1 Is Nothing) Then o.SetSupport 1, s1\n'
            '        If Not (s2 Is Nothing) Then o.SetSupport 2, s2\n'
            '        o.SetOrientation 1, 1\n'
            '        o.SetOrientation 2, 1\n'
            '    End If\n'
            '    e = Err.Description\n'
            '    Err.Clear\n'
            '    If Not (o Is Nothing) Then\n'
            '        gs.AppendHybridShape o\n'
            '        Part().Update\n'
            '        If Err.Number <> 0 Or Len(e) > 0 Then\n'
            '            Err.Clear\n'
            '            o.SetOrientation 1, -1\n'
            '            o.SetOrientation 2, -1\n'
            '            flipped = 1\n'
            '            Part().Update\n'
            '        End If\n'
            '        e = Err.Description\n'
            '        Err.Clear\n'
            '    End If\n'
            '    On Error GoTo 0\n'
            '    If o Is Nothing Then\n'
            '        Out "unsupported", "1"\n'
            '        Out "err", "catia_gsd_blend: " & e\n'
            '        SupReport gs\n'
            '        Exit Sub\n'
            '    End If\n'
            '    If Len(e) > 0 Then\n'
            '        Out "unsupported", "1"\n'
            '        Out "err", "catia_gsd_blend: " & e & " (翻转桥接方向后仍然失败)"\n'
            '        SupReport gs\n'
            '        Exit Sub\n'
            '    End If\n'
            '    Out "flipped", flipped\n'
            '    Out "created", o.Name\n'
            '    SupReport gs\n')
    body = _H + '''Sub Main()
    Dim gs, o, e, hs%(locals)s
    Set hs = HSF()
    Set gs = CurGeoset()
    If gs Is Nothing Then Fail "catia_gsd_blend: 找不到几何图形集, 请先用 catia_gsd_create_geoset 建一个"
%(pre)s
    On Error Resume Next
    Err.Clear
%(call)s
End Sub
Main
''' % {'locals': '', 'pre': pre, 'call': call.rstrip('\n')}
    v = (run_vbs(body).get('vars') or {})
    extra = '  曲线 %s <-> %s%s' % (c1, c2,
                                ', 翻转了桥接方向' if v.get('flipped') == '1' else '')
    return _res('桥接曲面(blend)', v, extra)


def t_thick_surface(args):
    surface = arg_s(args, 'surface')
    t1 = arg_f(args, 'thickness')
    t2 = arg_f(args, 'thickness2', 0.0)
    if not surface:
        raise ValueError('缺少参数 surface(要加厚的曲面元素名或序号)')
    if t1 <= 0:
        raise ValueError('thickness 必须大于 0, 收到 %g' % t1)
    body = _H + '''Sub Main()
    Dim gs, o, e, sfObj, r1, body0, where
    Set sfObj = SF()
    Set gs = CurGeoset()
    If gs Is Nothing Then Fail "catia_gsd_thick_surface: 找不到几何图形集"
    Set r1 = SupRef(%(surf)s)
    If r1 Is Nothing Then Fail "catia_gsd_thick_surface: 找不到曲面 " & %(surf)s
    Set body0 = Body()
    where = ""
    On Error Resume Next
    Err.Clear
    Part().InWorkObject = gs
    Err.Clear
    Set o = sfObj.AddNewVolumeThickSurface(r1, 1, %(t1)s, %(t2)s)
    If Err.Number = 0 Then
        where = "geoset"
        gs.AppendHybridShape o
        Err.Clear
    Else
        Err.Clear
        Part().InWorkObject = body0
        Err.Clear
        Set o = sfObj.AddNewThickSurface(r1, 1, %(t1)s, %(t2)s)
        If Err.Number = 0 Then where = "PartBody"
    End If
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        Part().InWorkObject = gs
        Err.Clear
        On Error GoTo 0
        Out "unsupported", "1"
        Out "err", "catia_gsd_thick_surface: " & e
        Out "shapes", body0.Shapes.Count
        SupReport gs
        Exit Sub
    End If
    Part().InWorkObject = gs
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "where", where
    Out "created", o.Name
    Out "shapes", body0.Shapes.Count
    SupReport gs
End Sub
Main
''' % {'surf': vbs(surface), 't1': vbs_num(t1), 't2': vbs_num(t2)}
    v = (run_vbs(body).get('vars') or {})
    where = v.get('where')
    if where == 'geoset':
        place = '  落在几何图形集里(Volume 加厚曲面)。'
    elif where == 'PartBody':
        place = ('  注意: 这个接口在 GS 里用不了, 已退到 PartBody 里建成**实体**加厚曲面'
                 '(PartBody 特征数 %s 个)。' % v.get('shapes', '?'))
    else:
        place = ''
    return _res('加厚曲面(%s, %g / %gmm)' % (surface, t1, t2), v, place)


def t_close_surface(args):
    surface = arg_s(args, 'surface')
    if not surface:
        raise ValueError('缺少参数 surface(要封闭的曲面元素名或序号)')
    body = _H + '''Sub Main()
    Dim gs, o, e, sfObj, r1, body0, where
    Set sfObj = SF()
    Set gs = CurGeoset()
    If gs Is Nothing Then Fail "catia_gsd_close_surface: 找不到几何图形集"
    Set r1 = SupRef(%(surf)s)
    If r1 Is Nothing Then Fail "catia_gsd_close_surface: 找不到曲面 " & %(surf)s
    Set body0 = Body()
    where = ""
    On Error Resume Next
    Err.Clear
    Part().InWorkObject = gs
    Err.Clear
    Set o = sfObj.AddNewVolumeCloseSurface(r1)
    If Err.Number = 0 Then
        where = "geoset"
        gs.AppendHybridShape o
        Err.Clear
    Else
        Err.Clear
        Part().InWorkObject = body0
        Err.Clear
        Set o = sfObj.AddNewCloseSurface(r1)
        If Err.Number = 0 Then where = "PartBody"
    End If
    If Err.Number <> 0 Then
        e = Err.Description
        Err.Clear
        Part().InWorkObject = gs
        Err.Clear
        On Error GoTo 0
        Out "unsupported", "1"
        Out "err", "catia_gsd_close_surface: " & e
        Out "shapes", body0.Shapes.Count
        SupReport gs
        Exit Sub
    End If
    Part().InWorkObject = gs
    Part().Update
    Out "update_err", Err.Description
    Err.Clear
    On Error GoTo 0
    Out "where", where
    Out "created", o.Name
    Out "shapes", body0.Shapes.Count
    SupReport gs
End Sub
Main
''' % {'surf': vbs(surface)}
    v = (run_vbs(body).get('vars') or {})
    where = v.get('where')
    if where == 'geoset':
        place = '  落在几何图形集里(Volume 封闭曲面)。'
    elif where == 'PartBody':
        place = ('  注意: 这个接口在 GS 里用不了, 已退到 PartBody 里建成**实体**封闭曲面'
                 '(PartBody 特征数 %s 个)。' % v.get('shapes', '?'))
    else:
        place = ''
    return _res('封闭曲面(%s)' % surface, v, place)


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

_POINTS3 = A('点列 [[x, y, z], ...]', {'type': 'array', 'items': N('')})
_NAMES = A('元素名或全局序号列表', {'type': 'string'})

TOOLS = [
    ('catia_gsd_create_geoset',
     '新建一个几何图形集(Geometrical Set)并设为当前, 之后的 GSD 元素都进它。',
     obj({'name': S('几何图形集名(可留空, CATIA 自己编号)')}), t_create_geoset),

    ('catia_gsd_set_active_geoset',
     '把已有几何图形集设为当前(InWorkObject), 之后的 GSD 元素都进它。',
     obj({'name': S('几何图形集名')}, ['name']), t_set_active_geoset),

    ('catia_gsd_list_elements',
     '列出所有几何图形集及其元素(名字/类型/全局序号), 同时给出主实体的草图与特征数。',
     obj({}), t_list_elements),

    ('catia_gsd_point',
     '按坐标创建空间点。',
     obj({'x': N('X'), 'y': N('Y'), 'z': N('Z')}, ['x', 'y', 'z']), t_point),

    ('catia_gsd_line',
     '两点建一条空间直线(两个端点也会一并建成点元素)。',
     obj({'x1': N('起点 X'), 'y1': N('起点 Y'), 'z1': N('起点 Z'),
          'x2': N('终点 X'), 'y2': N('终点 Y'), 'z2': N('终点 Z')},
         ['x1', 'y1', 'z1', 'x2', 'y2', 'z2']), t_line),

    ('catia_gsd_plane_offset',
     '把基准面(XY/YZ/ZX)沿法向偏移 offset 得到新平面。',
     obj({'reference': S('参考面: XY / YZ / ZX(默认 XY), 也可以是已有平面元素的名字'),
          'offset': N('偏移量(mm)')}, ['offset']), t_plane_offset),

    ('catia_gsd_plane_3points',
     '过三个点建平面。',
     obj({'x1': N('点1 X'), 'y1': N('点1 Y'), 'z1': N('点1 Z'),
          'x2': N('点2 X'), 'y2': N('点2 Y'), 'z2': N('点2 Z'),
          'x3': N('点3 X'), 'y3': N('点3 Y'), 'z3': N('点3 Z')},
         ['x1', 'y1', 'z1', 'x2', 'y2', 'z2', 'x3', 'y3', 'z3']), t_plane_3points),

    ('catia_gsd_spline',
     '按点列创建 3D 样条曲线。',
     obj({'points': _POINTS3}, ['points']), t_spline),

    ('catia_gsd_circle',
     '在支撑平面上按圆心+半径创建圆(可给起止角做圆弧, 角度是度)。',
     obj({'cx': N('圆心 X'), 'cy': N('圆心 Y'), 'cz': N('圆心 Z'),
          'radius': N('半径(mm)'),
          'support': S('支撑面: XY / YZ / ZX(默认 XY)或已有平面元素名'),
          'offset': N('支撑面再偏移多少 mm(默认 0)'),
          'start_angle': N('起始角(度), 默认 0'),
          'end_angle': N('终止角(度), 默认 360')},
         ['cx', 'cy', 'cz', 'radius']), t_circle),

    ('catia_gsd_project',
     '把元素沿法向投影到支撑面上。',
     obj({'element': S('要投影的元素(名或全局序号)'),
          'support': S('支撑面: XY / YZ / ZX 或平面元素名')},
         ['element', 'support']), t_project),

    ('catia_gsd_intersection',
     '求两个元素的相交(线 x 面 -> 点, 面 x 面 -> 线)。',
     obj({'element1': S('元素 1(名或全局序号)'),
          'element2': S('元素 2(名或全局序号)')},
         ['element1', 'element2']), t_intersection),

    ('catia_gsd_multi_section_surface',
     '多截面曲面(放样): 一串截面 + 可选引导线。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'sections': _NAMES, 'guides': A('引导线(可省)', {'type': 'string'})},
         ['sections']), t_multi_section_surface),

    ('catia_gsd_sweep',
     '扫掠曲面: 轮廓沿引导线扫。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'profile': S('轮廓(名或全局序号)'), 'guide': S('引导线(名或全局序号)')},
         ['profile', 'guide']), t_sweep),

    ('catia_gsd_extrude',
     '把轮廓沿方向拉伸成曲面。',
     obj({'profile': S('轮廓(名或全局序号)'),
          'direction': S('方向: X / Y / Z, 或一条直线元素的名字'),
          'limit': N('起始偏移(mm)'), 'limit2': N('结束偏移(mm), 默认 0')},
         ['profile', 'direction', 'limit']), t_extrude),

    ('catia_gsd_revolve',
     '把轮廓绕轴旋转成曲面。',
     obj({'profile': S('轮廓(名或全局序号)'), 'axis': S('旋转轴(直线元素名或序号)'),
          'angle': N('旋转角(度), 默认 360')},
         ['profile', 'axis']), t_revolve),

    ('catia_gsd_fill',
     '填充: 由封闭边界围成一张曲面。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'boundary': _NAMES}, ['boundary']), t_fill),

    ('catia_gsd_blend',
     '桥接两条曲线成一张曲面(可选支撑面)。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'curve1': S('曲线 1(名或全局序号)'), 'curve2': S('曲线 2(名或全局序号)'),
          'support1': S('曲线 1 的支撑面(可省)'), 'support2': S('曲线 2 的支撑面(可省)')},
         ['curve1', 'curve2']), t_blend),

    ('catia_gsd_offset_surface',
     '把曲面/曲线按法向偏移一个距离。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'surface': S('元素(名或全局序号)'), 'offset': N('偏移量(mm), 可正可负')},
         ['surface', 'offset']), t_offset_surface),

    ('catia_gsd_join',
     '接合(Join)多个元素成一条曲线/一张曲面。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'elements': _NAMES}, ['elements']), t_join),

    ('catia_gsd_split',
     '用切割元素切开一个元素(Split)。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'element': S('被切元素(名或全局序号)'), 'cutter': S('切割元素(名或全局序号)'),
          'orientation': I('保留哪一侧: 1 或 -1, 默认 1')},
         ['element', 'cutter']), t_split),

    ('catia_gsd_trim',
     '两个元素互相修剪(Trim)。CATIA 若拒绝这个 API 会明确回"不支持"。',
     obj({'element1': S('元素 1(名或全局序号)'), 'element2': S('元素 2(名或全局序号)')},
         ['element1', 'element2']), t_trim),

    ('catia_gsd_symmetry',
     '把元素关于参考(平面/直线)做对称。',
     obj({'element': S('元素(名或全局序号)'),
          'reference': S('对称参考: XY / YZ / ZX 或平面/直线元素名')},
         ['element', 'reference']), t_symmetry),

    ('catia_gsd_thick_surface',
     '曲面加厚成实体/体积(先试 GS 里的 Volume 加厚, 不行就退到 PartBody 里的实体加厚)。',
     obj({'surface': S('曲面(名或全局序号)'), 'thickness': N('加厚厚度(mm)'),
          'thickness2': N('另一侧厚度(mm), 默认 0')},
         ['surface', 'thickness']), t_thick_surface),

    ('catia_gsd_close_surface',
     '把封闭曲面变成实体/体积(先试 GS 里的 Volume 封闭, 不行就退到 PartBody 里的封闭曲面)。',
     obj({'surface': S('曲面(名或全局序号)')}, ['surface']), t_close_surface),
]
