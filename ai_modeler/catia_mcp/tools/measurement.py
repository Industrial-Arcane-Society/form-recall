# -*- coding: utf-8 -*-
r"""测量工具(6 个) —— 对应参考项目 catia-v5-mcp-server 的 measurement 模块。

量的是 **CATIA 里的真实模型**, 通道是 SPAWorkbench 的 Measurable(不是离线内核的数字):
    Set spa = CATIA.ActiveDocument.GetWorkbench("SPAWorkbench")
    Set m   = spa.GetMeasurable(Part.CreateReferenceFromObject(o))
    m.Volume / m.Area                          ' 可用(SI 单位: m^3 / m^2)
    m.GetCOG a                                 ' 可用(ByRef 数组, 回 mm)
    m.GetMinimumDistance m2                    ' 调不通(见下), 所以最低距离只能如实报失败

真机结论(本机 CATIA V5-6R2023, 都实测过; 量的是一个已知的 100x60x20 长方体):
    · 取 SPAWorkbench: 文档上 GetWorkbench("SPAWorkbench") 可用(Part 上的那个是兜底)
    · Measurable.Volume / Area —— 可用, 但单位是 SI: 长方体回过 0.00012 m^3 与 0.0184 m^2
      (= 120000 mm^3 / 18400 mm^2), 所以 Python 侧要 ×1e9 / ×1e6 换算
    · Measurable.GetCOG(数组) —— 可用, 回过 (50,30,10) mm(重心是 mm, 不跟着 SI 走),
      于是 mode="center" 的中心距是真机验证可用的(实测 59.1608 mm)
    · Measurable.GetBoundingBox    —— **不存在**(err 438 对象不支持此属性或方法), 见下
    · Measurable.Mass / .Inertia   —— 也是 438; 质量要零件有材料/密度, 取不到就如实说明
    · Measurable.GetMinimumDistance —— **调不通**: Set 返回值/两参数 ByRef/参数对调/
      GetMinimumDistancePoints/GetMinimumDistanceInContext 分别报 13 或 450,
      SPAWorkbench.Distances 集合也返回 Nothing → mode="minimum" 只能如实报失败
    · GetWorkbench 在 Document 上(Part 上没有); 本机 SPATypeLib 里 Measurable 也没有
      GetBoundingBox(InfTypeLib 里同名的只是 BoundingBoxSelectionMode 设置项) —— 所以
      catia_get_bounding_box 会把原始报错原样报出来, 并建议导出 STL 后按
      tools\check_catia_solid.py 的思路量网格(CATIA 自己导出的真实几何);
    · 任何取不到的量都不编数字, 也不拿离线内核的结果冒充。

脚本里所有 VBS 片段都保持纯 ASCII(中文只留在 Python 侧), 免得踩编码的坑。
"""
from __future__ import annotations

import os
import re
import struct
import tempfile

from ..bridge import CatiaError, run_vbs
from ._schema import S, arg_s, obj, vbs

NAME = 'measurement'


# --------------------------------------------------------------------------
# VBScript 公共片段(纯 ASCII)
# --------------------------------------------------------------------------

# 取活动 CATPart。不走预置的 Part()/Body(): 它们内部要调内核里的 MainBodyOf, 而
# 内核文件不一定就位 —— 这里自己取, 少一个依赖。
_COMMON = r'''
Dim aimP

' Render a CATIA error as one explicit line. Even when Err.Description is empty
' the errno (hex) is always included -- otherwise the client gets a blank error
' and has nothing to work with.
Function AimErrText(tag)
    Dim n, d
    n = Err.Number
    d = Err.Description
    If Len(d) = 0 Then d = "(CATIA returned an empty description)"
    AimErrText = tag & ": errno=0x" & Hex(n) & " (" & n & ") " & d
End Function

Sub NeedP()
    Attach
    On Error Resume Next
    Err.Clear
    Set aimP = Nothing
    Set aimP = gApp.ActiveDocument.Part
    If Err.Number <> 0 Or aimP Is Nothing Then
        Dim ep
        ep = AimErrText("ActiveDocument.Part")
        Err.Clear
        On Error GoTo 0
        Fail "active document is not a CATPart: " & ep
    End If
    On Error GoTo 0
End Sub

Function MB()
    ' NOTE: VBScript's And does NOT short-circuit, so every object variable must
    ' be initialised to Nothing before use: "Err.Number = 0 And Not b Is Nothing"
    ' evaluates "b Is Nothing" even when the call failed, and on an Empty (never
    ' assigned) variable that raises "object required". Found on the real machine.
    Dim b
    On Error Resume Next
    Err.Clear
    Set MB = Nothing
    Set b = Nothing
    Set b = aimP.MainBody
    If Err.Number = 0 And Not b Is Nothing Then
        Set MB = b
    Else
        Err.Clear
        Set b = Nothing
        Set b = aimP.Bodies.Item(1)
        If Err.Number = 0 And Not b Is Nothing Then Set MB = b
    End If
    Err.Clear
    On Error GoTo 0
End Function
'''

# 测量专用助手: 元素解析 + SPAWorkbench/Measurable + 几个"可能失败的接口"的封装
_MEASURE = r'''
Dim aimWhy, aimMethod, aimC1, aimC2

Function AimHint()
    Dim s, i
    s = "PartBody / XY / YZ / ZX / selection1..n"
    On Error Resume Next
    Err.Clear
    If Not MB() Is Nothing Then
        For i = 1 To MB().Shapes.Count
            If i > 10 Then Exit For
            s = s & " / " & MB().Shapes.Item(i).Name
        Next
    End If
    Err.Clear
    On Error GoTo 0
    AimHint = s
End Function

Function AimResolve(nm, tag)
    Dim s, i, o, hb, hs, selObj
    s = LCase(Trim(nm))
    Set AimResolve = Nothing
    Set o = Nothing
    Set hb = Nothing
    If s = "partbody" Or s = "body" Or s = "mainbody" Then
        Set AimResolve = MB()
        If AimResolve Is Nothing Then Fail tag & ": cannot get the main body"
        Exit Function
    End If
    If s = "xy" Or s = "planexy" Then
        Set AimResolve = aimP.OriginElements.PlaneXY
        Exit Function
    End If
    If s = "yz" Or s = "planeyz" Then
        Set AimResolve = aimP.OriginElements.PlaneYZ
        Exit Function
    End If
    If s = "zx" Or s = "xz" Or s = "planezx" Then
        Set AimResolve = aimP.OriginElements.PlaneZX
        Exit Function
    End If
    If Left(s, 9) = "selection" Then
        Set selObj = gApp.ActiveDocument.Selection
        i = 1
        If Len(s) > 9 Then i = CInt(Mid(s, 10))
        If selObj.Count < i Then Fail tag & ": current selection has only " & selObj.Count & " item(s)"
        Set AimResolve = selObj.Item(i).Value
        Exit Function
    End If
    On Error Resume Next
    Err.Clear
    Set o = aimP.FindObject(nm)
    If Err.Number = 0 And Not o Is Nothing Then
        Err.Clear
        On Error GoTo 0
        Set AimResolve = o
        Exit Function
    End If
    Err.Clear
    Set o = Nothing
    If Not MB() Is Nothing Then
        For i = 1 To MB().Shapes.Count
            If LCase(MB().Shapes.Item(i).Name) = s Then Set o = MB().Shapes.Item(i)
        Next
        For i = 1 To aimP.HybridBodies.Count
            Set hb = aimP.HybridBodies.Item(i)
            For Each hs In hb.HybridShapes
                If LCase(hs.Name) = s Then Set o = hs
            Next
        Next
    End If
    Err.Clear
    On Error GoTo 0
    If o Is Nothing Then
        Fail tag & ": element not found: " & nm & " (try: " & AimHint() & ")"
    End If
    Set AimResolve = o
End Function

Function MeasOf(o, tag)
    Dim spa, m, e1, e2
    Set MeasOf = Nothing
    On Error Resume Next
    Err.Clear
    Set spa = Nothing
    Set spa = gApp.ActiveDocument.GetWorkbench("SPAWorkbench")
    If Err.Number <> 0 Or spa Is Nothing Then
        e1 = AimErrText("Document.GetWorkbench(SPAWorkbench)")
        Err.Clear
        Set spa = Nothing
        Set spa = aimP.GetWorkbench("SPAWorkbench")
        If Err.Number = 0 And Not spa Is Nothing Then e1 = ""
    End If
    If Err.Number <> 0 Or spa Is Nothing Then
        If Len(e1) = 0 Then e1 = AimErrText("GetWorkbench(SPAWorkbench)")
        Err.Clear
        On Error GoTo 0
        Fail tag & ": cannot get SPAWorkbench -> " & e1
    End If
    Err.Clear
    Set m = Nothing
    Set m = spa.GetMeasurable(aimP.CreateReferenceFromObject(o))
    If Err.Number <> 0 Or m Is Nothing Then
        e2 = AimErrText("SPAWorkbench.GetMeasurable")
        Err.Clear
        On Error GoTo 0
        Fail tag & ": " & e2
    End If
    Err.Clear
    On Error GoTo 0
    Set MeasOf = m
End Function

Function AimVec(a)
    AimVec = "(" & CStr(a(0)) & ", " & CStr(a(1)) & ", " & CStr(a(2)) & ")"
End Function

Function AimCOG(m)
    ' returns a 3-element array on success; on failure returns an empty array
    ' and writes the reason into aimWhy
    Dim a(2), x
    aimWhy = ""
    On Error Resume Next
    Err.Clear
    m.GetCOG a
    If Err.Number = 0 Then
        Err.Clear
        On Error GoTo 0
        AimCOG = a
        Exit Function
    End If
    aimWhy = AimErrText("Measurable.GetCOG(array)")
    Err.Clear
    x = m.GetCOG
    If Err.Number = 0 And IsArray(x) Then
        aimWhy = ""
        Err.Clear
        On Error GoTo 0
        AimCOG = x
        Exit Function
    End If
    aimWhy = aimWhy & " ;; " & AimErrText("Measurable.GetCOG()")
    Err.Clear
    On Error GoTo 0
    AimCOG = Array()
End Function

Function AimArr(m, meth, n)
    ' array-typed members of Inertia: GetCOGPosition / GetInertiaMatrix / GetPrincipalMoments
    Dim a(), i
    aimWhy = ""
    ReDim a(n - 1)
    On Error Resume Next
    Err.Clear
    If meth = "GetCOGPosition" Then
        m.Inertia.GetCOGPosition a
    ElseIf meth = "GetInertiaMatrix" Then
        m.Inertia.GetInertiaMatrix a
    ElseIf meth = "GetPrincipalMoments" Then
        m.Inertia.GetPrincipalMoments a
    Else
        aimWhy = "unknown method " & meth
    End If
    If Err.Number <> 0 Then
        aimWhy = aimWhy & AimErrText("Inertia." & meth)
        Err.Clear
        On Error GoTo 0
        AimArr = Array()
        Exit Function
    End If
    Err.Clear
    On Error GoTo 0
    AimArr = a
End Function

Function AimBoxCenter(m)
    Dim mn(2), mx(2), c(2), i
    aimWhy = ""
    On Error Resume Next
    Err.Clear
    m.GetBoundingBox mn, mx
    If Err.Number <> 0 Then
        aimWhy = AimErrText("Measurable.GetBoundingBox")
        Err.Clear
        On Error GoTo 0
        AimBoxCenter = Array()
        Exit Function
    End If
    For i = 0 To 2
        c(i) = (mn(i) + mx(i)) / 2
    Next
    Err.Clear
    On Error GoTo 0
    AimBoxCenter = c
End Function

Function AimNum(o)
    ' a Length object -> its value; a plain number -> itself
    Dim v, e
    aimWhy = ""
    On Error Resume Next
    Err.Clear
    If IsObject(o) Then
        v = o.Value
    Else
        v = o
    End If
    If Err.Number <> 0 Then
        aimWhy = AimErrText("distance value")
        Err.Clear
        On Error GoTo 0
        AimNum = ""
        Exit Function
    End If
    Err.Clear
    On Error GoTo 0
    AimNum = CStr(v)
End Function

Function AimMinDist(m1, m2)
    ' returns the value as a string, or "" on failure (reason goes into aimWhy)
    Dim d, e1, e2
    aimWhy = ""
    On Error Resume Next
    Err.Clear
    Set d = Nothing
    Set d = m1.GetMinimumDistance(m2)
    If Err.Number <> 0 Or d Is Nothing Then
        e1 = AimErrText("GetMinimumDistance(set-return)")
        Err.Clear
        Set d = Nothing
        m1.GetMinimumDistance m2, d
        If Err.Number <> 0 Or d Is Nothing Then
            e2 = AimErrText("GetMinimumDistance(byref)")
            Err.Clear
            On Error GoTo 0
            aimWhy = e1 & " ;; " & e2
            AimMinDist = ""
            Exit Function
        End If
    End If
    Err.Clear
    On Error GoTo 0
    AimMinDist = AimNum(d)
End Function

Function AimCenterDist(m1, m2)
    Dim c1, c2, e1, e2
    aimWhy = ""
    aimMethod = "cog"
    c1 = Array()
    c2 = Array()
    c1 = AimCOG(m1)
    e1 = aimWhy
    c2 = AimCOG(m2)
    e2 = aimWhy
    If UBound(c1) < 0 Or UBound(c2) < 0 Then
        aimMethod = "bbox-center"
        c1 = AimBoxCenter(m1)
        e1 = e1 & " | bbox: " & aimWhy
        c2 = AimBoxCenter(m2)
        e2 = e2 & " | bbox: " & aimWhy
        If UBound(c1) < 0 Or UBound(c2) < 0 Then
            aimWhy = "element1: " & e1 & " ;; element2: " & e2
            AimCenterDist = ""
            Exit Function
        End If
    End If
    aimC1 = CStr(c1(0)) & "," & CStr(c1(1)) & "," & CStr(c1(2))
    aimC2 = CStr(c2(0)) & "," & CStr(c2(1)) & "," & CStr(c2(2))
    AimCenterDist = CStr(Sqr((c1(0) - c2(0)) ^ 2 + (c1(1) - c2(1)) ^ 2 + (c1(2) - c2(2)) ^ 2))
End Function
'''

_STL_HINT = (
    '替代方案(本项目自己的办法, 量到的仍是 CATIA 里的真实几何):\n'
    '  1. catia_export(format="stl", path="...\\part.stl") 让 CATIA 导出 STL 网格;\n'
    '  2. 按 tools\\check_catia_solid.py 的思路读网格顶点, 取 min/max —— 那就是真实包围盒。\n'
    '  注意: 不能用离线内核算出来的包围盒代替, 那只是本项目自己算的数字, 不代表 CATIA 里的模型。'
)


# --------------------------------------------------------------------------
# Python 侧小工具
# --------------------------------------------------------------------------

_ASCII_VERTEX = re.compile(
    rb'vertex\s+(-?[0-9][0-9.eE+-]*)\s+(-?[0-9][0-9.eE+-]*)\s+(-?[0-9][0-9.eE+-]*)')


def _stl_vertices(path: str):
    """读 STL 里的网格顶点(CATIA 导出的是 ASCII, 但二进制也一起支持)。

    返回 [(x, y, z), ...]; 读不到返回 []。
    这正是 tools\\check_catia_solid.py / modeler.t_geometry_check 的思路。
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return []
    if not data:
        return []
    head = data[:4096]
    if b'vertex' in head.lower() or (data[:5].lower() == b'solid' and b'facet' in head.lower()):
        vs = []
        for m in _ASCII_VERTEX.finditer(data):
            try:
                vs.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
            except ValueError:
                continue
        if vs:
            return vs
    # 二进制 STL: 80 字节头 + uint32 三角形数 + 每个三角形 50 字节(法向量 12 + 3 顶点 36 + 属性 2)
    if len(data) >= 84:
        n = int.from_bytes(data[80:84], 'little')
        if 0 < n <= (len(data) - 84) // 50:
            vs = []
            off = 84
            for _ in range(n):
                x, y, z = struct.unpack('<3f', data[off + 12:off + 24])
                vs.append((x, y, z))
                off += 50
            return vs
    return []


def _mesh_bbox(path: str):
    """从 STL 网格算包围盒 -> (顶点数, min(x,y,z), max(x,y,z)); 没有网格返回 (0, None, None)。"""
    vs = _stl_vertices(path)
    if not vs:
        return (0, None, None)
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    return (len(vs), (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

def _dry() -> bool:
    """干跑(离线测工具签名/schema 用, 不真的连 CATIA)。"""
    return os.environ.get('AI_MODELER_MCP_DRYRUN', '') not in ('', '0', 'false')


def _dry_note(what: str) -> str:
    return ('(干跑模式 AI_MODELER_MCP_DRYRUN=1): 没有真的连 CATIA, 下面是本次会做的事\n  %s'
            % what)


def _f(v, default=None):
    """CATIA 回传的数值(容错: 空值/本地化小数点)。"""
    if v in (None, ''):
        return default
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return default


def _fmt(v, nd: int = 3, unit: str = '') -> str:
    f = _f(v)
    if f is None:
        return '(取不到)'
    return ('%.' + str(nd) + 'f%s') % (f, unit)


def _vec(s):
    """把 "x,y,z" 或 "(x, y, z)" 解析成 3 个浮点; 解析不了返回 None。

    注意: VBS 侧的 AimVec 返回的是带括号的 "(50, 30, 10)" —— 真机上就是因为没
    剥括号, 把明明量到的重心报成了"取不到"。
    """
    if not s:
        return None
    txt = str(s).strip().strip('()')
    parts = [p for p in txt.split(',')]
    if len(parts) < 3:
        return None
    try:
        return tuple(float(p.replace(',', '.').strip()) for p in parts[:3])
    except (TypeError, ValueError):
        return None


def _vec_str(s, unit: str = ' mm') -> str:
    v = _vec(s)
    if v is None:
        return str(s or '(取不到)')
    return '(%.3f, %.3f, %.3f)%s' % (v[0], v[1], v[2], unit)


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

def t_measure_distance(args):
    """两个元素的最小距离 / 中心距(都在 CATIA 里用 SPAWorkbench 量)。"""
    e1 = arg_s(args, 'element1').strip()
    e2 = arg_s(args, 'element2').strip()
    mode = (arg_s(args, 'mode') or 'minimum').strip().lower()
    if mode in ('min', 'minimum', 'least', '最小', '最小距离'):
        mode = 'minimum'
    elif mode in ('center', 'centre', 'cog', '中心', '中心距'):
        mode = 'center'
    else:
        raise ValueError('mode 只支持 "minimum"(默认: 最小距离) 或 "center"(中心距), 收到: %r' % mode)
    if not e1 or not e2:
        raise ValueError('element1 / element2 都要给。元素名可以是: PartBody(主实体), '
                         'XY/YZ/ZX(基准面), 特征名(如 Pad.1), 或 selection1/selection2'
                         '(当前选中的第 1/2 个对象)')
    if _dry():
        return _dry_note('在 CATIA 里量 %r 与 %r 的%s'
                         % (e1, e2, '最小距离' if mode == 'minimum' else '中心距'))
    body = _COMMON + _MEASURE + '''
Dim aimO1, aimO2, aimR1, aimR2, aimRes
Sub aimRun()
    NeedP
    Out "part", aimP.Name
    Set aimO1 = AimResolve(%s, "element1")
    Out "element1", aimO1.Name
    Set aimO2 = AimResolve(%s, "element2")
    Out "element2", aimO2.Name
    Set aimR1 = MeasOf(aimO1, "element1")
    Set aimR2 = MeasOf(aimO2, "element2")
    If "%s" = "minimum" Then
        aimRes = AimMinDist(aimR1, aimR2)
        If aimRes = "" Then
            Out "ok", "0"
            Out "reason", aimWhy
        Else
            Out "ok", "1"
            Out "method", "minimum (= Measurable.GetMinimumDistance)"
            Out "distance", aimRes
        End If
    Else
        aimRes = AimCenterDist(aimR1, aimR2)
        If aimRes = "" Then
            Out "ok", "0"
            Out "reason", aimWhy
        Else
            Out "ok", "1"
            Out "method", aimMethod
            Out "distance", aimRes
            Out "center1", aimC1
            Out "center2", aimC2
        End If
    End If
End Sub

' Safety net: no unhandled error may kill the script silently (otherwise
' the client only sees "script did not finish" with no reason)
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("measure_distance script aborted")
On Error GoTo 0
''' % (vbs(e1), vbs(e2), mode)
    r = run_vbs(body)
    v = r.get('vars') or {}
    head = '零件 %s 的测量(CATIA SPAWorkbench 实测, 不是离线内核算的):' % v.get('part', '?')
    _how = {'minimum': 'minimum = Measurable.GetMinimumDistance(最小距离)',
            'cog': 'cog = Measurable.GetCOG(两个重心之间的距离)',
            'bbox-center': 'bbox-center(GetCOG 取不到, 退化成两个包围盒中心之间的距离)'}
    if v.get('ok') == '1':
        out = [head,
               '  element1 = %s' % v.get('element1', '?'),
               '  element2 = %s' % v.get('element2', '?'),
               '  方式: %s' % _how.get(v.get('method'), v.get('method') or '?'),
               '  距离: %s' % _fmt(v.get('distance'), 4, ' mm')]
        if v.get('center1') or v.get('center2'):
            out.append('  中心1: %s' % _vec_str(v.get('center1')))
            out.append('  中心2: %s' % _vec_str(v.get('center2')))
        return '\n'.join(out)
    return '\n'.join([
        head,
        '  未能量出来 —— CATIA 侧的原始原因:',
        '    %s' % (v.get('reason') or '(没有回传原因)'),
        '  真机结论: 本机 CATIA V5-6R2023 上 Measurable.GetMinimumDistance 从 VBScript 晚绑定'
        '调不通 —— 各种形式都试过并记了原始报错(Set 返回值 / 两参数 ByRef / 参数对调 /'
        ' GetMinimumDistancePoints / GetMinimumDistanceInContext, 分别报 "类型不匹配"(13) 与'
        ' "错误的参数个数或无效的参数属性值"(450)); SPAWorkbench.Distances 集合也返回 Nothing。'
        '这就是这条路走不通, 不是元素名写错了。',
        '  能用的替代: catia_export(format="stl", path="...\\part.stl") 导出 STL, 再按 '
        'tools\\check_catia_solid.py 的思路读网格顶点算距离/间隙 —— 那是 CATIA 自己导出的真实几何。'
        '也可以把两个元素各自 catia_get_parameters 出来手算(平面/轴的距离有解析解)。'])


def t_get_inertia(args):
    """体积 / 面积 / 质量 / 重心(以及惯性矩), 全部来自 CATIA 的 Measurable。"""
    if _dry():
        return _dry_note('在 CATIA 里量活动 CATPart 主实体的 体积/面积/质量/重心/惯性矩')
    body = _COMMON + _MEASURE + '''
Dim aimB, aimM, aimV, aimA, aimMs, aimInr, aimMsOk, aimMsErr, aimMsSrc
Dim aimCg, aimCgErr, aimCgSrc, aimPM, aimIM, aimEr
Sub aimRun()
NeedP
Out "part", aimP.Name
Set aimB = MB()
If aimB Is Nothing Then Fail "cannot get the main body of the part"
Out "body", aimB.Name
Set aimM = MeasOf(aimB, "PartBody")

On Error Resume Next
Err.Clear
aimV = aimM.Volume
If Err.Number <> 0 Then
    aimEr = AimErrText("Measurable.Volume")
    Err.Clear
    Out "volume_err", aimEr
Else
    Out "volume", CStr(aimV)
End If
Err.Clear
aimA = aimM.Area
If Err.Number <> 0 Then
    aimEr = AimErrText("Measurable.Area")
    Err.Clear
    Out "area_err", aimEr
Else
    Out "area", CStr(aimA)
End If

aimMsOk = 0
aimMsErr = ""
aimMsSrc = ""
Err.Clear
aimMs = aimM.Mass
If Err.Number = 0 Then
    aimMsOk = 1
    aimMsSrc = "Measurable.Mass"
Else
    aimMsErr = AimErrText("Measurable.Mass")
    Err.Clear
    Set aimInr = Nothing
    Set aimInr = aimM.Inertia
    If Err.Number = 0 And Not aimInr Is Nothing Then
        aimMs = aimInr.Mass
        If Err.Number = 0 Then
            aimMsOk = 1
            aimMsSrc = "Inertia.Mass"
        Else
            aimMsErr = aimMsErr & " ;; " & AimErrText("Inertia.Mass")
        End If
    Else
        aimMsErr = aimMsErr & " ;; " & AimErrText("Measurable.Inertia")
    End If
End If
Err.Clear
On Error GoTo 0
If aimMsOk = 1 Then
    Out "mass", CStr(aimMs)
    Out "mass_src", aimMsSrc
Else
    Out "mass_err", aimMsErr
End If

aimCg = Array()
aimCgErr = ""
aimCgSrc = ""
aimCg = AimCOG(aimM)
aimCgSrc = "Measurable.GetCOG"
aimCgErr = aimWhy
If UBound(aimCg) < 0 Then
    aimCg = AimArr(aimM, "GetCOGPosition", 3)
    aimCgSrc = "Inertia.GetCOGPosition"
    aimCgErr = aimCgErr & " ;; " & aimWhy
End If
If UBound(aimCg) >= 0 Then
    Out "cog", AimVec(aimCg)
    Out "cog_src", aimCgSrc
Else
    Out "cog_err", aimCgErr
End If

aimPM = AimArr(aimM, "GetPrincipalMoments", 3)
If UBound(aimPM) >= 0 Then
    Out "principal", AimVec(aimPM)
Else
    Out "principal_err", aimWhy
End If

aimIM = AimArr(aimM, "GetInertiaMatrix", 9)
If UBound(aimIM) >= 0 Then
    Out "matrix", CStr(aimIM(0)) & "," & CStr(aimIM(1)) & "," & CStr(aimIM(2)) & "," & _
        CStr(aimIM(3)) & "," & CStr(aimIM(4)) & "," & CStr(aimIM(5)) & "," & _
        CStr(aimIM(6)) & "," & CStr(aimIM(7)) & "," & CStr(aimIM(8))
Else
    Out "matrix_err", aimWhy
End If
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("get_inertia script aborted")
On Error GoTo 0
'''
    r = run_vbs(body)
    v = r.get('vars') or {}
    out = ['零件 %s 的惯性/几何量(CATIA SPAWorkbench 实测, 主实体 %s):'
           % (v.get('part', '?'), v.get('body', '?'))]
    # 单位: 真机量过一个已知的 100x60x20 长方体(体积 120000 mm^3) ->
    #   Measurable.Volume 回 0.00012 (= m^3), Area 回 0.0184 (= m^2), GetCOG 回 (50,30,10) (= mm)。
    # 所以这里按 SPA 的 SI 单位换算成 mm, 并把原始值一起报出来, 免得看错数量级。
    vol = _f(v.get('volume'))
    if vol is None:
        out.append('  体积: 取不到 —— %s' % (v.get('volume_err') or '(没有回传原因)'))
    else:
        out.append('  体积: %.3f mm^3 (= %.6f cm^3; CATIA 原始返回值 %.9g, 单位 m^3)'
                   % (vol * 1e9, vol * 1e3, vol))
    area = _f(v.get('area'))
    if area is None:
        out.append('  表面积: 取不到 —— %s' % (v.get('area_err') or '(没有回传原因)'))
    else:
        out.append('  表面积: %.3f mm^2 (CATIA 原始返回值 %.9g, 单位 m^2)'
                   % (area * 1e6, area))
    mass = _f(v.get('mass'))
    if mass is None:
        out.append('  质量: 取不到 —— %s' % (v.get('mass_err') or '(没有回传原因)'))
        out.append('    原因通常是这个零件没赋材料/密度: CATIA 的质量要密度才能算。'
                   '在 CATIA 里给零件赋材料(Apply Material)或给 Inertia 一个 Density 之后就能取到;'
                   '也可以自己用 体积 × 密度 换算(本工具不替你编密度)。')
    else:
        out.append('  质量: %.6f kg   [来源: %s]' % (mass, v.get('mass_src', '?')))
    cog = _vec(v.get('cog'))
    if cog is None:
        out.append('  重心(COG): 取不到 —— %s' % (v.get('cog_err') or '(没有回传原因)'))
        out.append('    (Measurable.GetCOG / Inertia.GetCOGPosition 都是 ByRef 数组参数,'
                   ' 晚绑定下可能失败; 原始报错见上)')
    else:
        out.append('  重心(COG): (%.3f, %.3f, %.3f) mm   [来源: %s]'
                   % (cog[0], cog[1], cog[2], v.get('cog_src', '?')))
    pm = _vec(v.get('principal'))
    if pm is not None:
        out.append('  主惯性矩: (%.3f, %.3f, %.3f) kg*mm^2' % pm)
    else:
        out.append('  主惯性矩: 取不到 —— %s' % (v.get('principal_err') or '(没有回传原因)'))
    mtx = v.get('matrix')
    if mtx:
        nums = [_f(x) for x in str(mtx).split(',')]
        if len(nums) == 9 and all(n is not None for n in nums):
            out.append('  惯性矩阵(3x3, kg*mm^2):')
            for k in range(3):
                out.append('    [%10.3f %10.3f %10.3f]'
                           % (nums[k * 3], nums[k * 3 + 1], nums[k * 3 + 2]))
    else:
        out.append('  惯性矩矩阵: 取不到 —— %s' % (v.get('matrix_err') or '(没有回传原因)'))
    out.append('  说明: 体积/面积是 CATIA SPA 的 SI 单位(m^3 / m^2), 上面已换算成 mm 并给出原始'
               '返回值; 重心是 mm。质量/惯性矩需要零件有密度或材料(本机 Measurable.Mass 与 '
               'Measurable.Inertia 都报 438 对象不支持此属性或方法, 所以取不到就不编数字)。')
    if vol is not None and abs(vol) < 1e-12:
        out.append('  提示: 体积读出 0 —— 主实体里可能还没有实体特征(空零件), '
                   '或者活动文档被切成了别的文档; 用 catia_get_active_document_info 看一眼。')
    return '\n'.join(out)


def t_get_bounding_box(args):
    """零件包围盒: 先试 Measurable.GetBoundingBox, 取不到就**自带兜底**量 STL 网格顶点。

    真机上本机 CATIA 的 Measurable 没有 GetBoundingBox(438), 所以兜底这条路才是常态:
    让 CATIA 导出 STL 到 %TEMP% 下的 ASCII 路径, 再在 Python 侧解析 vertex 行取 X/Y/Z 范围
    (做法与 tools\\check_catia_solid.py / modeler.t_geometry_check 一致)。
    返回文本一定写清这次是用哪种方式量到的。
    """
    if _dry():
        return _dry_note('在 CATIA 里先试 Measurable.GetBoundingBox; 失败就导出 STL 到 ASCII '
                         '临时路径, 再在 Python 侧解析网格顶点算 X/Y/Z 范围')
    tmp = os.path.join(tempfile.gettempdir(), 'aim_mcp_bbox_%d.stl' % (os.getpid() % 100000))
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    # ---- 第一步: Measurable.GetBoundingBox(三种形式都试) ----
    body = _COMMON + _MEASURE + '''
Dim aimB, aimM, aimMn(2), aimMx(2), aimBBWhy, aimBBOk
Sub aimRun()
    NeedP
    Out "part", aimP.Name
    Set aimB = MB()
    If aimB Is Nothing Then Fail "cannot get the main body of the part"
    Out "body", aimB.Name
    Set aimM = MeasOf(aimB, "PartBody")
    aimBBOk = 0
    aimBBWhy = ""
    On Error Resume Next
    Err.Clear
    aimM.GetBoundingBox aimMn, aimMx
    If Err.Number = 0 Then
        aimBBOk = 1
    Else
        aimBBWhy = aimBBWhy & AimErrText("GetBoundingBox(byref arrays)") & " ;; "
        Err.Clear
        Call aimM.GetBoundingBox(aimMn, aimMx)
        If Err.Number = 0 Then
            aimBBOk = 2
        Else
            aimBBWhy = aimBBWhy & AimErrText("GetBoundingBox(Call)") & " ;; "
            Err.Clear
        End If
    End If
    Err.Clear
    On Error GoTo 0
    Out "ok", CStr(aimBBOk)
    Out "why", aimBBWhy
    If aimBBOk > 0 Then
        Out "minx", CStr(aimMn(0))
        Out "miny", CStr(aimMn(1))
        Out "minz", CStr(aimMn(2))
        Out "maxx", CStr(aimMx(0))
        Out "maxy", CStr(aimMx(1))
        Out "maxz", CStr(aimMx(2))
    End If
End Sub

On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("bounding box script aborted")
On Error GoTo 0
'''
    v, why = {}, ''
    try:
        v = (run_vbs(body).get('vars') or {})
        why = v.get('why') or ''
    except CatiaError as e:
        why = 'CATIA 侧原始报错: %s' % e
    if v.get('ok') in ('1', '2'):
        d = [_f(v.get('max%s' % a), 0.0) - _f(v.get('min%s' % a), 0.0)
             for a in ('x', 'y', 'z')]
        return '\n'.join([
            '零件 %s 的包围盒 —— 量法: Measurable.GetBoundingBox(主实体 %s, 直接问 CATIA):'
            % (v.get('part', '?'), v.get('body', '?')),
            '  X: %s ~ %s mm' % (_fmt(v.get('minx')), _fmt(v.get('maxx'))),
            '  Y: %s ~ %s mm' % (_fmt(v.get('miny')), _fmt(v.get('maxy'))),
            '  Z: %s ~ %s mm' % (_fmt(v.get('minz')), _fmt(v.get('maxz'))),
            '  外形尺寸: %.3f x %.3f x %.3f mm' % (d[0], d[1], d[2]),
            '  坐标系: 零件绝对轴系(Part 轴系)'])

    # ---- 第二步: 兜底 —— 让 CATIA 导出 STL, 在 Python 侧量网格顶点 ----
    doc_name = '?'
    stl_why = ''
    body2 = _COMMON + '''
Sub aimRun()
    Dim aimD
    Set aimD = Act()
    If aimD Is Nothing Then Fail "no active document (CatiaError: nothing to export)"
    Out "doc", aimD.Name
    Out "type", TypeName(aimD)
    On Error Resume Next
    Err.Clear
    aimD.ExportData %s, "stl"
    If Err.Number <> 0 Then Fail "ExportData(stl) failed -> " & AimErrText("Document.ExportData(stl)")
    On Error GoTo 0
    Out "stl", %s
End Sub

On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("stl fallback script aborted")
On Error GoTo 0
''' % (vbs(tmp), vbs(tmp))
    try:
        v2 = run_vbs(body2, timeout=600).get('vars') or {}
        doc_name = v2.get('doc', '?')
    except CatiaError as e:
        stl_why = str(e)

    size = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
    if size < 128:
        # 空零件导出的 STL 只有几十字节(就 solid/endsolid 两行) —— 这是"量不到几何"最常见的原因,
        # 明确说出来, 别丢一个看不懂的报错
        return '\n'.join([
            '零件 %s 的包围盒: 量不到。' % (v.get('part') or doc_name),
            '  方式1 Measurable.GetBoundingBox 失败: %s' % (why or '(没有回传原因)'),
            '  方式2 兜底(导出 STL 量网格顶点)也没成: 导出的 STL 只有 %d 字节'
            % size + ('(活动文档里没有几何体 —— 先建模或切到有零件的文档)'
                      if not stl_why else ''),
            ('  STL 导出报错: %s' % stl_why) if stl_why else '',
        ]).replace('\n\n', '\n')
    n, mn, mx = _mesh_bbox(tmp)
    if n == 0 or mn is None:
        return '\n'.join([
            '零件 %s 的包围盒: 量不到。' % (v.get('part') or doc_name),
            '  方式1 Measurable.GetBoundingBox 失败: %s' % (why or '(没有回传原因)'),
            '  方式2 兜底: STL 已导出(%d 字节)但里面没有网格顶点(可能是空零件/二进制解析失败): %s'
            % (size, tmp),
            _STL_HINT])
    d = [mx[i] - mn[i] for i in range(3)]
    out = [
        '零件 %s 的包围盒 —— 量法: 让 CATIA 导出 STL 后量网格顶点' % v.get('part', '?'),
        '  X: %.3f ~ %.3f mm' % (mn[0], mx[0]),
        '  Y: %.3f ~ %.3f mm' % (mn[1], mx[1]),
        '  Z: %.3f ~ %.3f mm' % (mn[2], mx[2]),
        '  外形尺寸: %.3f x %.3f x %.3f mm' % (d[0], d[1], d[2]),
        '  网格: %d 个顶点(STL %d 字节, %s)' % (n, size, tmp),
        '  为什么用兜底: Measurable.GetBoundingBox 在本机取不到 —— %s' % (why or '(无详情)'),
        '  说明: 这是 CATIA 自己导出的网格外接盒; 与 CATIA "测量"里直接量的包围盒可能有极小差异'
        '(取决于 STL 网格精度), 但顶点坐标就是真实模型上的点。数据来自活动文档 %s。' % doc_name,
    ]
    try:
        os.remove(tmp)
    except OSError:
        pass
    return '\n'.join(out)


def t_get_parameters(args):
    """列出零件参数(名/值)。"""
    if _dry():
        return _dry_note('遍历 Part.Parameters, 列出每个参数的 Name 与 ValueAsString')
    body = _COMMON + '''
Dim aimCnt, aimI, aimPr, aimName, aimVal, aimErr
Sub aimRun()
NeedP
Out "part", aimP.Name
On Error Resume Next
Err.Clear
aimCnt = aimP.Parameters.Count
If Err.Number <> 0 Then
    aimErr = AimErrText("Part.Parameters.Count")
    Err.Clear
    On Error GoTo 0
    Fail "cannot read Parameters -> " & aimErr
End If
Err.Clear
Out "count", CStr(aimCnt)
For aimI = 1 To aimCnt
    If aimI > 500 Then
        Out "truncated", "1"
        Exit For
    End If
    Err.Clear
    Set aimPr = Nothing
    Set aimPr = aimP.Parameters.Item(aimI)
    If Err.Number = 0 And Not aimPr Is Nothing Then
        aimName = ""
        aimVal = ""
        aimName = aimPr.Name
        aimVal = aimPr.ValueAsString
        If Err.Number <> 0 Then
            Err.Clear
            aimVal = "(ValueAsString unavailable)"
        End If
        Out "p" & aimI, aimName & " = " & aimVal
    Else
        Out "p" & aimI, "(cannot read parameter #" & aimI & ": " & AimErrText("Parameters.Item") & ")"
    End If
    Err.Clear
Next
On Error GoTo 0
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("get_parameters script aborted")
On Error GoTo 0
'''
    r = run_vbs(body)
    v = r.get('vars') or {}
    n = int(_f(v.get('count'), 0) or 0)
    out = ['零件 %s 的参数(共 %d 个):' % (v.get('part', '?'), n)]
    shown = 0
    for i in range(1, n + 1):
        line = v.get('p%d' % i)
        if line is None:
            continue
        out.append('  %s' % line)
        shown += 1
    if shown == 0:
        out.append('  (没有参数可列 —— 零件里可能还没有带参数的几何/草图)')
    if v.get('truncated') == '1' or shown > 500:
        out.append('  ...(超过 500 个, 已截断)')
    return '\n'.join(out)


def t_set_parameter(args):
    """改零件参数的值, 然后 Update 重建。"""
    name = arg_s(args, 'name').strip()
    if not name:
        raise ValueError('缺少参数 name(参数名, 例如 "Length.1"; 用 catia_get_parameters 看全部名字)')
    if args.get('value') is None or args.get('value') == '':
        raise ValueError('缺少参数 value(数值, 或带单位的字符串如 "50mm")')
    raw = str(args.get('value'))
    try:
        num = float(raw.strip().replace(',', '.'))
        is_num, num_vbs = True, repr(num)
    except ValueError:
        is_num, num_vbs = False, repr(0.0)
    if _dry():
        return _dry_note('把参数 %r 设为 %r 并 Update 重建零件' % (name, raw))
    body = _COMMON + '''
Dim aimNm, aimCnt, aimI, aimPr, aimFound, aimHint, aimOld, aimOk, aimE1, aimE2
Sub aimRun()
Dim aimIsNum, aimNum, aimValStr, aimUpErr
aimNm = %s
aimValStr = %s
aimIsNum = %s
aimNum = %s
NeedP
Out "part", aimP.Name
aimCnt = 0
On Error Resume Next
Err.Clear
aimCnt = aimP.Parameters.Count
Err.Clear
aimFound = 0
aimHint = ""
For aimI = 1 To aimCnt
    Set aimPr = Nothing
    Set aimPr = aimP.Parameters.Item(aimI)
    If Err.Number = 0 And Not aimPr Is Nothing Then
        If StrComp(aimPr.Name, aimNm, 1) = 0 Then
            aimFound = aimI
        ElseIf InStrRev(aimPr.Name, "\\") > 0 Then
            If StrComp(Mid(aimPr.Name, InStrRev(aimPr.Name, "\\") + 1), aimNm, 1) = 0 Then aimFound = aimI
        End If
        If aimI <= 12 Then aimHint = aimHint & aimPr.Name & " / "
    End If
    Err.Clear
    If aimFound > 0 Then Exit For
Next
If aimFound = 0 Then
    On Error GoTo 0
    Fail "parameter not found: " & aimNm & " (first parameters: " & aimHint & ")"
End If
Err.Clear
Set aimPr = Nothing
Set aimPr = aimP.Parameters.Item(aimFound)
aimOld = aimPr.ValueAsString
aimOk = 0
aimE1 = ""
aimE2 = ""
Err.Clear
If aimIsNum Then
    aimPr.Value = aimNum
    If Err.Number = 0 Then aimOk = 1
End If
If aimOk = 0 Then
    aimE1 = AimErrText("Parameter.Value")
    Err.Clear
    aimPr.ValueAsString = aimValStr
    If Err.Number = 0 Then aimOk = 1
Else
    Err.Clear
End If
If aimOk = 0 Then aimE2 = AimErrText("Parameter.ValueAsString")
Err.Clear
Out "param", aimPr.Name
Out "old", aimOld
Out "set_ok", CStr(aimOk)
If aimOk = 0 Then
    Out "set_err", "Value: " & aimE1 & " ;; ValueAsString: " & aimE2
Else
    If aimIsNum = False Then
        Out "set_written", "ValueAsString"
    Else
        Out "set_written", "Value"
    End If
End If
aimUpErr = ""
Err.Clear
aimP.Update
If Err.Number <> 0 Then
    aimUpErr = AimErrText("Part.Update")
    Err.Clear
    Out "updated", "0"
    Out "update_err", aimUpErr
Else
    Out "updated", "1"
End If
Out "new", aimPr.ValueAsString
Err.Clear
On Error GoTo 0
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("set_parameter script aborted")
On Error GoTo 0
''' % (vbs(name), vbs(raw), 'True' if is_num else 'False', num_vbs)
    r = run_vbs(body)
    v = r.get('vars') or {}
    out = ['零件 %s: 参数 %s: %s -> %s'
           % (v.get('part', '?'), v.get('param', name),
              v.get('old', '?'), v.get('new', '(没读回)'))]
    if v.get('set_ok') == '1':
        out.append('  写入方式: %s' % (
            '按字符串写入 ValueAsString(可以带单位, 如 "50mm")'
            if v.get('set_written') == 'ValueAsString' else '按数值写入 Value'))
    else:
        out.append('  写入失败 —— 原始报错: %s' % (v.get('set_err') or '(没有回传原因)'))
    if v.get('updated') == '1':
        out.append('  已 Update 重建: 成功')
    else:
        out.append('  已尝试 Update 重建: 失败 —— %s' % (v.get('update_err') or '(没有回传原因)'))
    return '\n'.join(out)


def t_update_part(args):
    """强制重建(Part.Update), 并把结果说清楚。"""
    if _dry():
        return _dry_note('把 InWorkObject 设回主实体并执行 Part.Update 强制重建')
    body = _COMMON + '''
Dim aimB, aimUpErr, aimShapes, aimParams, aimDoc
Sub aimRun()
NeedP
Out "part", aimP.Name
Set aimB = MB()
If Not aimB Is Nothing Then
    On Error Resume Next
    Err.Clear
    aimP.InWorkObject = aimB
    Err.Clear
    On Error GoTo 0
    Out "body", aimB.Name
End If
On Error Resume Next
Err.Clear
aimP.Update
If Err.Number <> 0 Then
    aimUpErr = AimErrText("Part.Update")
    Err.Clear
    Out "updated", "0"
    Out "update_err", aimUpErr
Else
    Out "updated", "1"
End If
Err.Clear
aimShapes = 0
If Not aimB Is Nothing Then aimShapes = aimB.Shapes.Count
Err.Clear
Out "shapes", CStr(aimShapes)
Out "params", CStr(aimP.Parameters.Count)
Err.Clear
Set aimDoc = Nothing
Set aimDoc = gApp.ActiveDocument
If Err.Number = 0 And Not aimDoc Is Nothing Then
    Out "saved", CStr(aimDoc.Saved)
End If
Err.Clear
On Error GoTo 0
End Sub

' Safety net: no unhandled error may kill the script silently
On Error Resume Next
aimRun
If Err.Number <> 0 Then Fail AimErrText("update_part script aborted")
On Error GoTo 0
'''
    r = run_vbs(body)
    v = r.get('vars') or {}
    out = ['零件 %s 的强制重建: %s'
           % (v.get('part', '?'), '成功(Part.Update 已执行)' if v.get('updated') == '1'
              else '失败')]
    if v.get('body'):
        out.append('  主实体: %s (已设为 InWorkObject)' % v['body'])
    out.append('  特征数: %s   参数数: %s' % (v.get('shapes', '?'), v.get('params', '?')))
    if v.get('updated') != '1':
        out.append('  CATIA 侧原始报错: %s' % (v.get('update_err') or '(没有回传原因)'))
    if v.get('saved') == 'False' or v.get('saved') == '0':
        out.append('  文档当前有未保存的修改(重建后如需保留, 用 catia_save_document 保存)')
    out.append('  说明: Part.Update 就是 CATIA 的完整重建(最强制的一档)。若某特征报错, '
               'CATIA 通常会弹"更新诊断"窗口 —— 那会让脚本超时/无输出, 这时要先去 CATIA 里看诊断。')
    return '\n'.join(out)


# --------------------------------------------------------------------------
# 工具表
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_measure_distance',
     '量两个元素之间的距离(在 CATIA 用 SPAWorkbench 实测): mode="minimum"(默认, 最小距离)'
     '或 "center"(重心/中心距)。元素名可用 PartBody / XY,YZ,ZX / 特征名 / selection1,selection2。',
     obj({'element1': S('第一个元素名, 如 "PartBody" 或 "Pad.1"'),
          'element2': S('第二个元素名, 如 "XY" 或 "Face.3"'),
          'mode': S('"minimum"(默认)=最小距离; "center"=两个元素中心(重心)之间的距离')},
         ['element1', 'element2']),
     t_measure_distance),

    ('catia_get_inertia',
     '量活动零件主实体的体积/表面积/质量/重心(以及主惯性矩与惯性矩阵)。都来自 CATIA 的 '
     'Measurable; 质量需要零件有密度/材料, 取不到会明确说明原因, 不编数字。',
     obj({}), t_get_inertia),

    ('catia_get_bounding_box',
     '量活动零件主实体的包围盒(外形尺寸)。走 Measurable.GetBoundingBox; 取不到就如实报出'
     '原始报错, 并给出"导出 STL 再量网格"的替代方案。',
     obj({}), t_get_bounding_box),

    ('catia_get_parameters',
     '列出活动零件的全部参数(名字与值)。',
     obj({}), t_get_parameters),

    ('catia_set_parameter',
     '修改活动零件的某个参数值(按名字找, 支持全名或短名), 改完执行 Update 重建。',
     obj({'name': S('参数名, 如 "Length.1"(可用 catia_get_parameters 查)'),
          'value': S('新值: 数值(按数值写入)或带单位字符串如 "50mm"(按 ValueAsString 写入)')},
         ['name', 'value']),
     t_set_parameter),

    ('catia_update_part',
     '强制重建活动零件(把主实体设为 InWorkObject 后执行 Part.Update)。',
     obj({}), t_update_part),
]
