# -*- coding: utf-8 -*-
r"""只读探针 9: 量某个零件的体积与重心(Measurable.Volume / GetCOG, 本机可用)。

用途: 本机 GetBoundingBox/STL 导出都不可用, 但 Volume+GetCOG 可用 ——
用一个"只在草图第 2 个坐标上偏 43mm 的单凸台"零件量重心,
就能确定偏心落在全局哪个轴(Y 还是 Z)。

用法: python tools\probe_catia_cog.py Part8.CATPart
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'artifacts', 'crank_probe')

VBS = r'''Dim C, D, P, B, R, W, M, ts, i, fso, s, cg(2)
Set fso = CreateObject("Scripting.FileSystemObject")
Set ts = fso.OpenTextFile("@@OUT@@", 2, True)
On Error Resume Next
Set C = GetObject(, "CATIA.Application")
For i = 1 To C.Documents.Count
    If LCase(C.Documents.Item(i).Name) = LCase("@@DOC@@") Then Set D = C.Documents.Item(i)
Next
If D Is Nothing Then ts.WriteLine "NO_DOC": ts.Close: WScript.Quit 2
Set P = D.Part
Set B = P.Bodies.Item(1)
ts.WriteLine "part=" & P.Name & "  body=" & B.Name & "  shapes=" & B.Shapes.Count
Set R = P.CreateReferenceFromObject(B)
Set W = P.GetWorkbench("SPAWorkbench")
ts.WriteLine "getworkbench err=" & Err.Number & " " & Err.Description
If Err.Number <> 0 Then
    Err.Clear
    Set W = D.GetWorkbench("SPAWorkbench")
    ts.WriteLine "  fallback doc workbench err=" & Err.Number & " " & Err.Description
End If
Set M = W.GetMeasurable(R)
ts.WriteLine "getmeasurable err=" & Err.Number & " " & Err.Description
Err.Clear
s = M.Volume
ts.WriteLine "volume(m^3)=" & s & " err=" & Err.Number
ts.WriteLine "volume(mm^3)=" & (s * 1000000000)
Err.Clear
M.GetCOG cg
If Err.Number = 0 Then
    ts.WriteLine "cog(mm) = " & cg(0) & ", " & cg(1) & ", " & cg(2)
Else
    ts.WriteLine "GetCOG err=" & Err.Number & " " & Err.Description
End If
ts.Close
WScript.Echo "OK"
'''


def run(doc):
    out = os.path.join(OUT, 'cog_%s.txt' % doc.replace('.', '_'))
    vbs = os.path.join(OUT, 'cog_%s.vbs' % doc.replace('.', '_'))
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(VBS.replace('@@OUT@@', out).replace('@@DOC@@', doc))
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, text=True,
                       errors='replace', timeout=300)
    print('=== %s rc=%s %r' % (doc, p.returncode, (p.stdout or '').strip()[:60]))
    if os.path.exists(out):
        print(open(out, 'rb').read().decode('gbk', 'replace').strip())


def main():
    for doc in (sys.argv[1:] or ['Part8.CATPart', 'Part9.CATPart']):
        run(doc)
    return 0


if __name__ == '__main__':
    sys.exit(main())

