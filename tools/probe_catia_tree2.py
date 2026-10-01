"""只读探针 2: 读活动文档里每个 pad 的草图/长度, 用来还原 CATIA 侧真实建出了什么。"""
import win32com.client as win32


def s(o, attr, default='?', sub=None):
    try:
        v = getattr(o, attr)
    except Exception:
        return default
    if sub is not None:
        try:
            v = getattr(v, sub)
        except Exception:
            return default
    try:
        return v
    except Exception:
        return default


catia = win32.GetActiveObject('CATIA.Application')
doc = catia.ActiveDocument
part = doc.Part
out = []
body = part.Bodies.Item(1)
shapes = body.Shapes
out.append('shapes=%d' % shapes.Count)
for i in range(1, shapes.Count + 1):
    f = shapes.Item(i)
    line = '[%2d] name=%r sketch=%r' % (i, s(f, 'Name', '?'), s(f, 'Sketch', '?', 'Name'))
    line += ' len=%s' % s(f, 'FirstLimit', '?', 'Length')
    line += ' secondlen=%s' % s(f, 'SecondLimit', '?', 'Length')
    try:
        sk = f.Sketch
        org = sk.AbsoluteAxis.Origin
        line += ' origin=(%.4f,%.4f,%.4f)' % (org[0], org[1], org[2])
        line += ' dir=(%.3f,%.3f,%.3f)' % tuple(sk.AbsoluteAxis.Direction)
    except Exception as e:
        line += ' axis=<%s>' % e
    try:
        line += ' status=%s' % f.Status
    except Exception:
        pass
    out.append(line)

hb = part.HybridBodies.Item(1)
out.append('')
out.append('hybridbody=%r shapes=%d' % (s(hb, 'Name'), hb.HybridShapes.Count))
for j in range(1, hb.HybridShapes.Count + 1):
    hs = hb.HybridShapes.Item(j)
    line = '  [%2d] %r' % (j, s(hs, 'Name'))
    try:
        line += ' offset=%s' % hs.Offset.Value
    except Exception:
        pass
    try:
        line += ' updatable=%s' % hs.IsUpToDate
    except Exception:
        pass
    out.append(line)

text = '\n'.join(out)
open('probe_catia_tree2.txt', 'w', encoding='utf-8').write(text)
print(text)
