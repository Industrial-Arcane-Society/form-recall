# -*- coding: utf-8 -*-
r"""
生成 CATIA V5 批量导出 STEP 的脚本（v4）。

## 版本演进（每条都是实测出来的，不是推测）

| 版本 | 问题 | 怎么发现的 |
|---|---|---|
| v1 | 中文路径会让 CATIA 卡死 | 本项目 `catia.py:339` 的实测记录 |
| v1 | 导出格式常量只试一种 | 搜索只能给讨论帖，不能确定常量 |
| v2 | `Sub(..., ByRef idx)` —— VBScript 子过程参数不支持 ByRef | `cscript` 语法检查 |
| v2 | `fso` 等只在 `Main` 里赋值，子过程访问不到 | 逐行复核作用域 |
| v3 | 暂存放 `C:\` 根目录 → `CreateFolder` **错误 70（权限拒绝）** | 冒烟测试实跑 |
| v3 | `MakeDirs` 用 `Split("\")` 第一段是盘符 `"C:"`，对它建目录报"路径未找到" | 冒烟测试实跑 |
| **v4** | **`.CATScript` 写成 UTF-16 → CATIA 报「无效字符」(列 0)** | **用户实际运行截图** |

### v4 的关键教训

`cscript` 能读 UTF-16，**CATIA 的 CATScript 引擎不能**（它按 ANSI 解析，
UTF-16 的 BOM 与 `\x00` 字节直接变成"无效字符"）。
**语法检查通过 ≠ CATIA 能跑** —— 两种读取方式不同，这就是漏检的原因。

所以 v4：
  · **`.CATScript` 一律输出纯 ASCII**（CATIA 脚本最稳），提示语用英文；
    默认输入目录里的中文用 `ChrW()` 拼出来，文件本身仍是纯 ASCII；
  · `.vbs` 保持中文（`cscript` 支持 UTF-16），输出仍为 UTF-16。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================ CATScript（纯 ASCII！）
# 注意：本字符串里**不能出现任何非 ASCII 字符**，否则 CATIA 会报"无效字符"。
# 默认输入目录 "C:\Users\25643\Desktop\零件参数" 用 ChrW(38646)&ChrW(20214) 拼出
# （零=38646，件=20214），保证源码是纯 ASCII。
CATSCRIPT = r'''Language="VBSCRIPT"

'================================================================
' CATIA V5 - Batch export to STEP   (v4, ASCII-only source)
'
'   How to run:
'     CATIA -> Tools -> Macro -> Macros... -> pick this file -> Run
'
'   Two known CATIA pitfalls handled here:
'     1. Chinese characters in paths can HANG CATIA on export.
'        -> input files are first copied to an ASCII staging folder.
'     2. Writing this .CATScript as UTF-16 makes CATIA report
'        "invalid character" at column 0 -> keep this file pure ASCII.
'
'   Output folder defaults to %TEMP%\catia_step_out (ASCII).
'   If you want progress logs, use tools\catia_export_step.vbs instead.
'================================================================

Option Explicit

Dim fso, sStage, nOk, nErr, sFmt, nSeq

Sub CATMain()
    Dim sIn, sOut, sTmp

    Set fso = CreateObject("Scripting.FileSystemObject")

    ' --- staging folder under %TEMP% (writing to C:\ root fails, error 70) ---
    sTmp = CreateObject("WScript.Shell").ExpandEnvironmentStrings("%TEMP%")
    If sTmp = "" Or sTmp = "%TEMP%" Then sTmp = fso.GetSpecialFolder(2)
    sStage = fso.BuildPath(sTmp, "catia_step_stage")

    ' --- default input: C:\Users\25643\Desktop\<two CJK chars> ---
    Dim sDefIn
    sDefIn = "C:\Users\25643\Desktop\" & ChrW(38646) & ChrW(20214)

    sIn = InputBox("Input folder (contains CATPart / CATProduct):", _
                   "Batch export STEP", sDefIn)
    If sIn = "" Then Exit Sub
    If Not fso.FolderExists(sIn) Then
        MsgBox "Folder not found: " & sIn, 16, "Batch export STEP"
        Exit Sub
    End If

    sOut = InputBox("Output folder (ASCII path recommended):", _
                    "Batch export STEP", fso.BuildPath(sTmp, "catia_step_out"))
    If sOut = "" Then Exit Sub
    If Not fso.FolderExists(sOut) Then MakeDirs sOut
    If Not fso.FolderExists(sOut) Then
        MsgBox "Cannot create output folder: " & sOut, 16, "Batch export STEP"
        Exit Sub
    End If

    ' --- prepare ASCII staging folder ---
    On Error Resume Next
    If fso.FolderExists(sStage) Then fso.DeleteFolder sStage, True
    On Error GoTo 0
    MakeDirs sStage
    If Not fso.FolderExists(sStage) Then
        MsgBox "Cannot create staging folder: " & sStage, 16, "Batch export STEP"
        Exit Sub
    End If

    nOk = 0 : nErr = 0 : sFmt = "" : nSeq = 0

    Walk sIn, sOut, 0

    On Error Resume Next
    fso.DeleteFolder sStage, True
    On Error GoTo 0

    MsgBox "Done." & vbCrLf & _
           "OK: " & nOk & vbCrLf & _
           "Failed: " & nErr & vbCrLf & vbCrLf & _
           "Output: " & sOut & vbCrLf & _
           "Working export format: " & sFmt, 64, "Batch export STEP"
End Sub


'----------------------------------------------------------------
' Recursive walk: copy each file to ASCII stage, then export
'----------------------------------------------------------------
Sub Walk(sDir, sOutDir, iDepth)
    Dim oFolder, oFile, oSub, sExt, sTmp, sTarget
    If iDepth > 8 Then Exit Sub
    Set oFolder = fso.GetFolder(sDir)

    For Each oFile In oFolder.Files
        sExt = LCase(fso.GetExtensionName(oFile.Name))
        If sExt = "catpart" Or sExt = "catproduct" Then
            nSeq = nSeq + 1
            ' ASCII staging name; keeps Chinese names away from CATIA
            sTmp = sStage & "\p" & Right("000000" & CStr(nSeq), 6) & "." & sExt
            On Error Resume Next
            Err.Clear
            oFile.Copy sTmp, True
            On Error GoTo 0
            If fso.FileExists(sTmp) Then
                sTarget = fso.BuildPath(sOutDir, fso.GetBaseName(oFile.Name) & ".stp")
                If ExportOne(sTmp, sTarget) Then
                    nOk = nOk + 1
                Else
                    nErr = nErr + 1
                End If
            Else
                nErr = nErr + 1
            End If
        End If
    Next

    For Each oSub In oFolder.SubFolders
        Walk oSub.Path, sOutDir, iDepth + 1
    Next
End Sub


'----------------------------------------------------------------
' Export one document; try both format strings
'----------------------------------------------------------------
Function ExportOne(sSrc, sTarget)
    Dim oDoc, aFmt, k, bOk
    ExportOne = False

    On Error Resume Next
    Err.Clear
    Set oDoc = CATIA.Documents.Open(sSrc)
    If Err.Number <> 0 Then
        On Error GoTo 0
        Exit Function
    End If
    On Error GoTo 0

    If sFmt <> "" Then
        aFmt = Array(sFmt)
    Else
        aFmt = Array("stp", "step")
    End If

    bOk = False
    For k = 0 To UBound(aFmt)
        On Error Resume Next
        Err.Clear
        oDoc.ExportData sTarget, aFmt(k)
        If Err.Number = 0 Then
            bOk = True
            If sFmt = "" Then sFmt = aFmt(k)
            On Error GoTo 0
            Exit For
        End If
        On Error GoTo 0
    Next

    On Error Resume Next
    oDoc.Close
    On Error GoTo 0

    If bOk And fso.FileExists(sTarget) Then ExportOne = True
End Function


'----------------------------------------------------------------
' Create folders level by level (CreateFolder does not create parents)
'   First segment is a drive letter like "C:" - never call CreateFolder on it.
'----------------------------------------------------------------
Sub MakeDirs(sPath)
    Dim aParts, k, sCur
    If sPath = "" Then Exit Sub
    If fso.FolderExists(sPath) Then Exit Sub
    aParts = Split(sPath, "\")
    If UBound(aParts) < 1 Then Exit Sub
    sCur = aParts(0)                     ' drive, e.g. "C:"
    For k = 1 To UBound(aParts)
        If aParts(k) <> "" Then
            If Right(sCur, 1) = "\" Then
                sCur = sCur & aParts(k)
            Else
                sCur = sCur & "\" & aParts(k)
            End If
            On Error Resume Next
            If Not fso.FolderExists(sCur) Then fso.CreateFolder sCur
            On Error GoTo 0
        End If
    Next
End Sub
'''

# ============================================================ VBS（推荐，带日志）
VBS = r'''Option Explicit

'================================================================
' CATIA V5 批量导出 STEP   (v4)
'
'   用法 A：双击本文件
'   用法 B：cscript //nologo catia_export_step.vbs "输入目录" "输出目录"
'
' 设计要点：
'   · 中文路径会卡死 CATIA（本项目实测），所以先把待转文件复制到
'     ASCII 暂存目录（%TEMP%\catia_step_stage），CATIA 只在 ASCII 路径下工作；
'     ⚠️ 不要用 C:\ 根目录：实测 CreateFolder 返回错误 70（权限拒绝）。
'   · 导出格式 "stp" / "step" 两种都试，成功的那种写进日志；
'   · 断点续跑：输出里已存在的 .stp 会跳过。
'================================================================

Dim fso, oCatia, sInDir, sOutDir, sStageDir, sLogPath, sFmt
Dim nOk, nSkip, nErr, oMap, nIdx

Sub Main()
    Set fso = CreateObject("Scripting.FileSystemObject")

    Dim sTmp
    sTmp = CreateObject("WScript.Shell").ExpandEnvironmentStrings("%TEMP%")
    If sTmp = "" Or sTmp = "%TEMP%" Then sTmp = fso.GetSpecialFolder(2)
    sStageDir = fso.BuildPath(sTmp, "catia_step_stage")

    If WScript.Arguments.Count >= 2 Then
        sInDir = WScript.Arguments(0)
        sOutDir = WScript.Arguments(1)
    Else
        sInDir = InputBox("输入目录（含 CATPart / CATProduct 的文件夹）:", _
                          "批量导出 STEP", "C:\Users\25643\Desktop\零件参数")
        If sInDir = "" Then Exit Sub
        sOutDir = InputBox("输出目录（STEP 写到这里；建议纯英文路径）:", _
                           "批量导出 STEP", fso.BuildPath(sTmp, "catia_step_out"))
        If sOutDir = "" Then Exit Sub
    End If

    If Not fso.FolderExists(sInDir) Then
        MsgBox "输入目录不存在: " & sInDir, 16, "批量导出 STEP"
        Exit Sub
    End If
    If Not fso.FolderExists(sOutDir) Then MakeDirs sOutDir
    If Not fso.FolderExists(sOutDir) Then
        MsgBox "输出目录建不出来（可能没有写权限）: " & sOutDir, 16, "批量导出 STEP"
        Exit Sub
    End If
    sLogPath = fso.BuildPath(sOutDir, "_export_log.txt")
    WriteLog "==== CATIA STEP 批量导出 v4 ===="
    WriteLog "输入: " & sInDir
    WriteLog "输出: " & sOutDir
    WriteLog "暂存: " & sStageDir
    WriteLog "开始: " & Now()

    On Error Resume Next
    If fso.FolderExists(sStageDir) Then fso.DeleteFolder sStageDir, True
    On Error GoTo 0
    MakeDirs sStageDir
    If Not fso.FolderExists(sStageDir) Then
        WriteLog "暂存目录建不出来: " & sStageDir
        MsgBox "暂存目录建不出来: " & sStageDir & vbCrLf & _
               "请检查 %TEMP% 是否可写。", 16, "批量导出 STEP"
        Exit Sub
    End If

    Set oMap = CreateObject("Scripting.Dictionary")
    nIdx = 0
    StageFolder sInDir, ""
    WriteLog "已暂存 " & oMap.Count & " 个文件"

    If oMap.Count = 0 Then
        MsgBox "输入目录里没找到 .CATPart / .CATProduct", 48, "批量导出 STEP"
        Exit Sub
    End If

    If Not ConnectCatia() Then Exit Sub

    nOk = 0 : nSkip = 0 : nErr = 0
    sFmt = ""
    Dim k, sSrc, sRel, sTarget
    For Each k In oMap.Keys
        sSrc = k
        sRel = oMap(k)
        sTarget = sOutDir & "\" & sRel & ".stp"
        If fso.FileExists(sTarget) Then
            nSkip = nSkip + 1
        Else
            MakeDirs fso.GetParentFolderName(sTarget)
            If ExportOne(sSrc, sTarget) Then
                nOk = nOk + 1
            Else
                nErr = nErr + 1
            End If
        End If
    Next

    WriteLog "完成: " & Now()
    WriteLog "成功 " & nOk & " | 跳过 " & nSkip & " | 失败 " & nErr
    If sFmt <> "" Then WriteLog "可用的导出格式串: " & sFmt

    On Error Resume Next
    fso.DeleteFolder sStageDir, True
    On Error GoTo 0

    MsgBox "完成。" & vbCrLf & _
           "成功: " & nOk & vbCrLf & _
           "跳过(已存在): " & nSkip & vbCrLf & _
           "失败: " & nErr & vbCrLf & vbCrLf & _
           "输出: " & sOutDir & vbCrLf & _
           "日志: " & sLogPath & vbCrLf & vbCrLf & _
           "请把日志里『可用的导出格式串』那一行告诉我。", 64, "批量导出 STEP"
End Sub


Function ConnectCatia()
    ConnectCatia = False
    On Error Resume Next
    Err.Clear
    Set oCatia = GetObject(, "CATIA.Application")
    If Err.Number <> 0 Then
        Err.Clear
        Set oCatia = CreateObject("CATIA.Application")
    End If
    If Err.Number <> 0 Then
        WriteLog "无法启动/连接 CATIA: " & Err.Number & " " & Err.Description
        MsgBox "无法连接 CATIA。" & vbCrLf & _
               "请确认已安装 CATIA V5 并能正常启动。", 16, "批量导出 STEP"
        On Error GoTo 0
        Exit Function
    End If
    On Error GoTo 0
    On Error Resume Next
    oCatia.Visible = True
    oCatia.DisplayFileAlerts = False
    On Error GoTo 0
    ConnectCatia = True
End Function


Sub StageFolder(sDir, sRel)
    Dim oFolder, oFile, oSub, sExt, sNew, sChildRel, sBase
    Set oFolder = fso.GetFolder(sDir)

    For Each oFile In oFolder.Files
        sExt = LCase(fso.GetExtensionName(oFile.Name))
        If sExt = "catpart" Or sExt = "catproduct" Then
            nIdx = nIdx + 1
            sNew = sStageDir & "\p" & Right("00000" & CStr(nIdx), 5) & "." & sExt
            oFile.Copy sNew, True
            sBase = fso.GetBaseName(oFile.Name)
            If sRel = "" Then
                sChildRel = sBase
            Else
                sChildRel = sRel & "\" & sBase
            End If
            If Not oMap.Exists(sNew) Then oMap.Add sNew, sChildRel
            MakeDirs fso.GetParentFolderName(sOutDir & "\" & sChildRel)
        End If
    Next

    For Each oSub In oFolder.SubFolders
        If sRel = "" Then
            StageFolder oSub.Path, oSub.Name
        Else
            StageFolder oSub.Path, sRel & "\" & oSub.Name
        End If
    Next
End Sub


Function ExportOne(sSrc, sTarget)
    Dim oDoc, aFmt, i, bOk
    ExportOne = False

    On Error Resume Next
    Err.Clear
    Set oDoc = oCatia.Documents.Open(sSrc)
    If Err.Number <> 0 Then
        WriteLog "打开失败: " & sSrc & " | " & Err.Number & " " & Err.Description
        On Error GoTo 0
        Exit Function
    End If
    On Error GoTo 0

    If sFmt <> "" Then
        aFmt = Array(sFmt)
    Else
        aFmt = Array("stp", "step")
    End If

    bOk = False
    For i = 0 To UBound(aFmt)
        On Error Resume Next
        Err.Clear
        oDoc.ExportData sTarget, aFmt(i)
        If Err.Number = 0 Then
            bOk = True
            If sFmt = "" Then sFmt = aFmt(i)
            On Error GoTo 0
            Exit For
        Else
            WriteLog "格式 " & aFmt(i) & " 失败: " & Err.Number & " " & Err.Description
        End If
        On Error GoTo 0
    Next

    On Error Resume Next
    oDoc.Close
    On Error GoTo 0

    If Not bOk Then
        WriteLog "导出失败: " & sSrc
        Exit Function
    End If
    If Not fso.FileExists(sTarget) Then
        WriteLog "导出后找不到文件: " & sTarget
        Exit Function
    End If
    ExportOne = True
End Function


Sub MakeDirs(sPath)
    ' ⚠️ 第一段是盘符（"C:"），对它调 CreateFolder 会报"路径未找到"（实测）。
    Dim sCur, aParts, i
    If sPath = "" Then Exit Sub
    If fso.FolderExists(sPath) Then Exit Sub
    aParts = Split(sPath, "\")
    If UBound(aParts) < 1 Then Exit Sub
    sCur = aParts(0)
    For i = 1 To UBound(aParts)
        If aParts(i) <> "" Then
            If Right(sCur, 1) = "\" Then
                sCur = sCur & aParts(i)
            Else
                sCur = sCur & "\" & aParts(i)
            End If
            On Error Resume Next
            If Not fso.FolderExists(sCur) Then fso.CreateFolder sCur
            On Error GoTo 0
        End If
    Next
End Sub


Sub WriteLog(sLine)
    Dim f
    On Error Resume Next
    Set f = fso.OpenTextFile(sLogPath, 8, True)
    f.WriteLine sLine
    f.Close
    On Error GoTo 0
End Sub


Main
'''


def check_ascii(path) -> tuple:
    """确认文件是纯 ASCII（CATScript 必须如此，否则 CATIA 报"无效字符"）。"""
    with open(path, "rb") as f:
        raw = f.read()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    return (not bad), bad[:10]


def syntax_check(path) -> tuple:
    """用 cscript 检查 VBS 语法（注释掉裸 Main 调用以免真去连 CATIA）。"""
    try:
        with io.open(path, encoding="utf-16") as f:
            src = f.read()
    except Exception:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    stub = re.sub(r"(?m)^Main\s*$", "' [syntax check] Main not executed", src)
    tmp = os.path.join(tempfile.gettempdir(), "dsh_vbs_syntax_check.vbs")
    with io.open(tmp, "w", encoding="utf-16", newline="\r\n") as f:
        f.write(stub)
    try:
        p = subprocess.run(["cscript", "//nologo", tmp], capture_output=True,
                           timeout=60)
        out = (p.stdout or b"").decode("gbk", "replace") + \
              (p.stderr or b"").decode("gbk", "replace")
        return (p.returncode == 0), out.strip()
    except FileNotFoundError:
        return None, "cscript 不可用（跳过）"
    except Exception as e:
        return None, "语法检查异常: %s" % e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    cs = os.path.join(ROOT, "tools", "catia_batch_export_step.CATScript")
    vbs = os.path.join(ROOT, "tools", "catia_export_step.vbs")

    # CATScript：**纯 ASCII + CRLF**（UTF-16 会让 CATIA 报"无效字符"）
    with io.open(cs, "w", encoding="ascii", newline="\r\n") as f:
        f.write(CATSCRIPT)
    # VBS：UTF-16（cscript 支持，可带中文提示）
    with io.open(vbs, "w", encoding="utf-16", newline="\r\n") as f:
        f.write(VBS)

    print("已生成：")
    print("  tools/catia_batch_export_step.CATScript  （纯 ASCII + CRLF，CATIA 宏菜单用）")
    print("  tools/catia_export_step.vbs              （UTF-16，双击用，带日志）")
    print()
    ok_a, bad = check_ascii(cs)
    print("CATScript 纯 ASCII 检查: %s%s"
          % ("✅ 通过" if ok_a else "❌ 含非 ASCII 字节",
             "" if ok_a else " 位置/字节: %s" % bad))
    ok_s, out = syntax_check(vbs)
    if ok_s is None:
        print("VBS 语法检查: %s" % out)
    elif ok_s:
        print("VBS 语法检查: ✅ 通过")
    else:
        print("VBS 语法检查: ❌ 失败\n%s" % out)
    # 额外：把 CATScript 也交给 cscript 做语法检查（转成 UTF-16 临时副本）
    tmp = os.path.join(tempfile.gettempdir(), "dsh_cs_check.vbs")
    with io.open(tmp, "w", encoding="utf-16", newline="\r\n") as f:
        f.write(re.sub(r'Language="VBSCRIPT"',
                       "' Language=VBSCRIPT", CATSCRIPT))
    try:
        p = subprocess.run(["cscript", "//nologo", tmp], capture_output=True,
                           timeout=60)
        out2 = (p.stdout or b"").decode("gbk", "replace") + \
               (p.stderr or b"").decode("gbk", "replace")
        print("CATScript 语法检查: %s"
              % ("✅ 通过" if p.returncode == 0 else "❌ 失败\n%s" % out2.strip()))
        ok_cs = (p.returncode == 0)
    except Exception as e:
        print("CATScript 语法检查: 跳过（%s）" % e)
        ok_cs = True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return 0 if (ok_a and ok_s in (True, None) and ok_cs) else 1


if __name__ == "__main__":
    sys.exit(main())
