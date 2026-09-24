Option Explicit

'================================================================
' CATIA V5 批量导出 STEP   (v4)
'
'   用法 A：双击本文件
'   用法 B：cscript //nologo catia_export_step.vbs "输入目录" "输出目录"
'
' 设计要点：
'   · 中文路径会卡死 CATIA（本项目实测），所以先把待转文件复制到
'     ASCII 暂存目录（%TEMP%\catia_step_stage），CATIA 只在 ASCII 路径下工作；
'     ?? 不要用 C:\ 根目录：实测 CreateFolder 返回错误 70（权限拒绝）。
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
    ' ?? 第一段是盘符（"C:"），对它调 CreateFolder 会报"路径未找到"（实测）。
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
