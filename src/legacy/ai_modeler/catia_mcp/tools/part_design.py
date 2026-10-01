# -*- coding: utf-8 -*-
r"""零件设计工具(15 个) —— 对应参考项目 catia-v5-mcp-server 的 part_design 模块。

写法与 document.py 一致: TOOLS = [(工具名, 说明, inputSchema, 处理函数), ...],
处理函数拼一小段 VBScript 交给 bridge.run_vbs, 用 Out/Fail 回传结果。

这一份把真机上踩过的 CATIA / VBScript 坑都固化进去了(每处都有注释):
    · 中文版不能用 Bodies.Item("PartBody")      -> 用脚本里的 Body()(内部走 Part.MainBody)
    · 偏移平面上的草图必须先 OpenEdition        -> 用脚本里的 Sk(z)
    · 往 HybridBody 加平面会把 InWorkObject 抢走 -> 做实体特征前 _focus_body() 切回实体
    · 凸台朝 +Z 长、凹槽朝 -Z 切                -> 方向反了就靠 Part.Update 失败 -> FlipFeature 兜底
    · AddNewShaft 会把自动化连接搞断(真机实测)  -> shaft / groove 直接返回不支持说明, 绝不调用
    · 参数变了要 Part.Update                    -> 每个特征建完都重建
    · **VBScript 不分大小写, 变量名不能撞关键字** -> 所以只有 oNum / oFound / oRT 这类名字
      (曾经写成 Dim oN / Dim oR: oN==On, oR==Or, 整个脚本编译不过, cscript 既不回
       AIM:ERROR 也不回 AIM:DONE, bridge 只能报"脚本没有正常结束")
    · 脚本片段一律"先把模板字面量拼完, 再单独做一次 % 格式化", 不跟 Python 的隐式
      字符串拼接混用(混用会让 % 的作用范围落到整段拼接上, 报 not all arguments converted)

单位: 长度 mm, 角度 度(传进 CATIA 时按它的约定转成弧度)。
所有创建调用都是 On Error Resume Next + 失败 Fail "工具名: " & Err.Description,
把 CATIA 的原始错误带回去, 绝不静默假装成功。
"""
from __future__ import annotations

from ..bridge import run_vbs
from ._schema import (A, B, I, N, S, arg_b, arg_f, arg_i, arg_s, obj, vbs,
                      vbs_num)

NAME = 'part_design'

# 接口不存在/被真机验证为不可用时的统一话术
UNSUPPORTED = '该 CATIA 版本不支持通过 API 建此特征, 请在界面里手动做。'

_DRY_NOTE = ('  (干跑模式: 只校验参数并生成脚本, 没有真的连 CATIA —— '
             'AI_MODELER_MCP_DRYRUN=1)')


# --------------------------------------------------------------------------
# 脚本公共片段
# --------------------------------------------------------------------------

# 脚本是 Option Explicit, 每个片段用到的变量都要声明一次。
# 名字全部避开: VBScript 关键字(oN==On / oR==Or 这种坑), bridge 预置的
# gApp/gDoc/... 全局, 以及预置与内核里的 Sub/Function 名字。
_DIM = (
    'Dim oE, oErr1, oErrN, oErrTxt, oSk, oSk2, oSI, oSN, oF, oF2d, oNum, oI, oJ, oK\n'
    'Dim oNP, oCnt, oA, oX, oY, oZ, oX1, oX2, oY1, oY2, oDia, oDep, oRad, oPos, oType\n'
    'Dim oThk, oAng, oFlag, oFail, oSkip, oSel, oFound, oRef, oRef2, oRef3, oAxisLn\n'
    'Dim oPlane, oSpa, oMeas, oItem, oVal, oVal2, oName, oTp, oMargin, oPcd, oCbD\n'
    'Dim oCbDep, oFeat, oSh, oProd, oRoot, oDoc, oDoc2, oD2, oPart2, oPub, oAxis\n'
    'Dim oLimit, oAll, oList, oVol0, oVol1, oCut, oDel, oMsg, gFound, gBBoxOk, gWalk, gPIdx\n'
    'Dim gTopZ, gBotZ, gXMin, gXMax, gYMin, gYMax\n'
    'Dim oArr1(2), oArr2(2), oMtx(11), oXs(24), oYs(24)\n'
)


def _focus_body(tool: str) -> str:
    """坑 3: 混合几何体(偏移平面)会把 InWorkObject 抢走, 建实体特征前切回实体并重建。"""
    tmpl = (
        "' quirk 3: a hybrid body steals InWorkObject -> put the solid body back\n"
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Part().InWorkObject = Body()\n'
        'Part().Update\n'
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oErrN <> 0 Then Fail "%s: cannot make the part body the in-work object: " & oErrTxt\n'
    )
    return tmpl % tool


def _create(tool: str, call: str, var: str = 'oF', soft: bool = False) -> str:
    """On Error Resume Next + 失败带回 CATIA 原始错误。

    soft=True: 只累计失败(oFail/oErr1)并给成功的计数(oCnt), 由调用方决定要不要 Fail
               —— 用在"所有边批量倒圆"这类一条失败不该毁掉全部的地方。
    soft=False: 直接 Fail, 带上工具名和 CATIA 的 Err.Description。
    """
    head = (
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set %s = Nothing\n'
        'Set %s = %s\n'
    ) % (var, var, call)
    tail = (
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oErrN <> 0 Or %s Is Nothing Then\n'
    ) % var
    if soft:
        mid = (
            'oFail = oFail + 1\n'
            'If Len(oErrTxt) = 0 Then oErrTxt = "CATIA returned no object"\n'
            'If Len(oErr1) = 0 Then oErr1 = oErrTxt\n'
            'Else\n'
            'oCnt = oCnt + 1\n'
        )
    else:
        mid = 'Fail "%s: " & oErrTxt\n' % tool
    return head + tail + mid + 'End If\n'


def _flip_after(tool: str) -> str:
    """坑 4: 凸台朝 +Z 长、凹槽朝 -Z 切; 方向反了重建会失败 -> 翻转一次再试。"""
    tmpl = (
        "' quirk 4: pad grows to +Z, pocket cuts to -Z; if the update is refused,\n"
        "' flip the feature once and update again (how the core build proved it out)\n"
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Part().Update\n'
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'If oErrN <> 0 Then\n'
        'If FlipFeature(oF) Then\n'
        'Part().Update\n'
        'If Err.Number = 0 Then Out "flipped", "1"\n'
        'End If\n'
        'If Err.Number <> 0 Then\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Fail "%s: the feature was created but the part will not update: " & oErrTxt\n'
        'End If\n'
        'Err.Clear\n'
        'End If\n'
        'On Error GoTo 0\n'
    )
    return tmpl % tool


def _out_counts() -> str:
    return (
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oNum = FeatureCount()\n'
        'oSN = Body().Sketches.Count\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Out "features", oNum\n'
        'Out "sketches", oSN\n'
    )


def _pick_sketch(tool: str, idx_expr: str) -> str:
    """挑草图: 省略(0)=实体里最后一个; 1 起编号与 catia_list_features 列的一致。"""
    tmpl = (
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oSN = Body().Sketches.Count\n'
        'If Err.Number <> 0 Then\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Fail "%s: cannot count the sketches of the part body: " & oErrTxt\n'
        'End If\n'
        'On Error GoTo 0\n'
        'If oSN < 1 Then Fail "%s: the part body has no sketch yet - create a sketch first"\n'
        'If oSI <= 0 Then oSI = oSN\n'
        'If oSI > oSN Then Fail "%s: sketch_index " & oSI & " is out of range 1.." & oSN\n'
        'Set oSk = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oSk = Body().Sketches.Item(oSI)\n'
        'If Err.Number <> 0 Or oSk Is Nothing Then\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Fail "%s: cannot get sketch #" & oSI & ": " & oErrTxt\n'
        'End If\n'
        'On Error GoTo 0\n'
        'Out "sketch", oSk.Name\n'
        'Out "sketch_index", oSI\n'
        'Out "sketch_count", oSN\n'
    )
    return ('oSI = %s\n' % idx_expr) + (tmpl % ((tool,) * 4))


def _limit_block(tool: str, want: int) -> str:
    """want=1: 把第一限制改成 UpToNext(1); 老接口没有 LimitMode 就只警告不谎报。"""
    if not want:
        return ''
    tmpl = (
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oF.FirstLimit.LimitMode = 1\n'
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oErrN <> 0 Then\n'
        'Out "warn", "%s: the feature was created with the given size, but LimitMode=UpToNext was refused: " & oErrTxt\n'
        'Else\n'
        'Out "limit", "up_to_next"\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Part().Update\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'End If\n'
    )
    return tmpl % tool


def _spa_block() -> str:
    """SPA 工作台(量包围盒/体积/面积/长度)。

    真机实测(中文版 V5-6R2023): **GetWorkbench 只在 Document 上有**, Part.GetWorkbench
    直接 438 "对象不支持此属性或方法"; 而且这个版本的 SPATypeLib 里 Measurable 上
    **没有 GetBoundingBox**(只有 Area/Volume 之类能用)。所以:
        · Doc() 先试, Part() 兜底(别的版本上 Part 也行)
        · 拿不到就保持 Nothing, 调用方必须容忍(比如孔的顶面高度改用特征高度估算)
    """
    return (
        'Set oSpa = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oSpa = Doc().GetWorkbench("SPAWorkbench")\n'
        'If Err.Number <> 0 Or oSpa Is Nothing Then\n'
        'Err.Clear\n'
        'Set oSpa = Part().GetWorkbench("SPAWorkbench")\n'
        'End If\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
    )


# 量体积 + 从特征高度估算材料顶面 z。
# 为什么需要: 本机 Measurable.GetBoundingBox 不存在, 孔/凹槽的草图必须先放在材料
# **上方**才能靠 -Z 切下去(凸台朝 +Z 长)。真机实测过: 草图放在 z=0 而材料在 z=0..20 时,
# Pocket 不会报错, 但**一点材料都没切掉**(体积前后一样) —— 静默错模型。
# 所以这里既估高度, 也用体积核对"到底切掉了没有"。
_MEASURE_DEFS = (
    'Function AimVolume()\n'
    'Dim w, m\n'
    'AimVolume = -1\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set w = Doc().GetWorkbench("SPAWorkbench")\n'
    'If Err.Number <> 0 Or w Is Nothing Then\n'
    'Err.Clear\n'
    'Set w = Part().GetWorkbench("SPAWorkbench")\n'
    'End If\n'
    'If Err.Number = 0 And Not w Is Nothing Then\n'
    'Set m = w.GetMeasurable(RefOf(Body()))\n'
    'If Err.Number = 0 And Not m Is Nothing Then AimVolume = m.Volume\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'End Function\n'
    '\n'
    'Function AimTopZ()\n'
    "' fallback when no bbox API: sum the height of every Pad (sketches are normally on XY)\n"
    'Dim i, n, sh, h, t\n'
    'AimTopZ = 0\n'
    't = 0\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'n = FeatureCount()\n'
    'Err.Clear\n'
    'For i = 1 To n\n'
    'Set sh = Nothing\n'
    'Err.Clear\n'
    'Set sh = Body().Shapes.Item(i)\n'
    'If Err.Number = 0 And Not sh Is Nothing Then\n'
    'h = 0\n'
    'Err.Clear\n'
    'h = sh.FirstLimit.Dimension.Value\n'
    'If Err.Number = 0 Then\n'
    'If TypeName(sh) = "Pad" Then t = t + h\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'AimTopZ = t\n'
    'End Function\n'
    '\n'
    'Function AimReverse(oF, oDep)\n'
    "' flip a pocket to the other side. 真机实测: Pocket 上 ReverseDirection / Reverse 都不存在\n"
    "' (438), DirectionOrientation 设得进去但不生效; 真正有效的是把第一限制改成**负值**\n"
    "' (等价于负深度) —— 实测体积会从 1.92e-4 掉到 1.7786e-4, 说明真的换了一侧切。\n"
    'AimReverse = False\n'
    'If oF Is Nothing Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'If oDep > 0 Then\n'
    'oF.FirstLimit.Dimension.Value = -oDep\n'
    'Else\n'
    'oF.FirstLimit.Dimension.Value = -25\n'
    'End If\n'
    'If Err.Number = 0 Then AimReverse = True\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'End Function\n'
    '\n'
    'Function AimCutOk(oF, oDep)\n'
    "' 切完用体积核对: 0=量不到体积没法核对, 1=切到了, 2=翻到另一侧后切到了\n"
    'Dim v2\n'
    'AimCutOk = 0\n'
    'v2 = AimVolume()\n'
    'If oVol0 <= 0 Or v2 <= 0 Then Exit Function\n'
    'If Abs(oVol0 - v2) > 0.000000001 Then\n'
    'AimCutOk = 1\n'
    'Exit Function\n'
    'End If\n'
    'If Not AimReverse(oF, oDep) Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Part().Update\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'v2 = AimVolume()\n'
    'If v2 > 0 Then\n'
    'If Abs(oVol0 - v2) > 0.000000001 Then AimCutOk = 2\n'
    'End If\n'
    'End Function\n'
)


def _bbox_block() -> str:
    """量实体包围盒: 孔/凹槽的草图要放在材料上方, 靠它拿顶面高度 + 四角位置。"""
    return (
        "' measure the solid bounding box (needs the SPA workbench licence)\n"
        'gBBoxOk = False\n'
        'gTopZ = 0\n'
        'gBotZ = 0\n'
        'gXMin = 0\n'
        'gXMax = 0\n'
        'gYMin = 0\n'
        'gYMax = 0\n'
        + _spa_block() +
        'If Not oSpa Is Nothing Then\n'
        'Set oMeas = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oMeas = oSpa.GetMeasurable(RefOf(Body()))\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If Not oMeas Is Nothing Then\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oMeas.GetBoundingBox oArr1, oArr2\n'
        'If Err.Number = 0 Then\n'
        'gXMin = oArr1(0)\n'
        'gXMax = oArr2(0)\n'
        'gYMin = oArr1(1)\n'
        'gYMax = oArr2(1)\n'
        'gBotZ = oArr1(2)\n'
        'gTopZ = oArr2(2)\n'
        'gBBoxOk = True\n'
        'End If\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'End If\n'
        'End If\n'
        'If gBBoxOk Then Out "bbox_ok", "1"\n'
        'If gBBoxOk Then Out "top_z", gTopZ\n'
        'If gBBoxOk Then Out "bot_z", gBotZ\n'
    )


# 拓扑(边/面)枚举: 用 Selection.Search 让 CATIA 把边/面选进选择集, 再取引用。
# 拿不到就 Out "unsupported" 并明确让用户手动做, 绝不假装成功 —— 所以每层都容错。
_TOPO_DEFS = (
    'Function AimItemOf(oBase, oIdx)\n'
    'Dim oT2\n'
    'Set oT2 = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oT2 = oBase.Item2(oIdx)\n'
    'If Err.Number <> 0 Or oT2 Is Nothing Then\n'
    'Err.Clear\n'
    'Set oT2 = oBase.Item(oIdx)\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Set AimItemOf = oT2\n'
    'End Function\n'
    '\n'
    'Function AimIsBody(oIt)\n'
    'Dim oV3, oT3\n'
    'AimIsBody = False\n'
    'If oIt Is Nothing Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oV3 = oIt.Value\n'
    'If Err.Number = 0 And Not oV3 Is Nothing Then\n'
    'Err.Clear\n'
    'If oV3 Is Body() Then AimIsBody = True\n'
    'If Err.Number <> 0 Then\n'
    'Err.Clear\n'
    'oT3 = TypeName(oV3)\n'
    'If oT3 = "Body" Or oT3 = "HybridBody" Or oT3 = "Part" Then AimIsBody = True\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'End Function\n'
    '\n'
    'Function AimCount(oBase)\n'
    'Dim i, oIt, n, c\n'
    'AimCount = 0\n'
    'If oBase Is Nothing Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'n = oBase.Count2\n'
    'If Err.Number <> 0 Or n <= 0 Then\n'
    'Err.Clear\n'
    'n = oBase.Count\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'c = 0\n'
    'For i = 1 To n\n'
    'Set oIt = AimItemOf(oBase, i)\n'
    'If Not AimIsBody(oIt) Then c = c + 1\n'
    'Next\n'
    'AimCount = c\n'
    'End Function\n'
    '\n'
    'Function AimRefAt(oBase, oK)\n'
    'Dim i, oIt, n, c, oRR\n'
    'Set AimRefAt = Nothing\n'
    'If oBase Is Nothing Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'n = oBase.Count2\n'
    'If Err.Number <> 0 Or n <= 0 Then\n'
    'Err.Clear\n'
    'n = oBase.Count\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'c = 0\n'
    'For i = 1 To n\n'
    'Set oIt = AimItemOf(oBase, i)\n'
    'If Not AimIsBody(oIt) Then\n'
    'c = c + 1\n'
    'If c = oK Then\n'
    'Set oRR = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oRR = oIt.Reference\n'
    'If Err.Number <> 0 Or oRR Is Nothing Then\n'
    'Err.Clear\n'
    'Set oRR = Part().CreateReferenceFromObject(oIt.Value)\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Set AimRefAt = oRR\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Next\n'
    'End Function\n'
    '\n'
    'Function AimFind(oSel, oWhat)\n'
    'Dim oColl\n'
    'Set oColl = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'oSel.Clear\n'
    'oSel.Add Body()\n'
    'Set oColl = oSel.Search("Topology." & oWhat & ",sel")\n'
    'If oColl Is Nothing Then Set oColl = oSel\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Set AimFind = oColl\n'
    'End Function\n'
    '\n'
    'Function AimFlatFace(oBase, oWantTop)\n'
    'Dim k, n, oR2, oS2, oM2, oMn(2), oMx(2), oBz\n'
    'Set AimFlatFace = Nothing\n'
    'n = AimCount(oBase)\n'
    'If n < 1 Then Exit Function\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oS2 = Part().GetWorkbench("SPAWorkbench")\n'
    'If Err.Number <> 0 Or oS2 Is Nothing Then\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Exit Function\n'
    'End If\n'
    'On Error GoTo 0\n'
    'If oWantTop Then oBz = -1E+30 Else oBz = 1E+30\n'
    'For k = 1 To n\n'
    'Set oR2 = AimRefAt(oBase, k)\n'
    'If Not oR2 Is Nothing Then\n'
    'Set oM2 = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oM2 = oS2.GetMeasurable(oR2)\n'
    'Err.Clear\n'
    'If Not oM2 Is Nothing Then\n'
    'Err.Clear\n'
    'oM2.GetBoundingBox oMn, oMx\n'
    'If Err.Number = 0 Then\n'
    'If Abs(oMx(2) - oMn(2)) < 0.0001 Then\n'
    'If oWantTop Then\n'
    'If oMx(2) > oBz Then\n'
    'oBz = oMx(2)\n'
    'Set AimFlatFace = oR2\n'
    'End If\n'
    'Else\n'
    'If oMx(2) < oBz Then\n'
    'oBz = oMx(2)\n'
    'Set AimFlatFace = oR2\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'End If\n'
    'Next\n'
    'End Function\n'
)


def _find_topo(tool: str, what: str, var: str = 'oNum') -> str:
    """拿到"要处理的边/面"的集合。

    两条路(真机实测过第一条在本机不可用):
      1. Selection.Search("Topology.Edge|Face,sel") —— 本机(中文版 V5-6R2023)所有写法
         都失败("方法 Search 失败"; 只有 Name=* 这类查询能用, 搜不到 B-Rep 拓扑)。
      2. **用户在 CATIA 里已经选好的元素**(当前选择集) —— 用它当来源, 于是
         "先在界面里选中边/面, 再让工具倒圆/抽壳" 这条路在本机是通的。
    两条都不行就 Out "unsupported", 明确让用户手动做, 绝不假装成功。
    """
    key = 'edges' if what == 'Edge' else 'faces'
    tmpl = (
        'Set oSel = Sel()\n'
        'oNum = 0\n'
        "' (1) what the user already picked in CATIA wins (Selection.Search is broken here)\n"
        'Set oFound = Nothing\n'
        'If AimCount(oSel) >= 1 Then\n'
        'Set oFound = oSel\n'
        'Out "source", "selection"\n'
        'Else\n'
        "' (2) ask CATIA to find them for us (works on builds where the query is supported)\n"
        'Set oFound = AimFind(oSel, "%s")\n'
        'If AimCount(oFound) >= 1 Then Out "source", "search"\n'
        'End If\n'
        'oNum = AimCount(oFound)\n'
        'Out "%s", oNum\n'
        'If oNum < 1 Then\n'
        'Out "unsupported", "1"\n'
        'Out "feature", "%s"\n'
        'Out "detail", "CATIA found no %s: Selection.Search(""Topology.%s"") fails on this build '
        'and nothing is selected in the CATIA window - select the %s in CATIA first and call the '
        'tool again, or do this feature by hand"\n'
        'End If\n'
    )
    return tmpl % (what, key, tool, key[:-1], what, key[:-1])


def _face_ref(tool: str, idx: int, auto_top: bool) -> str:
    """取面: 给了序号用序号(1 起), 没给就自动取最上面的水平面。"""
    s = 'oSI = %d\n' % idx
    s += 'Set oRef = Nothing\n'
    s += 'If oSI >= 1 Then\n'
    s += 'Set oRef = AimRefAt(oFound, oSI)\n'
    s += ('If oRef Is Nothing Then Fail "%s: face_index " & oSI & " is out of range 1.." & oNum\n'
          % tool)
    s += 'Else\n'
    if auto_top:
        s += 'Set oRef = AimFlatFace(oFound, True)\n'
    s += 'End If\n'
    s += ('If oRef Is Nothing Then Fail "%s: cannot get the face - pass face_index '
          '(see catia_list_edges(what=""faces""))"\n') % tool
    s += 'Out "face", oSI\n'
    return s


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

def t_pad(args):
    h = arg_f(args, 'height', 0.0)
    if h <= 0:
        raise ValueError('height(凸台高度 mm)必须大于 0')
    lim = (arg_s(args, 'limit', 'dim') or 'dim').strip().lower()
    if lim in ('up_to_next', 'uptonext', 'next', 'up to next'):
        lim = 'up_to_next'
    elif lim in ('dim', 'dimension', ''):
        lim = 'dim'
    else:
        raise ValueError("limit 只能是 'dim'(按 height) 或 'up_to_next'(长到下一个面)")
    idx = arg_i(args, 'sketch_index', 0) or 0

    body = (_DIM
            + _pick_sketch('catia_pad', idx)
            + _focus_body('catia_pad')
            + _create('catia_pad', 'SF().AddNewPad(oSk, %s)' % vbs_num(h))
            + 'Out "feature", oF.Name\n'
            + _limit_block('catia_pad', 1 if lim == 'up_to_next' else 0)
            + _flip_after('catia_pad')
            + _out_counts())
    v = _vars(run_vbs(body))
    out = ['已创建凸台(Pad): 高度 %s mm  ->  特征 %s' % (h, v.get('feature', '?'))]
    if v.get('limit') == 'up_to_next':
        out.append('  限制: 到下一个面(UpToNext), height 只作兜底')
    if v.get('flipped') == '1':
        out.append('  方向: CATIA 首次重建失败, 已自动翻转凸台方向一次(朝 +Z 长)')
    out.append('  草图 #%s = %s   实体特征 %s 个 / 草图 %s 个' % (
        v.get('sketch_index', '?'), v.get('sketch', '?'),
        v.get('features', '?'), v.get('sketches', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_pocket(args):
    d = arg_f(args, 'depth', 0.0)
    if d <= 0:
        raise ValueError('depth(切除深度 mm)必须大于 0')
    upto = arg_b(args, 'up_to_next', False)
    idx = arg_i(args, 'sketch_index', 0) or 0

    body = (_DIM
            + _MEASURE_DEFS
            + _pick_sketch('catia_pocket', idx)
            + _focus_body('catia_pocket')
            + 'oVol0 = AimVolume()\n'
            + _create('catia_pocket', 'SF().AddNewPocket(oSk, %s)' % vbs_num(d))
            + 'Out "feature", oF.Name\n'
            + _limit_block('catia_pocket', 1 if upto else 0)
            + _flip_after('catia_pocket')
            + "' 真机实测: 草图在材料另一侧时 Pocket 不报错但一点材料都不切 -> 用体积核对,\n"
            + "' 没切到就把第一限制改成负值翻到另一侧再切一次\n"
            + 'oCut = AimCutOk(oF, oDep)\n'
            + 'Out "cut", oCut\n'
            + 'If oCut = 0 And oVol0 > 0 Then\n'
            + 'oDel = 0\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'Sel().Clear\n'
            + 'Sel().Add oF\n'
            + 'Sel().Delete\n'
            + 'If Err.Number = 0 Then oDel = 1\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'oMsg = "kept (could not delete it)"\n'
            + 'If oDel = 1 Then oMsg = "deleted"\n'
            + 'Fail "catia_pocket: the cut removed no material in either direction - the profile '
            'is most likely already inside a void (an overlapping hole/pocket) or completely '
            'outside the solid; the empty feature was " & oMsg\n'
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    out = ['已创建凹槽(Pocket): 深度 %s mm  ->  特征 %s' % (d, v.get('feature', '?'))]
    out.append('  草图 #%s = %s   (凹槽朝 -Z 切, 草图应放在材料上方)' % (
        v.get('sketch_index', '?'), v.get('sketch', '?')))
    if v.get('limit') == 'up_to_next':
        out.append('  限制: 到下一个面(UpToNext)')
    _cut_line(out, v)
    out.append('  实体特征 %s 个 / 草图 %s 个' % (v.get('features', '?'),
                                         v.get('sketches', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


_UNSUP_READ_BODY = (
    'Out "unsupported", "1"\n'
    'Out "feature", "%s"\n'
    'Out "detail", "%s"\n'
    'oNum = -1\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oDoc = Act()\n'
    'If Not oDoc Is Nothing Then\n'
    'Set oPart2 = oDoc.Part\n'
    'If Err.Number = 0 And Not oPart2 Is Nothing Then\n'
    'oNum = MainBodyOf(oPart2).Shapes.Count\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Out "features", oNum\n'
)

_SHAFT_WHY = ('AddNewShaft / AddNewGroove disconnect the automation session on this '
              'CATIA build (measured on the real machine), so this tool never calls them')


def _revolve_unsupported(args, kind: str) -> str:
    """shaft / groove: 真机实测会断开自动化连接, 所以只读一眼文档再明确说不支持。"""
    ang = arg_f(args, 'angle', 360.0)
    v = {}
    try:
        v = _vars(run_vbs(_DIM + (_UNSUP_READ_BODY % (kind, _SHAFT_WHY))))
    except Exception:                                      # noqa: BLE001
        v = {}                    # 连不上也不影响"不支持"这个结论, 不是假装成功
    out = [UNSUPPORTED,
           '  想要的特征: %s, 角度 %s 度' % (kind, ang),
           '  原因: AddNewShaft / AddNewGroove 会让本机 CATIA 的自动化连接断开(真机实测), '
           '所以本工具**不会**调用它们(调用会毁掉整个会话)。',
           '  替代做法: 用多层圆盘近似 —— 分多次 catia_pad(每层一个草图, 高度=层厚); '
           '环形切除可以用草图圆 + catia_pocket 近似。',
           '  也可以在界面里手动做旋转体/旋转槽, 之后其它工具照常能用。']
    if v.get('features') not in (None, '', '-1'):
        out.append('  当前实体特征数: %s' % v.get('features'))
    out.append(_note_text(v))
    return _join(out)


def t_shaft(args):
    return _revolve_unsupported(args, 'Shaft (旋转体)')


def t_groove(args):
    return _revolve_unsupported(args, 'Groove (旋转槽/环形切除)')


def _edge_sel_list(args):
    """把 edge_index / edge_indices 归一成 "1,3,5" + all 标志。"""
    idx = arg_i(args, 'edge_index', 0) or 0
    raw = args.get('edge_indices') or []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    got = []
    if idx > 0:
        got.append(idx)
    for x in raw:
        try:
            n = int(float(x))
        except (TypeError, ValueError):
            raise ValueError('edge_indices 里必须都是整数序号: %r' % (x,))
        if n < 1:
            raise ValueError('edge_indices 只能从 1 开始: %r' % (x,))
        if n not in got:
            got.append(n)
    all_e = arg_b(args, 'all_edges', not got)
    return ','.join(str(n) for n in got), all_e


def _edge_dress(args, tool: str, kind: str):
    """fillet / chamfer 共用: 按序号(或全部)对实体边倒圆/倒角。"""
    if kind == 'fillet':
        val = arg_f(args, 'radius', 0.0)
        if val <= 0:
            raise ValueError('radius(圆角半径 mm)必须大于 0')
        call = 'SF().AddNewEdgeFilletWithConstantRadius(oRef, 1, %s)' % vbs_num(val)
        label = '圆角(Fillet)'
        what = 'filleted'
    else:
        val = arg_f(args, 'length', 0.0)
        if val <= 0:
            raise ValueError('length(倒角长度 mm)必须大于 0')
        call = ('SF().AddNewChamfer(oRef, 0, 0, 0, %s, %s)'
                % (vbs_num(val), vbs_num(val)))
        label = '倒角(Chamfer)'
        what = 'chamfered'
    lst, all_e = _edge_sel_list(args)

    body = (_DIM + _TOPO_DEFS
            + _focus_body(tool)
            + _find_topo(tool, 'Edge')
            + ('oList = %s\n' % vbs(lst))
            + ('oAll = %d\n' % (1 if all_e else 0))
            + 'oCnt = 0\n'
            + 'oFail = 0\n'
            + 'oErr1 = ""\n'
            + 'If oNum < 1 Then\n'
            + 'Out "made", 0\n'
            + 'Else\n'
            + ('If oAll = 0 And Len(oList) = 0 Then Fail "%s: give edge_index / edge_indices, '
               'or all_edges=True"\n') % tool
            + 'For oI = 1 To oNum\n'
            + 'oFlag = 0\n'
            + 'If oAll = 1 Then\n'
            + 'oFlag = 1\n'
            + 'ElseIf InStr("," & oList & ",", "," & oI & ",") > 0 Then\n'
            + 'oFlag = 1\n'
            + 'End If\n'
            + 'If oFlag = 1 Then\n'
            + 'Set oRef = AimRefAt(oFound, oI)\n'
            + 'If oRef Is Nothing Then\n'
            + 'oFail = oFail + 1\n'
            + 'If Len(oErr1) = 0 Then oErr1 = "cannot build a reference for edge #" & oI\n'
            + 'Else\n'
            + _create(tool, call, soft=all_e)
            + 'End If\n'
            + 'End If\n'
            + 'Next\n'
            + 'If oFail > 0 Then Out "warn", oFail & " edge(s) failed, first error: " & oErr1\n'
            + 'If oCnt = 0 Then\n'
            + 'If Len(oErr1) > 0 Then\n'
            + ('Fail "%s: no edge was %s; first error: " & oErr1\n' % (tool, what))
            + 'Else\n'
            + ('Fail "%s: no edge matched (the body has " & oNum & " edges) - check edge_index"\n'
               % tool)
            + 'End If\n'
            + 'End If\n'
            + 'Out "made", oCnt\n'
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and _int_of(v.get('made')) == 0:
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已创建%s: 半径/长度 %s mm, 成功处理 %s 条边' % (label, val, v.get('made', '?'))]
    out.append(_src_text(v))
    if v.get('edges') not in (None, ''):
        out.append('  实体边总数: %s 条(序号见 catia_list_edges)' % v.get('edges'))
    out.append('  实体特征 %s 个' % v.get('features', '?'))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_fillet(args):
    return _edge_dress(args, 'catia_fillet', 'fillet')


def t_chamfer(args):
    return _edge_dress(args, 'catia_chamfer', 'chamfer')


# --- 孔: 用"草图圆 + Pocket"实现(比 AddNewHole 稳得多) ---------------------

_HOLE_POS_DEFS = (
    'Sub AimHolePos()\n'
    'Dim oAa, oRr\n'
    'oNP = 0\n'
    'If oPos = "center" Then\n'
    'oXs(0) = 0\n'
    'oYs(0) = 0\n'
    'oNP = 1\n'
    'ElseIf oPos = "corners" Then\n'
    'oXs(0) = gXMin + oMargin\n'
    'oYs(0) = gYMin + oMargin\n'
    'oXs(1) = gXMax - oMargin\n'
    'oYs(1) = gYMin + oMargin\n'
    'oXs(2) = gXMax - oMargin\n'
    'oYs(2) = gYMax - oMargin\n'
    'oXs(3) = gXMin + oMargin\n'
    'oYs(3) = gYMax - oMargin\n'
    'oNP = 4\n'
    'ElseIf oPos = "pcd" Then\n'
    'If oCnt < 2 Then oCnt = 4\n'
    'oRr = oPcd / 2\n'
    'For oAa = 0 To oCnt - 1\n'
    'oXs(oAa) = oRr * Cos(2 * 3.14159265358979 * oAa / oCnt)\n'
    'oYs(oAa) = oRr * Sin(2 * 3.14159265358979 * oAa / oCnt)\n'
    'Next\n'
    'oNP = oCnt\n'
    'Else\n'
    'If oCnt < 1 Then oCnt = 1\n'
    'For oAa = 0 To oCnt - 1\n'
    'oXs(oAa) = oX + oAa * oDia * 1.5\n'
    'oYs(oAa) = oY\n'
    'Next\n'
    'oNP = oCnt\n'
    'End If\n'
    'End Sub\n'
    '\n'
    'Sub AimDrawCircles()\n'
    'Dim oBb\n'
    'For oBb = 0 To oNP - 1\n'
    'oF2d.CreateClosedCircle oXs(oBb), oYs(oBb), oRad\n'
    'Next\n'
    'End Sub\n'
)


def _hole_cut(tool: str) -> str:
    """草图圆 + Pocket, 并用体积核对"真的切掉了材料"(直孔/沉孔各用一次)。

    真机实测: 草图放错一侧(材料在 z=0..20, 草图在 z=0 往下切)时 CATIA **不报错**,
    但体积一点没变 —— 必须自己核对, 否则就是静默的错误模型。
    """
    head = (
        'oVol0 = AimVolume()\n'
        'Set oSk = Sk(oZ)\n'
        'Set oF2d = oSk.Factory2D\n'
        'If oF2d Is Nothing Then Fail "%s: cannot open a sketch on the offset plane at z=" & oZ\n'
        'Out "sketch", oSk.Name\n'
        'AimDrawCircles\n'
        'oSk.CloseEdition\n'
        'Out "holes", oNP\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Part().Update\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
    ) % tool
    tail = (
        'oCut = AimCutOk(oF, oDep)\n'
        'Out "cut", oCut\n'
        'If oCut = 0 And oVol0 > 0 Then\n'
        "' 两个方向都没切掉任何材料: 最多的情况是这块材料早就被别的孔/凹槽挖空了\n"
        "' (也可能草图整个在实体外面) -> 把那没用的特征删掉, 然后把两种情况都讲清楚\n"
        'oDel = 0\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Sel().Clear\n'
        'Sel().Add oF\n'
        'Sel().Delete\n'
        'If Err.Number = 0 Then oDel = 1\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'oMsg = "kept (could not delete it)"\n'
        'If oDel = 1 Then oMsg = "deleted"\n'
        'Fail "%s: the cut removed no material in either direction - the profile is most likely '
        'already inside a void (an overlapping hole/pocket) or completely outside the solid; '
        'the empty feature was " & oMsg\n'
        'End If\n'
    ) % tool
    return (head + _focus_body(tool) + _create(tool, 'SF().AddNewPocket(oSk, oDep)')
            + _flip_after(tool) + tail)


def t_hole(args):
    dia = arg_f(args, 'diameter', 0.0)
    if dia <= 0:
        raise ValueError('diameter(孔径 mm)必须大于 0')
    dep = arg_f(args, 'depth', 0.0)
    thru = arg_b(args, 'through', False)
    if not thru and dep <= 0:
        raise ValueError('要么给 depth(孔深 mm), 要么 through=True(通孔)')
    pos = (arg_s(args, 'pos', 'center') or 'center').strip().lower()
    pos = {'中心': 'center', '中间': 'center', '四角': 'corners', '角': 'corners',
           '圆周': 'pcd', '坐标': 'coord', '指定': 'coord'}.get(pos, pos)
    if pos not in ('center', 'corners', 'pcd', 'coord'):
        raise ValueError("pos 只能是 'center' / 'corners' / 'pcd' / 'coord'")
    htype = (arg_s(args, 'type', 'simple') or 'simple').strip().lower()
    htype = {'直孔': 'simple', 'cb': 'counterbore', '沉孔': 'counterbore',
             'cs': 'countersink', '锥形沉孔': 'countersink'}.get(htype, htype)
    if htype not in ('simple', 'counterbore', 'countersink'):
        raise ValueError("type 只能是 'simple' / 'counterbore' / 'countersink'")
    cnt = arg_i(args, 'count', 0) or 0
    pcd = arg_f(args, 'pcd', 0.0)
    margin = arg_f(args, 'margin', 0.0)
    if margin <= 0:
        margin = 10.0
    x = arg_f(args, 'x', 0.0)
    y = arg_f(args, 'y', 0.0)
    cbd = arg_f(args, 'cb_diameter', 0.0)
    cbdep = arg_f(args, 'cb_depth', 0.0)
    if pos == 'pcd' and pcd <= 0:
        raise ValueError('pos="pcd" 时必须给 pcd(分布圆直径 mm)')

    body = (_DIM
            + _HOLE_POS_DEFS
            + _MEASURE_DEFS
            + _bbox_block()
            + ('oDia = %s\n' % vbs_num(dia))
            + 'oRad = oDia / 2\n'
            + ('oPos = %s\n' % vbs(pos))
            + ('oCnt = %d\n' % cnt)
            + ('oPcd = %s\n' % vbs_num(pcd))
            + ('oMargin = %s\n' % vbs_num(margin))
            + ('oX = %s\n' % vbs_num(x))
            + ('oY = %s\n' % vbs_num(y))
            + ('oType = %s\n' % vbs(htype))
            + ('oDep = %s\n' % vbs_num(dep))
            + ('oCbD = %s\n' % vbs_num(cbd))
            + ('oCbDep = %s\n' % vbs_num(cbdep))
            + 'oZ = 0\n'
            + 'If gBBoxOk Then oZ = gTopZ\n'
            + "' no bbox API on this build -> estimate the top of the material from the pads\n"
            + 'If gBBoxOk = False Then oZ = AimTopZ()\n'
            + 'If oZ < 0 Then oZ = 0\n'
            + ('If %d = 1 Then\n' % (1 if thru else 0))
            + 'If gBBoxOk Then\n'
            + 'oDep = (gTopZ - gBotZ) + 2\n'
            + 'ElseIf oZ > 0 Then\n'
            + 'oDep = oZ + 20\n'
            + 'Else\n'
            + 'oDep = 200\n'
            + 'End If\n'
            + 'End If\n'
            + 'Out "depth", oDep\n'
            + 'Out "sketch_z", oZ\n'
            + 'oSkip = 0\n'
            + 'If oPos = "corners" And gBBoxOk = False Then\n'
            + 'oSkip = 1\n'
            + 'Out "unsupported", "1"\n'
            + 'Out "feature", "catia_hole: pos=corners"\n'
            + 'Out "detail", "the bounding box of the solid could not be measured (no SPA '
            'measurable), so the four corner positions are unknown - use pos=coord with x/y"\n'
            + 'End If\n'
            + 'If oSkip = 0 Then\n'
            + 'AimHolePos\n'
            + _hole_cut('catia_hole')
            + 'End If\n'
            + 'If oSkip = 0 And oType = "counterbore" Then\n'
            + 'If oCbD <= 0 Then\n'
            + 'Out "warn", "counterbore needs cb_diameter - only the plain hole was cut"\n'
            + 'Else\n'
            + 'If oCbDep <= 0 Then oCbDep = oCbD / 2\n'
            + 'oRad = oCbD / 2\n'
            + 'oDep = oCbDep\n'
            + _hole_cut('catia_hole')
            + 'Out "counterbore", "1"\n'
            + 'End If\n'
            + 'End If\n'
            + 'If oSkip = 0 And oType = "countersink" Then\n'
            + 'Out "warn", "a conical countersink cannot be made through this API path - '
            'only the plain hole was cut; make the cone by hand or with a revolve"\n'
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and not v.get('holes'):
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已打孔 %s 个: 直径 %s mm, 深 %s mm%s' % (
        v.get('holes', '?'), dia, v.get('depth', dep), '(通孔)' if thru else '')]
    out.append('  孔位: %s   草图平面 z=%s(材料上方, 往下切)' % (
        pos, v.get('sketch_z', '?')))
    if v.get('counterbore') == '1':
        out.append('  沉孔: 用第二个更大的圆再切一刀近似(直径 %s mm)' % cbd)
    _cut_line(out, v)
    if not v.get('bbox_ok'):
        out.append('  顶面 z 的来源: 本机没有包围盒 API(Measurable.GetBoundingBox 不存在), '
                   '按实体里各凸台的高度估出来的')
    out.append('  实体特征 %s 个 / 草图 %s 个' % (v.get('features', '?'),
                                         v.get('sketches', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def _last_feature_ref(tool: str) -> str:
    """取实体最后一个特征的引用(阵列/镜像都拿它当输入)。"""
    tmpl = (
        'oNum = 0\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oNum = FeatureCount()\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oNum < 1 Then Fail "%s: the part body has no feature yet - make one first"\n'
        'Set oFeat = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oFeat = Body().Shapes.Item(oNum)\n'
        'If Err.Number <> 0 Or oFeat Is Nothing Then\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Fail "%s: cannot get the last feature of the body: " & oErrTxt\n'
        'End If\n'
        'On Error GoTo 0\n'
        'Out "input", oFeat.Name\n'
        'Set oRef = RefOf(oFeat)\n'
    )
    return tmpl % (tool, tool)


def t_rect_pattern(args):
    cx = arg_i(args, 'count_x', 0)
    cy = arg_i(args, 'count_y', 0)
    sx = arg_f(args, 'spacing_x', 0.0)
    sy = arg_f(args, 'spacing_y', 0.0)
    if cx < 1 or cy < 1:
        raise ValueError('count_x / count_y 都必须是 >=1 的份数')
    if cx == 1 and cy == 1:
        raise ValueError('count_x 和 count_y 不能同时为 1(那样等于没有阵列)')
    call = ('SF().AddNewRectPattern(oRef, %d, %d, %s, %s, 1, 1, oRef2, oRef3, False, False, 0)'
            % (cx, cy, vbs_num(sx), vbs_num(sy)))
    body = (_DIM
            + _focus_body('catia_rect_pattern')
            + _last_feature_ref('catia_rect_pattern')
            + "' directions: the origin planes carry the axes (PlaneYZ -> +X, PlaneZX -> +Y)\n"
            + 'Set oRef2 = RefOf(Part().OriginElements.PlaneYZ)\n'
            + 'Set oRef3 = RefOf(Part().OriginElements.PlaneZX)\n'
            + _create('catia_rect_pattern', call)
            + 'Out "pattern", oF.Name\n'
            + _flip_after('catia_rect_pattern')
            + _out_counts())
    v = _vars(run_vbs(body))
    out = ['已创建矩形阵列: %s x %s 份, 间距 %s x %s mm  ->  特征 %s' % (
        cx, cy, sx, sy, v.get('pattern', '?'))]
    out.append('  被阵列的特征: %s   实体特征 %s 个' % (v.get('input', '?'),
                                              v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_circ_pattern(args):
    cnt = arg_i(args, 'count', 0)
    if cnt < 2:
        raise ValueError('count(总份数)必须 >=2')
    ang = arg_f(args, 'angle', 360.0)
    if ang == 0:
        ang = 360.0
    axis = (arg_s(args, 'axis', 'Z') or 'Z').strip().upper()
    if axis not in ('X', 'Y', 'Z'):
        raise ValueError("axis 只能是 'X' / 'Y' / 'Z'(默认 Z)")
    step = ang / cnt                       # 相邻两份的夹角(度)
    call = ('SF().AddNewCircPattern(oRef, 1, %d, 1, %s, 1, 1, oRef2, oRef3, False, %s, True)'
            % (cnt, vbs_num(step), vbs_num(ang)))
    body = (_DIM
            + _focus_body('catia_circ_pattern')
            + _last_feature_ref('catia_circ_pattern')
            + "' CircPattern wants a centre point and an axis line -> build them explicitly\n"
            + ('oAxis = %s\n' % vbs(axis))
            + ('oCnt = %d\n' % cnt)
            + 'Set oProd = Nothing\n'
            + 'Set oPart2 = Nothing\n'
            + 'Set oAxisLn = Nothing\n'
            + 'Set oPlane = Nothing\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'Set oPlane = Part().HybridBodies.Add()\n'
            + 'If Err.Number <> 0 Or oPlane Is Nothing Then\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Fail "catia_circ_pattern: cannot create a hybrid body for the rotation axis"\n'
            + 'End If\n'
            + 'Err.Clear\n'
            + 'Set oProd = HSF().AddNewPointCoord(0, 0, 0)\n'
            + 'If Err.Number <> 0 Then Set oProd = Nothing\n'
            + 'Err.Clear\n'
            + 'If oAxis = "X" Then\n'
            + 'Set oPart2 = HSF().AddNewPointCoord(1, 0, 0)\n'
            + 'ElseIf oAxis = "Y" Then\n'
            + 'Set oPart2 = HSF().AddNewPointCoord(0, 1, 0)\n'
            + 'Else\n'
            + 'Set oPart2 = HSF().AddNewPointCoord(0, 0, 1)\n'
            + 'End If\n'
            + 'If Err.Number <> 0 Then Set oPart2 = Nothing\n'
            + 'Err.Clear\n'
            + 'If Not oProd Is Nothing And Not oPart2 Is Nothing Then\n'
            + 'Set oAxisLn = HSF().AddNewLinePtPt(RefOf(oProd), RefOf(oPart2))\n'
            + 'If Err.Number <> 0 Then Set oAxisLn = Nothing\n'
            + 'End If\n'
            + 'Err.Clear\n'
            + 'If Not oProd Is Nothing Then oPlane.AppendHybridShape oProd\n'
            + 'If Not oPart2 Is Nothing Then oPlane.AppendHybridShape oPart2\n'
            + 'If Not oAxisLn Is Nothing Then oPlane.AppendHybridShape oAxisLn\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'If oProd Is Nothing Or oAxisLn Is Nothing Then\n'
            + 'Out "unsupported", "1"\n'
            + 'Out "feature", "catia_circ_pattern: HybridShapeFactory point/line"\n'
            + 'Out "detail", "the rotation centre point / axis line could not be built, so '
            'AddNewCircPattern cannot be called - do the circular pattern in the UI"\n'
            + 'Out "pattern", ""\n'
            + 'Out "input", ""\n'
            + 'Else\n'
            + 'Set oRef2 = RefOf(oProd)\n'
            + 'Set oRef3 = RefOf(oAxisLn)\n'
            + _focus_body('catia_circ_pattern')
            + _create('catia_circ_pattern', call)
            + 'Out "pattern", oF.Name\n'
            + _flip_after('catia_circ_pattern')
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and not v.get('pattern'):
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已创建圆形阵列: %s 份(总角 %s 度, 相邻间隔 %s 度, 绕原点的 %s 轴)  ->  特征 %s' % (
        cnt, ang, round(step, 6), axis, v.get('pattern', '?'))]
    out.append('  被阵列的特征: %s   实体特征 %s 个' % (v.get('input', '?'),
                                              v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_mirror(args):
    pl = (arg_s(args, 'plane', 'YZ') or 'YZ').strip().upper()
    if pl not in ('YZ', 'ZX', 'XY'):
        raise ValueError("plane 只能是 'YZ' / 'ZX' / 'XY'")
    body = (_DIM
            + _focus_body('catia_mirror')
            + _last_feature_ref('catia_mirror')
            + ('Set oRef2 = RefOf(Part().OriginElements.Plane%s)\n' % pl)
            + _create('catia_mirror', 'SF().AddNewMirror(oRef2)')
            + 'Out "mirror", oF.Name\n'
            + _flip_after('catia_mirror')
            + _out_counts())
    v = _vars(run_vbs(body))
    out = ['已创建镜像: 以 %s 平面镜像最后一个特征  ->  特征 %s' % (pl, v.get('mirror', '?'))]
    out.append('  被镜像的特征: %s   实体特征 %s 个' % (v.get('input', '?'),
                                              v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_shell(args):
    thk = arg_f(args, 'thickness', 0.0)
    if thk <= 0:
        raise ValueError('thickness(壁厚 mm)必须大于 0')
    idx = arg_i(args, 'face_index', 0) or 0
    body = (_DIM + _TOPO_DEFS
            + _focus_body('catia_shell')
            + _find_topo('catia_shell', 'Face')
            + 'If oNum < 1 Then\n'
            + 'Out "shell", ""\n'
            + 'Else\n'
            + _face_ref('catia_shell', idx, True)
            + _create('catia_shell', 'SF().AddNewShell(oRef, %s, 0)' % vbs_num(thk))
            + 'Out "shell", oF.Name\n'
            + _flip_after('catia_shell')
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and not v.get('shell'):
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已抽壳(Shell): 壁厚 %s mm, 移除面 #%s  ->  特征 %s' % (
        thk, v.get('face') or '自动(最上面的水平面)', v.get('shell', '?'))]
    out.append(_src_text(v))
    out.append('  实体面总数: %s   实体特征 %s 个' % (v.get('faces', '?'),
                                            v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_draft(args):
    ang = arg_f(args, 'angle', 0.0)
    idx = arg_i(args, 'face_index', 0)
    if idx < 1:
        raise ValueError('face_index(要拔模的面序号, 1 起)必填, 序号见 '
                         'catia_list_edges(what="faces")')
    if ang == 0:
        raise ValueError('angle(拔模角, 度)不能是 0')
    call = 'SF().AddNewDraft(oRef, oRef2, 0, Nothing, 0, 0, 1, 0, oAng, 0)'
    body = (_DIM + _TOPO_DEFS
            + _focus_body('catia_draft')
            + _find_topo('catia_draft', 'Face')
            + 'If oNum < 1 Then\n'
            + 'Out "draft", ""\n'
            + 'Else\n'
            + _face_ref('catia_draft', idx, False)
            + ('oAng = %s * 3.14159265358979 / 180\n' % vbs_num(ang))
            + "' try 1: neutral = PlaneXY (the usual case: the part was padded from XY)\n"
            + 'Set oRef2 = RefOf(Part().OriginElements.PlaneXY)\n'
            + 'oFail = 0\n'
            + 'oCnt = 0\n'
            + 'oErr1 = ""\n'
            + _create('catia_draft', call, soft=True)
            + 'If oFail > 0 Then\n'
            + "' try 2: neutral = the lowest horizontal face of the solid\n"
            + 'Set oRef2 = AimFlatFace(oFound, False)\n'
            + 'If oRef2 Is Nothing Then Fail "catia_draft: no usable neutral face was found '
            '(try another face_index, or draft in the UI)"\n'
            + 'oFail = 0\n'
            + 'oCnt = 0\n'
            + 'oErr1 = ""\n'
            + 'Set oF = Nothing\n'
            + _create('catia_draft', call, soft=True)
            + 'If oFail > 0 Then Fail "catia_draft: " & oErr1\n'
            + 'End If\n'
            + 'Out "draft", oF.Name\n'
            + _flip_after('catia_draft')
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and not v.get('draft'):
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已拔模(Draft): 面 #%s, 角度 %s 度(方向 +Z, 中性面 PlaneXY / 自动兜底)  ->  特征 %s' % (
        idx, ang, v.get('draft', '?'))]
    out.append(_src_text(v))
    out.append('  实体面总数: %s   实体特征 %s 个' % (v.get('faces', '?'),
                                            v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_thickness(args):
    thk = arg_f(args, 'thickness', 0.0)
    if thk <= 0:
        raise ValueError('thickness(加厚厚度 mm)必须大于 0')
    idx = arg_i(args, 'face_index', 1) or 1
    body = (_DIM + _TOPO_DEFS
            + _focus_body('catia_thickness')
            + _find_topo('catia_thickness', 'Face')
            + 'If oNum < 1 Then\n'
            + 'Out "thick", ""\n'
            + 'Else\n'
            + _face_ref('catia_thickness', idx, False)
            + _create('catia_thickness', 'SF().AddNewThickness(oRef, %s)' % vbs_num(thk))
            + 'Out "thick", oF.Name\n'
            + _flip_after('catia_thickness')
            + 'End If\n'
            + _out_counts())
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1' and not v.get('thick'):
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已加厚(Thickness): 厚度 %s mm, 加厚面 #%s  ->  特征 %s' % (
        thk, v.get('face', '?'), v.get('thick', '?'))]
    out.append(_src_text(v))
    out.append('  实体面总数: %s   实体特征 %s 个' % (v.get('faces', '?'),
                                            v.get('features', '?')))
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_list_features(args):
    body = (_DIM
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oNum = FeatureCount()\n'
            + 'oSN = Body().Sketches.Count\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'If oNum < 0 Then oNum = 0\n'
            + 'If oSN < 0 Then oSN = 0\n'
            + 'Out "features", oNum\n'
            + 'Out "sketches", oSN\n'
            + 'For oI = 1 To oNum\n'
            + 'Set oSh = Nothing\n'
            + 'oName = "?"\n'
            + 'oTp = "?"\n'
            + 'oVal = ""\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'Set oSh = Body().Shapes.Item(oI)\n'
            + 'If Err.Number = 0 And Not oSh Is Nothing Then\n'
            + 'oName = oSh.Name\n'
            + 'oTp = TypeName(oSh)\n'
            + 'Err.Clear\n'
            + 'oVal2 = ""\n'
            + 'oVal2 = oSh.FirstLimit.Dimension.Value\n'
            + 'If Err.Number = 0 Then oVal = oVal2\n'
            + 'Err.Clear\n'
            + 'End If\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Out "f" & oI, oName & Chr(9) & oTp & Chr(9) & oVal\n'
            + 'Next\n'
            + 'For oI = 1 To oSN\n'
            + 'Set oSh = Nothing\n'
            + 'oName = "?"\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'Set oSh = Body().Sketches.Item(oI)\n'
            + 'If Err.Number = 0 And Not oSh Is Nothing Then oName = oSh.Name\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Out "s" & oI, oName\n'
            + 'Next\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oNum = Body().HybridBodies.Count\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Out "hybrid", oNum\n')
    v = _vars(run_vbs(body))
    n = _int_of(v.get('features'))
    sn = _int_of(v.get('sketches'))
    out = ['实体特征 %d 个(序号用于 pattern/mirror 的"最后一个特征"):' % n]
    for i in range(1, n + 1):
        cells = (v.get('f%d' % i) or '?').split('\t')
        line = '  %d. %s' % (i, cells[0])
        if len(cells) > 1 and cells[1]:
            line += '   [%s]' % cells[1]
        if len(cells) > 2 and cells[2]:
            line += '   主尺寸 %s mm' % cells[2]
        out.append(line)
    out.append('草图 %d 个(catia_pad / catia_pocket 的 sketch_index 按这个 1 起编号):' % sn)
    for i in range(1, sn + 1):
        out.append('  %d. %s' % (i, v.get('s%d' % i, '?')))
    out.append('几何图形集(混合几何体) %s 个' % v.get('hybrid', '?'))
    out.append(_note_text(v))
    return _join(out)


def t_list_edges(args):
    what = (arg_s(args, 'what', 'edges') or 'edges').strip().lower()
    what = {'edge': 'edges', '边': 'edges', 'face': 'faces', '面': 'faces',
            'both': 'both', 'all': 'both'}.get(what, what)
    if what not in ('edges', 'faces', 'both'):
        raise ValueError("what 只能是 'edges' / 'faces' / 'both'")
    want_e = what in ('edges', 'both')
    want_f = what in ('faces', 'both')
    body = _DIM + _TOPO_DEFS + _spa_block() + 'Set oSel = Sel()\n'
    if want_e:
        body += ('Set oFound = AimFind(oSel, "Edge")\n'
                 'oNum = AimCount(oFound)\n'
                 'Out "edges", oNum\n'
                 'For oI = 1 To oNum\n'
                 'Set oRef = AimRefAt(oFound, oI)\n'
                 'oVal = ""\n'
                 'If Not oRef Is Nothing Then\n'
                 'Set oMeas = Nothing\n'
                 'On Error Resume Next\n'
                 'Err.Clear\n'
                 'If Not oSpa Is Nothing Then Set oMeas = oSpa.GetMeasurable(oRef)\n'
                 'If Not oMeas Is Nothing Then oVal = oMeas.Length\n'
                 'Err.Clear\n'
                 'On Error GoTo 0\n'
                 'End If\n'
                 'Out "e" & oI, oVal\n'
                 'Next\n')
    else:
        body += 'Out "edges", 0\n'
    if want_f:
        body += ('Set oFound = AimFind(oSel, "Face")\n'
                 'oNum = AimCount(oFound)\n'
                 'Out "faces", oNum\n'
                 'For oI = 1 To oNum\n'
                 'Set oRef = AimRefAt(oFound, oI)\n'
                 'oVal = ""\n'
                 'If Not oRef Is Nothing Then\n'
                 'Set oMeas = Nothing\n'
                 'On Error Resume Next\n'
                 'Err.Clear\n'
                 'If Not oSpa Is Nothing Then Set oMeas = oSpa.GetMeasurable(oRef)\n'
                 'If Not oMeas Is Nothing Then oVal = oMeas.Area\n'
                 'Err.Clear\n'
                 'On Error GoTo 0\n'
                 'End If\n'
                 'Out "c" & oI, oVal\n'
                 'Next\n')
    else:
        body += 'Out "faces", 0\n'
    v = _vars(run_vbs(body))
    out = []
    ne = _int_of(v.get('edges'))
    nf = _int_of(v.get('faces'))
    if want_e:
        out.append('实体边 %d 条(1 起编号, 给 catia_fillet / catia_chamfer 用):' % ne)
        for i in range(1, ne + 1):
            out.append('  %d. 长度 %s mm' % (i, v.get('e%d' % i) or '?'))
        if ne == 0:
            out.append('  (没枚举到边)')
    if want_f:
        out.append('实体面 %d 个(1 起编号, 给 catia_shell / catia_draft / catia_thickness 用):' % nf)
        for i in range(1, nf + 1):
            out.append('  %d. 面积 %s mm2' % (i, v.get('c%d' % i) or '?'))
        if nf == 0:
            out.append('  (没枚举到面)')
    if not v.get('_dry') and ((want_e and ne == 0) or (want_f and nf == 0)):
        out.append(UNSUPPORTED)
        out.append('  说明: 实体边/面是靠 Selection.Search("Topology.Edge|Face") 枚举的, '
                   '本机 CATIA 没返回结果, 请在界面里手动选边/面做这些特征。')
    out.append(_note_text(v))
    return _join(out)


# --------------------------------------------------------------------------
# 输出拼装
# --------------------------------------------------------------------------

def _vars(r) -> dict:
    v = dict(r.get('vars') or {})
    if r.get('dry_run'):
        v['_dry'] = '1'
    return v


def _int_of(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _join(parts) -> str:
    return '\n'.join(str(p) for p in parts if p)


def _cut_line(out, v) -> None:
    """把"这一刀到底切掉材料没有"的核对结果写进返回文本(真机实测过: 不核对会静默错模型)。"""
    cut = v.get('cut')
    if cut == '1':
        out.append('  已核对(Measurable.Volume): 这一刀真的切掉了材料')
    elif cut == '2':
        out.append('  方向: 草图在材料另一侧, 已自动把切除方向翻到另一侧并切成功(体积核对通过)')
    elif cut == '0':
        out.append('  注意: 本机量不到体积(SPA 不可用), 没法核对"到底切掉材料没有", 请看一眼模型')
    elif cut is not None:
        out.append('  注意: 切除方向核对没通过(cut=%s), 请检查草图位置' % cut)


def _src_text(v) -> str:
    """边/面是从哪来的: 用户选中的选择集, 还是 CATIA 自己搜出来的。"""
    if v.get('source') == 'selection':
        return ('  来源: 你在 CATIA 窗口里选中的元素(本机 Selection.Search 查不到实体拓扑, '
                '所以先用选择集)')
    if v.get('source') == 'search':
        return '  来源: CATIA Selection.Search 枚举出来的'
    return ''


def _warn_text(v) -> str:
    w = v.get('warn')
    return ('  注意: %s' % w) if w else ''


def _unsup_text(v) -> str:
    parts = [UNSUPPORTED]
    if v.get('feature'):
        parts.append('  接口/特征: %s' % v['feature'])
    if v.get('detail'):
        parts.append('  CATIA 说明: %s' % v['detail'])
    return '\n'.join(parts)


def _note_text(v) -> str:
    return _DRY_NOTE if v.get('_dry') else ''


# --------------------------------------------------------------------------
# 工具表(15 个, 名字与清单逐字一致)
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_pad',
     '创建凸台(Pad): 用实体里已有的草图朝 +Z 长出一个高度; limit="up_to_next" 可长到下一个面。',
     obj({'height': N('凸台高度 mm(朝 +Z 长)'),
          'limit': S("'dim'(默认, 用 height) 或 'up_to_next'(长到下一个面)"),
          'sketch_index': I('用第几个草图(1 起, 见 catia_list_features; 省略=最后一个)')},
         ['height']), t_pad),

    ('catia_pocket',
     '创建凹槽(Pocket): 用实体里最后一个草图朝 -Z 切除 depth 深(草图要放在材料上方)。',
     obj({'depth': N('切除深度 mm(朝 -Z 切)'),
          'up_to_next': B('true=切到下一个面(忽略 depth)')},
         ['depth']), t_pocket),

    ('catia_shaft',
     '旋转体(Shaft): 本机 CATIA 上 AddNewShaft 会让自动化连接断开(真机实测), 所以本工具不调用它, '
     '只返回不支持说明 + 多层圆盘/界面手动的替代做法。',
     obj({'angle': N('旋转角度, 度(默认 360)')}, ['angle']), t_shaft),

    ('catia_groove',
     '旋转槽(Groove): 同 shaft, AddNewGroove 会让自动化连接断开, 本工具返回不支持说明与替代做法。',
     obj({'angle': N('环形切除角度, 度(默认 360)')}, ['angle']), t_groove),

    ('catia_fillet',
     '对实体边倒圆(Fillet)。不写 edge_index/edge_indices 时默认对所有边倒圆(序号见 catia_list_edges)。',
     obj({'radius': N('圆角半径 mm'),
          'edge_index': I('第几条边(1 起)'),
          'edge_indices': A('多条边序号, 如 [1,3,5]', I('边序号')),
          'all_edges': B('true=对所有边倒圆(默认: 没给序号时为 true)')},
         ['radius']), t_fillet),

    ('catia_chamfer',
     '对实体边倒角(Chamfer), 两段等长。不写序号时默认对所有边倒角。',
     obj({'length': N('倒角长度 mm'),
          'edge_index': I('第几条边(1 起)'),
          'edge_indices': A('多条边序号, 如 [1,3,5]', I('边序号')),
          'all_edges': B('true=对所有边倒角(默认: 没给序号时为 true)')},
         ['length']), t_chamfer),

    ('catia_hole',
     '打孔: 用"草图圆 + Pocket"实现(比 AddNewHole 稳), 草图自动放在材料上方往下切。'
     'pos 支持 center/corners/pcd/coord; counterbore 用第二个大圆近似; countersink(锥形)API 不支持, 只切直孔。',
     obj({'diameter': N('孔径 mm'),
          'depth': N('孔深 mm(through=true 时忽略)'),
          'through': B('true=通孔(按包围盒高度自动算深度)'),
          'pos': S("孔位: 'center'(默认) / 'corners'(四角, 需要能测包围盒) / 'pcd'(圆周均布) / 'coord'(用 x,y)"),
          'count': I('孔数(pos=pcd 时的圆周份数, 默认 4; pos=coord 时沿 X 排几个)'),
          'pcd': N('pos=pcd 时的分布圆直径 mm'),
          'margin': N('pos=corners 时距边的距离 mm(默认 10)'),
          'x': N('pos=coord 时的 X 坐标 mm'),
          'y': N('pos=coord 时的 Y 坐标 mm'),
          'type': S("'simple'(默认) / 'counterbore'(沉孔, 用大圆再切一刀近似) / 'countersink'(锥形沉孔, 不支持)"),
          'cb_diameter': N('沉孔直径 mm'),
          'cb_depth': N('沉孔深度 mm(默认 cb_diameter/2)')},
         ['diameter']), t_hole),

    ('catia_rect_pattern',
     '对实体最后一个特征做矩形阵列(方向取自原点平面: PlaneYZ=+X, PlaneZX=+Y)。',
     obj({'count_x': I('X 方向份数(>=1)'),
          'count_y': I('Y 方向份数(>=1)'),
          'spacing_x': N('X 方向间距 mm'),
          'spacing_y': N('Y 方向间距 mm')},
         ['count_x', 'count_y', 'spacing_x', 'spacing_y']), t_rect_pattern),

    ('catia_circ_pattern',
     '对实体最后一个特征做圆形阵列(绕原点 X/Y/Z 轴, 自动建中心点与轴线的参考)。',
     obj({'count': I('总份数(>=2)'),
          'angle': N('总角度, 度(默认 360)'),
          'axis': S("旋转轴: 'Z'(默认) / 'X' / 'Y'")},
         ['count']), t_circ_pattern),

    ('catia_mirror',
     '以原点平面镜像实体最后一个特征(Mirror)。',
     obj({'plane': S("镜像平面: 'YZ'(关于 YZ 平面, 即沿 X 镜像) / 'ZX' / 'XY'")},
         ['plane']), t_mirror),

    ('catia_shell',
     '抽壳(Shell): 按壁厚挖空, 可指定要移除(开口)的面; 不指定时自动取最上面的水平面。',
     obj({'thickness': N('壁厚 mm'),
          'face_index': I('要移除的面序号(1 起, 见 catia_list_edges(what="faces"); 省略=自动)')},
         ['thickness']), t_shell),

    ('catia_draft',
     '拔模(Draft): 把指定面绕中性面(默认原点 PlaneXY)拔一个角度, 拔模方向 +Z; '
     '中性面用 PlaneXY 失败时自动改用最下面的水平面。',
     obj({'angle': N('拔模角度, 度'),
          'face_index': I('要拔模的面序号(1 起, 见 catia_list_edges(what="faces"))')},
         ['angle', 'face_index']), t_draft),

    ('catia_thickness',
     '加厚(Thickness): 对曲面加厚成实体(默认第 1 个面)。',
     obj({'thickness': N('加厚厚度 mm'),
          'face_index': I('要加厚的面序号(1 起; 省略=第 1 个面)')},
         ['thickness']), t_thickness),

    ('catia_list_features',
     '列出实体的特征(序号/名称/类型/主尺寸)和草图(序号/名称), 用于选 sketch_index、'
     '确认阵列与镜像作用在哪个特征上。',
     obj({}), t_list_features),

    ('catia_list_edges',
     '列出实体的边(长度)与/或面(面积), 供 catia_fillet / catia_chamfer / catia_shell / '
     'catia_draft / catia_thickness 选序号。',
     obj({'what': S("'edges'(默认) / 'faces' / 'both'")}), t_list_edges),
]
