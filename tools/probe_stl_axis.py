# -*- coding: utf-8 -*-
r"""只读探针 8: 先把零件另存为临时 CATPart(未命名文档 ExportData 会失败), 再导出 STL,
量网格的 X/Y/Z 范围 —— 用来确定偏心到底落在全局哪个轴上。

用法: python tools\probe_stl_axis.py Part8.CATPart
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'artifacts', 'crank_probe')

VBS = r'''Dim C, D, i, fso
Set fso = CreateObject("Scripting.FileSystemObject")
Set C = GetObject(, "CATIA.Application")
For i = 1 To C.Documents.Count
    If LCase(C.Documents.Item(i).Name) = LCase("@@DOC@@") Then Set D = C.Documents.Item(i)
Next
If D Is Nothing Then WScript.Echo "NO_DOC": WScript.Quit 2
On Error Resume Next
D.SaveAs "@@CATPART@@"
If Err.Number <> 0 Then
    WScript.Echo "SAVEAS_FAILED " & Err.Description
    Err.Clear
End If
If fso.FileExists("@@STL@@") Then fso.DeleteFile "@@STL@@", True
D.ExportData "@@STL@@", "stl"
If Err.Number <> 0 Then
    WScript.Echo "EXPORT_FAILED " & Err.Description
    WScript.Quit 4
End If
WScript.Echo "OK"
'''


def binary_stl(path):
    raw = open(path, 'rb').read()
    n = struct.unpack('<I', raw[80:84])[0]
    rec = np.frombuffer(raw[84:84 + n * 50], dtype=np.uint8).reshape(n, 50)
    return rec[:, 12:48].copy().view('<f4').reshape(-1, 3)


def main():
    doc = sys.argv[1] if len(sys.argv) > 1 else 'Part8.CATPart'
    stem = doc.replace('.', '_')
    catpart = os.path.join(OUT, 'stl_%s.CATPart' % stem)
    stl = os.path.join(OUT, 'stl_%s.stl' % stem)
    vbs = os.path.join(OUT, 'stl_%s.vbs' % stem)
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(VBS.replace('@@DOC@@', doc).replace('@@CATPART@@', catpart).replace('@@STL@@', stl))
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, text=True,
                       errors='replace', timeout=300)
    print('vbs rc=%s out=%r' % (p.returncode, (p.stdout or '').strip()[:200]))
    if not os.path.exists(stl):
        print('没有 STL')
        return 1
    v = binary_stl(stl)
    print('doc=%s  verts=%d' % (doc, len(v)))
    print('  X %9.3f .. %9.3f' % (v[:, 0].min(), v[:, 0].max()))
    print('  Y %9.3f .. %9.3f   <-- Y 有偏心就落在这里' % (v[:, 1].min(), v[:, 1].max()))
    print('  Z %9.3f .. %9.3f' % (v[:, 2].min(), v[:, 2].max()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
