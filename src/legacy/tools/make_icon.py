# -*- coding: utf-8 -*-
"""生成程序图标 (纯标准库: 自写光栅化 + PNG 编码 + ICO 封装)。

用法:  python tools/make_icon.py
输出:  assets/app.ico, assets/app.png
"""
from __future__ import annotations

import math
import os
import struct
import sys
import zlib

W = H = 256
SS = 3  # 超采样倍数


def blend(dst, idx, rgb, a):
    if a <= 0:
        return
    a = min(1.0, a)
    r, g, b = dst[idx], dst[idx + 1], dst[idx + 2]
    dst[idx] = r + (rgb[0] - r) * a
    dst[idx + 1] = g + (rgb[1] - g) * a
    dst[idx + 2] = b + (rgb[2] - b) * a
    dst[idx + 3] = max(dst[idx + 3], int(255 * a))


def fill_poly(buf, w, h, pts, rgb, alpha=1.0):
    if len(pts) < 3:
        return
    ys = [p[1] for p in pts]
    y0 = max(0, int(math.floor(min(ys))))
    y1 = min(h - 1, int(math.ceil(max(ys))))
    n = len(pts)
    for y in range(y0, y1 + 1):
        cy = y + 0.5
        xs = []
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            if (ay <= cy < by) or (by <= cy < ay):
                t = (cy - ay) / (by - ay)
                xs.append(ax + t * (bx - ax))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            xa = max(0, int(math.ceil(xs[k] - 0.5)))
            xb = min(w - 1, int(math.floor(xs[k + 1] - 0.5)))
            for x in range(xa, xb + 1):
                blend(buf, (y * w + x) * 4, rgb, alpha)


def rounded_rect(buf, w, h, x0, y0, x1, y1, r, rgb_top, rgb_bot):
    for y in range(int(y0), int(y1) + 1):
        for x in range(int(x0), int(x1) + 1):
            dx = 0.0
            dy = 0.0
            if x < x0 + r:
                dx = (x0 + r) - x
            elif x > x1 - r:
                dx = x - (x1 - r)
            if y < y0 + r:
                dy = (y0 + r) - y
            elif y > y1 - r:
                dy = y - (y1 - r)
            if dx * dx + dy * dy > r * r:
                continue
            t = (y - y0) / max(1.0, (y1 - y0))
            col = (rgb_top[0] + (rgb_bot[0] - rgb_top[0]) * t,
                   rgb_top[1] + (rgb_bot[1] - rgb_top[1]) * t,
                   rgb_top[2] + (rgb_bot[2] - rgb_top[2]) * t)
            blend(buf, (y * w + x) * 4, col, 1.0)


def build(size=W, ss=SS):
    w = h = size
    buf = [0.0] * (w * h * 4)
    rounded_rect(buf, w, h, 8, 8, w - 8, h - 8, 46,
                 (0.208, 0.255, 0.322), (0.118, 0.145, 0.196))
    # 等轴测立方体
    cx, cy = w * 0.5, h * 0.53
    s = w * 0.30
    t = w * 0.155
    top = (cx, cy - t * 1.42)
    right = (cx + s, cy - t * 0.42)
    mid = (cx, cy + t * 0.58)
    left = (cx - s, cy - t * 0.42)
    down = t * 1.46
    fill_poly(buf, w, h, [top, right, mid, left], (0.545, 0.714, 0.914))
    fill_poly(buf, w, h, [left, mid, (mid[0], mid[1] + down), (left[0], left[1] + down)],
              (0.208, 0.451, 0.729))
    fill_poly(buf, w, h, [right, mid, (mid[0], mid[1] + down), (right[0], right[1] + down)],
              (0.122, 0.302, 0.545))
    # 高光
    fill_poly(buf, w, h, [(cx, cy - t * 1.42), (cx + s * 0.55, cy - t * 0.83),
                          (cx, cy - t * 0.42), (cx - s * 0.55, cy - t * 0.83)],
              (0.741, 0.847, 0.965), 0.55)
    # 顶部小火花 (AI 点)
    spark = (w * 0.775, h * 0.245)
    for (rx, ry) in ((0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0),
                     (0.71, 0.71), (0.71, -0.71), (-0.71, 0.71), (-0.71, -0.71)):
        fill_poly(buf, w, h, [
            (spark[0] + rx * 34, spark[1] + ry * 34),
            (spark[0] + ry * 9, spark[1] - rx * 9),
            (spark[0] - rx * 34, spark[1] - ry * 34),
            (spark[0] - ry * 9, spark[1] + rx * 9)],
            (0.98, 0.78, 0.28), 0.95)
    out = bytearray(len(buf))
    for i in range(0, len(buf), 4):
        out[i] = int(max(0.0, min(1.0, buf[i])) * 255)
        out[i + 1] = int(max(0.0, min(1.0, buf[i + 1])) * 255)
        out[i + 2] = int(max(0.0, min(1.0, buf[i + 2])) * 255)
        out[i + 3] = int(max(0.0, min(255.0, buf[i + 3])))
    return bytes(out)


def write_png(path, w, h, rgba):
    raw = b''.join(b'\x00' + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)
    return png


def write_ico(path, png_by_size):
    sizes = sorted(png_by_size.keys())
    header = struct.pack('<HHH', 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b''
    data = b''
    for s in sizes:
        blob = png_by_size[s]
        entries += struct.pack('<BBBBHHII', s if s < 256 else 0, s if s < 256 else 0,
                               0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        data += blob
    with open(path, 'wb') as f:
        f.write(header + entries + data)
    return path


def downscale(rgba, size, factor):
    n = size // factor
    out = bytearray(n * n * 4)
    for y in range(n):
        for x in range(n):
            r = g = b = a = 0
            for dy in range(factor):
                for dx in range(factor):
                    i = ((y * factor + dy) * size + (x * factor + dx)) * 4
                    r += rgba[i]
                    g += rgba[i + 1]
                    b += rgba[i + 2]
                    a += rgba[i + 3]
            k = factor * factor
            o = (y * n + x) * 4
            out[o] = r // k
            out[o + 1] = g // k
            out[o + 2] = b // k
            out[o + 3] = a // k
    return bytes(out)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, 'assets')
    os.makedirs(assets, exist_ok=True)
    rgba = build()
    big = write_png(os.path.join(assets, 'app.png'), W, H, rgba)
    pngs = {256: big}
    for f in (4, 8):   # 64, 32
        s = W // f
        pngs[s] = write_png(os.path.join(assets, '_tmp_%d.png' % s), s, s,
                            downscale(rgba, W, f))
    s = W // 16
    pngs[s] = write_png(os.path.join(assets, '_tmp_%d.png' % s), s, s, downscale(rgba, W, 16))
    for f in os.listdir(assets):
        if f.startswith('_tmp_'):
            os.remove(os.path.join(assets, f))
    ico = write_ico(os.path.join(assets, 'app.ico'), pngs)
    print('生成图标: %s (%d 字节)' % (ico, os.path.getsize(ico)))
    print('生成预览: %s (%d 字节)' % (os.path.join(assets, 'app.png'),
                                     os.path.getsize(os.path.join(assets, 'app.png'))))


if __name__ == '__main__':
    main()
