# -*- coding: utf-8 -*-
r"""只读探针 3: 把 CATIA 里每个 Pad 的长度、草图圆心/半径读回来, 核对偏心有没有做出来。

用法: python tools\probe_catia_pads.py [文档名子串]
"""
from __future__ import annotations

import sys

import win32com.client as win32


def try_(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


def pad_len(f):
    for path in (lambda: f.FirstLimit.Dimension.Value,
                 lambda: f.FirstLimit.Length,
                 lambda: f.SecondLimit.Dimension.Value):
        v = try_(path, None)
        if v is not None:
            return v
    return None


def circles(sk):
    out = []
    els = try_(lambda: sk.GeometricElements, None)
    if els is None:
        return out
    for i in range(1, els.Count + 1):
        el = els.Item(i)
        c = try_(lambda: el.Circle2D, None)
        if c is None:
            continue
        cx = try_(lambda: c.Center.X, None)
        cy = try_(lambda: c.Center.Y, None)
        r = try_(lambda: c.Radius, None)
        out.append((cx, cy, r))
    return out


def dump(doc, detail):
    part = doc.Part
    lines = ['--- %s' % doc.Name]
    for bi in range(1, part.Bodies.Count + 1):
        body = part.Bodies.Item(bi)
        lines.append('    body %r shapes=%d' % (body.Name, body.Shapes.Count))
        for i in range(1, body.Shapes.Count + 1):
            f = body.Shapes.Item(i)
            sk = try_(lambda: f.Sketch, None)
            row = '      [%2d] len=%s' % (i, pad_len(f))
            if sk is not None and detail:
                cs = circles(sk)
                row += ' circles=%s' % ([(round(a, 3) if a is not None else None,
                                          round(b, 3) if b is not None else None,
                                          round(r, 3) if r is not None else None)
                                         for a, b, r in cs],)
            lines.append(row)
    bbox = None
    try:
        ref = part.CreateReferenceFromObject(part.MainBody)
        spa = win32.Dispatch(part.GetWorkbench('SPAWorkbench')) if False else None
    except Exception:
        pass
    return lines, bbox


def main():
    want = sys.argv[1].lower() if len(sys.argv) > 1 else ''
    catia = win32.GetActiveObject('CATIA.Application')
    docs = catia.Documents
    out = []
    for i in range(1, docs.Count + 1):
        doc = docs.Item(i)
        name = try_(lambda: doc.Name, '?')
        if want and want not in name.lower():
            continue
        try:
            lines, _ = dump(doc, detail=True)
        except Exception as e:
            lines = ['--- %s  <skip: %s>' % (name, e)]
        out.extend(lines)
    text = '\n'.join(out)
    open('probe_catia_pads.txt', 'w', encoding='utf-8').write(text)
    print(text)


if __name__ == '__main__':
    main()
