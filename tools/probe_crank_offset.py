# -*- coding: utf-8 -*-
r"""只读探针 4: 单个"偏心连杆颈"凸台推到 CATIA, 量它落在哪个轴上、偏了多少。

判据: 连杆颈 r=21、偏心 =+43(沿草图第二个坐标) —— 若 bbox 的 Y 是 [22,64]、
Z 是 [-21,21], 说明偏心做在全局 Y 上(与内核 make_crankshaft 的 dy 一致);
若反了(Y=[-21,21], Z=[22,64])则导出把偏心放错了轴。

用法: python tools\probe_crank_offset.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ai_modeler.catia_backend import catia_export as CE      # noqa: E402

OUT = os.path.join(ROOT, 'artifacts', 'crank_probe')

BBOX_VBS = r'''Dim CATIA, doc, part, spa, ref, m, bb, i, s
Set CATIA = GetObject(, "CATIA.Application")
Set doc = CATIA.ActiveDocument
Set part = doc.Part
Set ref = part.CreateReferenceFromObject(part.MainBody)
Set spa = part.GetWorkbench("SPAWorkbench")
Set m = spa.GetMeasurable(ref)
bb = m.GetBoundingBox
s = doc.Name
For i = 0 To 5
    s = s & "|" & FormatNumber(bb(i), 4)
Next
Dim ts
Set ts = CreateObject("Scripting.FileSystemObject").OpenTextFile("@@OUT@@", 8, True)
ts.WriteLine s
ts.Close
'''


class _Doc:
    name = 'CrankOffset'
    source_prompt = 'crank pin offset probe'


def push(tag, entries):
    vbs = os.path.join(OUT, 'off_%s.vbs' % tag)
    log = os.path.join(OUT, 'off_%s.log' % tag)
    io = os.path.join(OUT, 'off_%s.bbox' % tag)
    for p in (log, io):
        if os.path.exists(p):
            os.remove(p)
    with open(vbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(CE.render_push_vbs(_Doc(), entries, [], out_catpart='', start_cmd='',
                                   wait_sec=180, log_path=log))
    p = subprocess.run(['cscript', '//nologo', vbs], capture_output=True, text=True,
                       errors='replace', timeout=300)
    print('  push rc=%s %r' % (p.returncode, (p.stdout or '').strip()[:120]))
    bvbs = os.path.join(OUT, 'bbox_%s.vbs' % tag)
    with open(bvbs, 'w', encoding='utf-16', newline='\r\n') as f:
        f.write(BBOX_VBS.replace('@@OUT@@', io))
    time.sleep(1)
    p = subprocess.run(['cscript', '//nologo', bvbs], capture_output=True, text=True,
                       errors='replace', timeout=300)
    time.sleep(1)
    txt = open(io, encoding='utf-8').read().strip() if os.path.exists(io) else '<no bbox>'
    print('  bbox %s' % txt)
    if os.path.exists(log):
        raw = open(log, 'rb').read().decode('gbk', 'replace')
        for ln in raw.splitlines():
            if 'features done' in ln or 'FAILED' in ln:
                print('  ' + ln.strip())


def main():
    os.makedirs(OUT, exist_ok=True)
    # 记录格式: kind|cx|cy|z|n1|n2|n3|n4|name|profile|axis
    pin = dict(kind='circle_pad', plane='X', cx=43.0, cy=0.0, z=0.0, r=21.0, h=40.0,
               name='pin', note='')
    main = dict(kind='circle_pad', plane='X', cx=0.0, cy=0.0, z=0.0, r=25.0, h=40.0,
                name='main', note='')
    print('pin-only (偏心 +43 应落在 Y):')
    push('pin', [pin])
    print('main-only (全在轴线上, 作为对照):')
    push('main', [main])
    return 0


if __name__ == '__main__':
    sys.exit(main())
