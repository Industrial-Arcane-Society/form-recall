# -*- coding: utf-8 -*-
r"""批量给零件建模结果"截图"。

流程走的是**和界面完全一样的链路**: 中文描述 -> nlparser 解析 -> docmodel 建模
-> 网格 -> 软件渲染 -> PNG。所以这些图就是程序真实建出来的样子。

输出:
    零件截图\<序号>_<零件名>.png   每个零件一张四视图 (等轴测 / 前 / 右 / 俯)
    零件截图\总览.png              所有零件的缩略图拼版
    零件截图\索引.md               文件名 -> 零件名 / 描述 / 外形尺寸
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from ai_modeler import kernel as K          # noqa: E402
from ai_modeler import nlparser as NLP      # noqa: E402
from ai_modeler import partlib as PL        # noqa: E402
from ai_modeler.nlparser import CAPS        # noqa: E402
from ai_modeler import softrender as render  # noqa: E402

OUT_DIR = os.path.join(ROOT, '零件截图')
VIEWS = ('iso', 'front', 'right', 'top')
TILE = (330, 260)
THUMB = (200, 150)

# 除零件库之外, 再补几个通用模板做出来的经典件
EXTRA = [
    ('整体叶盘', '叶盘 外径220 厚30 叶片数24 叶展45'),
    ('阶梯轴', '阶梯轴 直径40 长60, 直径30 长50, 直径20 长40'),
    ('带孔底板', '底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12'),
    ('圆角方块', '画一个 120×80×15 的长方体, 四角圆角 R8, 中心打一个直径 20 的通孔'),
    ('法兰盘', '法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120'),
    ('单个叶片', '叶片 展长 90 弦长 50 45'),
]


def phrase_for(cat):
    d = PL.PARTS[cat]
    p = dict(PL.TYPICAL.get(cat, {}))
    if not p:
        return None
    bits = []
    for k, v in p.items():
        if k not in d['params']:
            continue
        bits.append('%s%g' % (d['params'][k][0], v))
    if not bits:
        return None
    return '%s %s' % (cat, ' '.join(bits))


def build(phrase):
    """中文 -> 模型网格 (走真实解析链路)。"""
    res = NLP.parse(phrase)
    body = res.doc.build(deadline=time.time() + 20)
    polys = body[0] if isinstance(body, tuple) else body
    return polys, res


def four_view(polys, path):
    """把四个视角拼成一张 2x2 的图。"""
    tw, th = TILE
    W, H = tw * 2, th * 2
    canvas = bytearray(W * H * 3)
    for idx, v in enumerate(VIEWS):
        r = render.Renderer(tw, th)
        r.render(polys, view=v)
        gx, gy = (idx % 2) * tw, (idx // 2) * th
        for y in range(th):
            s = y * tw * 3
            d = ((gy + y) * W + gx) * 3
            canvas[d:d + tw * 3] = r.fb[s:s + tw * 3]
        for x in range(tw):
            o = ((gy + th - 1) * W + gx + x) * 3
            canvas[o:o + 3] = b'\x2f\x38\x46'
        for y in range(th):
            o = ((gy + y) * W + gx + tw - 1) * 3
            canvas[o:o + 3] = b'\x2f\x38\x46'
    render.write_png(path, W, H, bytes(canvas))
    return True


def thumb(polys, tw, th):
    r = render.Renderer(tw, th)
    r.render(polys, view='iso', edges=False)
    return r.fb


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    CAPS['partlib'] = True
    CAPS['synonyms'] = True

    jobs = []
    for cat in PL.PARTS:
        ph = phrase_for(cat)
        if ph:
            jobs.append((cat, ph, '零件库'))
    jobs.extend([(n, p, '通用模板') for (n, p) in EXTRA])

    index = []
    thumbs = []
    t0 = time.time()
    for i, (name, phrase, src_) in enumerate(jobs, 1):
        try:
            polys, res = build(phrase)
            if not polys:
                print('  [跳过] %-14s 没建出模型' % name, flush=True)
                continue
            bb = K.bbox(polys)
            dim = (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
            safe = ''.join(ch for ch in name if ch not in r'\/:*?"<>|')
            fn = '%02d_%s.png' % (i, safe)
            four_view(polys, os.path.join(OUT_DIR, fn))
            thumbs.append((i, name, thumb(polys, *THUMB)))
            index.append((fn, name, src_, dim, len(polys), phrase))
            print('  [%2d/%d] %-14s 面=%-6d %.1f x %.1f x %.1f' %
                  (i, len(jobs), name, len(polys), dim[0], dim[1], dim[2]), flush=True)
        except Exception as exc:
            print('  [失败] %-14s %s' % (name, str(exc)[:70]), flush=True)

    if thumbs:
        cols = 6
        rows = (len(thumbs) + cols - 1) // cols
        tw, th = THUMB
        W, H = cols * tw, rows * th
        canvas = bytearray(b'\x11\x16\x1d' * (W * H))
        for k, (_i, _n, fb) in enumerate(thumbs):
            gx, gy = (k % cols) * tw, (k // cols) * th
            for y in range(th):
                s = y * tw * 3
                d = ((gy + y) * W + gx) * 3
                canvas[d:d + tw * 3] = fb[s:s + tw * 3]
        render.write_png(os.path.join(OUT_DIR, '总览.png'), W, H, bytes(canvas))

    T = chr(96)
    lines = ['# 零件截图索引', '',
             '由 ' + T + 'tools/make_shots.py' + T + ' 自动生成 -- 每张图都是**程序真实建出来的模型**',
             '(中文描述 -> 解析 -> 建模 -> 渲染), 不是借来的图片。', '',
             '每张图是四视图拼版: **左上 等轴测 / 右上 前视 / 左下 右视 / 右下 俯视**', '',
             '| 图片 | 零件 | 来源 | 外形尺寸 (mm) | 面片数 | 建模用的中文描述 |',
             '|---|---|---|---|---|---|']
    for fn, name, src_, dim, nface, phrase in index:
        lines.append('| ' + T + fn + T + ' | **%s** | %s | %.1f x %.1f x %.1f | %d | ' % (
            name, src_, dim[0], dim[1], dim[2], nface) + T + phrase + T + ' |')
    lines += ['', '另外 ' + T + '总览.png' + T + ' 是所有零件的缩略图拼版。', '']
    open(os.path.join(OUT_DIR, '索引.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print('\n共 %d 个零件截图, 用时 %.0f 秒 -> %s' % (len(index), time.time() - t0, OUT_DIR))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
