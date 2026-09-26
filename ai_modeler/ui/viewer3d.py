# -*- coding: utf-8 -*-
"""三维预览控件 —— 基于 tkinter Canvas 的软件渲染器。

支持: 轨道相机 (旋转/缩放/平移)、透视/正交投影、背面剔除、
      画家算法消隐、朗伯光照、面片按特征着色、坐标轴与包围盒。
"""
from __future__ import annotations

import math
import tkinter as tk
from typing import List, Optional, Sequence, Tuple

from ai_modeler.core import kernel as K
from ai_modeler.ui import theme as TH

PALETTE = [
    '#4a90d9', '#5cb85c', '#f0ad4e', '#d9534f', '#9b59b6', '#1abc9c',
    '#e67e22', '#3498db', '#2ecc71', '#e74c3c', '#16a085', '#8e44ad',
]


def _hex_rgb(h: str):
    h = h.lstrip('#')
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def _rgb_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(round(c * 255)))) for c in rgb)


def _norm(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if l < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / l, v[1] / l, v[2] / l)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


class Camera(object):
    def __init__(self):
        self.target = (0.0, 0.0, 0.0)
        self.distance = 400.0
        self.yaw = math.radians(-55.0)
        self.pitch = math.radians(24.0)
        self.fov = math.radians(38.0)
        self.perspective = True

    def basis(self):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        eye = (self.target[0] + self.distance * cp * cy,
               self.target[1] + self.distance * cp * sy,
               self.target[2] + self.distance * sp)
        fwd = _norm((self.target[0] - eye[0], self.target[1] - eye[1], self.target[2] - eye[2]))
        up0 = (0.0, 0.0, 1.0)
        if abs(_dot(fwd, up0)) > 0.999:
            up0 = (0.0, 1.0, 0.0)
        right = _norm(_cross(fwd, up0))
        up = _cross(right, fwd)
        return eye, fwd, right, up

    def orbit(self, dyaw, dpitch):
        self.yaw += dyaw
        lim = math.radians(89.0)
        self.pitch = max(-lim, min(lim, self.pitch + dpitch))

    def zoom(self, factor):
        self.distance = max(1.0, min(1e6, self.distance * factor))

    def pan(self, dx, dy):
        _e, _f, right, up = self.basis()
        sx = self.distance * 0.0016
        self.target = (self.target[0] - right[0] * dx * sx + up[0] * dy * sx,
                       self.target[1] - right[1] * dx * sx + up[1] * dy * sx,
                       self.target[2] - right[2] * dx * sx + up[2] * dy * sx)


class Viewer3D(tk.Canvas):
    """三维模型预览画布。"""

    LIGHT = _norm((0.45, -0.55, 0.72))
    FILL = _norm((-0.55, 0.45, 0.35))

    def __init__(self, master, background=None, **kw):
        if background is None:
            background = TH.VIEW_BOT
        tk.Canvas.__init__(self, master, background=background,
                           highlightthickness=0, **kw)
        self.cam = Camera()
        self.polys: List[K.Polygon] = []
        self.show_edges = False
        self.show_axes = True
        self.show_grid = False
        self.max_faces = 9000
        self._drag = None
        self._last = (0, 0)
        self._model_bbox = None
        self._info_lines: List[str] = []
        self._dragging = False
        self.edge_limit = 2500      # 辅助线只标折角和轮廓，不描绘三角剖分线
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<ButtonPress-2>', self._on_press)
        self.bind('<B2-Motion>', self._on_drag)
        self.bind('<ButtonPress-3>', self._on_press)
        self.bind('<B3-Motion>', self._on_drag)
        self.bind('<ButtonRelease-2>', self._on_release)
        self.bind('<ButtonRelease-3>', self._on_release)
        self.bind('<MouseWheel>', self._on_wheel)
        self.bind('<Button-4>', lambda e: self._zoom_at(1.12))
        self.bind('<Button-5>', lambda e: self._zoom_at(1 / 1.12))
        self.bind('<Double-Button-1>', lambda e: self.fit_view())
        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<Motion>', self._on_motion)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_model(self, polys, fit: bool = True):
        self.polys = list(polys or [])
        self._model_bbox = K.bbox(self.polys)
        if fit:
            self.fit_view()
        else:
            self.redraw()

    def set_info(self, lines):
        self._info_lines = list(lines or [])
        self._draw_overlay()

    def fit_view(self):
        bb = self._model_bbox or K.bbox(self.polys)
        if not bb:
            self.cam.target = (0, 0, 0)
            self.cam.distance = 400.0
            self.redraw()
            return
        cx, cy, cz = (bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0
        dx, dy, dz = bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]
        radius = max(0.5, 0.5 * math.sqrt(dx * dx + dy * dy + dz * dz))
        self.cam.target = (cx, cy, cz)
        w, h = max(self.winfo_width(), 60), max(self.winfo_height(), 60)
        half_angle = math.atan(math.tan(self.cam.fov / 2.0) * min(1.0, w / h))
        self.cam.distance = max(radius + 1.0, radius / math.sin(half_angle) * 1.12)
        self.redraw()

    def set_view(self, name: str):
        n = (name or '').lower()
        if n in ('front', '前'):
            self.cam.yaw, self.cam.pitch = math.radians(-90), 0.0
        elif n in ('back', '后'):
            self.cam.yaw, self.cam.pitch = math.radians(90), 0.0
        elif n in ('top', '俯'):
            self.cam.pitch = math.radians(89.0)
        elif n in ('bottom', '仰'):
            self.cam.pitch = math.radians(-89.0)
        elif n in ('left', '左'):
            self.cam.yaw, self.cam.pitch = math.radians(180), 0.0
        elif n in ('right', '右'):
            self.cam.yaw, self.cam.pitch = 0.0, 0.0
        else:
            self.cam.yaw, self.cam.pitch = math.radians(-55), math.radians(24)
        self.redraw()

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _on_press(self, e):
        mode = 'pan' if (e.state & 0x0001 or e.num in (2, 3)) else 'orbit'
        self._drag = mode
        self._last = (e.x, e.y)
        self.focus_set()

    def _on_drag(self, e):
        if not self._drag:
            return
        dx, dy = e.x - self._last[0], e.y - self._last[1]
        self._last = (e.x, e.y)
        self._dragging = True
        if self._drag == 'orbit':
            self.cam.orbit(-dx * 0.009, dy * 0.009)
        else:
            self.cam.pan(dx, dy)
        self.redraw()

    def _on_release(self, _e):
        if self._drag is not None:
            self._drag = None
            self._dragging = False
            self.redraw()

    def _on_wheel(self, e):
        self._zoom_at(1.12 if e.delta > 0 else 1 / 1.12)

    def _zoom_at(self, f):
        self.cam.zoom(1.0 / f if f > 1 else 1.0 * (1.0 / f))
        self.redraw()

    def _on_motion(self, e):
        self._cursor_pos = (e.x, e.y)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _draw_background(self, w, h):
        TH.gradient(self, 0, 0, w, h, TH.VIEW_TOP, TH.VIEW_BOT, tag='bgrad')

    def _feature_edges(self, eye):
        """找出轮廓和明显折角，跳过圆柱分段与共面剖分接缝。"""
        adjacent = {}

        def key(v):
            return tuple(round(value, 5) for value in v)

        def edge_key(a, b):
            ka, kb = key(a), key(b)
            return (ka, kb) if ka < kb else (kb, ka)

        for polygon in self.polys:
            points = polygon.points()
            if len(points) < 3:
                continue
            normal = _norm(polygon.plane.normal)
            to_eye = _norm((eye[0] - points[0][0], eye[1] - points[0][1],
                            eye[2] - points[0][2]))
            facing = _dot(normal, to_eye) > 0.0
            for index, point in enumerate(points):
                edge = edge_key(point, points[(index + 1) % len(points)])
                adjacent.setdefault(edge, []).append((normal, facing))

        cos_limit = math.cos(math.radians(35.0))
        visible = set()
        for edge, sides in adjacent.items():
            if len(sides) == 1:
                visible.add(edge)
            elif len(sides) == 2:
                (a, front_a), (b, front_b) = sides
                if front_a != front_b or abs(_dot(a, b)) < cos_limit:
                    visible.add(edge)
        return visible, edge_key

    def redraw(self):
        self.delete('all')
        w = max(self.winfo_width(), 60)
        h = max(self.winfo_height(), 60)
        self._draw_background(w, h)
        if self.show_grid:
            self._draw_grid(w, h)
        if not self.polys:
            self.create_text(w / 2, h / 2,
                         text='还没有模型\n\n在左侧写一句中文, 例如\n'
                              '「画一个 100×60×20 的长方体, 中心打直径 10 的通孔」\n'
                              '然后点「生成模型」',
                         fill='#a5b1c1', font=(TH.UI, 11), justify='center')
            self._draw_overlay()
            return
        eye, fwd, right, up = self.cam.basis()
        f = (h / 2.0) / math.tan(self.cam.fov / 2.0)
        cx, cy = w / 2.0, h / 2.0
        near = self.cam.distance * 0.02

        faces = []
        for p in self.polys:
            n = p.plane.normal
            pts = p.points()
            if len(pts) < 3:
                continue
            # 视空间坐标
            cam_pts = []
            zsum = 0.0
            surf = (0.0, 0.0, 0.0)
            ok = True
            for v in pts:
                dx, dy, dz = v[0] - eye[0], v[1] - eye[1], v[2] - eye[2]
                z = _dot((dx, dy, dz), fwd)
                if z < near:
                    ok = False
                    break
                cam_pts.append((_dot((dx, dy, dz), right), _dot((dx, dy, dz), up), z))
                zsum += z
            if not ok or len(cam_pts) < 3:
                continue
            depth = zsum / len(cam_pts)
            # 背面剔除: 面法向与视线方向
            to_eye = _norm((eye[0] - pts[0][0], eye[1] - pts[0][1], eye[2] - pts[0][2]))
            facing = _dot(_norm(n), to_eye)
            if facing <= 0.0:
                continue
            # 投影
            screen = []
            if self.cam.perspective:
                for (x, y, z) in cam_pts:
                    screen.append((cx + f * x / z, cy - f * y / z))
            else:
                scale = f / max(self.cam.distance, 1e-6)
                for (x, y, _z) in cam_pts:
                    screen.append((cx + scale * x, cy - scale * y))
            xs = [s[0] for s in screen]
            ys = [s[1] for s in screen]
            if max(xs) < -20 or min(xs) > w + 20 or max(ys) < -20 or min(ys) > h + 20:
                continue
            area = abs(sum(xs[i] * ys[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * ys[i]
                           for i in range(len(xs)))) / 2.0
            if area < 0.6:
                continue
            nn = _norm(n)
            lam = max(0.0, _dot(nn, self.LIGHT))
            fill = max(0.0, _dot(nn, self.FILL))
            shade = 0.38 + 0.52 * lam + 0.24 * fill
            if facing < 0.30:
                shade *= 0.80 + 0.20 * (facing / 0.30)
            shade = max(0.34, min(1.18, shade))
            base = _hex_rgb(PALETTE[p.tag % len(PALETTE)])
            color = _rgb_hex((base[0] * shade, base[1] * shade, base[2] * shade))
            faces.append((depth, screen, color, area, pts))

        if len(faces) > self.max_faces:
            faces.sort(key=lambda t: -t[3])
            faces = faces[:self.max_faces]
        faces.sort(key=lambda t: -t[0])

        # 仅在静止时绘制真实轮廓/折角；每个三角面都描边会出现密集辅助线。
        want_edges = self.show_edges and len(faces) <= self.edge_limit and not self._dragging
        visible_edges, edge_key = self._feature_edges(eye) if want_edges else (set(), None)
        for (_d, screen, color, _a, points) in faces:
            flat = []
            for (sx, sy) in screen:
                flat.append(sx)
                flat.append(sy)
            self.create_polygon(flat, fill=color, outline=color, width=0)
            if want_edges:
                line_color = _rgb_hex(tuple(channel * 0.67
                                            for channel in _hex_rgb(color)))
                for index, point in enumerate(points):
                    if edge_key(point, points[(index + 1) % len(points)]) not in visible_edges:
                        continue
                    a = screen[index]
                    b = screen[(index + 1) % len(screen)]
                    self.create_line(a[0], a[1], b[0], b[1], fill=line_color, width=1)

        if self.show_axes:
            self._draw_axes(eye, fwd, right, up, f, cx, cy, w, h)
        self._draw_overlay()
        self._draw_bbox_tag(w, h)

    def _draw_grid(self, w, h):
        eye, fwd, right, up = self.cam.basis()
        f = (h / 2.0) / math.tan(self.cam.fov / 2.0)
        cx, cy = w / 2.0, h / 2.0
        bb = self._model_bbox
        if bb:
            span = max(bb[3] - bb[0], bb[4] - bb[1]) * 0.9 + 20.0
        else:
            span = 200.0
        step = 10.0
        while span / step > 24:
            step *= 2
        n = int(span / step) + 1
        segs = []
        for i in range(-n, n + 1):
            segs.append(((-n * step, i * step, 0.0), (n * step, i * step, 0.0)))
            segs.append(((i * step, -n * step, 0.0), (i * step, n * step, 0.0)))

        def proj(p):
            dx, dy, dz = p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]
            z = _dot((dx, dy, dz), fwd)
            if z < 1e-3:
                return None
            x = _dot((dx, dy, dz), right)
            y = _dot((dx, dy, dz), up)
            if self.cam.perspective:
                return (cx + f * x / z, cy - f * y / z)
            scale = f / max(self.cam.distance, 1e-6)
            return (cx + scale * x, cy - scale * y)

        for (a, b) in segs:
            pa, pb = proj(a), proj(b)
            if pa and pb:
                self.create_line(pa[0], pa[1], pb[0], pb[1], fill=TH.GRID, width=1)

    def _draw_axes(self, eye, fwd, right, up, f, cx, cy, w, h):
        ox, oy = 62, h - 62
        L = 34
        axes = [((1, 0, 0), TH.AXIS_X, 'X'), ((0, 1, 0), TH.AXIS_Y, 'Y'),
                ((0, 0, 1), TH.AXIS_Z, 'Z')]
        for (d, col, label) in axes:
            vx = _dot(d, right) * L
            vy = -_dot(d, up) * L
            depth = _dot(d, fwd)
            self.create_line(ox, oy, ox + vx, oy + vy, fill=col, width=2)
            self.create_text(ox + vx * 1.28, oy + vy * 1.28, text=label, fill=col,
                             font=('Consolas', 9, 'bold'))

    def _draw_overlay(self):
        self.delete('overlay')
        for i, line in enumerate(self._info_lines):
            self.create_text(12, 12 + i * 17, text=line, anchor='nw', fill='#9fb0c2',
                             font=('Consolas', 9), tags='overlay')

    def _draw_bbox_tag(self, w, h):
        bb = self._model_bbox
        if not bb:
            return
        txt = '外形尺寸  %.1f × %.1f × %.1f mm' % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
        self.create_text(w - 14, h - 12, text=txt, anchor='se', fill='#8d9aab',
                         font=(TH.UI, 9), tags='overlay')
