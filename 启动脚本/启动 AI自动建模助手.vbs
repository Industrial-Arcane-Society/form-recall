Option Explicit
Dim fso, sh, base, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(base & "\ai_modeler\__main__.py") Then
    base = fso.GetParentFolderName(base)
End If

Function FindPython()
    Dim p, folder
    FindPython = ""
    p = base & "\.venv\Scripts\pythonw.exe"
    If fso.FileExists(p) Then FindPython = p : Exit Function
    p = base & "\.venv\Scripts\python.exe"
    If fso.FileExists(p) Then FindPython = p : Exit Function
    p = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python")
    If fso.FolderExists(p) Then
        For Each folder In fso.GetFolder(p).SubFolders
            If fso.FileExists(folder.Path & "\pythonw.exe") Then FindPython = folder.Path & "\pythonw.exe" : Exit Function
            If fso.FileExists(folder.Path & "\python.exe") Then FindPython = folder.Path & "\python.exe" : Exit Function
        Next
    End If
    p = sh.ExpandEnvironmentStrings("%ProgramFiles%\Python")
    If fso.FolderExists(p) Then
        For Each folder In fso.GetFolder(p).SubFolders
            If fso.FileExists(folder.Path & "\pythonw.exe") Then FindPython = folder.Path & "\pythonw.exe" : Exit Function
            If fso.FileExists(folder.Path & "\python.exe") Then FindPython = folder.Path & "\python.exe" : Exit Function
        Next
    End If
    p = "C:\Python314\pythonw.exe"
    If fso.FileExists(p) Then FindPython = p : Exit Function
    p = "C:\Python314\python.exe"
    If fso.FileExists(p) Then FindPython = p
End Function

sh.CurrentDirectory = base
exe = FindPython()
If exe = "" Then
    On Error Resume Next
    sh.Run """pythonw"" -m ai_modeler", 0, False
    If Err.Number <> 0 Then
        Err.Clear
        sh.Run """python"" -m ai_modeler", 1, False
    End If
    If Err.Number <> 0 Then MsgBox "Python 3.9 or later was not found. Install Python and enable Add Python to PATH.", 16, "AI Modeler"
Else
    sh.Run """" & exe & """ -m ai_modeler", 0, False
End If