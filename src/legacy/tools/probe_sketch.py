# -*- coding: utf-8 -*-
r"""只读探针 6: 打开某个 Pad 的草图(OpenEdition), 读回圆心/半径, 再关掉不保存。

用途: 核对"草图第二个坐标"落在全局哪个轴(偏心有没有做出来)。
用法: python tools\probe_sketch.py Part8.CATPart 1
"""
from __future__ import annotations

import sys

import win32com.client as win32


def t(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default if default is not None else '<%s>' % e


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else 'Part8.CATPart'
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    catia = win32.GetActiveObject('CATIA.Application')
    docs = catia.Documents
    doc = None
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        if t(lambda: d.Name, '') == want:
            doc = d
            break
    if doc is None:
        print('没找到 %s' % want)
        return 1
    part = doc.Part
    body = part.Bodies.Item(1)
    pad = body.Shapes.Item(idx)
    sk = pad.Sketch
    print('pad=%s sketch=%s' % (t(lambda: pad.Name), t(lambda: sk.Name)))
    for label, fn in (('FirstLimit', lambda: pad.FirstLimit.Dimension.Value),
                      ('SecondLimit', lambda: pad.SecondLimit.Dimension.Value)):
        print('  %s = %s' % (label, t(fn)))
    ok = t(lambda: sk.OpenEdition(), None)
    print('  OpenEdition -> %s' % ok)
    try:
        els = sk.GeometricElements
        print('  GeometricElements=%d' % els.Count)
        for i in range(1, els.Count + 1):
            el = els.Item(i)
            c = t(lambda: el.Circle2D, None)
            if isinstance(c, str):
                print('    [%d] %s' % (i, c))
                continue
            print('    [%d] circle center=(%s, %s) r=%s' % (
                i, t(lambda: c.Center.X), t(lambda: c.Center.Y), t(lambda: c.Radius)))
    except Exception as e:
        print('  GeometricElements failed: %s' % e)
    try:
        sk.CloseEdition()
        print('  CloseEdition ok')
    except Exception as e:
        print('  CloseEdition failed: %s' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
