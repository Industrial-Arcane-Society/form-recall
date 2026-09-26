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
               scale_override=None, depth_cues: bool = False) -> bool:
        self._fill_bg()
        self.zb = array('f', [-1e30]) * (self.w * self.h)
        self.nb = array('f', [0.0]) * (self.w * self.h * 3) if edges else None
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
        depth_span = max(max(vz) - min(vz), 1e-6)
        cx = (mnx + mxx) / 2.0
        cy = (mny + mxy) / 2.0
        ox, oy = self.w / 2.0, self.h / 2.0

        if depth_cues:
            # A fixed world-space light can leave a front orthographic view
            # nearly uniform. Keep the key light in front of the camera and high
            # above the part so recessed planes remain readable.
            light = tuple(-0.32 * right[i] + 0.48 * fwd[i] + 0.82 * up[i]
                          for i in range(3))
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
            facing = n[0] * fwd[0] + n[1] * fwd[1] + n[2] * fwd[2]
            if depth_cues and facing < 0.0:
                n = (-n[0], -n[1], -n[2])
            diffuse = n[0] * light[0] + n[1] * light[1] + n[2] * light[2]
            lam = max(0.0, diffuse) if depth_cues else abs(diffuse)
            shade = (0.33 + 0.67 * lam) if depth_cues else (0.26 + 0.74 * lam)
            spec = max(0.0, diffuse) ** (24 if depth_cues else 28) * (0.035 if depth_cues else 0.40)
            col = (min(255, int(255 * (base[0] * shade + spec))),
                   min(255, int(255 * (base[1] * shade + spec))),
                   min(255, int(255 * (base[2] * shade + spec))))
            i0, i1, i2 = 3 * i, 3 * i + 1, 3 * i + 2
            self._tri(((vx[i0] - cx) * scale + ox,
                       oy - (vy[i0] - cy) * scale, vz[i0]),
                      ((vx[i1] - cx) * scale + ox,
                       oy - (vy[i1] - cy) * scale, vz[i1]),
                      ((vx[i2] - cx) * scale + ox,
                       oy - (vy[i2] - cy) * scale, vz[i2]), col, n)

        if depth_cues:
            self._apply_depth_cues(depth_span)

        if edges:
            self._draw_surface_edges(depth_span)
        return True

    def _apply_depth_cues(self, depth_span: float) -> None:
        """Add restrained distance falloff and contact darkening to the mesh.

        The renderer has no shadow map or ambient-occlusion buffer. A small
        screen-space depth comparison makes a recessed coplanar-looking region
        darker near its lip, which helps in orthographic review images.
        """
        w, h, fb, zb = self.w, self.h, self.fb, self.zb
        clear = -1e29
        visible = [z for z in zb if z > clear]
        if not visible:
            return
        closest = max(visible)
        depth_span = max(depth_span, 1e-6)
        offsets = []
        for radius in (1, 2, 4):
            offsets.extend(((radius, 0), (-radius, 0), (0, radius), (0, -radius),
                            (radius, radius), (radius, -radius),
                            (-radius, radius), (-radius, -radius)))
        threshold = depth_span * 0.018

        for y in range(h):
            row = y * w
            for x in range(w):
                idx = row + x
                depth = zb[idx]
                if depth <= clear:
                    continue
                relative_depth = max(0.0, min(1.0, (closest - depth) / depth_span))
                distance_shade = 1.0 - 0.48 * (relative_depth ** 0.62)
                occlusion = 0.0
                for dx, dy in offsets:
                    nx, ny = x + dx, y + dy
                    if nx < 0 or nx >= w or ny < 0 or ny >= h:
                        continue
                    neighbor = zb[ny * w + nx]
                    if neighbor <= clear:
                        continue
                    delta = neighbor - depth
                    if delta <= threshold:
                        continue
                    distance = max(abs(dx), abs(dy))
                    amount = ((delta - threshold) / depth_span) * 1.30
                    amount *= max(0.20, 1.0 - distance / 6.0)
                    occlusion = max(occlusion, min(0.27, amount))
                factor = distance_shade * (1.0 - occlusion)
                offset = idx * 3
                fb[offset] = int(fb[offset] * factor)
                fb[offset + 1] = int(fb[offset + 1] * factor)
                fb[offset + 2] = int(fb[offset + 2] * factor)

    # ------------------------------------------------------------------
    def _tri(self, a, b, c, col, normal=None):
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
                if self.nb is not None and normal is not None:
                    no = idx * 3
                    self.nb[no] = normal[0]
                    self.nb[no + 1] = normal[1]
                    self.nb[no + 2] = normal[2]

    # ------------------------------------------------------------------
    def _draw_surface_edges(self, depth_span: float) -> None:
        """Find visible creases and depth steps from the rasterized surfaces.

        Per-pixel normals avoid exposing hidden BSP/T-junction mesh seams as
        floating lines. Depth jumps catch parallel faces at different heights,
        which is the important cue for recessed planes in straight-on views.
        """
        w, h, zb, nb, fb = self.w, self.h, self.zb, self.nb, self.fb
        clear = -1e29
        cos_crease = math.cos(math.radians(32.0))
        depth_jump = max(0.05, depth_span * 0.018)
        for y in range(h):
            row = y * w
            for x in range(w):
                idx = row + x
                depth = zb[idx]
                if depth <= clear:
                    continue
                no = idx * 3
                nx, ny, nz = nb[no], nb[no + 1], nb[no + 2]
                edge = False
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    px, py = x + dx, y + dy
                    if px < 0 or px >= w or py < 0 or py >= h:
                        edge = True
                        break
                    other_idx = py * w + px
                    other_depth = zb[other_idx]
                    if other_depth <= clear:
                        edge = True
                        break
                    other_offset = other_idx * 3
                    dot = (nx * nb[other_offset] + ny * nb[other_offset + 1] +
                           nz * nb[other_offset + 2])
                    if dot < cos_crease or (depth < other_depth - depth_jump):
                        edge = True
                        break
                if edge:
                    offset = idx * 3
                    fb[offset] = int(fb[offset] * 0.48)
                    fb[offset + 1] = int(fb[offset + 1] * 0.48)
                    fb[offset + 2] = int(fb[offset + 2] * 0.48)

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
        # Review views need to distinguish a setback from a coplanar face. The
        # z-aware edge pass excludes hidden seams; depth cues add soft contact
        # darkening without changing the model geometry.
        renderer.render(polys, view=view, edges=True, depth_cues=True,
                        scale_override=shared_scale)
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
        r.render(polys, view=v, edges=True, depth_cues=True)
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
        renderer.render(polys, view=name, edges=True, depth_cues=True,
                        scale_override=scale)
        gx, gy = (index % columns) * tw, (index // columns) * th
        for y in range(th):
            src = y * tw * 3
            dst = (y + gy) * width * 3 + gx * 3
            canvas[dst:dst + tw * 3] = renderer.fb[src:src + tw * 3]
    write_png(path, width, height, bytes(canvas))
    return True
