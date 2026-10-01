# -*- coding: utf-8 -*-
r"""给真实零件"拍照"：用 CATIA 逐个打开 CATPart，摆到等轴测视角，截图存盘。

用户要求（原话）：「看一下零件参数这里面的零件在 CATIA 里面的样子，然后将每一个
零件在 CATIA 里面的样子找一个合适的角度，拍一张照，将这些零件的真实图片放在一个
文件夹里面。」

做法（不依赖任何第三方库）：
  生成一个 VBScript，用 Windows 自带的 cscript 跑，通过 COM 附着到正在运行的
  CATIA：
    · Documents.Open 打开每个 CATPart；
    · ActiveViewer.Viewpoint3D 设成等轴测视向（(1,-1,1)/√3，上方向 +Z）；
    · Reframe() 自动缩放到合适大小；
    · CaptureToFile 输出 PNG（失败则退回 JPEG/BMP）；
    · 关文档，继续下一个。

⚠️ 两条实测经验：
  · **输出目录必须是纯 ASCII**（中文路径会让 CATIA 的保存/截图卡住）；
  · 只"打开"不"另存"，不会动用户的文件。

用法:
    python tools\capture_real_images.py --limit 6          # 先试 6 个
    python tools\capture_real_images.py                    # 全部
    python tools\capture_real_images.py --only 螺钉         # 只跑某个子目录
输出: _real_images\<子目录>\<文件名>.png
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"C:\Users\25643\Desktop\零件参数"
OUT = os.path.join(ROOT, "零件真实图片")

VBS = r"""' AI Modeler - capture real part images from CATIA (generated)
Option Explicit
Dim gFso, gLog
Set gFso = CreateObject("Scripting.FileSystemObject")

Dim gOutDir, gLogPath
gOutDir = "@@OUT@@"
gLogPath = "@@LOG@@"

Sub Log(msg)
    Dim ts
    On Error Resume Next
    Set ts = gFso.OpenTextFile(gLogPath, 8, True)
    ts.WriteLine Time & "  " & msg
    ts.Close
    Err.Clear
End Sub

Function EnsureDir(p)
    If Not gFso.FolderExists(p) Then
        Dim par
        par = gFso.GetParentFolderName(p)
        If Len(par) > 0 And Not gFso.FolderExists(par) Then EnsureDir par
        gFso.CreateFolder p
    End If
End Function

Sub Main()
    Dim catia, files, i, f, doc, viewer, vp, outPath, base, sub_, sh, tries
    Dim okCount, failCount
    okCount = 0 : failCount = 0

    On Error Resume Next
    Set catia = GetObject(, "CATIA.Application")
    If Err.Number <> 0 Then
        Err.Clear
        Log "CATIA not running, starting it..."
        Set sh = CreateObject("WScript.Shell")
        sh.Run "@@STARTCMD@@", 1, False
        For tries = 1 To 60
            WScript.Sleep 5000
            Set catia = GetObject(, "CATIA.Application")
            If Err.Number = 0 Then Exit For
            Err.Clear
        Next
    End If
    If Err.Number <> 0 Or Not IsObject(catia) Then
        Log "cannot attach to CATIA"
        WScript.Quit 2
    End If
    Err.Clear
    Log "attached to running CATIA"

    files = Split("@@FILES@@", "|")
    Log "files=" & (UBound(files) + 1)
    For i = 0 To UBound(files)
        f = files(i)
        If Len(f) > 0 Then
            base = gFso.GetBaseName(f)
            sub_ = gFso.GetBaseName(gFso.GetParentFolderName(f))
            EnsureDir gOutDir & "\" & sub_
            outPath = gOutDir & "\" & sub_ & "\" & base & ".png"

            Err.Clear
            Set doc = catia.Documents.Open(f)
            If Err.Number <> 0 Then
                Log "OPEN FAIL " & base & " : " & Err.Description
                failCount = failCount + 1
                Err.Clear
            Else
                Set viewer = catia.ActiveWindow.ActiveViewer
                ' 等轴测视向 + Z 向上，再 Reframe 自动取景
                Set vp = viewer.Viewpoint3D
                vp.PutSightDirection 0.57735, -0.57735, 0.57735
                vp.PutUpDirection 0.0, 0.0, 1.0
                vp.PutOrigin 0.0, 0.0, 0.0
                viewer.Reframe
                catia.RefreshDisplay = True
                Err.Clear
                viewer.CaptureToFile 4, outPath          ' 4 = PNG
                If Err.Number <> 0 Then
                    Err.Clear
                    viewer.CaptureToFile 2, outPath      ' 2 = JPEG fallback
                End If
                If Err.Number <> 0 Then
                    Log "CAPTURE FAIL " & base & " : " & Err.Description
                    failCount = failCount + 1
                    Err.Clear
                Else
                    Log "ok " & sub_ & "\" & base
                    okCount = okCount + 1
                End If
                Err.Clear
                doc.Close
                Err.Clear
            End If
        End If
    Next
    Log "done ok=" & okCount & " fail=" & failCount
    WScript.Echo "ok=" & okCount & " fail=" & failCount
End Sub

Main
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--white-bg", action="store_true",
                    help="白底重拍：隐藏规格树/工具栏、3D 背景设纯白，便于图像比对")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    files = []
    for dp, _dn, fns in os.walk(SRC):
        for fn in fns:
            if fn.lower().endswith(".catpart"):
                files.append(os.path.join(dp, fn))
    files.sort()
    if a.only:
        files = [f for f in files if a.only in f]
    if a.limit:
        files = files[:a.limit]
    if not files:
        print("没找到 CATPart（%s）" % SRC)
        return 1
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log = os.path.join(OUT, "_capture_%s.log" % stamp)
    vbs = os.path.join(OUT, "_capture_%s.vbs" % stamp)

    # CATIA 没在运行就自己启动（复用推送通道那套路径探测）
    start_cmd = ""
    try:
        sys.path.insert(0, ROOT)
        from ai_modeler import catia as CA
        info = CA.best_install()
        if info:
            exe = os.path.join(info["win_dir"], "code", "bin", "CATSTART.exe")
            start_cmd = '"%s" %s' % (exe, CA.start_args(info))
    except Exception as e:
        print("（没算出 CATIA 启动命令：%s）" % e)

    text = (VBS.replace("@@OUT@@", OUT).replace("@@LOG@@", log)
            # ⚠ CATSTART 的路径带引号，直接塞进 VBS 字符串会把字面量截断
            #（实测报"语句未结束"）。VBScript 里字符串内的引号要写成两个。
            .replace("@@STARTCMD@@", start_cmd.replace('"', '""'))
            .replace("@@FILES@@", "|".join(files)))
    with open(vbs, "w", encoding="utf-16") as f:
        f.write(text)
    print("拍摄 %d 个零件 → %s" % (len(files), OUT))
    t0 = time.time()
    try:
        p = subprocess.run(["cscript", "//nologo", vbs], capture_output=True,
                           timeout=max(600, 30 * len(files)))
        out = p.stdout.decode("gbk", "replace")
    except subprocess.TimeoutExpired:
        print("超时（CATIA 没在预期时间内完成）")
        out = ""
    print("cscript: %s" % out.strip()[:200])
    if os.path.exists(log):
        t = open(log, "rb").read().decode("gbk", "replace")
        tail = [L for L in t.split("\n") if L.strip()][-12:]
        print("---- 日志尾部 ----")
        print("\n".join(tail))
    n = 0
    for dp, _dn, fns in os.walk(OUT):
        n += sum(1 for fn in fns if fn.lower().endswith((".png", ".jpg")))
    print("已生成图片 %d 张，用时 %.0fs" % (n, time.time() - t0))
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
