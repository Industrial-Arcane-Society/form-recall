# -*- coding: utf-8 -*-
r"""把 CATIA 截出来的 BMP 转成 PNG（纯标准库，不依赖 Pillow）。

为什么需要：CATIA 的 `CaptureToFile` 格式枚举实测 —— 传 4 出来的是 **BMP**
（不是我预期的 PNG），于是 518 张图全挂着 `.png` 的后缀却是 BMP 内容。
看图的人会遇到"打不开/格式不对"，所以这里按真实格式改名并转码。

支持 24 位与 32 位未压缩 BMP（CATIA 截屏就是这两种），其余情况如实报错跳过。

用法: python tools\bmp_to_png.py [目录]
"""
from __future__ import annotations

import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_bmp(path):
    raw = open(path, "rb").read()
    if raw[:2] != b"BM":
        raise ValueError("不是 BMP")
    off = struct.unpack_from("<I", raw, 10)[0]
    hdr = struct.unpack_from("<I", raw, 14)[0]
    if hdr < 40:
        raise ValueError("BMP 头类型不支持(%d)" % hdr)
    w, h, planes, bpp = struct.unpack_from("<iiHH", raw, 18)
    comp = struct.unpack_from("<I", raw, 30)[0]
    if comp not in (0, 3):
        raise ValueError("压缩方式不支持(%d)" % comp)
    if bpp not in (24, 32):
        raise ValueError("位深不支持(%d)" % bpp)
    flip = h > 0
    h = abs(h)
    stride = ((w * bpp // 8) + 3) // 4 * 4
    px = []
    for y in range(h):
        sy = (h - 1 - y) if flip else y
        row = raw[off + sy * stride: off + sy * stride + w * bpp // 8]
        px.append(row)
    return w, h, bpp, px


def write_png(path, w, h, bpp, rows):
    step = bpp // 8
    raw = bytearray()
    for row in rows:
        raw.append(0)                      # filter: none
        if step == 3:
            raw.extend(row)
        else:                              # BGRA -> RGB
            for i in range(0, len(row), 4):
                raw.extend((row[i + 2], row[i + 1], row[i]))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(out)


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_real_images")
    ok = fail = skip = 0
    for dp, _dn, fns in os.walk(d):
        for fn in fns:
            if not fn.lower().endswith((".bmp", ".png")):
                continue
            p = os.path.join(dp, fn)
            raw = open(p, "rb").read(4)
            if raw[:2] != b"BM":
                skip += 1                      # 已经是真 PNG 了
                continue
            try:
                w, h, bpp, rows = read_bmp(p)
                dst = os.path.splitext(p)[0] + ".png"
                write_png(dst, w, h, bpp, rows)
                if os.path.abspath(dst).lower() != os.path.abspath(p).lower():
                    os.remove(p)
                ok += 1
            except Exception as e:
                fail += 1
                print("  ✗ %s: %s" % (fn, e))
    print("转换完成：成功 %d，跳过（已是 PNG）%d，失败 %d" % (ok, skip, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
