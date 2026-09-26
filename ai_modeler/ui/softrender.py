# -*- coding: utf-8 -*-
r"""纯标准库三维渲染器 —— 把零件网格渲染成 PNG 图片。

不依赖 OpenGL / Pillow / numpy:
    · 自己做视图变换、深度缓冲 (z-buffer)、扫描线三角填充
    · 自己写 PNG 编码 (zlib + CRC32)
    · 只画"折角明显"的特征边, 得到 CAD 那种轮廓线
用途: 批量给零件生成截图, 方便肉眼检查形状到底对不对。
"""
from __future__ import annotations

import math
import struct
import zlib
from array import array
from typing import Sequence

from ai_modeler.core import kernel as K

BG_TOP = (0x22, 0x2b, 0x37)
BG_BOT = (0x0d, 0x11, 0x17)
EDGE_COL = (22, 27, 34)


def write_png(path: str, w: int, h: int, rgb: bytes) -> None:
    """rgb: 长度 w*h*3 的字节串 (自上而下)。"""
    rows = bytearray()
    stride = w * 3
    for y in range(h):
        rows.append(0)                       # 每行的 filter type
        rows += rgb[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(bytes(rows), 6)))
        f.write(chunk(b'IEND', b''))


def view_matrix(az_deg: float, el_deg: float):
    """由方位角/俯仰角构造视线基 (右, 上, 朝向观察者)。"""
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    fwd = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    up0 = (0.0, 0.0, 1.0)
    if abs(fwd[2]) > 0.999:
        up0 = (0.0, 1.0, 0.0)
    right = K.v_norm(K.v_cross(fwd, up0))
    up = K.v_norm(K.v_cross(right, fwd))
    return right, up, fwd


VIEWS = {
    'iso':   (35.0, 28.0),
    # The isometric camera sees the +X, +Y and +Z sides. Keep the named front
    # view on +Y so a front-facing feature remains visible in both ISO and front
    # review images; back is the opposite side.
    'front': (90.0, 0.0),
    'right': (0.0, 0.0),
    'top':   (-90.0, 90.0),
    'back':  (-90.0, 0.0),
    'left':  (180.0, 0.0),
    'bottom': (-90.0, -90.0),
}


class Renderer:
    def __init__(self, w: int = 640, h: int = 480):
        self.w = int(w)
        self.h = int(h)
        self.fb = bytearray(self.w * self.h * 3)
        self.zb = array('f', [-1e30]) * (self.w * self.h)

    def _fill_bg(self):
        w, h, fb = self.w, self.h, self.fb
        for y in range(h):
            t = y / max(1, h - 1)
            col = bytes((int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
                         int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
                         int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)))
            fb[y * w * 3:(y + 1) * w * 3] = col * w

    # ------------------------------------------------------------------
    def render(self, polys: Sequence[K.Polygon], view: str = 'iso',
               light=(0.42, -0.52, 0.74), base=(0.60, 0.67, 0.78),
               edges: bool = False, margin: float = 0.09,
               spin_deg: float = 0.0,
               scale_override=None) -> bool:
        self._fill_bg()
        self.zb = array('f', [-1e30]) * (self.w * self.h)
        tris = K.polygons_to_triangles(polys)
        if not tris:
            return False

        az, el = VIEWS.get(view, VIEWS['iso'])
        right, up, fwd = view_matrix(az + spin_deg, el)

        allv = []
        for (a, b, c, _t) in tris:
            allv.extend((a, b, c))
        vx = [K.v_dot(v, right) for v in allv]
        vy = [K.v_dot(v, up) for v in allv]
        vz = [K.v_dot(v, fwd) for v in allv]
        mnx, mxx = min(vx), max(vx)
        mny, mxy = min(vy), max(vy)
        span = max(mxx - mnx, mxy - mny, 1e-6)
        scale = (scale_override if scale_override is not None else
                 (1.0 - 2.0 * margin) * min(self.w, self.h) / span)
        cx = (mnx + mxx) / 2.0
        cy = (mny + mxy) / 2.0
        ox, oy = self.w / 2.0, self.h / 2.0

        ln = math.sqrt(sum(x * x for x in light)) or 1.0
        light = tuple(x / ln for x in light)

        order = sorted(range(len(tris)),
                       key=lambda i: (vz[3 * i] + vz[3 * i + 1] + vz[3 * i + 2]))
        for i in order:
            a, b, c, _tg = tris[i]
            n = K.v_cross(K.v_sub(b, a), K.v_sub(c, a))
            nl = K.v_len(n)
            if nl < 1e-12:
                continue
            n = (n[0] / nl, n[1] / nl, n[2] / nl)
            lam = abs(n[0] * light[0] + n[1] * light[1] + n[2] * light[2])
            shade = 0.26 + 0.74 * lam
            spec = max(0.0, n[0] * light[0] + n[1] * light[1] + n[2] * light[2]) ** 28 * 0.40
            col = (min(255, int(255 * (base[0] * shade + spec))),
                   min(255, int(255 * (base[1] * shade + spec))),
                   min(255, int(255 * (base[2] * shade + spec))))
            i0, i1, i2 = 3 * i, 3 * i + 1, 3 * i + 2
            self._tri(((vx[i0] - cx) * scale + ox,
                       oy - (vy[i0] - cy) * scale, vz[i0]),
                      ((vx[i1] - cx) * scale + ox,
                       oy - (vy[i1] - cy) * scale, vz[i1]),
                      ((vx[i2] - cx) * scale + ox,
                       oy - (vy[i2] - cy) * scale, vz[i2]), col)

        if edges:
            def screen_of(k):
                return ((k[0] - cx) * scale + ox, oy - (k[1] - cy) * scale, 0.0)
            self._draw_feature_edges(tris, screen_of)
        return True

    # ------------------------------------------------------------------
    def _tri(self, a, b, c, col):
        W, H, fb, zb = self.w, self.h, self.fb, self.zb
        x0, y0, z0 = a
        x1, y1, z1 = b
        x2, y2, z2 = c
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            return
        inv = 1.0 / area
        minx = max(0, int(min(x0, x1, x2)))
        maxx = min(W - 1, int(max(x0, x1, x2)) + 1)
        miny = max(0, int(min(y0, y1, y2)))
        maxy = min(H - 1, int(max(y0, y1, y2)) + 1)
        if minx > maxx or miny > maxy:
            return
        r, g, bl = col
        d10x, d10y = x1 - x0, y1 - y0
        d20x, d20y = x2 - x0, y2 - y0
        for py in range(miny, maxy + 1):
            fy = py + 0.5 - y0
            row = py * W
            for px in range(minx, maxx + 1):
                fx = px + 0.5 - x0
                w0 = (d10x * fy - fx * d10y) * inv
                if w0 < 0.0 or w0 > 1.0:
                    continue
                w1 = (fx * d20y - d20x * fy) * inv
                if w1 < 0.0 or (w0 + w1) > 1.0:
                    continue
                z = z0 + (z1 - z0) * w1 + (z2 - z0) * w0
                idx = row + px
                if z <= zb[idx]:
                    continue
                zb[idx] = z
                o = idx * 3
                fb[o] = r
                fb[o + 1] = g
                fb[o + 2] = bl

    # ------------------------------------------------------------------
    def _draw_feature_edges(self, tris, to_screen):
        """只画折角明显的边。

        顶点按量化坐标建 位置->屏幕坐标 的索引, 一次算好直接查表;
        否则每条边都要回扫全部三角形找端点, 大网格直接退化成 O(n^2)。
        """
        key_of = {}
        scr = {}
        edge_faces = {}
        face_n = []

        def vkey(v):
            k = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
            i = key_of.get(k)
            if i is None:
                i = key_of[k] = len(key_of)
                scr[i] = to_screen(k)
            return i

        for (a, b, c, _t) in tris:
            n = K.v_cross(K.v_sub(b, a), K.v_sub(c, a))
            nl = K.v_len(n)
            face_n.append(None if nl < 1e-12 else (n[0] / nl, n[1] / nl, n[2] / nl))
        for i, (a, b, c, _t) in enumerate(tris):
            ka, kb, kc = vkey(a), vkey(b), vkey(c)
            for (kp, kq) in ((ka, kb), (kb, kc), (kc, ka)):
                if kp == kq:
                    continue
                e = (kp, kq) if kp < kq else (kq, kp)
                edge_faces.setdefault(e, []).append(i)

        cos_lim = math.cos(math.radians(20.0))
        for e, fs in edge_faces.items():
            # Boolean meshes can contain an edge shared by several coplanar
            # triangles. It is an internal tessellation seam, not a visible CAD
            # feature edge; drawing it creates distracting radial spokes.
            if len(fs) > 2:
                continue
            if len(fs) == 2:
                n1, n2 = face_n[fs[0]], face_n[fs[1]]
                if n1 and n2:
                    d = abs(n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2])
                    if d >= cos_lim:
                        continue
            p1, p2 = scr.get(e[0]), scr.get(e[1])
            if p1 is None or p2 is None:
                continue
            self._line(p1, p2, EDGE_COL)

    def _line(self, p1, p2, col, thick: int = 1):
        x1, y1 = p1[0], p1[1]
        x2, y2 = p2[0], p2[1]
        n = int(max(abs(x2 - x1), abs(y2 - y1))) + 1
        if n <= 0 or n > 8000:
            return
        dx = (x2 - x1) / n
        dy = (y2 - y1) / n
        for i in range(n + 1):
            self._px(int(x1 + dx * i), int(y1 + dy * i), col, thick)

    def _px(self, x, y, col, t=1):
        W, H, fb = self.w, self.h, self.fb
        for dy in range(t):
            py = y + dy
            if py < 0 or py >= H:
                continue
            base = py * W
            for dx in range(t):
                px = x + dx
                if 0 <= px < W:
                    o = (base + px) * 3
                    fb[o], fb[o + 1], fb[o + 2] = col

    def save(self, path: str) -> None:
        write_png(path, self.w, self.h, bytes(self.fb))


def render_to_png(polys, path: str, w: int = 640, h: int = 480,
                  view: str = 'iso', **kw) -> bool:
    r = Renderer(w, h)
    ok = r.render(polys, view=view, **kw)
    r.save(path)
    return ok


FOUR = ('iso', 'front', 'right', 'top')
THREE = ('front', 'right', 'top')


def save_three_view(polys, path: str, tile=(330, 260)) -> bool:
    """Save front/right/top views at one shared scale for proportion checks."""
    tw, th = tile
    W, H = tw * len(THREE), th
    canvas = bytearray(W * H * 3)
    triangles = K.polygons_to_triangles(polys)
    if not triangles:
        write_png(path, W, H, bytes(canvas))
        return False
    vertices = [v for a, b, c, _tag in triangles for v in (a, b, c)]
    max_projected_span = 1e-6
    for view in THREE:
        az, el = VIEWS[view]
        right, up, _fwd = view_matrix(az, el)
        px = [K.v_dot(v, right) for v in vertices]
        py = [K.v_dot(v, up) for v in vertices]
        max_projected_span = max(max_projected_span,
                                 max(px) - min(px), max(py) - min(py))
    shared_scale = (1.0 - 2.0 * 0.09) * min(tw, th) / max_projected_span
    for idx, view in enumerate(THREE):
        renderer = Renderer(tw, th)
        # This preview is used by vision models for silhouette inspection. The
        # legacy edge pass can expose hidden/non-manifold mesh seams, so keep this
        # orthographic image solid-shaded and let the z-buffer show only surfaces.
        renderer.render(polys, view=view, edges=False, scale_override=shared_scale)
        gx = idx * tw
        for y in range(th):
            src = y * tw * 3
            dst = (y * W + gx) * 3
            canvas[dst:dst + tw * 3] = renderer.fb[src:src + tw * 3]
    write_png(path, W, H, bytes(canvas))
    return True


def save_four_view(polys, path: str, tile=(330, 260)) -> bool:
    """把等轴测 / 前 / 右 / 俯 四个视角拼成一张 2x2 的图。"""
    tw, th = tile
    W, H = tw * 2, th * 2
    canvas = bytearray(W * H * 3)
    for idx, v in enumerate(FOUR):
        r = Renderer(tw, th)
        r.render(polys, view=v, edges=False)
        gx, gy = (idx % 2) * tw, (idx // 2) * th
        for y in range(th):
            s = y * tw * 3
            d = ((gy + y) * W + gx) * 3
            canvas[d:d + tw * 3] = r.fb[s:s + tw * 3]
        for x in range(tw):
            o = ((gy + th - 1) * W + gx + x) * 3
            canvas[o:o + 3] = b'/8F'
        for y in range(th):
            o = ((gy + y) * W + gx + tw - 1) * 3
            canvas[o:o + 3] = b'/8F'
    write_png(path, W, H, bytes(canvas))
    return True


def save_multiview(polys, path: str, views=None, tile=(330, 260), columns=3) -> bool:
    """Save several named orthographic views using one scale across all panels.

    The caller should describe panel order alongside the image because the
    minimal PNG renderer intentionally does not depend on a font package.
    """
    views = tuple(views or ('front', 'right', 'top'))
    unknown = [view for view in views if view not in VIEWS]
    if unknown:
        raise ValueError('未知视角: %s' % ', '.join(unknown))
    columns = max(1, int(columns))
    tw, th = (int(tile[0]), int(tile[1]))
    rows = max(1, (len(views) + columns - 1) // columns)
    width, height = columns * tw, rows * th
    canvas = bytearray(width * height * 3)
    triangles = K.polygons_to_triangles(polys)
    if not triangles:
        write_png(path, width, height, bytes(canvas))
        return False

    vertices = [v for a, b, c, _tag in triangles for v in (a, b, c)]
    max_span = 1e-6
    for name in views:
        az, el = VIEWS[name]
        right, up, _fwd = view_matrix(az, el)
        px = [K.v_dot(v, right) for v in vertices]
        py = [K.v_dot(v, up) for v in vertices]
        max_span = max(max_span, max(px) - min(px), max(py) - min(py))
    scale = (1.0 - 2.0 * 0.09) * min(tw, th) / max_span

    for index, name in enumerate(views):
        renderer = Renderer(tw, th)
        renderer.render(polys, view=name, edges=False, scale_override=scale)
        gx, gy = (index % columns) * tw, (index // columns) * th
        for y in range(th):
            src = y * tw * 3
            dst = (y + gy) * width * 3 + gx * 3
            canvas[dst:dst + tw * 3] = renderer.fb[src:src + tw * 3]
    write_png(path, width, height, bytes(canvas))
    return True
