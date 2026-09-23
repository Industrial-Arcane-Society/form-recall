# -*- coding: utf-8 -*-
r"""装配工具(9 个) —— 对应参考项目 catia-v5-mcp-server 的 assembly 模块。

对象模型要点(照着 CATIA 的真实接口写):
    · 装配文档 = CATProduct, 根对象是 Product; 子零件在 product.Products 里
    · 插入已有文件: Documents.Open + Products.AddExternalComponent(文档)
    · 新建零件组件: Products.AddNewComponent("Part", 名字)(失败时退回"新建文档 + 插入")
    · 约束: product.Constraints.AddMonoEltCst(类型, 参考) / AddBiEltCst(类型, 参考1, 参考2)
      类型值(VBScript 里没有 CATIA 常量, 只能写字面量): 0=Fix 固定, 1=Distance 偏移,
      2=On 重合, 6=Angle 角度, 8=Parallelism 平行, 11=Perpendicularity 垂直, 20=SurfContact
    · 移动: product.Move.Apply(12 个数的数组) —— 前 9 个数是三个坐标轴(x/y/z 轴各 3 个分量),
      最后 3 个是平移量(与 Position.GetComponents 同一套排列, 也叫列主序)

两个 VBScript 坑(真机踩过, 这里都规避了):
    · 名字不能撞 VBScript 关键字: 变量只能叫 oNum / oRT 这类(oN==On, oR==Or,
      Dim 下去 cscript 直接语法错误, 脚本既不回 AIM:ERROR 也不回 AIM:DONE)
    · 装配文档里没有 Part: 脚本绝不能调 Part()/Body()(bridge 的 NeedPart 会直接 Fail);
      要零件就顺着 component.ReferenceProduct -> Parent -> Part 找

元素怎么指(约束工具的参数是字符串):
    "组件名/XY"     组件自己的原点平面(YZ / ZX 也行)
    "组件名/发布名" 组件里"发布(Publication)"出来的几何
    "组件名"        只写组件名 = 用它的 XY 原点平面(省事写法)
    "2/XY"          用 catia_list_components 里的序号代替名字
"""
from __future__ import annotations

import math
import os

from ..bridge import run_vbs
from ._schema import A, I, N, S, arg_f, arg_i, arg_s, obj, vbs, vbs_num

NAME = 'assembly'

UNSUPPORTED = '该 CATIA 版本不支持通过 API 建此特征, 请在界面里手动做。'

_DRY_NOTE = ('  (干跑模式: 只校验参数并生成脚本, 没有真的连 CATIA —— '
             'AI_MODELER_MCP_DRYRUN=1)')

# 约束类型字面量(VBScript 里没有 CATIA 常量)
CST_FIX = 0        # catCstTypeReference
CST_DISTANCE = 1   # catCstTypeDistance   -> 偏移约束
CST_ON = 2         # catCstTypeOn         -> 重合约束
CST_ANGLE = 6      # catCstTypeAngle

_CST_CN = {0: '固定(Fix)', 1: '偏移(Offset)', 2: '重合(Coincidence)', 3: '同轴',
           4: '相切', 6: '角度(Angle)', 7: '平面角度', 8: '平行', 9: '轴向平行',
           11: '垂直', 12: '轴向垂直', 20: '面接触', 21: '线接触', 22: '点接触'}


# --------------------------------------------------------------------------
# 脚本公共片段
# --------------------------------------------------------------------------

# 变量名全部避开 VBScript 关键字(oN==On / oR==Or)与 bridge 预置全局/函数名。
_DIM = (
    'Dim oErr1, oErrN, oErrTxt, oI, oJ, oNum, oDoc, oDoc2, oD2, oRoot, oComp\n'
    'Dim oComp2, oPart2, oRef, oRef2, oCst, oCol, oSel, oKids, oKey, oKey2\n'
    'Dim oElem, oElem2, oName, oTp, oVal, oVal2, oPrefix, oDepth, oMode, oS, oV2\n'
    'Dim oAxis, oMtx(11), gWalk, gPIdx\n'
)


def _prod_root(tool: str, key: str = '') -> str:
    """定位要操作的装配(根 Product)。

    真机实测: AddNewComponent/新建零件文档之后, CATIA 会把新零件文档切成活动文档
    (而且是在我们的 Activate 之后才切), 所以不能死认"活动文档必须是装配"。这里的顺序:
      1. 活动文档就是 CATProduct -> 用它;
      2. 否则在打开的文档里挑: 先找**装得下 <key> 这个组件**的产品文档,
         再退到"文档列表里最后一个产品文档";
      3. 都没有才报错(并把当前活动文档名字带上, 方便排查)。
    """
    tmpl = (
        'Set oDoc = Act()\n'
        'Set oRoot = Nothing\n'
        'Set oPart2 = Nothing\n'
        'oMode = ""\n'
        'oName = ""\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'If Not oDoc Is Nothing Then\n'
        'oName = oDoc.Name\n'
        'Set oPart2 = oDoc.Part\n'
        'If Err.Number = 0 And Not oPart2 Is Nothing Then oMode = "part"\n'
        'Err.Clear\n'
        'End If\n'
        'On Error GoTo 0\n'
        'If oMode <> "part" Then\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oRoot = oDoc.Product\n'
        'If Err.Number <> 0 Then Set oRoot = Nothing\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If Not oRoot Is Nothing Then Out "doc_source", "active"\n'
        'End If\n'
        'If oRoot Is Nothing Then\n'
        'Set oRoot = AimPickRoot(%s)\n'
        'If Not oRoot Is Nothing Then Out "doc_source", "scanned"\n'
        'End If\n'
        'If oRoot Is Nothing Then Fail "%s: no CATProduct to work on - open/activate the '
        'assembly first (active document: " & oName & ")"\n'
        "' keep the assembly document at hand (Selection / Activate need it)\n"
        'Set oDoc2 = oRoot.Parent\n'
        'If Not oDoc2 Is Nothing Then Set oDoc = oDoc2\n'
        'Out "product", oRoot.Name\n'
        'Out "doc", oDoc.Name\n'
    )
    return tmpl % (vbs(key) if key else '""', tool)


# 产品树遍历: 名字找/序号找/列出/挑装配文档。
# 序号按"先根(1)、再深度优先"记, 与 catia_list_components 打出来的序号完全一致。
_PROD_DEFS = (
    'Function AimByName(oP, oKey)\n'
    'Dim oKids, i, oC, oFound, oS\n'
    'Set AimByName = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'oS = ""\n'
    'oS = oP.Name\n'
    'If Err.Number = 0 Then\n'
    'If LCase(oS) = LCase(oKey) Then\n'
    'Set AimByName = oP\n'
    'Err.Clear\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'oS = ""\n'
    'oS = oP.PartNumber\n'
    'If Err.Number = 0 Then\n'
    'If LCase(oS) = LCase(oKey) Then\n'
    'Set AimByName = oP\n'
    'Err.Clear\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Set oKids = oP.Products\n'
    'If Err.Number <> 0 Or oKids Is Nothing Then Exit Function\n'
    'Err.Clear\n'
    'For i = 1 To oKids.Count\n'
    'Set oC = Nothing\n'
    'Err.Clear\n'
    'Set oC = oKids.Item(i)\n'
    'If Err.Number = 0 And Not oC Is Nothing Then\n'
    'Set oFound = AimByName(oC, oKey)\n'
    'If Not oFound Is Nothing Then\n'
    'Set AimByName = oFound\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    'End Function\n'
    '\n'
    'Function AimWalk(oP, oKey)\n'
    'Dim oKids, i, oC, oFound\n'
    'Set AimWalk = Nothing\n'
    'gWalk = gWalk + 1\n'
    'If gWalk = oKey Then\n'
    'Set AimWalk = oP\n'
    'Exit Function\n'
    'End If\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oKids = oP.Products\n'
    'If Err.Number <> 0 Or oKids Is Nothing Then Exit Function\n'
    'Err.Clear\n'
    'For i = 1 To oKids.Count\n'
    'Set oC = Nothing\n'
    'Err.Clear\n'
    'Set oC = oKids.Item(i)\n'
    'If Err.Number = 0 And Not oC Is Nothing Then\n'
    'Set oFound = AimWalk(oC, oKey)\n'
    'If Not oFound Is Nothing Then\n'
    'Set AimWalk = oFound\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    'End Function\n'
    '\n'
    'Function AimFindProd(oRoot, oKey)\n'
    'Set AimFindProd = Nothing\n'
    'If Len(oKey) = 0 Then Exit Function\n'
    'If IsNumeric(oKey) Then\n'
    'gWalk = 0\n'
    'Set AimFindProd = AimWalk(oRoot, CLng(oKey))\n'
    'Exit Function\n'
    'End If\n'
    'Set AimFindProd = AimByName(oRoot, oKey)\n'
    'End Function\n'
    '\n'
    'Function AimPickRoot(oKey)\n'
    "' pick the assembly to use when the active document is not a CATProduct\n"
    'Dim i, d, p, oPP, oHit, oLast, n\n'
    'Set AimPickRoot = Nothing\n'
    'Set oLast = Nothing\n'
    'n = 0\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'For i = 1 To gApp.Documents.Count\n'
    'Set d = Nothing\n'
    'Set p = Nothing\n'
    'Set oPP = Nothing\n'
    'Err.Clear\n'
    'Set d = gApp.Documents.Item(i)\n'
    'If Err.Number = 0 And Not d Is Nothing Then\n'
    'Set oPP = d.Part\n'
    'If Err.Number <> 0 Or oPP Is Nothing Then\n'
    'Err.Clear\n'
    'Set p = d.Product\n'
    'If Err.Number = 0 And Not p Is Nothing Then\n'
    'n = n + 1\n'
    'Set oLast = p\n'
    'If Len(oKey) > 0 Then\n'
    'Set oHit = Nothing\n'
    'Set oHit = AimFindProd(p, oKey)\n'
    'If Not oHit Is Nothing Then\n'
    'Set AimPickRoot = p\n'
    'Out "doc", d.Name\n'
    'Err.Clear\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    "' nothing matched by component -> fall back to the last product document in the list\n"
    'If n = 1 Then\n'
    'Set AimPickRoot = oLast\n'
    'ElseIf n > 1 And Len(oKey) = 0 Then\n'
    'Set AimPickRoot = oLast\n'
    'End If\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'End Function\n'
    '\n'
    'Sub AimWalkOut(oP, oPrefix, oDepth)\n'
    'Dim oKids, i, oC, oS, oT\n'
    'gWalk = gWalk + 1\n'
    'oS = ""\n'
    'oT = ""\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'oS = oP.Name\n'
    'oT = oP.PartNumber\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    "Out \"p\" & gWalk, gWalk & Chr(9) & oDepth & Chr(9) & oPrefix & oS & Chr(9) & oT\n"
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oKids = oP.Products\n'
    'If Err.Number <> 0 Or oKids Is Nothing Then\n'
    'Err.Clear\n'
    'Exit Sub\n'
    'End If\n'
    'Err.Clear\n'
    'For i = 1 To oKids.Count\n'
    'Set oC = Nothing\n'
    'Err.Clear\n'
    'Set oC = oKids.Item(i)\n'
    'If Err.Number = 0 And Not oC Is Nothing Then AimWalkOut oC, oPrefix & oS & "/", oDepth + 1\n'
    'Err.Clear\n'
    'Next\n'
    'End Sub\n'
    '\n'
    'Function AimPartOf(oP)\n'
    'Dim oPP, oDD, i, oT, oS\n'
    'Set AimPartOf = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oPP = oP.ReferenceProduct\n'
    'If Err.Number <> 0 Or oPP Is Nothing Then Set oPP = oP\n'
    'Err.Clear\n'
    'Set oDD = oPP.Parent\n'
    'If Err.Number = 0 And Not oDD Is Nothing Then\n'
    'Err.Clear\n'
    'Set oT = oDD.Part\n'
    'If Err.Number = 0 And Not oT Is Nothing Then\n'
    'Set AimPartOf = oT\n'
    'Err.Clear\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'For i = 1 To gApp.Documents.Count\n'
    'Set oDD = Nothing\n'
    'Err.Clear\n'
    'Set oDD = gApp.Documents.Item(i)\n'
    'If Err.Number = 0 And Not oDD Is Nothing Then\n'
    'Set oT = Nothing\n'
    'Err.Clear\n'
    'Set oT = oDD.Product\n'
    'If Err.Number = 0 And Not oT Is Nothing Then\n'
    'If oT Is oPP Then\n'
    'Set oS = Nothing\n'
    'Err.Clear\n'
    'Set oS = oDD.Part\n'
    'If Err.Number = 0 And Not oS Is Nothing Then\n'
    'Set AimPartOf = oS\n'
    'Err.Clear\n'
    'Exit Function\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    'End Function\n'
    '\n'
    'Function AimElemRef(oP, oElem)\n'
    'Dim oPP, oPub, oV3\n'
    'Set AimElemRef = Nothing\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oPP = AimPartOf(oP)\n'
    'Err.Clear\n'
    'If oPP Is Nothing Then Exit Function\n'
    'If oElem = "XY" Then\n'
    'Set AimElemRef = oPP.CreateReferenceFromObject(oPP.OriginElements.PlaneXY)\n'
    'ElseIf oElem = "YZ" Then\n'
    'Set AimElemRef = oPP.CreateReferenceFromObject(oPP.OriginElements.PlaneYZ)\n'
    'ElseIf oElem = "ZX" Or oElem = "XZ" Then\n'
    'Set AimElemRef = oPP.CreateReferenceFromObject(oPP.OriginElements.PlaneZX)\n'
    'Else\n'
    'Set oPub = Nothing\n'
    'Err.Clear\n'
    'Set oPub = oP.Publications.Item(oElem)\n'
    'If Err.Number = 0 And Not oPub Is Nothing Then\n'
    'Set oV3 = Nothing\n'
    'Err.Clear\n'
    'Set oV3 = oPub.Valuation\n'
    'If Err.Number = 0 And Not oV3 Is Nothing Then\n'
    'Err.Clear\n'
    'Set AimElemRef = oPP.CreateReferenceFromObject(oV3)\n'
    'End If\n'
    'End If\n'
    'End If\n'
    'Err.Clear\n'
    'End Function\n'
)


# 约束不一定都挂在根上, 子装配有自己的 Constraints 集合 -> 递归走一遍整棵产品树。
# 本机 Product.Constraints 取不到(438), 所以退回 Relations 里筛 Constraint 对象。
_CST_TREE_SUB = (
    'Sub AimCstTree(oP, oPrefix)\n'
    'Dim oKids, i, oC, oCol, oRel, oCst, oName, oT, oV, oV2, oS, n\n'
    'On Error Resume Next\n'
    'Err.Clear\n'
    'Set oRel = Nothing\n'
    'Set oCol = oP.Constraints\n'
    'If Err.Number <> 0 Then Set oCol = Nothing\n'
    'Err.Clear\n'
    'If oCol Is Nothing Then\n'
    "' this build has no Product.Constraints -> fall back to the Relations collection\n"
    'Out "via", "relations"\n'
    'Err.Clear\n'
    'Set oRel = oP.Relations\n'
    'If Err.Number = 0 And Not oRel Is Nothing Then\n'
    'n = 0\n'
    'Err.Clear\n'
    'n = oRel.Count\n'
    'Err.Clear\n'
    'For i = 1 To n\n'
    'Set oCst = Nothing\n'
    'oName = "?"\n'
    'oT = -1\n'
    'oV = ""\n'
    'Err.Clear\n'
    'Set oCst = oRel.Item(i)\n'
    'If Err.Number = 0 And Not oCst Is Nothing Then\n'
    'Err.Clear\n'
    'oName = oCst.Name\n'
    'Err.Clear\n'
    'oT = oCst.Type\n'
    'Err.Clear\n'
    'oV2 = ""\n'
    'oV2 = oCst.Dimension.Value\n'
    'If Err.Number = 0 Then oV = oV2\n'
    'Err.Clear\n'
    'End If\n'
    'gPIdx = gPIdx + 1\n'
    'Out "c" & gPIdx, oPrefix & oName & Chr(9) & oT & Chr(9) & oV\n'
    'Next\n'
    'End If\n'
    'Err.Clear\n'
    'End If\n'
    'If oCol Is Nothing And oRel Is Nothing Then\n'
    'Out "unsupported", "1"\n'
    'End If\n'
    'If Not oCol Is Nothing Then\n'
    'n = 0\n'
    'Err.Clear\n'
    'n = oCol.Count\n'
    'Err.Clear\n'
    'For i = 1 To n\n'
    'Set oCst = Nothing\n'
    'oName = "?"\n'
    'oT = -1\n'
    'oV = ""\n'
    'Err.Clear\n'
    'Set oCst = oCol.Item(i)\n'
    'If Err.Number = 0 And Not oCst Is Nothing Then\n'
    'Err.Clear\n'
    'oName = oCst.Name\n'
    'Err.Clear\n'
    'oT = oCst.Type\n'
    'Err.Clear\n'
    'oV2 = ""\n'
    'oV2 = oCst.Dimension.Value\n'
    'If Err.Number = 0 Then oV = oV2\n'
    'Err.Clear\n'
    'End If\n'
    'gPIdx = gPIdx + 1\n'
    'Out "c" & gPIdx, oPrefix & oName & Chr(9) & oT & Chr(9) & oV\n'
    'Next\n'
    'End If\n'
    'Err.Clear\n'
    'Set oKids = oP.Products\n'
    'If Err.Number <> 0 Or oKids Is Nothing Then\n'
    'Err.Clear\n'
    'On Error GoTo 0\n'
    'Exit Sub\n'
    'End If\n'
    'Err.Clear\n'
    'For i = 1 To oKids.Count\n'
    'Set oC = Nothing\n'
    'Err.Clear\n'
    'Set oC = oKids.Item(i)\n'
    'If Err.Number = 0 And Not oC Is Nothing Then\n'
    'oS = ""\n'
    'oS = oC.Name\n'
    'AimCstTree oC, oPrefix & oS & "/"\n'
    'End If\n'
    'Err.Clear\n'
    'Next\n'
    'On Error GoTo 0\n'
    'End Sub\n'
)


def _resolve_comp(tool: str, key: str, var: str = 'oComp') -> str:
    """按名字或序号在装配树里找组件, 找不到就报错。"""
    head = (
        'Set %s = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set %s = AimFindProd(oRoot, %s)\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
    ) % (var, var, vbs(key))
    tail = (
        'If %s Is Nothing Then Fail "%s: no component matches %s - see catia_list_components"\n'
    ) % (var, tool, key.replace('"', ' '))
    return head + tail


def _create_cst(tool: str, call: str) -> str:
    """约束创建 + 先探一下本机到底有没有 Product.Constraints。

    真机实测(中文版 V5-6R2023): **Product 上没有 Constraints 集合**(读它是 438
    "对象不支持此属性或方法"), 所以装配约束在这台机器上没法用 API 建 —— 这种情况
    明确回"不支持, 请在界面里手动做", 不要抛一个看不懂的 438 出来。
    """
    head = (
        'Set oCol = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oCol = oRoot.Constraints\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oCol Is Nothing Then\n'
        'Out "unsupported", "1"\n'
        'Out "feature", "Product.Constraints"\n'
        'Out "detail", "this CATIA build exposes no Product.Constraints collection (reading it '
        'fails with 438), so assembly constraints cannot be created through the API - add the '
        'constraint by hand in the Assembly Design workbench"\n'
        'Out "constraint", ""\n'
        'Else\n'
        'Set oCst = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oCst = %s\n'
    ) % call
    tail = (
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oErrN <> 0 Or oCst Is Nothing Then Fail "%s: " & oErrTxt\n'
        'Out "constraint", oCst.Name\n'
        'End If\n'
    ) % tool
    return head + tail


def _set_dim(tool: str, value_expr: str) -> str:
    """偏移/角度约束建完要写数值; 写不上就大声报出来, 不假装成功。"""
    head = (
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oCst.Dimension.Value = %s\n'
    ) % value_expr
    tail = (
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'If oErrN <> 0 Then\n'
        'Out "warn", "%s: the constraint was created but its driving value could not be set '
        '(it keeps the default): " & oErrTxt\n'
        'Else\n'
        'Out "value", oCst.Dimension.Value\n'
        'End If\n'
    ) % tool
    return head + tail


def _split_el(tool: str, s: str, need_geom: bool = True):
    """把 "组件/元素" 拆成 (组件, 元素)。只写组件名 = 用它的 XY 平面。"""
    txt = (s or '').strip()
    if not txt:
        raise ValueError('%s: 元素不能为空, 写法是 "组件名/XY"(或 "组件名/发布名")' % tool)
    comp, elem = txt, ''
    for sep in ('/', ':'):
        if sep in txt:
            comp, elem = txt.split(sep, 1)
            comp, elem = comp.strip(), elem.strip()
            break
    if not comp:
        raise ValueError('%s: 组件名不能为空: %r' % (tool, s))
    if not elem:
        if not need_geom:
            return comp, ''
        elem = 'XY'          # 只写组件名 -> 按它的 XY 原点平面处理(最常用)
    if elem.upper() in ('XY', 'YZ', 'ZX', 'XZ'):
        elem = elem.upper()
    return comp, elem


def _elem_ref(tool: str, comp: str, elem: str, var: str = 'oRef') -> str:
    """解析一个元素字符串, 拿不到参考就明确报错(附带怎么写)。"""
    head = _resolve_comp(tool, comp, 'oComp')
    mid = (
        'Set %s = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set %s = AimElemRef(oComp, %s)\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
    ) % (var, var, vbs(elem))
    tail = (
        'If %s Is Nothing Then Fail "%s: cannot build a reference for element %s - use '
        'component/XY|YZ|ZX or a publication name"\n'
    ) % (var, tool, (comp + '/' + elem).replace('"', ' '))
    return head + mid + tail


# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

def t_add_component(args):
    path = (arg_s(args, 'path', '') or '').strip()
    nm = (arg_s(args, 'name', '') or '').strip()
    if not path and not nm:
        raise ValueError('至少给一个: path(已有的 .CATPart/.CATProduct 完整路径) '
                         '或 name(新建空零件的名字)')
    if path:
        if not os.path.isfile(path):
            raise ValueError('文件不存在: %s' % path)
        tmpl = (
            "' reuse the document if the same file is already open in CATIA\n"
            'Set oDoc2 = Nothing\n'
            'On Error Resume Next\n'
            'Err.Clear\n'
            'For oI = 1 To gApp.Documents.Count\n'
            'Set oD2 = Nothing\n'
            'Err.Clear\n'
            'Set oD2 = gApp.Documents.Item(oI)\n'
            'If Err.Number = 0 And Not oD2 Is Nothing Then\n'
            'oVal = ""\n'
            'Err.Clear\n'
            'oVal = oD2.FullName\n'
            'If Err.Number = 0 And Len(oVal) > 0 Then\n'
            'If LCase(oVal) = LCase(%s) Then Set oDoc2 = oD2\n'
            'End If\n'
            'End If\n'
            'Err.Clear\n'
            'Next\n'
            'If oDoc2 Is Nothing Then\n'
            'Err.Clear\n'
            'Set oDoc2 = gApp.Documents.Open(%s)\n'
            'If Err.Number <> 0 Or oDoc2 Is Nothing Then\n'
            'oErrTxt = Err.Description\n'
            'Err.Clear\n'
            'On Error GoTo 0\n'
            'Fail "catia_add_component: cannot open the file: " & oErrTxt\n'
            'End If\n'
            'Else\n'
            'Out "reused", "1"\n'
            'End If\n'
            'Err.Clear\n'
            'Set oComp = Nothing\n'
            'Set oComp = oRoot.Products.AddExternalComponent(oDoc2)\n'
            'oErrN = Err.Number\n'
            'oErrTxt = Err.Description\n'
            'Err.Clear\n'
            'On Error GoTo 0\n'
            'If oErrN <> 0 Or oComp Is Nothing Then Fail "catia_add_component: " & oErrTxt\n'
            'Out "component", oComp.Name\n'
            'oName = ""\n'
            'On Error Resume Next\n'
            'Err.Clear\n'
            'oName = oComp.PartNumber\n'
            'Err.Clear\n'
            'On Error GoTo 0\n'
            'Out "part_number", oName\n'
            'On Error Resume Next\n'
            'Err.Clear\n'
            'oDoc.Activate\n'
            'Err.Clear\n'
            'On Error GoTo 0\n'
        ) % (vbs(path), vbs(path))
        body = _DIM + _PROD_DEFS + _prod_root('catia_add_component') + tmpl
        v = _vars(run_vbs(body))
        out = ['已把 %s 作为组件加入装配 -> 组件 %s' % (os.path.basename(path),
                                             v.get('component', '?'))]
        if v.get('part_number'):
            out.append('  零件号: %s' % v['part_number'])
        if v.get('reused') == '1':
            out.append('  该文件本来就在 CATIA 里开着, 直接复用了打开的文档')
        out.append(_warn_text(v))
        out.append(_note_text(v))
        return _join(out)

    tmpl = (
        'Set oComp = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oComp = oRoot.Products.AddNewComponent("Part", %s)\n'
        'oErrN = Err.Number\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Out "mode", "AddNewComponent"\n'
        'If oErrN <> 0 Or oComp Is Nothing Then\n'
        "' AddNewComponent is not available on every build -> new CATPart + insert it\n"
        'Set oD2 = Nothing\n'
        'Set oComp = Nothing\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Set oD2 = gApp.Documents.Add("Part")\n'
        'If Err.Number = 0 And Not oD2 Is Nothing Then\n'
        'Err.Clear\n'
        'oD2.Name = %s\n'
        'Err.Clear\n'
        'Set oComp = oRoot.Products.AddExternalComponent(oD2)\n'
        'End If\n'
        'If Err.Number <> 0 Or oComp Is Nothing Then\n'
        'oErrTxt = Err.Description\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Fail "catia_add_component: cannot create a new part component: " & oErrTxt\n'
        'End If\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Out "mode", "new document + AddExternalComponent"\n'
        'End If\n'
        'Out "component", oComp.Name\n'
        'oName = ""\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'oName = oComp.PartNumber\n'
        'Err.Clear\n'
        'oDoc.Activate\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Out "part_number", oName\n'
    ) % (vbs(nm), vbs(nm))
    body = _DIM + _PROD_DEFS + _prod_root('catia_add_component', nm) + tmpl
    v = _vars(run_vbs(body))
    out = ['已加入新零件组件: %s' % v.get('component', nm)]
    out.append(_doc_line(v))
    out.append('  方式: %s' % v.get('mode', '?'))
    if v.get('part_number'):
        out.append('  零件号: %s' % v['part_number'])
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_add_new_part(args):
    nm = (arg_s(args, 'name', '') or '').strip()
    if not nm:
        raise ValueError('name(新零件名)必填')
    return t_add_component({'name': nm})


def t_fix_constraint(args):
    comp = arg_s(args, 'component', '')
    if not comp:
        raise ValueError('component(组件名或序号)必填')
    body = (_DIM + _PROD_DEFS + _prod_root('catia_fix_constraint', comp)
            + _resolve_comp('catia_fix_constraint', comp)
            + "' a Fix constraint wants a reference to the component itself: get it from the\n"
            + "' selection (Selection.Item(1).Reference), fall back to its XY origin plane\n"
            + 'Set oSel = oDoc.Selection\n'
            + 'Set oRef = Nothing\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oSel.Clear\n'
            + 'oSel.Add oComp\n'
            + 'If oSel.Count >= 1 Then Set oRef = oSel.Item(1).Reference\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'If oRef Is Nothing Then\n'
            + 'Set oPart2 = AimPartOf(oComp)\n'
            + 'If Not oPart2 Is Nothing Then\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'Set oRef = oPart2.CreateReferenceFromObject(oPart2.OriginElements.PlaneXY)\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'End If\n'
            + 'End If\n'
            + 'If oRef Is Nothing Then Fail "catia_fix_constraint: cannot build a reference to '
            'the component - fix it in the UI, or publish the component reference"\n'
            + _create_cst('catia_fix_constraint',
                          'oRoot.Constraints.AddMonoEltCst(%d, oRef)' % CST_FIX)
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oSel.Clear\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Out "component", oComp.Name\n')
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1':
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已给组件 %s 加固定约束(Fix) -> 约束 %s' % (v.get('component', comp),
                                            v.get('constraint', '?'))]
    out.append(_doc_line(v))
    out.append('  说明: 固定约束优先拿"组件本身"的参考(Selection.Item(1).Reference), '
               '取不到时退回组件源零件的 XY 原点平面。')
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def _bi_constraint(args, tool: str, cst_type: int, value=None, label=''):
    e1 = arg_s(args, 'element1', '')
    e2 = arg_s(args, 'element2', '')
    c1, m1 = _split_el(tool, e1)
    c2, m2 = _split_el(tool, e2)
    body = (_DIM + _PROD_DEFS + _prod_root(tool, c1)
            + _elem_ref(tool, c1, m1, 'oRef')
            + _elem_ref(tool, c2, m2, 'oRef2')
            + _create_cst(tool, 'oRoot.Constraints.AddBiEltCst(%d, oRef, oRef2)' % cst_type))
    if value is not None:
        body += _set_dim(tool, vbs_num(value))
    body += ('Out "type", %d\n' % cst_type)
    v = _vars(run_vbs(body))
    if v.get('unsupported') == '1':
        return _join([_unsup_text(v), _note_text(v)])
    out = ['已创建%s约束 -> %s' % (label, v.get('constraint', '?'))]
    out.append(_doc_line(v))
    out.append('  元素: %s  <->  %s' % (c1 + '/' + m1, c2 + '/' + m2))
    if value is not None:
        out.append('  数值: %s%s' % (value, ' 度(写进 CATIA 时换算成弧度)'
                                 if cst_type == CST_ANGLE else ' mm'))
    out.append('  提示: 元素参考是从"组件源零件的原点平面/发布(Publication)"建的; '
               '同一个零件在装配里被插了多次时, CATIA 可能认到第一个实例上。')
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_coincidence_constraint(args):
    return _bi_constraint(args, 'catia_coincidence_constraint', CST_ON, None, '重合(Coincidence)')


def t_offset_constraint(args):
    if args.get('offset') is None:
        raise ValueError('offset(偏移距离 mm)必填(0 也算合法值)')
    off = arg_f(args, 'offset', 0.0)
    return _bi_constraint(args, 'catia_offset_constraint', CST_DISTANCE, off, '偏移(Offset)')


def t_angle_constraint(args):
    ang = arg_f(args, 'angle', 0.0)
    if ang == 0:
        raise ValueError('angle(角度, 度)不能是 0')
    return _bi_constraint(args, 'catia_angle_constraint', CST_ANGLE,
                          ang * math.pi / 180.0, '角度(Angle)')


def _move_matrix(trans, axis, ang_deg):
    """12 个数: 前 9 个是 x/y/z 三个轴的坐标分量, 后 3 个是平移量。

    排列与 CAA 文档一致: Move.Apply 取"变换矩阵的四列", 前 9 个数依次是
    x 轴、y 轴、z 轴(每个 3 个分量), 最后 3 个是平移量 —— 也就是列主序。
    文档里绕 X 轴 45 度的例子正好是 [1,0,0, 0,.707,.707, 0,-.707,.707], 与这里的算法一致。
    """
    m = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    if ang_deg:
        a = math.radians(ang_deg)
        c, s = math.cos(a), math.sin(a)
        if axis == 'X':
            m = [1, 0, 0, 0, c, s, 0, -s, c]
        elif axis == 'Y':
            m = [c, 0, -s, 0, 1, 0, s, 0, c]
        elif axis == 'Z':
            m = [c, s, 0, -s, c, 0, 0, 0, 1]
        else:
            x, y, z = axis
            n = math.sqrt(x * x + y * y + z * z) or 1.0
            x, y, z = x / n, y / n, z / n
            k = 1 - c
            # 罗德里格斯公式, 同样按"三个轴的坐标分量"排(列主序)
            m = [c + x * x * k, x * y * k + z * s, x * z * k - y * s,
                 y * x * k - z * s, c + y * y * k, y * z * k + x * s,
                 z * x * k + y * s, z * y * k - x * s, c + z * z * k]
    tx, ty, tz = trans
    return [round(v, 10) for v in m + [tx, ty, tz]]


def t_move_component(args):
    comp = arg_s(args, 'component', '')
    if not comp:
        raise ValueError('component(组件名或序号)必填')
    trans = args.get('translation')
    if trans is not None:
        if not isinstance(trans, (list, tuple)) or len(trans) != 3:
            raise ValueError('translation 必须是 3 个数: [dx, dy, dz]')
        trans = [float(x) for x in trans]
    axis_raw = (arg_s(args, 'rotation_axis', '') or '').strip()
    ang = arg_f(args, 'rotation_angle', 0.0)
    axis = None
    if axis_raw:
        up = axis_raw.upper()
        if up in ('X', 'Y', 'Z'):
            axis = up
        else:
            try:
                vec = [float(x) for x in axis_raw.replace(';', ',').split(',')]
                if len(vec) != 3:
                    raise ValueError
                axis = vec
            except (TypeError, ValueError):
                raise ValueError("rotation_axis 只能是 'X'/'Y'/'Z', 或 '1,0,0' 这样的三分量")
    if ang and not axis:
        raise ValueError('给了 rotation_angle 就必须给 rotation_axis')
    if axis and not ang:
        raise ValueError('给了 rotation_axis 就必须给 rotation_angle')
    if not trans and not ang:
        raise ValueError('要么给 translation=[dx,dy,dz], 要么给 rotation_axis + rotation_angle')

    mtx = _move_matrix(trans or [0.0, 0.0, 0.0], axis, ang)
    lines = ''.join('oMtx(%d) = %s\n' % (i, vbs_num(x)) for i, x in enumerate(mtx))
    body = (_DIM + _PROD_DEFS + _prod_root('catia_move_component', comp)
            + _resolve_comp('catia_move_component', comp)
            + lines
            + "' Move.Apply takes the 12 numbers; older builds only know Move.Move -> try both\n"
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oComp.Move.Apply oMtx\n'
            + 'oErrN = Err.Number\n'
            + 'If oErrN <> 0 Then\n'
            + 'Err.Clear\n'
            + 'oComp.Move.Move oMtx\n'
            + 'oErrN = Err.Number\n'
            + 'End If\n'
            + 'oErrTxt = Err.Description\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'If oErrN <> 0 Then Fail "catia_move_component: " & oErrTxt\n'
            + 'On Error Resume Next\n'
            + 'Err.Clear\n'
            + 'oRoot.Update\n'
            + 'Err.Clear\n'
            + 'On Error GoTo 0\n'
            + 'Out "component", oComp.Name\n')
    v = _vars(run_vbs(body))
    out = ['已移动组件 %s' % v.get('component', comp)]
    out.append(_doc_line(v))
    if trans:
        out.append('  平移: dx=%s dy=%s dz=%s mm' % tuple(trans))
    if ang:
        out.append('  旋转: 绕 %s 轴 %s 度' % (
            axis if isinstance(axis, str) else '(%s)' % ','.join(str(x) for x in axis), ang))
    out.append('  说明: 变换用组件位移矩阵(Move.Apply, 12 个数)。已固定/受约束的组件可能拒绝移动, '
               '那种情况 CATIA 会报错并带回这里。')
    out.append(_warn_text(v))
    out.append(_note_text(v))
    return _join(out)


def t_list_components(args):
    body = (_DIM + _PROD_DEFS + _prod_root('catia_list_components')
            + 'gWalk = 0\n'
            + 'AimWalkOut oRoot, "", 0\n'
            + 'Out "count", gWalk\n')
    v = _vars(run_vbs(body))
    n = _int_of(v.get('count'))
    out = ['装配 %s 里的组件(深度优先, 序号给 component 用):' % v.get('product', '?')]
    out.append(_doc_line(v))
    for i in range(1, n + 1):
        cells = (v.get('p%d' % i) or '?').split('\t')
        if len(cells) >= 4:
            idx, depth, name, pn = cells[0], cells[1], cells[2], cells[3]
            tag = '  [根]' if depth == '0' else ''
            out.append('  %s. %s%s%s' % (idx, '  ' * _int_of(depth), name, tag))
            if pn:
                out.append('       零件号: %s' % pn)
        else:
            out.append('  %d. %s' % (i, v.get('p%d' % i)))
    if n == 0:
        out.append('  (装配里还没有组件: 用 catia_add_component / catia_add_new_part 加)')
    out.append(_note_text(v))
    return _join(out)


def t_list_constraints(args):
    body = (_DIM + _CST_TREE_SUB + _PROD_DEFS + _prod_root('catia_list_constraints')
            + 'gPIdx = 0\n'
            + 'AimCstTree oRoot, ""\n'
            + 'Out "count", gPIdx\n')
    v = _vars(run_vbs(body))
    n = _int_of(v.get('count'))
    if v.get('unsupported') == '1' and n == 0:
        return _join([UNSUPPORTED,
                      '  接口/特征: Product.Constraints / Product.Relations',
                      '  说明: 这台 CATIA 的 Product 上没有 Constraints 集合(读它报 438), '
                      'Relations 也取不到, 所以约束列不出来 —— 请在界面里看装配的约束, '
                      '或先把约束手动建好。',
                      _note_text(v)])
    out = ['装配 %s 的约束 %d 个:' % (v.get('product', '?'), n)]
    if v.get('via') == 'relations':
        out.append('  注意: 这台 CATIA 的 Product 上没有 Constraints 集合(读它报 438), '
                   '这里退到 Relations 里数了一遍; 界面里建的约束也应该会以 Constraint '
                   '关系出现, 如果这里数出来是 0 而界面里明明有, 那就是 Relations 也取不到。')
    out.append(_doc_line(v))
    for i in range(1, n + 1):
        cells = (v.get('c%d' % i) or '?').split('\t')
        name = cells[0] if cells else '?'
        tp = _int_of(cells[1]) if len(cells) > 1 else -1
        val = cells[2] if len(cells) > 2 else ''
        line = '  %d. %s' % (i, name)
        if tp in _CST_CN:
            line += '   [%s]' % _CST_CN[tp]
        elif tp >= 0:
            line += '   [类型 %d]' % tp
        if val:
            line += '   值 %s' % val
        out.append(line)
    if n == 0:
        out.append('  (还没有约束: 用 catia_fix_constraint / catia_coincidence_constraint 等加)')
    out.append('  说明: 值对角度约束是按弧度给的(CATIA 内部单位), 其它是 mm。')
    out.append(_note_text(v))
    return _join(out)


# --------------------------------------------------------------------------
# 输出拼装
# --------------------------------------------------------------------------

def _doc_line(v) -> str:
    """报一下这次动的是哪个装配文档 —— 多文档会话里这点很关键。"""
    if not v.get('doc'):
        return ''
    src = {'active': '活动文档', 'scanned': '自动挑的(活动文档不是装配)'}.get(
        v.get('doc_source', ''), v.get('doc_source', '?'))
    return '  装配文档: %s (%s)' % (v['doc'], src)


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


def _warn_text(v) -> str:
    w = v.get('warn')
    return ('  注意: %s' % w) if w else ''


def _unsup_text(v) -> str:
    """接口不存在时统一话术(必须原样返回给 AI 客户端)。"""
    parts = [UNSUPPORTED]
    if v.get('feature'):
        parts.append('  接口/特征: %s' % v['feature'])
    if v.get('detail'):
        parts.append('  CATIA 说明: %s' % v['detail'])
    return '\n'.join(parts)


def _note_text(v) -> str:
    return _DRY_NOTE if v.get('_dry') else ''


# --------------------------------------------------------------------------
# 工具表(9 个, 名字与清单逐字一致)
# --------------------------------------------------------------------------

TOOLS = [
    ('catia_add_component',
     '把已有零件/子装配文件插进当前装配(path), 或新建一个空零件组件(name); '
     '文件已经在 CATIA 里开着就直接复用。',
     obj({'path': S('已有 .CATPart / .CATProduct 的完整路径'),
          'name': S('新建空零件的名字(与 path 二选一)')}), t_add_component),

    ('catia_add_new_part',
     '在当前装配里新建一个零件组件(Products.AddNewComponent("Part", 名字); '
     '老版本不支持时退回"新建 CATPart 文档 + 插入")。',
     obj({'name': S('新零件名(零件号)')}, ['name']), t_add_new_part),

    ('catia_fix_constraint',
     '给组件加固定约束(Fix, catCstTypeReference)。component 可以是名字或 '
     'catia_list_components 里的序号。',
     obj({'component': S('组件名或序号(1 起, 见 catia_list_components)')},
         ['component']), t_fix_constraint),

    ('catia_coincidence_constraint',
     '两个元素的重合约束(catCstTypeOn)。元素写法: "组件名/XY"、"组件名/YZ"、"组件名/ZX" '
     '(组件原点平面)或 "组件名/发布名"; 只写组件名=用它的 XY 平面。',
     obj({'element1': S('第一个元素, 如 "Part1/XY" 或 "Part1"'),
          'element2': S('第二个元素, 如 "Part2/YZ" 或 "Part2/孔轴线(发布名)"')},
         ['element1', 'element2']), t_coincidence_constraint),

    ('catia_offset_constraint',
     '两个元素的偏移(距离)约束(catCstTypeDistance), 单位 mm。元素写法同重合约束。',
     obj({'element1': S('第一个元素, 如 "Part1/XY"'),
          'element2': S('第二个元素, 如 "Part2/XY"'),
          'offset': N('偏移距离 mm')},
         ['element1', 'element2', 'offset']), t_offset_constraint),

    ('catia_angle_constraint',
     '两个元素的角度约束(catCstTypeAngle), 角度用度给, 写进 CATIA 时换算成弧度。',
     obj({'element1': S('第一个元素, 如 "Part1/ZX"'),
          'element2': S('第二个元素, 如 "Part2/ZX"'),
          'angle': N('角度, 度')},
         ['element1', 'element2', 'angle']), t_angle_constraint),

    ('catia_move_component',
     '平移/旋转一个组件(Product.Move.Apply, 12 个数的位移矩阵)。translation 与 '
     'rotation_axis+rotation_angle 至少给一个。',
     obj({'component': S('组件名或序号(1 起)'),
          'translation': A('平移量 [dx, dy, dz] mm', N('分量 mm')),
          'rotation_axis': S("旋转轴: 'X' / 'Y' / 'Z', 或 '1,0,0' 这样的三分量"),
          'rotation_angle': N('旋转角度, 度(逆时针为正, 需与 rotation_axis 同时给)')},
         ['component']), t_move_component),

    ('catia_list_components',
     '列出装配树里的所有组件(序号/层级/名称/零件号), 序号可直接给 fix / move / 约束工具用。',
     obj({}), t_list_components),

    ('catia_list_constraints',
     '列出装配(含子装配)里的所有约束: 名称/类型/数值。',
     obj({}), t_list_constraints),
]
