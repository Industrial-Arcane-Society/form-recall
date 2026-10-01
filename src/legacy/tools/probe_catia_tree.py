"""只读探针: 连上正在运行的 CATIA, 导出活动文档的特征树, 并再试一次 Part.Update。

仅用于诊断「推送到 CATIA 失败」时 CATIA 侧到底停在哪一步。
不改模型、不保存。用法: python tools/probe_catia_tree.py
"""
import sys

import win32com.client as win32


def nm(o, default=''):
    try:
        return str(o.Name)
    except Exception:
        return default


def ty(o):
    try:
        return str(o.Type)
    except Exception:
        return '?'


def dump_body(body, out, depth=1):
    out.append('%sBODY %s (%s)' % ('  ' * depth, nm(body), ty(body)))
    try:
        shapes = body.Shapes
        n = shapes.Count
    except Exception as e:
        out.append('%s  <shapes unreadable: %s>' % ('  ' * depth, e))
        return
    out.append('%s  shapes=%d' % ('  ' * depth, n))
    for i in range(1, n + 1):
        try:
            f = shapes.Item(i)
        except Exception as e:
            out.append('%s  [%d] <unreadable %s>' % ('  ' * depth, i, e))
            continue
        line = '%s  [%d] %-16s %s' % ('  ' * depth, i, ty(f), nm(f))
        try:
            sk = f.Sketch
            line += '  sketch=%s' % nm(sk)
        except Exception:
            pass
        out.append(line)


def main():
    catia = win32.GetActiveObject('CATIA.Application')
    out = []
    try:
        out.append('CATIA %s' % catia.SystemConfiguration)
    except Exception:
        out.append('CATIA <version unavailable>')
    docs = catia.Documents
    out.append('documents=%d' % docs.Count)
    for i in range(1, docs.Count + 1):
        out.append('  doc[%d] %s' % (i, nm(docs.Item(i))))
    doc = catia.ActiveDocument
    out.append('active=%s' % nm(doc))
    part = doc.Part
    out.append('part=%s' % nm(part))
    try:
        out.append('inwork=%s' % nm(part.InWorkObject))
    except Exception as e:
        out.append('inwork=<%s>' % e)

    try:
        bodies = part.Bodies
        out.append('bodies=%d' % bodies.Count)
        for i in range(1, bodies.Count + 1):
            dump_body(bodies.Item(i), out)
    except Exception as e:
        out.append('bodies error: %s' % e)

    try:
        hbs = part.HybridBodies
        out.append('hybridbodies=%d' % hbs.Count)
        for i in range(1, hbs.Count + 1):
            hb = hbs.Item(i)
            out.append('  HB %s' % nm(hb))
            for j in range(1, hb.HybridShapes.Count + 1):
                s = hb.HybridShapes.Item(j)
                out.append('    [%d] %-24s %s' % (j, ty(s), nm(s)))
    except Exception as e:
        out.append('hybrid error: %s' % e)

    try:
        part.Update()
        out.append('part.Update() -> OK')
    except Exception as e:
        out.append('part.Update() -> FAILED: %s' % e)

    try:
        spa = part.GetWorkbench('SPAWorkbench')
        ref = part.CreateReferenceFromObject(part.MainBody)
        out.append('volume=%s' % spa.GetMeasurable(ref).Volume)
    except Exception as e:
        out.append('volume error: %s' % e)

    text = '\n'.join(out)
    with open('probe_catia_tree.txt', 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(text[:4000])


if __name__ == '__main__':
    sys.exit(main())
