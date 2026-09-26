# -*- coding: utf-8 -*-
"""界面主题 —— 统一配色、字体、控件样式与卡片/渐变绘制工具。

支持两套调色板 (浅色 / 深色), 通过 set_mode() 切换。
注意: 所有取色函数都用 None 作默认值再在函数体内解析,
      否则默认参数会在 import 时被"焊死"成当时那套颜色, 切换主题就会失效。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --------------------------------------------------------------------------
# 调色板
# --------------------------------------------------------------------------
LIGHT = {
    'BG': '#eef1f6', 'PANEL': '#ffffff', 'PANEL_ALT': '#f6f8fc',
    'BORDER': '#dbe2ec', 'FIELD': '#ffffff', 'STRIPE': '#fafbfe',
    'TEXT': '#1f2937', 'TEXT_DIM': '#5b6675', 'TEXT_MUTE': '#98a2b3',
    'ACCENT': '#2f6fbf', 'ACCENT_HI': '#3b85dd', 'ACCENT_DK': '#255a9b',
    'ACCENT_SOFT': '#e3eefc', 'ON_ACCENT': '#ffffff',
    'OK': '#16a34a', 'CUT': '#e07b39', 'WARN': '#d97706', 'ERR': '#dc2626',
    'DISABLED': '#b6bfcc',
    'BTN': '#f1f4f9', 'BTN_HI': '#e8eef8', 'BTN_DN': '#dfe6f0',
    'BTN_OFF': '#f0f2f5', 'BTN_OFF_FG': '#b6bfcc',
    'HEAD_HI': '#e9eef7', 'SEL_BG': '#e3eefc', 'SEL_FG': '#255a9b',
    'TROUGH': '#e7ecf4', 'SCROLL': '#e6ebf3', 'SCROLL_HI': '#d3dbe7',
    'HEAD_A': '#3b82d6', 'HEAD_B': '#24568f',
    'HEAD_TXT': '#ffffff', 'HEAD_SUB': '#cfe0f7', 'HEAD_VER': '#b9d3f2',
    'VIEW_TOP': '#2c3745', 'VIEW_BOT': '#161c24', 'GRID': '#39434f',
}
DARK = {
    'BG': '#0f1319', 'PANEL': '#171c24', 'PANEL_ALT': '#1e242e',
    'BORDER': '#2b3340', 'FIELD': '#11161d', 'STRIPE': '#1b212a',
    'TEXT': '#e6ebf2', 'TEXT_DIM': '#a9b4c4', 'TEXT_MUTE': '#6f7b8c',
    'ACCENT': '#4c8fe0', 'ACCENT_HI': '#63a2ee', 'ACCENT_DK': '#3a76bf',
    'ACCENT_SOFT': '#1b2b40', 'ON_ACCENT': '#ffffff',
    'OK': '#3fbf6a', 'CUT': '#f0954f', 'WARN': '#e0a52c', 'ERR': '#f0564f',
    'DISABLED': '#525d6c',
    'BTN': '#212936', 'BTN_HI': '#2a3444', 'BTN_DN': '#1a212b',
    'BTN_OFF': '#1b212a', 'BTN_OFF_FG': '#525d6c',
    'HEAD_HI': '#28323f', 'SEL_BG': '#1b2b40', 'SEL_FG': '#8dbbf2',
    'TROUGH': '#1e242e', 'SCROLL': '#232b37', 'SCROLL_HI': '#313c4c',
    'HEAD_A': '#1e4f86', 'HEAD_B': '#0f2136',
    'HEAD_TXT': '#ffffff', 'HEAD_SUB': '#9fc4ec', 'HEAD_VER': '#7ba6d6',
    'VIEW_TOP': '#222b37', 'VIEW_BOT': '#0c1015', 'GRID': '#333d4a',
}
AXIS_X, AXIS_Y, AXIS_Z = '#e0574f', '#66c05f', '#4f8fe0'

MODE = 'light'


def set_mode(mode):
    """切换调色板, 把颜色灌进模块全局名。必须在建界面之前调用。"""
    global MODE
    MODE = mode if mode in ('light', 'dark') else 'light'
    globals().update(DARK if MODE == 'dark' else LIGHT)
    return MODE


def is_dark():
    return MODE == 'dark'


set_mode('light')

# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------
UI = 'Microsoft YaHei UI'
MONO = 'Consolas'

F = {
    'h1': (UI, 11, 'bold'),
    'h2': (UI, 10, 'bold'),
    'body': (UI, 9),
    'body_b': (UI, 9, 'bold'),
    'small': (UI, 8),
    'tiny': (UI, 7),
    'mono': (MONO, 9),
    'mono_s': (MONO, 8),
    'input': (UI, 10),
    'brand': (UI, 13, 'bold'),
    'num': (MONO, 9),
}


def rgb(hexstr):
    h = hexstr.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hexs(r, g, b):
    return '#%02x%02x%02x' % (max(0, min(255, int(r))),
                              max(0, min(255, int(g))),
                              max(0, min(255, int(b))))


def mix(c1, c2, t):
    a, b = rgb(c1), rgb(c2)
    return hexs(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def gradient(canvas, x0, y0, x1, y1, c1, c2, tag='bg', horizontal=False):
    """用细条模拟渐变 (tkinter 没有原生渐变)。"""
    if horizontal:
        steps = max(2, min(120, int(x1 - x0)))
        for i in range(steps):
            col = mix(c1, c2, i / (steps - 1.0))
            canvas.create_rectangle(x0 + (x1 - x0) * i / steps, y0,
                                    x0 + (x1 - x0) * (i + 1) / steps, y1,
                                    fill=col, outline=col, tags=tag)
    else:
        steps = max(2, min(120, int(y1 - y0)))
        for i in range(steps):
            col = mix(c1, c2, i / (steps - 1.0))
            canvas.create_rectangle(x0, y0 + (y1 - y0) * i / steps,
                                    x1, y0 + (y1 - y0) * (i + 1) / steps,
                                    fill=col, outline=col, tags=tag)


def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    """tkinter 画不了圆角矩形, 用多边形折线近似。"""
    r = max(0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------
# 样式
# --------------------------------------------------------------------------

def apply(root, scale=1.0):
    k = max(1.0, float(scale))
    st = ttk.Style(root)
    for name in ('clam', 'alt', 'default'):
        try:
            st.theme_use(name)
            break
        except tk.TclError:
            continue

    st.configure('.', font=F['body'], background=BG, foreground=TEXT, focuscolor=ACCENT)
    st.configure('TFrame', background=BG)
    st.configure('Bar.TFrame', background=PANEL)
    st.configure('Card.TFrame', background=PANEL)
    st.configure('TLabel', background=BG, foreground=TEXT)
    st.configure('Card.TLabel', background=PANEL, foreground=TEXT)
    st.configure('Dim.TLabel', background=PANEL, foreground=TEXT_DIM, font=F['small'])
    st.configure('Mute.TLabel', background=PANEL, foreground=TEXT_MUTE, font=F['small'])
    st.configure('Head.TLabel', background=PANEL, foreground=TEXT, font=F['h2'])

    # --- 按钮 ---
    st.configure('TButton', font=F['body'], padding=(int(12 * k), int(6 * k)),
                 borderwidth=0, background=BTN, foreground=TEXT, relief='flat',
                 anchor='center')
    st.map('TButton',
           background=[('pressed', BTN_DN), ('active', BTN_HI), ('disabled', BTN_OFF)],
           foreground=[('disabled', BTN_OFF_FG)])

    st.configure('Accent.TButton', background=ACCENT, foreground=ON_ACCENT,
                 font=F['body_b'], padding=(int(16 * k), int(7 * k)))
    st.map('Accent.TButton',
           background=[('pressed', ACCENT_DK), ('active', ACCENT_HI),
                       ('disabled', mix(ACCENT, PANEL, 0.55))],
           foreground=[('disabled', mix(ON_ACCENT, PANEL, 0.45))])

    st.configure('Ghost.TButton', background=PANEL, foreground=TEXT_DIM,
                 padding=(int(10 * k), int(6 * k)), borderwidth=0)
    st.map('Ghost.TButton',
           background=[('active', ACCENT_SOFT), ('pressed', mix(ACCENT_SOFT, ACCENT, 0.18))],
           foreground=[('active', ACCENT), ('disabled', DISABLED)])

    # --- 输入 ---
    st.configure('TEntry', fieldbackground=FIELD, background=FIELD,
                 bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                 insertcolor=ACCENT, foreground=TEXT, padding=int(5 * k))
    st.map('TEntry', bordercolor=[('focus', ACCENT)],
           lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
    st.configure('TCombobox', fieldbackground=FIELD, background=FIELD,
                 bordercolor=BORDER, arrowcolor=TEXT_DIM, padding=int(4 * k),
                 foreground=TEXT, lightcolor=BORDER, darkcolor=BORDER)
    st.map('TCombobox',
           fieldbackground=[('readonly', FIELD), ('focus', FIELD)],
           foreground=[('readonly', TEXT)],
           bordercolor=[('focus', ACCENT)],
           arrowcolor=[('active', ACCENT)])
    try:
        root.option_add('*TCombobox*Listbox.background', FIELD)
        root.option_add('*TCombobox*Listbox.foreground', TEXT)
        root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        root.option_add('*TCombobox*Listbox.selectForeground', ON_ACCENT)
    except Exception:
        pass

    # --- 树 ---
    st.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                 rowheight=int(27 * k), borderwidth=0, font=F['body'])
    st.configure('Treeview.Heading', background=PANEL_ALT, foreground=TEXT_DIM,
                 font=F['small'], relief='flat', padding=(int(8 * k), int(6 * k)),
                 borderwidth=0)
    st.map('Treeview.Heading', background=[('active', HEAD_HI)],
           foreground=[('active', ACCENT)])
    st.map('Treeview', background=[('selected', SEL_BG)], foreground=[('selected', SEL_FG)])

    # --- 选项卡 ---
    st.configure('TNotebook', background=PANEL, borderwidth=0, tabmargins=(8, 6, 8, 0))
    st.configure('TNotebook.Tab', background=PANEL_ALT, foreground=TEXT_DIM,
                 padding=(int(16 * k), int(8 * k)), font=F['body'], borderwidth=0)
    st.map('TNotebook.Tab',
           background=[('selected', PANEL), ('active', HEAD_HI)],
           foreground=[('selected', ACCENT), ('active', TEXT)])

    # --- 其它 ---
    st.configure('TProgressbar', background=ACCENT, troughcolor=TROUGH,
                 bordercolor=TROUGH, lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
    st.configure('TSeparator', background=BORDER)
    for orient in ('Vertical', 'Horizontal'):
        st.configure(orient + '.TScrollbar', background=SCROLL, troughcolor=BG,
                     bordercolor=BG, arrowcolor=TEXT_MUTE, gripcount=0, borderwidth=0)
        st.map(orient + '.TScrollbar', background=[('active', SCROLL_HI)])
    st.configure('TPanedwindow', background=BG, sashrelief='flat', sashwidth=int(6 * k))

    # --- 菜单 ---
    try:
        root.option_add('*Menu.background', PANEL)
        root.option_add('*Menu.foreground', TEXT)
        root.option_add('*Menu.activeBackground', ACCENT)
        root.option_add('*Menu.activeForeground', ON_ACCENT)
        root.option_add('*Menu.relief', 'flat')
        root.option_add('*Menu.borderWidth', 0)
    except Exception:
        pass
    return st


# --------------------------------------------------------------------------
# 卡片
# --------------------------------------------------------------------------

def card(parent, width=None, bg=None):
    """返回 (外层, 内容层): 外层放进布局, 内容层放控件。"""
    bg = PANEL if bg is None else bg
    outer = tk.Frame(parent, bg=BORDER, bd=0, highlightthickness=0)
    if width:
        outer.configure(width=width)
        outer.pack_propagate(False)
    inner = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
    inner.pack(fill='both', expand=True, padx=1, pady=1)
    return outer, inner


def section(parent, title, hint=None, bg=None):
    """卡片内的小标题: 左侧一根主色竖条 + 标题 (+ 右侧提示)。"""
    bg = PANEL if bg is None else bg
    head = tk.Frame(parent, bg=bg)
    head.pack(fill='x', padx=12, pady=(11, 7))
    tk.Frame(head, bg=ACCENT, width=3, height=15).pack(side='left', padx=(0, 8))
    tk.Label(head, text=title, bg=bg, fg=TEXT, font=F['h2']).pack(side='left')
    if hint:
        tk.Label(head, text=hint, bg=bg, fg=TEXT_MUTE, font=F['small']).pack(side='right')
    return head


def hairline(parent, bg=None, pad=10):
    bg = PANEL if bg is None else bg
    f = tk.Frame(parent, bg=bg)
    f.pack(fill='x', padx=pad, pady=6)
    tk.Frame(f, bg=BORDER, height=1).pack(fill='x')
    return f


def chip(parent, text, fg=None, bg=None, font=None):
    return tk.Label(parent, text=text, fg=fg or ACCENT, bg=bg or ACCENT_SOFT,
                    font=font or F['tiny'], padx=7, pady=2)


def textbox(parent, **kw):
    """统一样式的多行文本框底色 (浅色白底 / 深色暗底)。"""
    kw.setdefault('relief', 'flat')
    kw.setdefault('bd', 0)
    kw.setdefault('highlightthickness', 1)
    kw.setdefault('highlightbackground', BORDER)
    kw.setdefault('highlightcolor', ACCENT)
    kw.setdefault('bg', FIELD)
    kw.setdefault('fg', TEXT)
    kw.setdefault('insertbackground', ACCENT)
    kw.setdefault('selectbackground', ACCENT_SOFT)
    kw.setdefault('selectforeground', SEL_FG)
    return tk.Text(parent, **kw)
