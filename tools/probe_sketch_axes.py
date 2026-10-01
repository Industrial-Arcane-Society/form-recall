# -*- coding: utf-8 -*-
r"""只读探针 7: 用 VBS(和助手核心同一套 late-binding 调用)读草图圆心与基准坐标系。

用法: python tools\probe_sketch_axes.py Part8.CATPart 1
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'artifacts', 'crank_probe')

VBS = r'''Dim C, D, P, B, S, A, O, E, G, i, ts, fso
Set fso = CreateObject("Scripting.FileSystemObject")
Set ts = fso.OpenTextFile("@@OUT@@", 2, True)
On Error Resume Next
Set C = GetObject(, "CATIA.Application")
For i = 1 To C.Documents.Count
    If LCase(C.Documents.Item(i).Name) = LCase("@@DOC@@") Then Set D = C.Documents.Item(i)
Next
If D Is Nothing Then
    ts.WriteLine "NO_DOC": ts.Close: WScript.Quit 2
End If
Set P = D.Part
Set B = P.Bodies.Item(1)
Set S = B.Shapes.Item(@@IDX@@).Sketch
ts.WriteLine "sketch=" & S.Name
ts.WriteLine "openedition err=" & Err.Number & " " & Err.Description
Err.Clear
Set G = S.GeometricElements
ts.WriteLine "elements err=" & Err.Number & " " & Err.Description
If Err.Number = 0 Then
    ts.WriteLine "elements = " & G.Count
    For i = 1 To G.Count
        Set E = G.Item(i)
        Err.Clear
        Set O = E.Circle2D
        If Err.Number = 0 Then
            ts.WriteLine "  circle r=" & O.Radius & " center=(" & O.Center.X & ", " & O.Center.Y & ")"
        Else
            ts.WriteLine "  element " & i & " not a circle: " & Err.Description
        End If
        Err.Clear
    Next
End If
Err.Clear
Set A = S.AbsoluteAxis
If Err.Number = 0 Then
    O = A.Origin
    ts.WriteLine "origin = " & O(0) & ", " & O(1) & ", " & O(2)
    O = A.XAxisDirection
    ts.WriteLine "xaxis  = " & O(0) & ", " & O(1) & ", " & O(2)
    O = A.YAxisDirection
    ts.WriteLine "yaxis  = " & O(0) & ", " & O(1) & ", " & O(2)
Else
    ts.WriteLine "AbsoluteAxis err=" & Err.Number & " " & Err.Description
End If
Err.Clear
ts.Close
WScript.Echo "OK"
'''


def run(doc, idx):
    stem = '%s_%d' % (doc.replace('.', '_'), idx)
    out = os.path.join(OUT, 'sketch_%s.txt' % stem)
    vbs = os.path.join(OUT, 'sketch_%s.vbs' % stem)
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(VBS.replace('@@OUT@@', out).replace('@@DOC@@', doc).replace('@@IDX@@', str(idx)))
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, text=True,
                       errors='replace', timeout=300)
    print('=== %s #%d  rc=%s %r' % (doc, idx, p.returncode, (p.stdout or '').strip()[:80]))
    if os.path.exists(out):
        print(open(out, 'rb').read().decode('gbk', 'replace').strip())


def main():
    pairs = [('Part8.CATPart', 1), ('Part9.CATPart', 1)]
    if len(sys.argv) > 2:
        pairs = [(sys.argv[1], int(sys.argv[2]))]
    for doc, idx in pairs:
        run(doc, idx)
    return 0


if __name__ == '__main__':
    sys.exit(main())
