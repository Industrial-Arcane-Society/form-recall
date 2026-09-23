# -*- coding: utf-8 -*-
"""给界面截图 (纯标准库: ctypes 抓窗口 + 手写 PNG 编码, 不依赖 Pillow)。

用 PrintWindow + PW_RENDERFULLCONTENT 抓取, 不需要把窗口切到前台,
所以不会打断用户正在做的事。
"""
from __future__ import annotations
import ctypes
import struct
import zlib
from ctypes import wintypes


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]


def _write_png(path, w, h, bgra):
    """把 BGRA 原始像素写成 PNG (真彩色, 无 alpha)。"""
    stride = w * 4
    body = bytearray()
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        body.append(0)          # 每行的 filter type
        body += rgb

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    open(path, 'wb').write(
        b'\x89PNG\r\n\x1a\n' +
        chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
        chunk(b'IDAT', zlib.compress(bytes(body), 6)) +
        chunk(b'IEND', b''))


def capture(hwnd, path, scale=1):
    """抓取指定窗口, 可选整数倍缩放。"""
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    rc = wintypes.RECT()
    if not u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rc)):
        raise RuntimeError('GetWindowRect 失败')
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        raise RuntimeError('窗口尺寸异常 %dx%d' % (w, h))
    hdc = u32.GetWindowDC(wintypes.HWND(hwnd))
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    try:
        u32.PrintWindow(wintypes.HWND(hwnd), mdc, 2)   # PW_RENDERFULLCONTENT
        bi = _BMIH()
        bi.biSize = ctypes.sizeof(_BMIH)
        bi.biWidth, bi.biHeight = w, -h                # 负高度 = 自上而下
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        data = buf.raw
        if scale > 1:
            data, w, h = _zoom(data, w, h, scale)
        _write_png(path, w, h, data)
    finally:
        g32.DeleteObject(bmp)
        g32.DeleteDC(mdc)
        u32.ReleaseDC(wintypes.HWND(hwnd), hdc)
    return w, h


def _zoom(bgra, w, h, k):
    """最近邻整数倍放大 (截图看着清楚一点)。"""
    stride = w * 4
    out = bytearray(w * k * h * k * 4)
    ow = w * k
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        big = bytearray(ow * 4)
        for x in range(w):
            px = row[x * 4:x * 4 + 4]
            big[x * k * 4:(x + 1) * k * 4] = px * k
        for j in range(k):
            o = (y * k + j) * ow * 4
            out[o:o + ow * 4] = big
    return bytes(out), ow, h * k
