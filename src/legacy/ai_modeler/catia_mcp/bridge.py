# -*- coding: utf-8 -*-
r"""CATIA V5 MCP 桥 —— 用 cscript + COM 驱动 CATIA, 不依赖 pywin32。

为什么不用 pywin32: 本机的 CATIA V5-6R2023 是中文版, 直推通道(cscript + COM)
已经真机验证过(含 MainBody/偏移平面/InWorkObject/中文路径等一堆坑), 复用这条
通道既省依赖又省调试; 需要 pywin32 的客户端(如参考项目)也能用同一套工具面。

对外只有两个函数:
    ensure_catia()                 连接(必要时启动) CATIA
    run_vbs(body, out_keys=())     执行一段 VBScript, 返回 {'ok', 'vars', 'log'}

脚本约定(所有工具模块都按这个写):
    · 脚本里用 Out "键", 值    回传结果 -> Python 侧拿到 vars['键']
    · 出错就 Fail "说明"       -> Python 侧抛 CatiaError
    · 常用助手(预置 + 内核里都有):
        Doc()/Part()/Body()/SF()/HSF()/Sel()   取当前文档/零件/实体/工厂/选择集
        Sk(z)                                  在高度 z 的偏移平面上新建草图(已 OpenEdition)
        PlaneAtZ(z)                            高度 z 的参考平面
        MainBodyOf(part)                       取实体(中文版 CATIA 不能用 Bodies.Item)
        RefOf(o) / Update1() / FeatureCount()  取参考 / 切回实体并 Update / 数特征
    · 脚本是 UTF-16 保存的, 中文可以直接写在里面(cscript 读得了; 参考项目的
      "纯 ASCII"约束是针对 CATIA 自己的 CATScript 宏, 不是这里)。

真机踩过的三个坑(都已在本文件里处理, 工具模块照抄即可):
    1. **Application.ActiveDocument 是只读的** —— 想切活动文档要用 `d.Activate`,
       直接赋值会报 "对象不支持此属性或方法: 'ActiveDocument'"。
    2. **VBScript 标识符不分大小写** —— 局部变量不能与函数同名: 在 `Function Sk`
       里写 `Dim sk` 会报 "名称重定义"。工具脚本里也别用 sk/pad/out/fail 这类名字。
    3. 脚本必须**整段可编译**, 一个语法错误会让所有 Out 都拿不到 —— 所以
       run_vbs 会把 cscript 的原始报错一起抛出来, 别把失败当成功。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # .../ai_modeler/catia_mcp
PKG = os.path.dirname(HERE)                                # .../ai_modeler
ROOT = os.path.dirname(PKG)                                # 项目根目录
PLUGIN_DIR = os.path.join(ROOT, 'catia_plugin')
CORE_VBS = os.path.join(PLUGIN_DIR, 'catia_build_core.vbs.txt')
PROGID = 'CATIA.Application'

# 干跑模式(供离线测试协议与工具签名, 不真的动 CATIA)
DRY_RUN = os.environ.get('AI_MODELER_MCP_DRYRUN', '') not in ('', '0', 'false')


class CatiaError(RuntimeError):
    """CATIA 侧执行失败(脚本里 Fail 出来的, 或 cscript 直接报错)。"""


# --------------------------------------------------------------------------
# 预置脚本(ASCII): 连接 + 输出/失败 + 取对象 + 新建文档
# --------------------------------------------------------------------------

_PRELUDE = r'''Option Explicit
Dim gApp, gDoc, gPart, gBody, gSF, gHSF, gSel, gHB, gFso, gLogPath, gStartCmd
Set gFso = CreateObject("Scripting.FileSystemObject")
gLogPath = ""

Sub Out(k, v)
    WScript.Echo "AIM:" & k & "=" & v
End Sub

Sub Fail(m)
    WScript.Echo "AIM:ERROR=" & m
    WScript.Quit 1
End Sub

Sub Attach()
    On Error Resume Next
    Err.Clear
    Set gApp = GetObject(, "CATIA.Application")
    If Err.Number <> 0 Or IsEmpty(gApp) Then
        Err.Clear
        ' start CATIA if it is not running (StartArgs are passed in from Python)
        If Len(gStartCmd) > 0 Then
            CreateObject("WScript.Shell").Run gStartCmd, 0, False
            Dim i, t0
            t0 = Timer
            For i = 1 To 120
                WScript.Sleep 1000
                Err.Clear
                Set gApp = GetObject(, "CATIA.Application")
                If Err.Number = 0 And Not IsEmpty(gApp) Then Exit For
                If Timer - t0 > 150 Then Exit For
            Next
        End If
        If Err.Number <> 0 Or IsEmpty(gApp) Then
            Fail "cannot attach to CATIA (is it running?)"
        End If
    End If
    On Error GoTo 0
End Sub

Function Act()
    Attach
    On Error Resume Next
    Err.Clear
    Set Act = Nothing
    Set Act = gApp.ActiveDocument
    If Err.Number <> 0 Then Err.Clear
    On Error GoTo 0
End Function

Sub NeedDoc()
    Attach
    On Error Resume Next
    Err.Clear
    Set gDoc = gApp.ActiveDocument
    If Err.Number <> 0 Or gDoc Is Nothing Then
        Err.Clear
        On Error GoTo 0
        Fail "no active document"
    End If
    On Error GoTo 0
End Sub

Sub NeedPart()
    NeedDoc
    On Error Resume Next
    Err.Clear
    Set gPart = gDoc.Part
    If Err.Number <> 0 Or gPart Is Nothing Then
        Err.Clear
        On Error GoTo 0
        Fail "active document is not a CATPart: " & gDoc.Name
    End If
    On Error GoTo 0
    Set gBody = MainBodyOf(gPart)
    Err.Clear
    Set gSF = gPart.ShapeFactory
    Err.Clear
    Set gHSF = gPart.HybridShapeFactory
    Err.Clear
    Set gSel = gDoc.Selection
    Err.Clear
End Sub

Function Doc()
    NeedDoc
    Set Doc = gDoc
End Function

Function Part()
    NeedPart
    Set Part = gPart
End Function

Function Body()
    NeedPart
    Set Body = gBody
End Function

Function SF()
    NeedPart
    Set SF = gSF
End Function

Function HSF()
    NeedPart
    Set HSF = gHSF
End Function

Function Sel()
    NeedDoc
    Set Sel = gDoc.Selection
End Function

Function NewDoc(kind, nm)
    Attach
    Dim d
    On Error Resume Next
    Err.Clear
    If LCase(kind) = "part" Then
        Set d = gApp.Documents.Add("Part")
    Else
        Set d = gApp.Documents.Add("Product")
    End If
    If Err.Number <> 0 Then
        Err.Clear
        On Error GoTo 0
        Fail "cannot create " & kind & " document"
    End If
    On Error GoTo 0
    If Len(nm) > 0 Then
        On Error Resume Next
        d.Name = nm
        Err.Clear
        On Error GoTo 0
    End If
    ' ⚠️ CATIA 的 Application.ActiveDocument 是**只读**的(赋值会报
    '    "对象不支持此属性或方法: 'ActiveDocument'"), 要切活动文档必须用
    '    Document.Activate。
    On Error Resume Next
    Err.Clear
    d.Activate
    If Err.Number <> 0 Then
        Err.Clear
        gApp.Windows.Item(1).Activate
    End If
    Err.Clear
    On Error GoTo 0
    Set NewDoc = d
End Function

Function PlaneAtZ(z)
    If Abs(z) < 0.000001 Then
        Set PlaneAtZ = Part().OriginElements.PlaneXY
        Exit Function
    End If
    Dim ref, off
    Set ref = Part().CreateReferenceFromObject(Part().OriginElements.PlaneXY)
    Set off = HSF().AddNewPlaneOffset(ref, z, False)
    If IsEmpty(gHB) Then Set gHB = Nothing
    If gHB Is Nothing Then Set gHB = Part().HybridBodies.Add()
    On Error Resume Next
    gHB.AppendHybridShape(off)
    Err.Clear
    On Error GoTo 0
    Part().Update
    Set PlaneAtZ = off
End Function

Function Sk(z)
    ' ⚠️ 局部变量不要叫 sk: VBScript 标识符不分大小写, 局部变量与函数同名会报
    '    "名称重定义"(真机踩过)。同理别用 g/sf 这类与全局同名的名字。
    Dim s2
    Set s2 = Body().Sketches.Add(PlaneAtZ(z))
    s2.OpenEdition
    Set Sk = s2
End Function

Function RefOf(o)
    Set RefOf = Part().CreateReferenceFromObject(o)
End Function

Function Update1()
    On Error Resume Next
    Err.Clear
    Part().InWorkObject = Body()
    Err.Clear
    Part().Update
    If Err.Number <> 0 Then
        Err.Clear
        Update1 = False
        Exit Function
    End If
    On Error GoTo 0
    Update1 = True
End Function

Function FeatureCount()
    On Error Resume Next
    FeatureCount = Body().Shapes.Count
    Err.Clear
    On Error GoTo 0
End Function
'''

_EPILOGUE = r'''
WScript.Echo "AIM:DONE=1"
'''


def _read_core() -> str:
    """复用已经真机验证过的构建内核(MainBodyOf / 草图 / 凸台凹槽 / 翻转)。"""
    try:
        with open(CORE_VBS, 'r', encoding='utf-8') as f:
            txt = f.read()
    except OSError:
        return ''
    # 内核里的中文注释不能进 VBS(见模块说明), 逐行丢掉非 ASCII 注释
    out = []
    for line in txt.splitlines():
        if line.lstrip().startswith("'"):
            out.append("'")
            continue
        try:
            line.encode('ascii')
            out.append(line)
        except UnicodeEncodeError:
            out.append("'")
    return '\n'.join(out)


def build_script(body: str, start_cmd: str = '') -> str:
    """拼出完整 VBScript: 预置 + 内核 + 工具片段 + 收尾。"""
    head = _PRELUDE
    if start_cmd:
        head = head.replace('Dim gApp, gDoc',
                            'Dim gApp, gDoc').replace(
            'Set gFso = CreateObject("Scripting.FileSystemObject")',
            'Set gFso = CreateObject("Scripting.FileSystemObject")\n'
            'gStartCmd = "%s"' % start_cmd.replace('"', '""'))
    core = _read_core()
    # 内核用到的全局量在预置里已声明; 若内核缺失也不致命(只影响草图相关工具)
    return head + '\n' + core + '\n' + (body or '') + '\n' + _EPILOGUE


def _start_cmd() -> str:
    """给脚本用的"启动 CATIA"命令行(取不到就留空, 脚本会直接报连不上)。"""
    try:
        from .. import catia as C
    except Exception:
        return ''
    try:
        info = C.best_install()
        if not info:
            return ''
        exe = info.get('starter') or C.find_catia_exe()
        if not exe:
            return ''
        args = C.start_args(info) if hasattr(C, 'start_args') else []
        if args:
            return subprocess.list2cmdline([exe] + list(args))
        return subprocess.list2cmdline([exe])
    except Exception:
        return ''


def run_vbs(body: str, out_keys=(), timeout: int = 300, keep: bool = False) -> dict:
    """执行一段工具脚本。

    返回 {'ok': bool, 'vars': {键: 文本}, 'log': 文本, 'script': 路径}
    出错抛 CatiaError(带 CATIA 侧的说明 + cscript 输出尾部)。
    """
    script = build_script(body, _start_cmd())
    path = os.path.join(tempfile.gettempdir(),
                        'aim_mcp_%d.vbs' % int(time.time() * 1000))
    # UTF-16: 与本项目已验证过的推送脚本一致(cscript 读 UTF-16 没问题)
    with open(path, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(script)

    if DRY_RUN:
        return {'ok': True, 'vars': {}, 'log': '(dry-run) 未执行', 'script': path,
                'dry_run': True}

    try:
        p = subprocess.run(['cscript', '//nologo', path], capture_output=True,
                           timeout=timeout)
        out = _decode(p.stdout) + _decode(p.stderr)
    except subprocess.TimeoutExpired:
        raise CatiaError('CATIA 脚本超时(%ss): %s' % (timeout, path))
    finally:
        if not keep:
            try:
                os.remove(path)
            except OSError:
                pass

    vars_, err = {}, None
    for line in out.splitlines():
        m = re.match(r'AIM:([A-Za-z_][\w]*)=(.*)$', line.strip())
        if m:
            k, v = m.group(1), m.group(2)
            if k == 'ERROR':
                err = v
            elif k != 'DONE':
                vars_[k] = v
    tail = '\n'.join(l for l in out.splitlines() if l.strip())[-800:]
    if err:
        raise CatiaError('%s\n--- CATIA ---\n%s' % (err, tail))
    if 'AIM:DONE=1' not in out:
        raise CatiaError('脚本没有正常结束(可能是 CATIA 弹窗挡住了)\n--- cscript ---\n%s' % tail)
    if out_keys:
        missing = [k for k in out_keys if k not in vars_]
        if missing:
            raise CatiaError('脚本没有回传: %s\n--- cscript ---\n%s' % (', '.join(missing), tail))
    return {'ok': True, 'vars': vars_, 'log': tail, 'script': path}


def _decode(b: bytes) -> str:
    if not b:
        return ''
    for enc in ('gbk', 'utf-8'):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode('gbk', 'replace')


def ensure_catia(timeout: int = 180) -> dict:
    """连接 CATIA(没开就按配置启动), 返回 {'ok', 'info'}。"""
    body = (
        'Dim d, n, i\n'
        'Attach\n'
        'Out "version", ""\n'
        'n = gApp.Documents.Count\n'
        'Out "doc_count", n\n'
        'On Error Resume Next\n'
        'Err.Clear\n'
        'Out "active", gApp.ActiveDocument.Name\n'
        'Err.Clear\n'
        'On Error GoTo 0\n'
        'Out "name", "CATIA"\n'
    )
    return run_vbs(body, timeout=timeout)


def host_info() -> str:
    try:
        from .. import catia as C
        ok, msg = C.probe()
        return msg if ok else ('未检测到 CATIA: ' + msg)
    except Exception as e:
        return '探测 CATIA 失败: %s' % e
