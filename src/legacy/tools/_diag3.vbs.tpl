' 诊断 3：用 StartCommand 设等轴测视图 + 逐个试 Put* 的可用签名
Option Explicit
Dim catia, doc, viewer, vp, fso, ts, logPath, f
logPath = "@@LOG@@"
f = "@@FILE@@"
Set fso = CreateObject("Scripting.FileSystemObject")
Set ts = fso.CreateTextFile(logPath, True)
On Error Resume Next
Set catia = GetObject(, "CATIA.Application")
Set doc = catia.Documents.Open(f)
doc.Activate
Set viewer = catia.ActiveWindow.ActiveViewer
Err.Clear
catia.StartCommand "Isometric View"
ts.WriteLine "StartCommand Isometric err=" & Err.Number & " " & Err.Description
WScript.Sleep 1500
Err.Clear
Set vp = viewer.Viewpoint3D
vp.PutOrigin 0, 0, 0
ts.WriteLine "PutOrigin(3 int) err=" & Err.Number & " " & Err.Description
Err.Clear
vp.PutUpDirection 0, 0, 1
ts.WriteLine "PutUpDirection(3 int) err=" & Err.Number & " " & Err.Description
Err.Clear
vp.PutSightDirection Array(1, -1, 1)
ts.WriteLine "PutSightDirection(Array) err=" & Err.Number & " " & Err.Description
Err.Clear
viewer.Reframe
ts.WriteLine "Reframe err=" & Err.Number
Err.Clear
viewer.CaptureToFile 4, "@@TMP@@\diag3.bmp"
ts.WriteLine "capture err=" & Err.Number & " 文件=" & fso.FileExists("@@TMP@@\diag3.bmp")
doc.Close
ts.Close
WScript.Echo "done"
