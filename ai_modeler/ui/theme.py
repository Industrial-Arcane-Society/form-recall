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
    # DSH Web-inspired neutral surfaces: clear hierarchy, quiet borders, one
    # restrained brand color, and a darker dedicated modeling viewport.
    'BG': '#f4f6fa', 'PANEL': '#ffffff', 'PANEL_ALT': '#f8f9fc',
    'BORDER': '#e4e7ee', 'FIELD': '#ffffff', 'STRIPE': '#fafbfc',
    'TEXT': '#252a35', 'TEXT_DIM': '#616a78', 'TEXT_MUTE': '#929aa7',
    'ACCENT': '#5968e8', 'ACCENT_HI': '#6c7af2', 'ACCENT_DK': '#4654cf',
    'ACCENT_SOFT': '#eef0ff', 'ON_ACCENT': '#ffffff',
    'USER_BG': '#fff0f5', 'TOOL_BG': '#f7f8fa',
    'SEND': '#e85b9f', 'SEND_HI': '#d94d91',
    'OK': '#24966a', 'CUT': '#c97746', 'WARN': '#bd7a17', 'ERR': '#cf4b55',
    'DISABLED': '#bbc1cb',
    'BTN': '#f5f6f9', 'BTN_HI': '#eceef4', 'BTN_DN': '#e2e5ec',
    'BTN_OFF': '#f5f6f8', 'BTN_OFF_FG': '#b7bdc7',
    'HEAD_HI': '#f0f2f7', 'SEL_BG': '#eef0ff', 'SEL_FG': '#4654cf',
    'TROUGH': '#eceef3', 'SCROLL': '#e8ebf1', 'SCROLL_HI': '#d9dee8',
    'HEAD_A': '#ffffff', 'HEAD_B': '#ffffff',
    'HEAD_TXT': '#252a35', 'HEAD_SUB': '#616a78', 'HEAD_VER': '#929aa7',
    'VIEW_TOP': '#242e3a', 'VIEW_BOT': '#171e27', 'GRID': '#35414f',
}
DARK = {
    'BG': '#151922', 'PANEL': '#1c212b', 'PANEL_ALT': '#232a35',
    'BORDER': '#303847', 'FIELD': '#181d26', 'STRIPE': '#202631',
    'TEXT': '#e8ebf1', 'TEXT_DIM': '#b0b8c5', 'TEXT_MUTE': '#858f9e',
    'ACCENT': '#8792ff', 'ACCENT_HI': '#9ba4ff', 'ACCENT_DK': '#6e79e5',
    'ACCENT_SOFT': '#2a2f4a', 'ON_ACCENT': '#ffffff',
    'USER_BG': '#3b2935', 'TOOL_BG': '#232a34',
    'SEND': '#e85b9f', 'SEND_HI': '#f276b0',
    'OK': '#55bf91', 'CUT': '#e49a70', 'WARN': '#e5b25b', 'ERR': '#f0757c',
    'DISABLED': '#667080',
    'BTN': '#272e3a', 'BTN_HI': '#313949', 'BTN_DN': '#202631',
    'BTN_OFF': '#202631', 'BTN_OFF_FG': '#667080',
    'HEAD_HI': '#2d3441', 'SEL_BG': '#2a2f4a', 'SEL_FG': '#b5bcff',
    'TROUGH': '#2b3240', 'SCROLL': '#303847', 'SCROLL_HI': '#414b5d',
    'HEAD_A': '#1c212b', 'HEAD_B': '#1c212b',
    'HEAD_TXT': '#e8ebf1', 'HEAD_SUB': '#b0b8c5', 'HEAD_VER': '#858f9e',
    'VIEW_TOP': '#202935', 'VIEW_BOT': '#131820', 'GRID': '#313d4b',
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
    'h1': (UI, 14, 'bold'),
    'h2': (UI, 11, 'bold'),
    'body': (UI, 10),
    'body_b': (UI, 10, 'bold'),
    'small': (UI, 9),
    'tiny': (UI, 8),
    'mono': (MONO, 9),
    'mono_s': (MONO, 8),
    'input': (UI, 11),
    'brand': (UI, 14, 'bold'),
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
    try:
        root.configure(background=BG)
    except tk.TclError:
        pass
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
    st.configure('Surface.TFrame', background=PANEL_ALT)
    st.configure('TLabel', background=BG, foreground=TEXT)
    st.configure('Card.TLabel', background=PANEL, foreground=TEXT)
    st.configure('Dim.TLabel', background=PANEL, foreground=TEXT_DIM, font=F['small'])
    st.configure('Mute.TLabel', background=PANEL, foreground=TEXT_MUTE, font=F['small'])
    st.configure('Head.TLabel', background=PANEL, foreground=TEXT, font=F['h2'])

    # --- 按钮 ---
    st.configure('TButton', font=F['body'], padding=(int(11 * k), int(7 * k)),
                 borderwidth=0, background=BTN, foreground=TEXT, relief='flat',
                 anchor='center')
    st.map('TButton',
           background=[('pressed', BTN_DN), ('active', BTN_HI), ('disabled', BTN_OFF)],
           foreground=[('disabled', BTN_OFF_FG)])

    st.configure('Accent.TButton', background=ACCENT, foreground=ON_ACCENT,
                 font=F['body_b'], padding=(int(15 * k), int(8 * k)))
    st.map('Accent.TButton',
           background=[('pressed', ACCENT_DK), ('active', ACCENT_HI),
                       ('disabled', mix(ACCENT, PANEL, 0.55))],
           foreground=[('disabled', mix(ON_ACCENT, PANEL, 0.45))])

    st.configure('Ghost.TButton', background=PANEL, foreground=TEXT_DIM,
                 padding=(int(10 * k), int(6 * k)), borderwidth=0)
    st.map('Ghost.TButton',
           background=[('active', ACCENT_SOFT), ('pressed', mix(ACCENT_SOFT, ACCENT, 0.18))],
           foreground=[('active', ACCENT), ('disabled', DISABLED)])

    st.configure('Toolbar.TButton', background=PANEL, foreground=TEXT_DIM,
                 padding=(int(8 * k), int(6 * k)), font=F['small'], borderwidth=0)
    st.map('Toolbar.TButton',
           background=[('pressed', BTN_DN), ('active', BTN_HI), ('disabled', PANEL)],
           foreground=[('active', TEXT), ('disabled', DISABLED)])

    st.configure('TCheckbutton', background=PANEL, foreground=TEXT_DIM,
                 font=F['small'], padding=(int(3 * k), int(3 * k)))
    st.map('TCheckbutton',
           background=[('active', PANEL)],
           foreground=[('active', TEXT), ('disabled', DISABLED)])

    # --- 输入 ---
    st.configure('TEntry', fieldbackground=FIELD, background=FIELD,
                 bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                 insertcolor=ACCENT, foreground=TEXT, padding=int(7 * k))
    st.map('TEntry', bordercolor=[('focus', ACCENT)],
           lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
    st.configure('TCombobox', fieldbackground=FIELD, background=FIELD,
                 bordercolor=BORDER, arrowcolor=TEXT_DIM, padding=int(6 * k),
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
                 rowheight=int(30 * k), borderwidth=0, font=F['small'])
    st.configure('Treeview.Heading', background=PANEL_ALT, foreground=TEXT_DIM,
                 font=F['tiny'], relief='flat', padding=(int(9 * k), int(8 * k)),
                 borderwidth=0)
    st.map('Treeview.Heading', background=[('active', HEAD_HI)],
           foreground=[('active', ACCENT)])
    st.map('Treeview', background=[('selected', SEL_BG)], foreground=[('selected', SEL_FG)])

    # --- 选项卡 ---
    st.configure('TNotebook', background=PANEL, borderwidth=0, tabmargins=(6, 5, 6, 0))
    st.configure('TNotebook.Tab', background=PANEL_ALT, foreground=TEXT_DIM,
                 padding=(int(10 * k), int(7 * k)), font=F['small'], borderwidth=0)
    st.map('TNotebook.Tab',
           background=[('selected', PANEL), ('active', HEAD_HI)],
           foreground=[('selected', ACCENT), ('active', TEXT)])
    try:
        # The inspector uses its own compact flat navigation row.
        st.layout('Clean.TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
        st.layout('Clean.TNotebook.Tab', [])
        st.configure('Clean.TNotebook', background=PANEL, borderwidth=0,
                     tabmargins=0)
    except tk.TclError:
        pass

    # --- 其它 ---
    st.configure('TProgressbar', background=ACCENT, troughcolor=TROUGH,
                 bordercolor=TROUGH, lightcolor=ACCENT, darkcolor=ACCENT, thickness=int(5 * k))
    st.configure('TSeparator', background=BORDER)
    for orient in ('Vertical', 'Horizontal'):
        st.configure(orient + '.TScrollbar', background=SCROLL, troughcolor=BG,
                     bordercolor=BG, arrowcolor=TEXT_MUTE, gripcount=0, borderwidth=0)
        st.map(orient + '.TScrollbar', background=[('active', SCROLL_HI)])
    st.configure('TPanedwindow', background=BG, sashrelief='flat', sashwidth=int(7 * k))

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
