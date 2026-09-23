# -*- coding: utf-8 -*-
r"""重拍真实零件照片（**带背景帧**），让"图像相似度"第一次真正可比。

为什么要重拍、为什么带背景帧：
    现有 518 张真实照片是 CATIA **整窗截图**（深红渐变背景 + 规格树 + 工具栏），
    零件边缘很弱、背景渐变带抖动。我拿它们做过标定：**真配对 85.3% / 交叉配对
    84.1%，只差 1.2 个点** —— 这指标没有区分能力，拿它当"相似度 ≥95%"就是假通过。

    与其去改 CATIA 的背景设置（V5 自动化不暴露这个接口），不如**每个零件拍两张**：
      ① 零件可见 → `<型号>.bmp`
      ② 把零件隐藏、其它一切不动 → `<型号>_bg.bmp`
    两张相减 = **零件的精确掩码**：规格树、工具栏、背景渐变全部抵消，
    不需要任何分割算法，也不受截图噪声影响。这一步做完，"图 vs 图"才真的可比。

用法（需要 CATIA 在运行；脚本会沿用已运行的实例）：
    python tools\reshoot_real_parts.py --limit 3              # 先试 3 个
    python tools\reshoot_real_parts.py --only 销座             # 只拍某族
    python tools\reshoot_real_parts.py                        # 全部（约 500 个，耗时较长）
输出:
    零件真实图片_带背景\<族>\<型号>.png       零件照片
    零件真实图片_带背景\<族>\<型号>_bg.png    隐藏零件后的背景帧
    kb/res hooot_index.json → 见下方 INDEX
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"C:\Users\25643\Desktop\零件参数"
OUT = os.path.join(ROOT, "零件真实图片_带背景")
TMP = os.path.join(os.environ.get("TEMP", "."), "catia_reshoot")
INDEX = os.path.join(ROOT, "kb", "reshoot_index.json")

VBS = r"""' AI Modeler - 重拍真实零件（零件帧 + 隐藏零件后的背景帧）
Option Explicit
Dim gFso, gLog
Set gFso = CreateObject("Scripting.FileSystemObject")
Dim gOutDir, gLogPath, gTmp
gOutDir = "@@OUT@@"
gLogPath = "@@LOG@@"
gTmp = "@@TMP@@"

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
    Dim catia, files, i, f, doc, viewer, outPath, bgPath, base, sub_, ok, fail
    Dim sel, tries, shot
    ok = 0 : fail = 0
    On Error Resume Next
    Set catia = GetObject(, "CATIA.Application")
    If Err.Number <> 0 Then
        Err.Clear
        Set catia = CreateObject("CATIA.Application")
    End If
    If Err.Number <> 0 Then
        Log "无法附着 CATIA: " & Err.Description
        WScript.Echo "FAIL CATIA"
        Exit Sub
    End If
    Err.Clear
    catia.DisplayFileAlerts = False
    catia.RefreshDisplay = False
    ' ⚠ 不要写 catia.Visible = True：CATIA 的 Application 没有这个属性，
    '   在 On Error Resume Next 下它会留下错误状态，连累后面 Documents.Open 报 438
    '   （实测："open err=438 对象不支持此属性或方法"，排查了很久）。
    ' 等 CATIA 真正就绪（刚启动时 Documents 会短暂不可用）
    Dim ready, ndoc, wait
    ready = False
    For wait = 1 To 30
        Err.Clear
        ndoc = catia.Documents.Count
        If Err.Number = 0 Then
            ready = True
            Exit For
        End If
        Err.Clear
        WScript.Sleep 2000
    Next
    Log "CATIA 版本 " & catia.Version & "  就绪=" & ready & "  已有文档 " & ndoc

    ' ⚠ 先关掉遗留文档：实测 CATIA 里当时开着 2 个文档，隐藏当前零件后再拍，
    '   截到的却是另一个窗口 —— 两张图差出一大片，掩码直接报废。
    Dim dn, dc, closed
    closed = 0
    For dn = catia.Documents.Count To 1 Step -1
        Err.Clear
        Set dc = catia.Documents.Item(dn)
        If Err.Number = 0 Then
            dc.Close
            closed = closed + 1
        End If
        Err.Clear
    Next
    Log "开拍前关闭遗留文档 " & closed & " 个"

    Set files = CreateObject("Scripting.Dictionary")
    i = 0
    Dim listFile, ts, line
    listFile = gTmp & "\files.txt"
    Set ts = gFso.OpenTextFile(listFile, 1)
    Do While Not ts.AtEndOfStream
        line = ts.ReadLine
        If Len(line) > 0 Then
            files.Add i, line
            i = i + 1
        End If
    Loop
    ts.Close
    Log "待处理 " & files.Count & " 个零件"

    Dim k
    For k = 0 To files.Count - 1
        f = files(k)
        base = gFso.GetBaseName(f)
        sub_ = gFso.GetFileName(gFso.GetParentFolderName(f))
        EnsureDir gTmp
        outPath = gTmp & "\" & base & ".bmp"
        bgPath = gTmp & "\" & base & "_bg.bmp"

        Err.Clear
        Set doc = catia.Documents.Open(f)
        If Err.Number <> 0 Then
            Log "打开失败 " & base & "  err=" & Err.Number & " : " & Err.Description & _
                "  路径存在=" & gFso.FileExists(f) & "  路径=" & f
            Err.Clear
            fail = fail + 1
        Else
            ' ⚠ 视向**不要**用自动化去设：
            '   · vp.PutSightDirection 在这台机器的 CATIA 上报 err=450
            '     "错误的参数个数或无效的参数属性值"（整数、小数、数组都试过）；
            '   · catia.StartCommand "Isometric View" 会把 CATIA 卡住（实测 10 分钟不返回）。
            '   所以这里只做 Reframe 取景，**视角用 CATIA 当前视图** ——
            '   跑之前请手动点一次"等轴测视图"(Isometric View)，518 张就统一了。
            '   文档必须 Activate，否则 catia.ActiveWindow.ActiveViewer 拿不到（截图为空）。
            doc.Activate
            Set viewer = catia.ActiveWindow.ActiveViewer
            viewer.Reframe
            catia.RefreshDisplay = True
            ' ① 零件可见
            doc.Activate
            catia.RefreshDisplay = True
            shot = False
            For tries = 1 To 4
                Err.Clear
                viewer.CaptureToFile 4, outPath
                If gFso.FileExists(outPath) Then
                    shot = True
                    Exit For
                End If
                WScript.Sleep 800
            Next
            If Not shot Then
                Log "截图为空 " & base
                fail = fail + 1
            Else
                ' ② 隐藏零件（其余不动）后再拍一张
                On Error Resume Next
                Set sel = doc.Selection
                sel.Clear
                sel.Add doc.Part
                sel.VisProperties.SetShow 1        ' 1 = 不显示
                Err.Clear
                doc.Activate
                catia.RefreshDisplay = True
                WScript.Sleep 300
                ' ⚠ 这里**不能**再 Reframe：零件已隐藏，Reframe 会把相机重新取景，
                '   两张图尺寸/位置就对不上了（相减会得到一圈假轮廓）。
                Dim t2
                For t2 = 1 To 3
                    viewer.CaptureToFile 4, bgPath
                    If gFso.FileExists(bgPath) Then Exit For
                Next
                sel.VisProperties.SetShow 0            ' 恢复显示
                sel.Clear
                On Error GoTo 0
                ok = ok + 1
            End If
            doc.Close
            Set doc = Nothing
        End If
        If (k + 1) Mod 10 = 0 Then Log "进度 " & (k + 1) & "/" & files.Count
    Next
    Log "完成 ok=" & ok & " fail=" & fail
    WScript.Echo "DONE ok=" & ok & " fail=" & fail
End Sub

Main
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--src", default=SRC)
    a = ap.parse_args()

    files = []
    for dp, _dn, fns in os.walk(a.src):
        if a.only and a.only not in dp:
            continue
        for fn in fns:
            if fn.lower().endswith(".catpart"):
                files.append(os.path.join(dp, fn))
    files.sort()
    if a.limit:
        files = files[:a.limit]
    print("待重拍 %d 个 CATPart" % len(files))
    if not files:
        return 1
    os.makedirs(TMP, exist_ok=True)
    listfile = os.path.join(TMP, "files.txt")
    with io.open(listfile, "w", encoding="gbk", errors="replace") as f:
        for p in files:
            f.write(p + "\n")
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%m%d_%H%M")
    log = os.path.join(TMP, "reshoot_%s.log" % stamp)
    vbs = os.path.join(TMP, "reshoot_%s.vbs" % stamp)
    text = (VBS.replace("@@OUT@@", OUT).replace("@@LOG@@", log)
            .replace("@@TMP@@", TMP))
    # ⚠ VBS 必须用 **GBK(ANSI)** 写：cscript 按系统 ANSI 代码页读脚本，
    #   用 ascii+replace 会把中文（路径、日志文字）全变成 "?" ——
    #   实测日志里只剩 "????"，连报错原因都看不出来。
    with io.open(vbs, "w", encoding="gbk", errors="replace") as f:
        f.write(text)
    t0 = time.time()
    print("调 CATIA 拍摄中…（每个零件约 10~20 秒）")
    try:
        p = subprocess.run(["cscript", "//nologo", vbs], capture_output=True,
                           timeout=max(1800, 40 * len(files)))
        out = p.stdout.decode("gbk", "replace")
    except subprocess.TimeoutExpired:
        out = "（cscript 超时，已拍到的部分仍然有效）"
    print("cscript: %s" % out.strip()[:200])

    # BMP → PNG，按族归档
    from PIL import Image
    idx = []
    n_ok = 0
    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        sub = os.path.basename(os.path.dirname(f))
        src_p = os.path.join(TMP, base + ".bmp")
        src_b = os.path.join(TMP, base + "_bg.bmp")
        rec = {"model": base, "group": sub, "src": f}
        if os.path.exists(src_p):
            d = os.path.join(OUT, sub)
            os.makedirs(d, exist_ok=True)
            png = os.path.join(d, base + ".png")
            bg = os.path.join(d, base + "_bg.png")
            Image.open(src_p).convert("RGB").save(png)
            rec["img"] = os.path.relpath(png, ROOT)
            if os.path.exists(src_b):
                Image.open(src_b).convert("RGB").save(bg)
                rec["bg"] = os.path.relpath(bg, ROOT)
            rec["ok"] = "bg" in rec
            n_ok += 1
            try:
                os.remove(src_p)
                if os.path.exists(src_b):
                    os.remove(src_b)
            except Exception:
                pass
        else:
            rec["ok"] = False
            rec["err"] = "没有拍到"
        idx.append(rec)
    with io.open(INDEX, "w", encoding="utf-8") as f:
        json.dump({"n": len(idx), "ok": n_ok, "items": idx}, f,
                  ensure_ascii=False, indent=1)
    print("拍到 %d/%d（带背景帧的才计入 ok）；用时 %.0fs" % (n_ok, len(files), time.time() - t0))
    print("→ %s、kb/reshoot_index.json" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
