' ============================================================
'  AI 自动建模助手 —— 静默启动器 (不显示黑色控制台窗口)
'  双击本文件即可运行程序。
' ============================================================
Option Explicit
Dim fso, sh, base, exe, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)

Function FindPythonW()
    Dim p, f
    FindPythonW = ""
    p = base & "\.venv\Scripts\pythonw.exe"
    If fso.FileExists(p) Then FindPythonW = p : Exit Function
    p = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
    If fso.FolderExists(p) Then
        For Each f In fso.GetFolder(p).SubFolders
            If fso.FileExists(f.Path & "\pythonw.exe") Then
                FindPythonW = f.Path & "\pythonw.exe" : Exit Function
            End If
        Next
    End If
    p = sh.ExpandEnvironmentStrings("%ProgramFiles%\Python")
    If fso.FolderExists(p) Then
        For Each f In fso.GetFolder(p).SubFolders
            If fso.FileExists(f.Path & "\pythonw.exe") Then
                FindPythonW = f.Path & "\pythonw.exe" : Exit Function
            End If
        Next
    End If
    p = "C:\Python314\pythonw.exe"
    If fso.FileExists(p) Then FindPythonW = p : Exit Function
End Function

exe = FindPythonW()
If exe = "" Then
    ' 退回 PATH 上的 pythonw / python
    On Error Resume Next
    exe = "pythonw"
    sh.CurrentDirectory = base
    sh.Run """" & exe & """ -m ai_modeler", 0, False
    If Err.Number <> 0 Then
        exe = "python"
        Err.Clear
        sh.Run """" & exe & """ -m ai_modeler", 1, False
    End If
    If Err.Number <> 0 Then
        MsgBox "未找到 Python 解释器。" & vbCrLf & vbCrLf & _
               "请先安装 Python 3.9 或更高版本 (https://www.python.org/downloads/)," & vbCrLf & _
               "安装时务必勾选 Add Python to PATH。", 16, "AI 自动建模助手"
    End If
    WScript.Quit
End If

sh.CurrentDirectory = base
sh.Run """" & exe & """ -m ai_modeler", 0, False
