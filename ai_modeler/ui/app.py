# -*- coding: utf-8 -*-
"""AI 自动建模助手 —— 桌面图形界面 (tkinter, 纯标准库)。"""
from __future__ import annotations
from ai_modeler.paths import PROJECT_ROOT

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from ai_modeler import __version__
from ai_modeler.core import exporters, kernel as K
from ai_modeler.runtime import config as cfgmod
from ai_modeler.ui import theme as TH
from ai_modeler.core import docmodel as DM
from ai_modeler.core import nlparser as NLP
from ai_modeler.ui import softrender as SR
from ai_modeler.ui.viewer3d import Viewer3D

APP_TITLE = 'AI 自动建模助手  v%s' % __version__
ROOT_DIR = str(PROJECT_ROOT)


def enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用。

    否则在 125% / 150% 缩放的屏幕上, Windows 会把窗口连同内容整体放大,
    导致窗口比屏幕还大、右侧面板被挤到屏幕外面。
    """
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 系统 DPI 感知
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
FONT_UI = ('Microsoft YaHei UI', 9)
FONT_UI_B = ('Microsoft YaHei UI', 9, 'bold')
FONT_TITLE = ('Microsoft YaHei UI', 10, 'bold')
FONT_MONO = ('Consolas', 9)

PARAM_SCHEMA = {
    'box': [('length', '长 X (mm)'), ('width', '宽 Y (mm)'), ('height', '高 Z (mm)'),
            ('corner', '圆角/倒角 (mm)')],
    'cylinder': [('radius', '底部半径 (mm)'), ('radius2', '顶部半径(0=圆锥)'),
                 ('height', '高度 (mm)'), ('segments', '圆周分段')],
    'sphere': [('radius', '半径 (mm)'), ('segments', '圆周分段'), ('rings', '纬线分段')],
    'prism': [('height', '拉伸高度 (mm)')],
    'revolve': [('angle', '回转角度 (°)'), ('segments', '圆周分段')],
    'loft': [('span', '展长 (mm)'), ('root_chord', '根部弦长 (mm)'),
             ('tip_chord', '顶部弦长 (mm)'), ('thickness', '相对厚度'),
             ('twist', '总扭转角 (°)'), ('sweep', '总后掠 (mm)'), ('sections', '截面数')],
    'gear': [('teeth', '齿数'), ('module', '模数 (mm)'), ('height', '厚度 (mm)')],
    'hole': [('diameter', '直径 (mm)'), ('depth', '深度 (mm, 通孔忽略)'),
             ('count', '均布数量'), ('pcd', '分度圆直径 (mm)'), ('x', 'X 位置'), ('y', 'Y 位置')],
    'pocket': [('length', '长 (mm)'), ('width', '宽 (mm)'), ('depth', '深 (mm)'),
               ('corner', '圆角 (mm)')],
    'pattern': [('count', '数量'), ('angle', '总角度 (°)')],
}

# 零件库浏览器里"点我填入示例"用的典型参数值
SAMPLE_VALUES = {
    '深沟球轴承': {'d': 20, 'D': 47, 'B': 14},
    '圆锥滚子轴承': {'d': 25, 'D': 52, 'T': 16.25},
    '推力球轴承': {'d': 20, 'D': 35, 'H': 10},
    '直齿圆柱齿轮': {'m': 3, 'z': 24, 'b': 20},
    '同步带轮': {'d': 20, 'D': 80, 'B': 30},
    '链轮': {'d': 20, 'D': 100, 'B': 20},
    '多槽带轮': {'d': 25, 'D': 100, 'B': 30, 'n': 3},
    '制动盘': {'D': 280, 'T': 22, 'd': 64},
    '制动鼓': {'D': 300, 'd': 260, 'H': 80},
    '法兰': {'D': 160, 'd': 60, 'b': 20, 'K': 120, 'n': 6},
    '垫圈': {'d': 10, 'D': 20, 'h': 2},
    'O形圈': {'d': 20, 'D': 26, 'W': 3},
    '油封': {'d': 30, 'D': 47, 'b': 7},
    '轴套': {'d': 25, 'D': 35, 'L': 40},
    '联轴器': {'d': 25, 'D': 80, 'L': 60},
    '螺栓': {'d': 12, 'L': 50},
    '螺母': {'D': 12, 's': 19, 'm': 10},
    '螺旋弹簧': {'d': 2, 'D': 20, 'H': 50, 'n': 8},
    '锥形弹簧': {'d': 2, 'D': 24, 'H': 40, 'n': 6},
    '碟形弹簧': {'d': 20, 'D': 40, 'h': 3},
    '花键轴': {'d': 20, 'D': 25, 'L': 40, 'z': 8},
    '盘形凸轮': {'d': 40, 'D': 56, 'b': 10},
    '辐条手轮': {'D': 120, 'd': 20, 'H': 10, 'z': 5},
    '蜗杆': {'d': 32, 'D': 40, 'L': 60},
    '轴承座': {'d': 30, 'D': 60, 'H': 40},
    '平键': {'b': 8, 'h': 7, 'L': 40},
    '离合器摩擦片': {'d': 120, 'D': 200, 'h': 8},
    '挡圈': {'d': 20, 'D': 24, 's': 1.2},
    # ---- 高难度 / 极高难度 ----
    '斜齿圆柱齿轮': {'m': 3, 'z': 24, 'b': 25, 'beta': 18, 'dh': 20},
    '直齿锥齿轮': {'m': 4, 'z': 20, 'b': 22, 'delta': 45, 'dh': 15},
    '曲轴': {'d': 50, 'dp': 42, 'S': 86, 'n': 4},
    '凸轮轴': {'d': 30, 'h': 8, 'L': 320, 'n': 4, 'dj': 26},
    '涡轮叶轮': {'d': 30, 'D': 120, 'H': 40, 'n': 9, 'beta': 55},
    '活塞': {'D': 80, 'H': 70, 'd': 22, 'n': 3},
    '连杆': {'L': 120, 'D': 48, 'dh': 40, 'd': 24, 'dh2': 18},
    '万向节十字轴': {'L': 80, 'd': 20},
    '涡旋盘': {'D': 100, 'H': 30, 't': 4, 'n': 2.5},
    '三角转子': {'R': 60, 'H': 40, 'd': 24},
    '飞轮': {'D': 300, 'd': 40, 'h': 30, 'K': 250, 'n': 6},
    '气门': {'d': 8, 'D': 34, 'L': 105},
}

SAMPLES = [
    ('文档示例: 100×20 矩形拉伸', '100×20 毫米的矩形草图, 然后拉伸 20 毫米'),
    ('带孔底板', '底板 200×150×12, 四角各打一个直径 8 的通孔, 内缩 12'),
    ('圆角长方体 + 中心通孔', '画一个 120×80×15 的长方体, 四角圆角 R8, 在中心打一个直径 20 的通孔'),
    ('空心圆筒', '直径 60 高 120 的圆柱, 中心打一个直径 20 的通孔'),
    ('法兰盘', '法兰 外径 160 厚 20 内径 60, 均布 6 个直径 12 的螺栓孔, 分度圆 120'),
    ('阶梯轴', '阶梯轴 直径 40 长 60, 直径 30 长 50, 直径 20 长 40'),
    ('整体叶盘', '叶盘 外径 220 厚 30 叶片数 24 叶展 45'),
    ('单个叶片', '叶片 展长 90 弦长 50 45'),
    ('直齿圆柱齿轮', '齿轮 模数 3 齿数 24 厚 20, 中心孔直径 20'),
    ('球体', '球体 半径 40'),
    ('圆锥台', '圆台 底径 80 顶径 30 高 60'),
    ('套筒', '套筒 外径 80 内径 50 长 100'),
    ('斜齿圆柱齿轮 (高难度)', '斜齿圆柱齿轮 模数3 齿数24 齿宽25 螺旋角18 中心孔20'),
    ('曲轴 (高难度)', '曲轴 主轴颈50 连杆颈42 行程86 拐数4'),
    ('涡轮叶轮 (高难度)', '涡轮叶轮 轮毂直径30 外径120 高度40 叶片数9 扭转角55'),
    ('涡旋盘 (极高难度)', '涡旋盘 外径100 高度30 壁厚4 圈数2.5'),
    ('三角转子 (极高难度)', '三角转子 外接圆半径60 厚度40 中心孔24'),
]


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title(APP_TITLE)
        # 按屏幕 DPI 换算界面缩放, 保证窗口在任何缩放下都不超出屏幕
        s = 1.0
        try:
            s = max(1.0, min(2.5, self.winfo_fpixels('1i') / 96.0))
        except Exception:
            pass
        self.ui_scale = s
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1420 * s), int(sw * 0.94))
        h = min(int(880 * s), int(sh * 0.90))
        w = max(w, min(int(1040 * s), sw))
        h = max(h, min(int(660 * s), sh))
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 4)
        self.geometry('%dx%d+%d+%d' % (w, h, x, y))
        self.minsize(min(int(1000 * s), sw), min(int(620 * s), sh))
        try:
            self.iconbitmap(default=os.path.join(str(PROJECT_ROOT), 'assets', 'app.ico'))
        except Exception:
            pass
        self.cfg = cfgmod.load()
        self.doc = DM.Document('Part1')
        self.current_path = None
        self._q = None
        self._busy = False
        self._cancel = None
        self._budget_s = 30.0
        self._pending_prompt = None
        self._pending_images = []
        self.reference_images = []
        self._agent_context = []
        self._agent_context_summary = ''
        self._agent_summary_through = 0
        self._last_plan = None
        self._active_request = None
        self._agent_live_photo = None
        self._last_agent_preview_path = None
        self._last_body = []
        self._chat_photo_refs = []
        self._param_vars = {}
        # 调色板必须在建任何控件之前定下来
        try:
            TH.set_mode(self.cfg.get('theme', 'light'))
        except Exception:
            TH.set_mode('light')
        self._build_style()
        self._build_header()
        self._build_menu()
        self._build_toolbar()
        self._build_layout()
        self._build_status()
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    # ==================================================================
    # 界面构建
    # ==================================================================
    def _build_style(self):
        TH.apply(self, getattr(self, 'ui_scale', 1.0))

    def _build_header(self):
        hh = int(52 * self.ui_scale)
        head = tk.Canvas(self, height=hh, highlightthickness=0, bd=0)
        self._header_h = hh
        head.pack(side='top', fill='x')
        self._header = head
        head.bind('<Configure>', lambda e: self._paint_header())

    def _paint_header(self):
        c = self._header
        c.delete('all')
        w = max(c.winfo_width(), 260)
        h = getattr(self, '_header_h', 52)
        k = self.ui_scale
        TH.gradient(c, 0, 0, w, h, TH.HEAD_A, TH.HEAD_B, horizontal=True)
        cx, cy, s, t = 30.0 * k, h / 2.0, 11.0 * k, 6.0 * k
        top = [cx, cy - t * 1.5]
        rgt = [cx + s, cy - t * 0.5]
        mid = [cx, cy + t * 0.5]
        lft = [cx - s, cy - t * 0.5]
        dn = t * 1.7
        c.create_polygon(top + rgt + mid + lft, fill='#a8cdf5', outline='')
        c.create_polygon(lft + mid + [mid[0], mid[1] + dn] + [lft[0], lft[1] + dn],
                         fill='#4a86cc', outline='')
        c.create_polygon(rgt + mid + [mid[0], mid[1] + dn] + [rgt[0], rgt[1] + dn],
                         fill='#2d629f', outline='')
        c.create_text(54 * k, h / 2.0 - 2, text='AI 自动建模助手', anchor='w',
                      fill='#ffffff', font=TH.F['brand'])
        c.create_text(int(206 * k), h / 2.0 + 1, text='v' + __version__, anchor='w',
                      fill='#b9d3f2', font=TH.F['tiny'])
        c.create_text(w - 20 * k, h / 2.0 + 1, text='用一句中文, 直接生成三维模型',
                      anchor='e', fill='#cfe0f7', font=TH.F['body'])

    def _build_menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label='新建零件', accelerator='Ctrl+N', command=self.new_doc)
        fm.add_command(label='打开工程…', accelerator='Ctrl+O', command=self.open_project)
        fm.add_command(label='保存工程…', accelerator='Ctrl+S', command=self.save_project)
        fm.add_separator()
        fm.add_command(label='导出 STL 模型…', command=lambda: self.export('stl'))
        fm.add_command(label='导出 OBJ 模型…', command=lambda: self.export('obj'))
        fm.add_command(label='导出建模报告 (TXT)…', command=lambda: self.export('report'))
        fm.add_command(label='导出 CATIA 自动化脚本…', command=lambda: self.export('catia'))
        fm.add_separator()
        fm.add_command(label='模型截图: 四视图 (PNG)…', command=lambda: self.snapshot(True))
        fm.add_command(label='模型截图: 单张高清 (PNG)…', command=lambda: self.snapshot(False))
        fm.add_command(label='保存三视图 (PNG)…', command=lambda: self.snapshot_multiview('three'))
        fm.add_command(label='保存扩展多视图 (PNG)…', command=lambda: self.snapshot_multiview('extended'))
        fm.add_separator()
        fm.add_command(label='退出', command=self.on_close)
        m.add_cascade(label='文件', menu=fm)

        em = tk.Menu(m, tearoff=0)
        em.add_command(label='删除选中特征', command=self.delete_feature)
        em.add_command(label='特征上移', command=lambda: self.move_feature(-1))
        em.add_command(label='特征下移', command=lambda: self.move_feature(1))
        em.add_command(label='清空全部特征', command=self.clear_features)
        m.add_cascade(label='编辑', menu=em)

        vm = tk.Menu(m, tearoff=0)
        for label, key in (('等轴测视图', 'iso'), ('前视图', 'front'), ('后视图', 'back'),
                           ('左视图', 'left'), ('右视图', 'right'), ('俯视图', 'top'),
                           ('仰视图', 'bottom')):
            vm.add_command(label=label, command=lambda k=key: self.viewer.set_view(k))
        vm.add_separator()
        vm.add_command(label='适应窗口', accelerator='F', command=lambda: self.viewer.fit_view())
        self.var_edges = tk.BooleanVar(value=True)
        self.var_grid = tk.BooleanVar(value=True)
        self.var_axes = tk.BooleanVar(value=True)
        self.var_persp = tk.BooleanVar(value=True)
        vm.add_checkbutton(label='显示边线', variable=self.var_edges, command=self._sync_view)
        vm.add_checkbutton(label='显示网格', variable=self.var_grid, command=self._sync_view)
        vm.add_checkbutton(label='显示坐标轴', variable=self.var_axes, command=self._sync_view)
        vm.add_checkbutton(label='透视投影', variable=self.var_persp, command=self._sync_view)
        vm.add_separator()
        self.var_dark = tk.BooleanVar(value=TH.is_dark())
        vm.add_checkbutton(label='深色模式 (重启界面生效)', variable=self.var_dark,
                           command=self.switch_theme)
        m.add_cascade(label='视图', menu=vm)

        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label='MCP 服务配置…', command=self.show_mcp_config)
        tm.add_command(label='推送到 CATIA V5…', command=self.push_catia)
        tm.add_command(label='一键连接 CATIA（写配置 / 装插件）…', command=self.connect_catia)
        tm.add_command(label='安装 / 修复 CATIA 插件…', command=self.install_catia_plugin)
        tm.add_command(label='CATIA 模型库（catalog 标准件）…', command=self.open_catia_lib)
        tm.add_command(label='联网搜索设置…', command=self.show_online_config)
        tm.add_command(label='安装 / 管理本地 DeepSeek…', command=self.install_deepseek)
        tm.add_command(label='手动联网搜索…', command=self.manual_online)
        tm.add_command(label='AI 大模型设置…', command=self.show_llm_config)
        tm.add_separator()
        tm.add_command(label='运行内置自检', command=self.run_selfcheck)
        m.add_cascade(label='工具', menu=tm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label='打开使用说明 (使用说明.md)', command=self.open_manual)
        hm.add_command(label='打开 CATIA 连接操作说明', command=self.open_catia_manual)
        hm.add_command(label='界面用法速查', command=self.show_help)
        hm.add_command(label='关于', command=self.show_about)
        m.add_cascade(label='帮助', menu=hm)
        self.config(menu=m)

        self.bind_all('<Control-n>', lambda e: self.new_doc())
        self.bind_all('<Control-o>', lambda e: self.open_project())
        self.bind_all('<Control-s>', lambda e: self.save_project())
        self.bind_all('<Control-Return>', lambda e: self.generate())
        self.bind_all('<F5>', lambda e: self.generate())
        self.bind_all('<Key-f>', lambda e: self.viewer.fit_view())

    def _build_toolbar(self):
        bar = ttk.Frame(self, style='Bar.TFrame', padding=(10, 9))
        bar.pack(side='top', fill='x')
        tk.Frame(self, bg=TH.BORDER, height=1).pack(side='top', fill='x')

        ttk.Button(bar, text='➤  Agent 对话', style='Accent.TButton',
                   command=self.generate).pack(side='left')
        tk.Label(bar, text='Ctrl+Enter', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                 font=TH.F['tiny']).pack(side='left', padx=(8, 0))

        self._tool_sep(bar)
        tk.Label(bar, text='视角', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                 font=TH.F['small']).pack(side='left', padx=(0, 7))
        for label, key in (('等轴测', 'iso'), ('前', 'front'), ('右', 'right'), ('俯', 'top')):
            ttk.Button(bar, text=label, style='Ghost.TButton', width=6,
                       command=lambda k=key: self.viewer.set_view(k)).pack(side='left', padx=1)
        ttk.Button(bar, text='适应窗口', style='Ghost.TButton',
                   command=lambda: self.viewer.fit_view()).pack(side='left', padx=1)

        self._tool_sep(bar)
        ttk.Button(bar, text='▦ 多视图 PNG', style='Ghost.TButton',
                   command=self.snapshot_multiview).pack(side='left', padx=1)
        ttk.Button(bar, text='📷 模型截图', style='Ghost.TButton',
                   command=self.snapshot).pack(side='left', padx=1)
        ttk.Button(bar, text='导出 STL', style='Ghost.TButton',
                   command=lambda: self.export('stl')).pack(side='left', padx=1)
        ttk.Button(bar, text='保存工程', style='Ghost.TButton',
                   command=self.save_project).pack(side='left', padx=1)
        ttk.Button(bar, text='推送 CATIA', style='Ghost.TButton',
                   command=self.push_catia).pack(side='left', padx=1)

        self.lbl_busy = tk.Label(bar, text='', bg=TH.PANEL, fg=TH.ACCENT, font=TH.F['body_b'])
        self.lbl_busy.pack(side='right', padx=(10, 0))
        self.btn_stop = ttk.Button(bar, text='■  停止', style='Ghost.TButton',
                                   command=self.cancel_build, state='disabled')
        self.btn_stop.pack(side='right')

    def _tool_sep(self, parent):
        tk.Frame(parent, bg=TH.BORDER, width=1, height=22).pack(side='left', padx=11)

    def _build_layout(self):
        panes = ttk.PanedWindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10, pady=10)

        # ============================ 左: 对话 + 特征树 ============================
        left_card, left = TH.card(panes, width=int(430 * self.ui_scale))
        panes.add(left_card, weight=0)

        head = TH.section(left, '建模对话')
        ttk.Button(head, text='新对话', style='Ghost.TButton',
                   command=self.new_chat).pack(side='right', padx=(8, 0))
        self.cmb_sample = ttk.Combobox(head, values=[s[0] for s in SAMPLES],
                                       state='readonly', width=9)
        self.cmb_sample.set('示例…')
        self.cmb_sample.pack(side='right', padx=(5, 0))
        self.cmb_sample.bind('<<ComboboxSelected>>', self._on_sample)

        history_wrap = tk.Frame(left, bg=TH.PANEL)
        history_wrap.pack(fill='both', expand=True, padx=12, pady=(0, 7))
        self.chat_history = tk.Text(
            history_wrap, height=10, width=34, wrap='word', state='disabled',
            font=TH.F['small'], relief='flat', bd=0, highlightthickness=0,
            bg=TH.PANEL, fg=TH.TEXT, padx=6, pady=8, spacing1=3, spacing3=8)
        self.chat_history.pack(side='left', fill='both', expand=True)
        chat_scroll = ttk.Scrollbar(history_wrap, command=self.chat_history.yview)
        chat_scroll.pack(side='right', fill='y', padx=(5, 0))
        self.chat_history.configure(yscrollcommand=chat_scroll.set)
        self.chat_history.tag_configure('user', foreground=TH.ACCENT_DK,
                                        font=TH.F['body_b'])
        self.chat_history.tag_configure('assistant', foreground=TH.TEXT,
                                        font=TH.F['small'])
        self.chat_history.tag_configure('system', foreground=TH.TEXT_MUTE,
                                        font=TH.F['tiny'])
        self.chat_history.insert(
            'end', 'AI Modeler AgentLoop\n'
            '可以连续讨论当前模型、提出修改，或开始新建。Agent 会按本轮需要选择识别、建模和复核深度；'
            '预览会在右侧 Agent 页实时更新。模型只在本地渲染器展示，不自动同步 CATIA。\n',
            'system')

        self.composer = tk.Frame(
            left, bg=TH.PANEL, bd=0, highlightthickness=1,
            highlightbackground=TH.BORDER, highlightcolor=TH.ACCENT)
        self.composer.pack(fill='x', padx=12, pady=(0, 10))

        self.attachment_preview_frame = tk.Frame(self.composer, bg=TH.PANEL)
        self.attach_canvas = tk.Canvas(
            self.attachment_preview_frame, height=116, bg=TH.PANEL,
            highlightthickness=0, bd=0, xscrollincrement=24)
        self.attach_canvas.pack(fill='x', padx=9, pady=(8, 0))
        self.attach_scrollbar = ttk.Scrollbar(
            self.attachment_preview_frame, orient='horizontal',
            command=self.attach_canvas.xview)
        self.attach_canvas.configure(xscrollcommand=self.attach_scrollbar.set)
        self.attach_inner = tk.Frame(self.attach_canvas, bg=TH.PANEL)
        self.attach_window = self.attach_canvas.create_window(
            (0, 0), window=self.attach_inner, anchor='nw')
        self.attach_inner.bind('<Configure>', self._sync_attachment_strip)
        self.attach_canvas.bind('<Configure>', self._sync_attachment_strip)
        self.attach_canvas.bind('<MouseWheel>', self._scroll_attachment_strip)
        self._attachment_photo_refs = []

        self.txt = tk.Text(
            self.composer, height=2, width=34, wrap='word', font=TH.F['input'],
            relief='flat', bd=0, highlightthickness=0, bg=TH.PANEL, fg=TH.TEXT,
            insertbackground=TH.ACCENT, padx=13, pady=10,
            selectbackground=TH.ACCENT_SOFT, selectforeground=TH.ACCENT_DK, undo=True)
        self.txt.pack(fill='x', padx=4, pady=(6, 4))
        self._input_placeholder = '描述要建的零件，或直接粘贴参考图片…'
        self._input_placeholder_active = False
        self._restore_prompt_placeholder()
        self.txt.bind('<FocusIn>', self._clear_prompt_placeholder)
        self.txt.bind('<FocusOut>', self._restore_prompt_placeholder)
        self.txt.bind('<KeyRelease>', self._resize_prompt)
        self.txt.bind('<Return>', self._on_return)
        self.txt.bind('<Shift-Return>', lambda e: None)
        self.txt.bind('<<Paste>>', self._paste_clipboard_image)
        self.after_idle(self._resize_prompt)

        composer_footer = tk.Frame(self.composer, bg=TH.PANEL)
        composer_footer.pack(fill='x', padx=8, pady=(1, 8))
        ttk.Button(composer_footer, text='＋', width=3, style='Ghost.TButton',
                   command=self.attach_reference_images).pack(side='left')
        self.lbl_attachments = tk.Label(
            composer_footer, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
            font=TH.F['tiny'], anchor='w')
        self.lbl_attachments.pack(side='left', padx=(2, 8))
        tk.Label(composer_footer, text='◉ 完整对话', bg=TH.PANEL,
                 fg=TH.TEXT_DIM, font=TH.F['small']).pack(side='left')
        self.btn_send = tk.Canvas(
            composer_footer, width=34, height=34, bg=TH.PANEL,
            highlightthickness=0, bd=0, cursor='hand2')
        self.btn_send.pack(side='right', padx=(5, 0))
        self._send_circle = self.btn_send.create_oval(
            1, 1, 33, 33, fill='#e85b9f', outline='')
        self.btn_send.create_text(
            17, 16, text='↑', fill='#ffffff', font=(TH.UI, 15, 'bold'))
        self.btn_send.bind('<Button-1>', lambda _event: self.generate())
        self.btn_send.bind('<Enter>', lambda _event: self.btn_send.itemconfigure(
            self._send_circle, fill='#d94d91'))
        self.btn_send.bind('<Leave>', lambda _event: self.btn_send.itemconfigure(
            self._send_circle, fill='#e85b9f'))
        self.btn_model = ttk.Button(
            composer_footer, text=self._model_button_text(), style='Ghost.TButton',
            command=self.show_llm_config)
        self.btn_model.pack(side='right')

        TH.hairline(left)

        TH.section(left, '特征树', '双击启用 / 停用')
        tvbox = tk.Frame(left, bg=TH.PANEL)
        tvbox.pack(fill='both', expand=True, padx=12)
        self.tree = ttk.Treeview(tvbox, columns=('no', 'op', 'desc'), show='headings',
                                 selectmode='browse')
        self.tree.heading('no', text='#')
        self.tree.heading('op', text='操作')
        self.tree.heading('desc', text='特征')
        u = self.ui_scale
        self.tree.column('no', width=int(32 * u), anchor='center', stretch=False)
        self.tree.column('op', width=int(52 * u), anchor='center', stretch=False)
        self.tree.column('desc', width=int(246 * u), anchor='w')
        self.tree.pack(side='left', fill='both', expand=True)
        tvb = ttk.Scrollbar(tvbox, command=self.tree.yview)
        tvb.pack(side='right', fill='y', padx=(5, 0))
        self.tree.configure(yscrollcommand=tvb.set)
        self.tree.tag_configure('add', foreground=TH.TEXT)
        self.tree.tag_configure('cut', foreground=TH.CUT)
        self.tree.tag_configure('comp', foreground=TH.OK)
        self.tree.tag_configure('off', foreground=TH.DISABLED)
        self.tree.tag_configure('stripe', background=TH.STRIPE)
        self.tree.tag_configure('empty', foreground=TH.TEXT_MUTE)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.tree.bind('<Double-1>', self._on_tree_toggle)

        frow = tk.Frame(left, bg=TH.PANEL)
        frow.pack(fill='x', padx=12, pady=(7, 12))
        for text, cmd in (('↑ 上移', lambda: self.move_feature(-1)),
                          ('↓ 下移', lambda: self.move_feature(1)),
                          ('删除', self.delete_feature),
                          ('重命名', self.rename_feature)):
            ttk.Button(frow, text=text, style='Ghost.TButton',
                       command=cmd).pack(side='left', padx=(0, 5))

        # ============================== 中: 三维视口 ==============================
        mid_card, mid = TH.card(panes)
        panes.add(mid_card, weight=1)
        self.viewer = Viewer3D(mid)
        self.viewer.pack(fill='both', expand=True, padx=1, pady=(1, 0))
        foot = tk.Frame(mid, bg=TH.PANEL)
        foot.pack(fill='x', pady=(0, 1))
        tk.Label(foot, text='左键拖动 旋转     右键 / 中键拖动 平移     滚轮 缩放     双击 适应窗口',
                 bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small']).pack(pady=5)

        # ============================== 右: 属性面板 ==============================
        right_card, right = TH.card(panes, width=int(310 * self.ui_scale))
        panes.add(right_card, weight=0)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill='both', expand=True, padx=2, pady=2)

        self.tab_param = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_param, text='参数')
        tk.Label(self.tab_param, text='选中左侧特征后可改尺寸, 按「应用参数」立即重建。',
                 bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                 wraplength=270, justify='left').pack(anchor='w', padx=14, pady=(12, 4))
        self.param_body = tk.Frame(self.tab_param, bg=TH.PANEL)
        self.param_body.pack(fill='x', padx=14)

        self.tab_stat = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_stat, text='统计')
        self.lbl_stat = tk.Text(self.tab_stat, height=10, width=26, wrap='word',
                                font=TH.F['mono'], relief='flat', bd=0,
                                bg=TH.PANEL, fg=TH.TEXT, padx=12, pady=10)
        self.lbl_stat.pack(fill='both', expand=True)
        self.lbl_stat.tag_configure('k', foreground=TH.TEXT_MUTE)
        self.lbl_stat.tag_configure('v', foreground=TH.TEXT)
        self.lbl_stat.tag_configure('hl', foreground=TH.ACCENT, font=TH.F['body_b'])

        self.tab_log = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_log, text='日志')
        self.txt_log = tk.Text(self.tab_log, height=10, width=26, wrap='word',
                               font=TH.F['mono_s'], relief='flat', bd=0,
                               bg=TH.PANEL, fg=TH.TEXT_DIM, padx=12, pady=10)
        self.txt_log.pack(fill='both', expand=True)

        self.tab_agent = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_agent, text='Agent')
        self.lbl_agent_stage = tk.Label(
            self.tab_agent, text='等待任务', bg=TH.PANEL, fg=TH.TEXT_DIM,
            font=TH.F['body_b'], anchor='w', justify='left', wraplength=270)
        self.lbl_agent_stage.pack(fill='x', padx=12, pady=(12, 4))
        self.lbl_agent_preview = tk.Label(
            self.tab_agent, text='建模过程中生成的预览会显示在这里。',
            bg=TH.VIEW_BOT, fg=TH.TEXT_MUTE, font=TH.F['small'],
            justify='center', wraplength=270, compound='top')
        self.lbl_agent_preview.pack(fill='x', padx=10, pady=6)
        self.lbl_agent_detail = tk.Label(
            self.tab_agent, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
            font=TH.F['small'], anchor='nw', justify='left', wraplength=270)
        self.lbl_agent_detail.pack(fill='x', padx=12, pady=(2, 8))
        self.txt_agent_events = tk.Text(
            self.tab_agent, height=8, width=26, wrap='word',
            font=TH.F['mono_s'], relief='flat', bd=0,
            bg=TH.PANEL, fg=TH.TEXT_DIM, padx=12, pady=8)
        self.txt_agent_events.pack(fill='both', expand=True, padx=4, pady=(0, 8))
        self.txt_agent_events.configure(state='disabled')

        # ---------------- 零件库浏览器 ----------------
        self.tab_parts = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_parts, text='零件库')
        tk.Label(self.tab_parts, text='从机械设计手册学到的零件。展开分类 → 双击「★」填入示例描述。',
                 bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                 wraplength=270, justify='left').pack(anchor='w', padx=12, pady=(10, 4))
        srch = tk.Frame(self.tab_parts, bg=TH.PANEL)
        srch.pack(fill='x', padx=12)
        self.var_partsrch = tk.StringVar()
        pent = ttk.Entry(srch, textvariable=self.var_partsrch)
        pent.pack(side='left', fill='x', expand=True)
        pent.bind('<KeyRelease>', lambda e: self.refresh_parts())
        ttk.Button(srch, text='清', style='Ghost.TButton', width=3,
                   command=lambda: (self.var_partsrch.set(''), self.refresh_parts())
                   ).pack(side='left', padx=(4, 0))
        ptv = tk.Frame(self.tab_parts, bg=TH.PANEL)
        ptv.pack(fill='both', expand=True, padx=12, pady=(6, 12))
        self.tree_parts = ttk.Treeview(ptv, columns=('n',), show='tree',
                                       selectmode='browse')
        self.tree_parts.column('#0', width=206, anchor='w')
        self.tree_parts.pack(side='left', fill='both', expand=True)
        psb = ttk.Scrollbar(ptv, command=self.tree_parts.yview)
        psb.pack(side='right', fill='y', padx=(5, 0))
        self.tree_parts.configure(yscrollcommand=psb.set)
        self.tree_parts.bind('<Double-1>', self._on_part_pick)
        self.refresh_parts()

        self.tab_help = tk.Frame(self.nb, bg=TH.PANEL)
        self.nb.add(self.tab_help, text='用法')
        self.txt_help = tk.Text(self.tab_help, height=10, width=26, wrap='word',
                                font=TH.F['body'], relief='flat', bd=0,
                                bg=TH.PANEL, fg=TH.TEXT, padx=12, pady=10)
        self.txt_help.pack(fill='both', expand=True)
        self.txt_help.insert('1.0', HELP_TEXT)
        self.txt_help.configure(state='disabled')

    def _build_status(self):
        tk.Frame(self, bg=TH.BORDER, height=1).pack(side='bottom', fill='x')
        bar = tk.Frame(self, bg=TH.PANEL, height=32)
        bar.pack(side='bottom', fill='x')
        bar.pack_propagate(False)
        self._dot = tk.Canvas(bar, width=10, height=10, bg=TH.PANEL,
                              highlightthickness=0)
        self._dot.pack(side='left', padx=(12, 7))
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=TH.OK, outline='')
        self.lbl_status = tk.Label(bar, text='就绪', bg=TH.PANEL, fg=TH.TEXT_DIM,
                                   font=TH.F['body'])
        self.lbl_status.pack(side='left')
        self.progress = ttk.Progressbar(bar, length=int(168 * self.ui_scale),
                                        mode='determinate')
        self.progress.pack(side='right', padx=12)
        self.lbl_driver = tk.Label(bar, text='几何内核: 内置 BSP-CSG', bg=TH.PANEL,
                                   fg=TH.TEXT_MUTE, font=TH.F['small'])
        self.lbl_driver.pack(side='right', padx=6)

    # ==================================================================
    # 视图同步
    # ==================================================================
    def switch_theme(self):
        """切换浅色/深色。配色在建控件时就固化了, 所以存盘后重启界面进程。"""
        want = 'dark' if self.var_dark.get() else 'light'
        if want == TH.MODE:
            return
        self.cfg['theme'] = want
        try:
            cfgmod.save(self.cfg)
        except Exception as exc:
            self.log('主题写入配置失败: %s' % exc)
        what = '深色' if want == 'dark' else '浅色'
        msg = '已切换到「%s模式」。\n\n界面需要重启一次才能全部换色, 现在重启吗?' % what
        if not messagebox.askyesno('切换主题', msg):
            return
        self._relaunch()

    def _relaunch(self):
        """用同一个解释器重新拉起自己 (工作目录设到项目根, 保证 -m 能导入)。"""
        try:
            if self._busy:
                self.cancel_build()
            args = [sys.executable, '-m', 'ai_modeler']
            for a in sys.argv[1:]:
                if a not in ('-m', 'ai_modeler'):
                    args.append(a)
            self.destroy()
            os.chdir(ROOT_DIR)
            os.execv(sys.executable, args)
        except Exception as exc:
            messagebox.showerror('重启失败', '请手动关闭后重新打开程序。\n\n%s' % exc)

    def _sync_view(self):
        self.viewer.show_edges = self.var_edges.get()
        self.viewer.show_grid = self.var_grid.get()
        self.viewer.show_axes = self.var_axes.get()
        self.viewer.cam.perspective = self.var_persp.get()
        self.viewer.redraw()

    # ==================================================================
    # 生成流程
    # ==================================================================
    def _append_chat(self, role, text, images=None):
        if not hasattr(self, 'chat_history'):
            return
        labels = {'user': '你', 'assistant': 'AI Modeler Agent', 'system': 'AgentLoop'}
        tag = role if role in {'user', 'assistant', 'system'} else 'system'
        body = str(text or '').strip()
        self.chat_history.configure(state='normal')
        self.chat_history.insert('end', labels.get(role, 'AgentLoop') + '\n', tag)
        has_preview = False
        for path in images or []:
            photo = self._load_thumbnail(path, (96, 96))
            if photo is None:
                self.chat_history.insert(
                    'end', '[图片无法预览：%s] ' % os.path.basename(path), tag)
                continue
            self.chat_history.image_create('end', image=photo, padx=3, pady=3)
            self._chat_photo_refs.append(photo)
            has_preview = True
        if has_preview:
            self.chat_history.insert('end', '\n', tag)
        self.chat_history.insert('end', (body or '（空消息）') + '\n\n', tag)
        self.chat_history.see('end')
        self.chat_history.configure(state='disabled')

    def _load_thumbnail(self, path, size):
        """Load a correctly sized Tk image for both chat history and attachments."""
        try:
            from PIL import Image, ImageTk
            with Image.open(path) as source:
                resampling = getattr(Image, 'Resampling', Image).LANCZOS
                source.thumbnail(size, resampling)
                return ImageTk.PhotoImage(source.copy())
        except Exception:
            try:
                photo = tk.PhotoImage(file=path)
                divisor = max(1, (max(photo.width(), photo.height()) + max(size) - 1)
                              // max(size))
                return photo.subsample(divisor, divisor) if divisor > 1 else photo
            except Exception:
                return None

    def attach_reference_images(self):
        paths = filedialog.askopenfilenames(
            title='添加多视角参考图片',
            filetypes=[('图片', '*.png *.jpg *.jpeg *.webp *.gif'),
                       ('所有文件', '*.*')])
        if not paths:
            return
        self._add_reference_images(paths)

    def _add_reference_images(self, paths):
        from ai_modeler.agent.agentloop import MAX_REFERENCE_IMAGES
        combined = list(dict.fromkeys(self.reference_images + list(paths)))
        if len(combined) > MAX_REFERENCE_IMAGES:
            messagebox.showwarning('参考图片过多',
                                   '每轮最多附加 %d 张图片。' % MAX_REFERENCE_IMAGES)
            combined = combined[:MAX_REFERENCE_IMAGES]
        missing = [path for path in combined if not os.path.isfile(path)]
        if missing:
            messagebox.showerror('图片不可用', '找不到文件：\n' + '\n'.join(missing[:5]))
            return
        self.reference_images = combined
        self._update_reference_label()

    def _paste_clipboard_image(self, _event=None):
        """Attach clipboard images while leaving normal text paste to Tk."""
        try:
            from ai_modeler.sdk.clipboard_image import capture_clipboard_image
            path = capture_clipboard_image()
        except Exception as exc:
            self.status('剪贴板图片读取失败: %s' % exc, TH.ERR)
            return 'break'
        if not path:
            return None  # Let Tk's default binding paste text as usual.
        before = len(self.reference_images)
        self._add_reference_images([path])
        if len(self.reference_images) > before:
            self.status('已从剪贴板添加参考图片；发送前仍可继续输入文字。', TH.OK)
        return 'break'

    def clear_reference_images(self):
        self.reference_images = []
        self._update_reference_label()

    def _update_reference_label(self):
        names = [os.path.basename(path) for path in self.reference_images]
        if hasattr(self, 'lbl_attachments'):
            label = ('%d 张图片' % len(names)) if names else ''
            self.lbl_attachments.configure(text=label)
        if hasattr(self, 'attachment_preview_frame'):
            self._render_reference_previews()
        if hasattr(self, 'txt'):
            self._resize_prompt()

    def _render_reference_previews(self):
        for child in self.attach_inner.winfo_children():
            child.destroy()
        self._attachment_photo_refs = []
        if not self.reference_images:
            self.attachment_preview_frame.pack_forget()
            self.attach_scrollbar.pack_forget()
            return

        if not self.attachment_preview_frame.winfo_manager():
            self.attachment_preview_frame.pack(fill='x', before=self.txt)
        for path in self.reference_images:
            tile = tk.Frame(
                self.attach_inner, width=112, height=108, bg=TH.PANEL_ALT,
                highlightthickness=1, highlightbackground=TH.BORDER)
            tile.pack(side='left', padx=(0, 8), pady=2)
            tile.pack_propagate(False)
            image_label = tk.Label(
                tile, text='图片', bg=TH.PANEL_ALT,
                fg=TH.TEXT_MUTE, font=TH.F['tiny'])
            photo = self._load_thumbnail(path, (88, 88))
            if photo is not None:
                image_label.configure(image=photo, text='')
                self._attachment_photo_refs.append(photo)
            image_label.place(relx=0.5, rely=0.5, anchor='center')
            tk.Button(
                tile, text='×', command=lambda item=path: self.remove_reference_image(item),
                relief='flat', bd=0, bg=TH.PANEL_ALT, fg=TH.TEXT_MUTE,
                activebackground=TH.BTN_HI, font=TH.F['body_b'], cursor='hand2',
                padx=2, pady=0
            ).place(relx=1.0, x=-3, y=3, anchor='ne')

        self.after_idle(self._sync_attachment_strip)

    def _sync_attachment_strip(self, _event=None):
        if not hasattr(self, 'attach_canvas') or not self.reference_images:
            return
        bounds = self.attach_canvas.bbox('all')
        if bounds:
            self.attach_canvas.configure(scrollregion=bounds)
        required = self.attach_inner.winfo_reqwidth()
        visible = max(0, self.attach_canvas.winfo_width() - 6)
        if required > visible:
            if not self.attach_scrollbar.winfo_manager():
                self.attach_scrollbar.pack(fill='x', padx=9, pady=(0, 4))
        else:
            self.attach_scrollbar.pack_forget()

    def _scroll_attachment_strip(self, event):
        direction = -1 if event.delta > 0 else 1
        self.attach_canvas.xview_scroll(direction * 3, 'units')
        return 'break'

    def remove_reference_image(self, path):
        self.reference_images = [item for item in self.reference_images if item != path]
        self._update_reference_label()

    def _model_button_text(self):
        llmcfg = self.cfg.get('llm', {}) if isinstance(self.cfg.get('llm'), dict) else {}
        if llmcfg.get('enabled'):
            model = (llmcfg.get('model') or '模型未填写').strip()
        else:
            model = '本地规则'
        if len(model) > 18:
            model = model[:17] + '…'
        return '⚡ %s ▾' % model

    def _resize_prompt(self, _event=None):
        """Keep the composer compact, expanding only when the user adds lines."""
        if not hasattr(self, 'txt'):
            return
        if getattr(self, '_input_placeholder_active', False):
            lines = 1
        else:
            try:
                counted = self.txt.count('1.0', 'end-1c', 'displaylines')
                lines = int(counted[0]) if counted else 1
            except Exception:
                lines = max(1, int(self.txt.index('end-1c').split('.')[0]))
        max_lines = 5 if not self.reference_images else 4
        self.txt.configure(height=max(2, min(max_lines, lines)))

    def _clear_prompt_placeholder(self, _event=None):
        self.composer.configure(highlightbackground=TH.ACCENT)
        if getattr(self, '_input_placeholder_active', False):
            self.txt.delete('1.0', 'end')
            self._input_placeholder_active = False
            self.txt.configure(fg=TH.TEXT)

    def _restore_prompt_placeholder(self, _event=None):
        if hasattr(self, 'composer'):
            self.composer.configure(highlightbackground=TH.BORDER)
        if not hasattr(self, 'txt') or self.txt.get('1.0', 'end').strip():
            return
        self.txt.delete('1.0', 'end')
        self.txt.insert('1.0', self._input_placeholder)
        self.txt.configure(fg=TH.TEXT_MUTE)
        self._input_placeholder_active = True

    def _set_prompt_text(self, text):
        self.txt.delete('1.0', 'end')
        self._input_placeholder_active = False
        self.txt.configure(fg=TH.TEXT)
        if text:
            self.txt.insert('1.0', text)
        elif self.txt.focus_get() is not self.txt:
            self._restore_prompt_placeholder()
        self._resize_prompt()

    def _current_prompt(self):
        if getattr(self, '_input_placeholder_active', False):
            return ''
        return self.txt.get('1.0', 'end').strip()

    def new_chat(self):
        if self._busy:
            messagebox.showinfo('AgentLoop 正在运行', '请先停止当前任务，再新建对话。')
            return
        self._agent_context = []
        self._agent_context_summary = ''
        self._agent_summary_through = 0
        self._last_plan = None
        self.reference_images = []
        self._clear_agent_live_preview()
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.delete('1.0', 'end')
        self.txt_agent_events.configure(state='disabled')
        self._update_reference_label()
        self.chat_history.configure(state='normal')
        self.chat_history.delete('1.0', 'end')
        self._chat_photo_refs = []
        self.chat_history.insert(
            'end', 'AI Modeler AgentLoop\n'
            '可以连续讨论当前模型、提出修改，或开始新建。Agent 会按本轮需要选择识别、建模和复核深度；'
            '预览会在右侧 Agent 页实时更新。模型只在本地渲染器展示，不自动同步 CATIA。\n',
            'system')
        self.chat_history.configure(state='disabled')
        self._set_prompt_text('')
        self.clear_features()

    def _on_return(self, e):
        if e.state & 0x0001:
            return None
        self.generate()
        return 'break'

    def _on_sample(self, _e=None):
        name = self.cmb_sample.get()
        for (label, prompt) in SAMPLES:
            if label == name:
                self.load_sample(prompt)
                break

    def load_sample(self, prompt: str):
        self._set_prompt_text(prompt)
        self.txt.focus_set()

    def generate(self):
        if getattr(self, '_closing', False):
            return
        # Queue one follow-up send; retain its images as part of the same request.
        if self._busy:
            self._pending_prompt = self._current_prompt()
            self._pending_images = list(self.reference_images)
            self._set_prompt_text('')
            self.clear_reference_images()
            self.status('正在计算上一个模型, 稍后自动生成…')
            return
        self._agent_candidate_notice = None
        self._pending_prompt = None
        self._pending_images = []
        text = self._current_prompt()
        images = list(self.reference_images)
        if not text and not images:
            self.status('输入文字或添加参考图片后发送', TH.WARN)
            return
        llmcfg = self.cfg.get('llm', {}) if isinstance(self.cfg.get('llm'), dict) else {}
        self._append_chat('user', text or '请根据参考图建立模型。', images)
        self._set_prompt_text('')
        self.clear_reference_images()

        if llmcfg.get('enabled') and llmcfg.get('api_key'):
            self._agent_context.append({
                'role': 'user',
                'content': (text or '请根据参考图建立模型。') +
                           (('\n参考图：' + '、'.join(os.path.basename(p) for p in images))
                            if images else ''),
                'image_paths': list(images),
            })
            self._agent_generate(text, images)
            return

        if llmcfg.get('enabled'):
            reason = '大模型已启用，但接口地址、模型名称或 API Key 不完整。'
            self._append_chat('assistant', reason + ' 请检查「工具 → AI 大模型设置」。')
            self.status('大模型配置不完整', TH.ERR)
            return

        if images:
            reason = '多图理解和视觉复核需要配置支持图像输入与 tools 的大模型。'
            self._append_chat('assistant', reason + ' 请先在「工具 → AI 大模型设置」中配置。')
            self.status('需要配置多模态大模型', TH.ERR)
            return

        self._last_plan = None
        quick = NLP.parse(text)
        if quick.ok:
            self._agent_context.append({'role': 'user', 'content': text})
            self._agent_context.append({
                'role': 'assistant',
                'content': '当前未启用大模型，使用内置规则完成本地快速建模；未运行视觉复核。',
            })
            self._append_chat('assistant',
                              '当前未启用大模型，使用内置规则完成本地快速建模；未运行视觉复核。')
            self._finish_generate(text, None)
            return
        self._agent_context.append({'role': 'user', 'content': text})
        self._agent_context.append({
            'role': 'assistant',
            'content': '内置规则未能识别。配置支持 tools 的大模型后，可运行分阶段 AgentLoop。',
        })
        self._append_chat('assistant',
                          '内置规则未能识别。请配置支持 tools 的大模型后运行完整 AgentLoop。')
        self._finish_generate(text, None)

    # --------------------------------------------------------------
    def _budget(self):
        b = self.cfg.get('build', {}) if isinstance(self.cfg.get('build'), dict) else {}
        try:
            return max(5.0, float(b.get('time_budget', 30.0)))
        except (TypeError, ValueError):
            return 30.0

    def _face_cap(self):
        b = self.cfg.get('build', {}) if isinstance(self.cfg.get('build'), dict) else {}
        try:
            return max(2000, int(b.get('max_faces', 60000)))
        except (TypeError, ValueError):
            return 60000

    def _append_agent_event(self, message):
        if not hasattr(self, 'txt_agent_events'):
            return
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.insert('end', str(message) + '\n')
        if int(self.txt_agent_events.index('end-1c').split('.')[0]) > 240:
            self.txt_agent_events.delete('1.0', '41.0')
        self.txt_agent_events.see('end')
        self.txt_agent_events.configure(state='disabled')

    def _clear_agent_live_preview(self):
        self._last_agent_preview_path = None
        self._agent_live_photo = None
        for row in getattr(self, '_agent_context', []):
            if isinstance(row, dict) and row.get('image_role') == 'local_preview':
                row.pop('image_paths', None)
                row['content'] = str(row.get('content') or '').replace(
                    '\n随附为当前对话最近一次显示的本地模型预览，供后续几何讨论参考；'
                    '它不是用户提供的原始参考图。', '')
                row.pop('image_role', None)
        if hasattr(self, 'lbl_agent_preview'):
            self.lbl_agent_preview.configure(
                image='', text='建模过程中生成的预览会显示在这里。')
        if hasattr(self, 'lbl_agent_stage'):
            self.lbl_agent_stage.configure(text='等待任务')
        if hasattr(self, 'lbl_agent_detail'):
            self.lbl_agent_detail.configure(text='')

    def _show_agent_live_preview(self, payload):
        """Display a worker-produced PNG in the right Agent tab on Tk's UI thread."""
        payload = payload if isinstance(payload, dict) else {}
        preview = payload.get('preview') if isinstance(payload.get('preview'), dict) else {}
        path = preview.get('preview_image')
        stage = str(payload.get('stage') or 'preview')
        try:
            if path and os.path.isfile(path):
                self._last_agent_preview_path = path
                photo = tk.PhotoImage(file=path)
                available = max(180, self.tab_agent.winfo_width() - 32)
                factor = max(1, (photo.width() + available - 1) // available)
                if factor > 1:
                    photo = photo.subsample(factor, factor)
                self._agent_live_photo = photo
                self.lbl_agent_preview.configure(image=photo, text='')
            else:
                self.lbl_agent_preview.configure(
                    image='', text='预览图暂时无法显示。\n' + str(path or ''))
                self._agent_live_photo = None
        except Exception as exc:
            self.lbl_agent_preview.configure(
                image='', text='预览图暂时无法显示：%s' % exc)
            self._agent_live_photo = None
        dims = preview.get('dimensions') if isinstance(preview.get('dimensions'), dict) else {}
        size = dims.get('size') or []
        size_text = ' × '.join('%.2f' % float(value) for value in size) + ' mm' if size else '尺寸待计算'
        views = '、'.join(preview.get('preview_image_views') or [])
        self.lbl_agent_stage.configure(text='实时预览 · %s' % stage)
        self.lbl_agent_detail.configure(
            text='状态：%s\n外形：%s\n视图：%s\n%s' % (
                preview.get('state', 'unknown'), size_text, views or '—',
                preview.get('preview_image_source') or '本地网格预览'))
        self._append_agent_event('预览已更新：%s；%s' % (stage, size_text))

    def _agent_generate(self, text, images):
        from ai_modeler.agent import agentloop
        self._busy = True
        self._t0 = time.time()
        agent_cfg = self.cfg.get('agentloop', {}) if isinstance(self.cfg.get('agentloop'), dict) else {}
        try:
            self._budget_s = max(60.0, min(1800.0,
                                           float(agent_cfg.get('total_timeout_sec', 600))))
        except (TypeError, ValueError):
            self._budget_s = 600.0
        self._cancel = threading.Event()
        self.lbl_busy.configure(text='AgentLoop 运行中…')
        try:
            self.btn_stop.configure(state='normal')
        except Exception:
            pass
        self.progress.configure(value=0)
        self.status('Agent 正在判断本轮工作范围…')
        self.lbl_agent_stage.configure(text='正在判断本轮需要的工作…')
        self.lbl_agent_detail.configure(text='等待回合路由结果')
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.delete('1.0', 'end')
        self.txt_agent_events.configure(state='disabled')
        self.nb.select(self.tab_agent)
        self._q = q = queue.Queue()
        request = {'text': text, 'images': list(images), 'id': time.monotonic_ns()}
        self._active_request = request
        context = [dict(item) for item in self._agent_context[:-1]]
        context_summary = self._agent_context_summary
        summary_through = self._agent_summary_through
        current_plan = json.loads(json.dumps(self._last_plan)) if self._last_plan else None
        current_document = None
        if hasattr(self.doc, 'features'):
            document_snapshot = {
                'name': self.doc.name,
                'features': [feature.to_dict() for feature in self.doc.features],
            }
            # Avoid duplicating the whole feature tree on ordinary turns. Include
            # it whenever the live tree cannot be proven identical to the plan
            # (manual edits, an opened project, or a legacy-parser model).
            if not isinstance(current_plan, dict):
                if document_snapshot['features']:
                    current_document = document_snapshot
            else:
                try:
                    from ai_modeler.sdk import cadplan
                    planned_document = cadplan.plan_to_document(current_plan)
                    planned_features = [feature.to_dict()
                                        for feature in planned_document.features]
                    if planned_features != document_snapshot['features']:
                        current_document = document_snapshot
                except Exception:
                    # A conversion failure must never make the older plan look
                    # like an authoritative snapshot of the visible model.
                    current_document = document_snapshot
        cancel = self._cancel
        cfg_snapshot = json.loads(json.dumps(self.cfg))
        request_id = request['id']

        def work():
            try:
                result = agentloop.run_agent_loop(
                    text, image_paths=images, cfg=cfg_snapshot,
                    conversation_history=context,
                    conversation_summary=context_summary,
                    summary_through=summary_through,
                    progress=lambda message: q.put(('agent_progress', message, None)),
                    cancel_event=cancel, current_plan=current_plan,
                    current_document=current_document,
                    on_preview=lambda plan, preview, stage: q.put((
                        'agent_preview', {
                            'request_id': request_id,
                            'plan': plan,
                            'preview': preview,
                            'stage': stage,
                        }, None)))
                q.put(('agent_ok', result, None))
            except Exception as e:
                message = str(e)
                record_path = getattr(e, 'run_record', None)
                if record_path:
                    message += '\n运行记录：' + record_path
                q.put(('agent_err', message, traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()
        self.after(80, self._poll_agent)

    def _poll_agent(self):
        if not self._q:
            return
        try:
            while True:
                kind, value, detail = self._q.get_nowait()
                if kind == 'agent_progress':
                    self._append_chat('system', value)
                    self.lbl_agent_stage.configure(text=str(value))
                    self._append_agent_event(value)
                    self.status(value)
                    continue
                if kind == 'agent_preview':
                    request = self._active_request or {}
                    if value.get('request_id') == request.get('id'):
                        self._show_agent_live_preview(value)
                        self.status('建模过程预览已更新', TH.WARN)
                    continue
                self._unlock_ui()
                request = self._active_request or {'text': '', 'images': []}
                text = request['text']
                if kind == 'agent_ok':
                    result = value
                    self._agent_context_summary = result.get(
                        'conversation_summary', self._agent_context_summary)
                    self._agent_summary_through = result.get(
                        'summary_through', self._agent_summary_through)
                    reply = result.get('reply', 'AgentLoop 已结束。')
                    self._append_chat('assistant', reply)
                    assistant_context = {'role': 'assistant', 'content': reply}
                    preview_path = (result.get('preview') or {}).get('preview_image')
                    if preview_path and os.path.isfile(preview_path):
                        self._last_agent_preview_path = preview_path
                    if self._last_agent_preview_path and os.path.isfile(self._last_agent_preview_path):
                        assistant_context['content'] += (
                            '\n随附为当前对话最近一次显示的本地模型预览，供后续几何讨论参考；'
                            '它不是用户提供的原始参考图。')
                        assistant_context['image_paths'] = [self._last_agent_preview_path]
                        assistant_context['image_role'] = 'local_preview'
                    self._agent_context.append(assistant_context)
                    self.log('AgentLoop 状态：%s；复核轮次：%s；自动返修：%s；工具调用：%s；预览：%s' %
                             (result.get('state'), result.get('review_rounds', 0),
                              result.get('repair_cycles', 0), result.get('tool_calls', 0),
                              result.get('preview_calls', 0)))
                    if result.get('preview', {}).get('preview_image'):
                        self.log('本地复核图：' + result['preview']['preview_image'])
                    if result.get('run_record'):
                        self.log('AgentLoop 运行记录：' + result['run_record'])
                    routing = result.get('routing') or {}
                    self.lbl_agent_stage.configure(
                        text='本轮完成 · %s' % (routing.get('action') or result.get('state')))
                    self.lbl_agent_detail.configure(
                        text='工作流：%s\n复核：%s（%s 轮）\n工具调用：%s · 预览：%s\n%s' % (
                            routing.get('action', '—'), routing.get('review_mode', '—'),
                            result.get('review_rounds', 0), result.get('tool_calls', 0),
                            result.get('preview_calls', 0), routing.get('reason', '')))
                    if result.get('state') == 'conversation':
                        self._append_agent_event('连续对话已回复；当前模型与特征树保持不变。')
                        self.status('连续对话已回复')
                    elif result.get('state') == 'accepted':
                        try:
                            from ai_modeler.sdk import cadplan
                            self._agent_candidate_notice = None
                            self._last_plan = result['plan']
                            plan_doc = cadplan.plan_to_document(self._last_plan)
                            plan_doc.source_prompt = text
                            features = [feature.to_dict() for feature in plan_doc.features]
                            self._finish_generate(text, features, allow_legacy_fallback=False)
                        except Exception as exc:
                            message = 'AgentLoop 已接受 CADPlan，但转成本地模型失败：%s' % exc
                            self._append_chat('assistant', message)
                            self._agent_context.append({'role': 'assistant', 'content': message})
                            self.log(message)
                            self.status('CADPlan 转换失败', TH.ERR)
                    elif result.get('state') in {'previewed', 'reviewed'}:
                        candidate_shown = self._show_agent_candidate(text, result)
                        if candidate_shown and result.get('state') == 'reviewed':
                            self.status('多视图检查已通过；未运行独立相似度审查', TH.WARN)
                        elif candidate_shown:
                            self.status('计划已校验并预览；当前是未复核候选', TH.WARN)
                        else:
                            self.status('AgentLoop 已生成结果，但候选无法显示', TH.ERR)
                    else:
                        candidate_shown = self._show_agent_candidate(text, result)
                        if candidate_shown:
                            self.status('AgentLoop 尚未接受；当前仅显示候选模型', TH.WARN)
                        else:
                            self.status('AgentLoop 已停止：需要补充信息或仍有待处理偏差', TH.WARN)
                elif kind == 'agent_err':
                    message = 'AgentLoop 失败：' + str(value)
                    self._append_chat('assistant', message)
                    self._agent_context.append({'role': 'assistant', 'content': message})
                    self.log(message)
                    self.log(detail or '')
                    self.lbl_agent_stage.configure(text='本轮失败')
                    self.lbl_agent_detail.configure(text=str(value)[:1200])
                    self._append_agent_event(message)
                    self.status('AgentLoop 未完成', TH.ERR)
                self._active_request = None
                if not self._busy:
                    self._resume_pending_request()
                return
        except queue.Empty:
            pass
        if self._cancel is not None and self._cancel.is_set():
            self._q = queue.Queue()
            self._active_request = None
            self._unlock_ui()
            self._append_chat('assistant', '已取消当前 AgentLoop。')
            self.status('AgentLoop 已取消', TH.WARN)
            self._resume_pending_request()
            return
        if time.time() - getattr(self, '_t0', time.time()) > self._budget_s + 15:
            if self._cancel is not None:
                self._cancel.set()
            self._q = queue.Queue()
            self._active_request = None
            self._unlock_ui()
            message = 'AgentLoop 超过总时限，已停止等待；没有回退到规则猜测。'
            self._append_chat('assistant', message)
            self._agent_context.append({'role': 'assistant', 'content': message})
            self.status('AgentLoop 超时', TH.ERR)
            self._resume_pending_request()
            return
        self.after(120, self._poll_agent)

    def _show_agent_candidate(self, text, result):
        """Render a valid but unaccepted CADPlan so a user can inspect it."""
        plan = result.get('plan') if isinstance(result, dict) else None
        validation = result.get('validation') if isinstance(result, dict) else None
        if (not isinstance(plan, dict) or not isinstance(validation, dict)
                or validation.get('state') != 'valid'):
            return False
        try:
            from ai_modeler.sdk import cadplan
            self._last_plan = plan
            plan_doc = cadplan.plan_to_document(plan)
            plan_doc.source_prompt = text
            features = [feature.to_dict() for feature in plan_doc.features]
            shown = self._finish_generate(text, features, allow_legacy_fallback=False)
            if not shown:
                return False
            state = str(result.get('state') or 'unaccepted')
            self._agent_candidate_notice = (
                '候选预览已显示；AgentLoop 状态为 %s，模型尚未通过最终复核。' % state)
            self._append_chat('system', self._agent_candidate_notice)
            self.log(self._agent_candidate_notice)
            return True
        except Exception as exc:
            message = '有效 CADPlan 候选无法显示：%s' % exc
            self._append_chat('system', message)
            self.log(message)
            return False

    def _finish_generate(self, text, llm_features, allow_legacy_fallback=True):
        res = None
        # AI 结果合理性检查: 一个模型至少要有一个"实体"特征, 不能全是切除
        if llm_features and not any(
                (f.get('op') or 'add') != 'cut' for f in llm_features):
            self.log('本地 AI 只给出了切除特征, 缺实体, 结果作废并回退')
            llm_features = None
        if llm_features:
            try:
                self.doc = DM.Document.from_dict({'name': 'Part1',
                                                  'features': llm_features})
                self.doc.source_prompt = text
                res = NLP.ParseResult()
                res.doc = self.doc
                res.recognized = len(self.doc.features)
                res.steps.append('由本地 AI 解析生成 %d 个特征' % len(self.doc.features))
            except Exception as e:
                self.log('本地 AI 结果无法使用, 回退到内置规则引擎: %s' % e)
                res = None
        if res is None and allow_legacy_fallback:
            res = NLP.parse(text)
        if res is None:
            message = 'AgentLoop CADPlan 无法转换为本地特征；已停止，未回退到旧文本解析。'
            self._append_chat('assistant', message)
            self._agent_context.append({'role': 'assistant', 'content': message})
            self.log(message)
            self.status('CADPlan 转换失败', TH.ERR)
            self.doc = DM.Document('Part1')
            self.refresh_tree()
            self.viewer.set_model([], fit=True)
            self.lbl_stat.delete('1.0', 'end')
            return False
        if not res.ok:
            self.log('解析失败: ' + res.message)
            self.status('未识别出可建模的指令')
            self.doc = DM.Document('Part1')
            self.refresh_tree()
            self.viewer.set_model([], fit=True)
            self.lbl_stat.delete('1.0', 'end')
            if self.cfg.get('online', {}).get('enabled', True):
                self.log('本地词库不认识, 正在联网搜索…')
                self.online_lookup(text, auto=True)
            else:
                messagebox.showwarning('无法识别', res.message +
                                       '\n\n提示: 描述里至少要有一个"做出来"的东西, '
                                       '例如「长方体」「圆柱」「孔」「法兰」等。')
            return False
        self.doc = res.doc
        self.log('—' * 46)
        self.log('需求: ' + text)
        for line in res.steps:
            self.log('  · ' + line)
        for w in res.warnings:
            self.log('  ! ' + w)
        self.refresh_tree()
        self.viewer.polys = []
        self.run_build()
        return True

    def run_build(self):
        if self._busy:
            return
        self._busy = True
        self._t0 = time.time()
        self._budget_s = self._budget()
        self._cancel = threading.Event()
        self.lbl_busy.configure(text='正在计算…')
        try:
            self.btn_stop.configure(state='normal')
        except Exception:
            pass
        self.progress.configure(value=0)
        self.status('正在构建几何…')
        self._q = q = queue.Queue()
        deadline = time.time() + self._budget_s
        cancel = self._cancel
        cap = self._face_cap()

        def worker():
            try:
                body = self.doc.build(progress=lambda m, f: q.put(('p', m, f)),
                                      deadline=deadline, cancel=cancel, max_faces=cap)
                q.put(('ok', body, None))
            except DM.BuildAborted as e:
                q.put(('abort', e, None))
            except Exception as e:
                q.put(('err', str(e), traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        self.after(40, self._poll)

    def cancel_build(self):
        """用户在计算过程中点「停止」。"""
        if not self._busy:
            return
        if self._cancel is not None:
            self._cancel.set()
        self.status('正在停止…')
        self.lbl_busy.configure(text='正在停止…')

    def _unlock_ui(self):
        self._busy = False
        self.lbl_busy.configure(text='')
        try:
            self.btn_stop.configure(state='disabled')
        except Exception:
            pass

    def _resume_pending_request(self):
        """Resume one queued turn with both its prompt and its image attachments."""
        if self._pending_prompt is None:
            self._pending_images = []
            return False
        prompt = self._pending_prompt
        images = list(self._pending_images)
        self._pending_prompt = None
        self._pending_images = []
        self._set_prompt_text(prompt)
        self.reference_images = images
        self._update_reference_label()
        self.after(40, self.generate)
        return True

    def _poll(self):
        # 兜底看门狗: 万一工作线程卡在某一次超长布尔运算里,
        # 也要把界面解锁, 不然用户会以为程序死了
        if self._busy and (time.time() - getattr(self, '_t0', time.time())) > self._budget_s + 25:
            self._q = queue.Queue()          # 丢弃旧线程的结果
            self._unlock_ui()
            self.status('计算超时, 已解锁', TH.ERR)
            self.log('!! 计算长时间没有返回, 已强制解锁。'
                     '后台线程可能仍在跑, 完成后结果会被忽略。')
            messagebox.showwarning(
                '计算超时',
                '这次计算超过了 %.0f 秒还没有结束, 已经中止。\n\n'
                '常见原因和解决办法:\n'
                '  · 特征太多 / 阵列数量太大 → 减少数量\n'
                '  · 圆周分段太高 → 在参数页调到 24 或 16\n'
                '  · 尺寸自相矛盾 (孔比零件还大、孔互相重叠) → 检查尺寸\n\n'
                '也可以点右上角「停止」主动中断。'
                % (self._budget_s + 25))
            self._resume_pending_request()
            return
        try:
            while True:
                kind, a, b = self._q.get_nowait()
                if kind == 'p':
                    self.progress.configure(value=int(100 * (b or 0)))
                    self.status(a)
                elif kind == 'ok':
                    self._finish_build(a)
                    return
                elif kind == 'abort':
                    self._unlock_ui()
                    self.progress.configure(value=0)
                    self.status('计算已中止: ' + a.reason, TH.ERR)
                    self.log('计算中止: ' + a.reason)
                    if a.hint:
                        for line in a.hint.split('\n'):
                            self.log('  ' + line)
                    self.doc.last_log = self.doc.last_log or []
                    messagebox.showwarning('计算已中止', a.reason + '\n\n' + (a.hint or ''))
                    self._resume_pending_request()
                    return
                elif kind == 'err':
                    self._unlock_ui()
                    self.status('构建失败', TH.ERR)
                    self.log('构建失败: ' + a)
                    self.log(b or '')
                    messagebox.showerror('构建失败', a)
                    self._resume_pending_request()
                    return
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _finish_build(self, body):
        if self._pending_prompt is not None:
            # 用户在上一次计算途中又提交了新需求: 优先处理它
            self._unlock_ui()
            self._resume_pending_request()
            return
        self.lbl_busy.configure(text='')
        self.progress.configure(value=100)
        self.viewer.set_model(body, fit=False)
        self.viewer.fit_view()
        st = self.doc.stats()
        self.update_stats(st)
        self.viewer.set_info([
            '特征 %d 项   面片 %d' % (len(self.doc.features), len(body)),
            '体积 %.2f cm³' % (st['volume'] / 1000.0),
        ])
        candidate_notice = getattr(self, '_agent_candidate_notice', None)
        if candidate_notice:
            self.status(candidate_notice, TH.WARN)
        else:
            self.status('完成: %d 个特征, %d 个面片, 耗时 %.2fs' %
                        (len(self.doc.features), len(body), time.time() - getattr(self, '_t0', time.time())))
        for line in self.doc.last_log[-6:]:
            self.log('  ' + line)
        self._unlock_ui()          # 出图之后再解锁, 保证 _busy=False 就等于"结果已显示"

    def status(self, text, color=None):
        self.lbl_status.configure(text=text)
        if color is None:
            color = TH.WARN if self._busy else TH.OK
        try:
            self._dot.itemconfigure(self._dot_id, fill=color)
        except Exception:
            pass

    def log(self, text):
        self.txt_log.insert('end', text + '\n')
        self.txt_log.see('end')

    def update_stats(self, st):
        bb = st['bbox']
        lines = [
            '特征数量 : %d' % len(self.doc.features),
            '多边形面 : %d' % st['faces'],
            '三角面片 : %d' % st['triangles'],
        ]
        if bb:
            lines += [
                '包围盒 X : %.2f ~ %.2f' % (bb[0], bb[3]),
                '包围盒 Y : %.2f ~ %.2f' % (bb[1], bb[4]),
                '包围盒 Z : %.2f ~ %.2f' % (bb[2], bb[5]),
                '外形尺寸 : %.2f × %.2f × %.2f' % st['size'],
            ]
        lines += [
            '体积     : %.1f mm³' % st['volume'],
            '体积     : %.3f cm³' % (st['volume'] / 1000.0),
            '表面积   : %.1f mm²' % st['area'],
        ]
        self.lbl_stat.delete('1.0', 'end')
        self.lbl_stat.insert('1.0', '\n'.join(lines))

    # ==================================================================
    # 特征树
    # ==================================================================
    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.doc.features:
            self.tree.insert('', 'end', iid='__empty__',
                             values=('', '', '尚无特征 —— 发送建模需求后生成'),
                             tags=('empty',))
            self._on_tree_select()
            return
        for i, f in enumerate(self.doc.features):
            if f.op == DM.CUT:
                op, tag = '切除', 'cut'
            elif f.compound:
                op, tag = '组合', 'comp'
            else:
                op, tag = '合并', 'add'
            desc = f.describe()
            tags = [tag]
            if not f.enabled:
                tags.append('off')
                desc = '（已停用）' + desc
            if i % 2:
                tags.append('stripe')
            self.tree.insert('', 'end', iid=str(i), values=(i + 1, op, desc),
                             tags=tuple(tags))
        self._on_tree_select()

    def _sel_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            i = int(sel[0])
        except (TypeError, ValueError):
            return None
        return i if 0 <= i < len(self.doc.features) else None

    def _on_tree_select(self, _e=None):
        for w in self.param_body.winfo_children():
            w.destroy()
        self._param_vars = {}
        idx = self._sel_index()
        if idx is None:
            return
        f = self.doc.features[idx]
        head = tk.Frame(self.param_body, bg=TH.PANEL)
        head.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        tk.Label(head, text='%d' % (idx + 1), bg=TH.ACCENT, fg='#ffffff',
                 font=TH.F['body_b'], width=2, pady=2).pack(side='left')
        tk.Label(head, text=f.name, bg=TH.PANEL, fg=TH.TEXT, font=TH.F['h2']).pack(
            side='left', padx=8)
        tk.Label(self.param_body, text=f.describe(), bg=TH.PANEL, fg=TH.TEXT_MUTE,
                 font=TH.F['small'], wraplength=258, justify='left').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))
        schema = PARAM_SCHEMA.get(f.kind, [])
        r = 2
        for (key, label) in schema:
            val = f.params.get(key, '')
            if val is None:
                val = ''
            if isinstance(val, (list, tuple)):
                val = '×'.join('%.4g' % v for v in val) if key != 'profile' else '(%d 点)' % len(val)
            tk.Label(self.param_body, text=label, bg=TH.PANEL, fg=TH.TEXT_DIM,
                     font=TH.F['body']).grid(row=r, column=0, sticky='w', pady=2)
            var = tk.StringVar(value=str(val))
            ent = ttk.Entry(self.param_body, textvariable=var, width=11, justify='right')
            ent.grid(row=r, column=1, sticky='e', pady=2)
            self._param_vars[key] = (var, type(f.params.get(key)))
            r += 1
        if f.kind == 'hole':
            var = tk.BooleanVar(value=bool(f.params.get('through', True)))
            tk.Checkbutton(self.param_body, text='通孔 (取消勾选即为盲孔)',
                           variable=var, bg=TH.PANEL, fg=TH.TEXT_DIM,
                           activebackground=TH.PANEL, selectcolor='#ffffff',
                           font=TH.F['small'], bd=0, highlightthickness=0).grid(
                row=r, column=0, columnspan=2, sticky='w', pady=4)
            self._param_vars['through'] = (var, bool)
            r += 1
        ttk.Button(self.param_body, text='应用参数', style='Accent.TButton',
                   command=lambda: self.apply_params(idx)).grid(
            row=r, column=0, columnspan=2, sticky='ew', pady=(12, 4))

    def apply_params(self, idx):
        f = self.doc.features[idx]
        for key, (var, typ) in self._param_vars.items():
            raw = var.get()
            try:
                if typ is bool:
                    f.params[key] = bool(var.get())
                elif typ is int:
                    f.params[key] = int(float(raw))
                elif typ is float or typ is None:
                    if raw.strip() == '':
                        f.params.pop(key, None)
                    else:
                        f.params[key] = float(raw)
                else:
                    f.params[key] = raw
            except (TypeError, ValueError):
                self.log('参数 %s 的值无效: %r' % (key, raw))
        self.doc.invalidate()
        self._clear_agent_live_preview()
        self.refresh_tree()
        self.run_build()

    def delete_feature(self):
        idx = self._sel_index()
        if idx is None:
            return
        self.doc.remove(idx)
        self._clear_agent_live_preview()
        self.refresh_tree()
        self.run_build()

    def move_feature(self, delta):
        idx = self._sel_index()
        if idx is None:
            return
        self.doc.move(idx, delta)
        self._clear_agent_live_preview()
        self.refresh_tree()
        sel2 = str(max(0, min(len(self.doc.features) - 1, idx + delta)))
        if sel2 in self.tree.get_children():
            self.tree.selection_set(sel2)
        self.run_build()

    def rename_feature(self):
        idx = self._sel_index()
        if idx is None:
            return
        f = self.doc.features[idx]
        from tkinter import simpledialog
        name = simpledialog.askstring('重命名特征', '新名称:', initialvalue=f.name, parent=self)
        if name:
            f.name = name
            self.refresh_tree()

    def _on_tree_toggle(self, _e):
        idx = self._sel_index()
        if idx is None:
            return
        self.doc.features[idx].enabled = not self.doc.features[idx].enabled
        self._clear_agent_live_preview()
        self.refresh_tree()
        self.tree.selection_set(str(idx))
        self.run_build()

    def clear_features(self):
        self._last_plan = None
        self._clear_agent_live_preview()
        self.doc = DM.Document('Part1')
        self.refresh_tree()
        self.viewer.set_model([], fit=True)
        self.lbl_stat.delete('1.0', 'end')
        self.status('已清空')

    # ==================================================================
    # 文件操作
    # ==================================================================
    def new_doc(self):
        self.clear_features()
        self._last_plan = None
        self._set_prompt_text('')
        self.current_path = None
        self.title(APP_TITLE)

    def open_project(self):
        p = filedialog.askopenfilename(title='打开工程', filetypes=[
            ('AI 建模工程', '*.aimodel'), ('JSON 文件', '*.json'), ('所有文件', '*.*')])
        if not p:
            return
        try:
            d = json.load(open(p, 'r', encoding='utf-8'))
            self.doc = DM.Document.from_dict(d)
            self._last_plan = None
            self._clear_agent_live_preview()
            self.current_path = p
            self.title('%s — %s' % (APP_TITLE, os.path.basename(p)))
            if self.doc.source_prompt:
                self._set_prompt_text(self.doc.source_prompt)
            self.refresh_tree()
            self.run_build()
        except Exception as e:
            messagebox.showerror('打开失败', str(e))

    def save_project(self):
        if not self.doc.features:
            messagebox.showinfo('提示', '当前没有可保存的特征。')
            return
        p = self.current_path or filedialog.asksaveasfilename(
            title='保存工程', defaultextension='.aimodel',
            initialfile='%s.aimodel' % self.doc.name,
            filetypes=[('AI 建模工程', '*.aimodel'), ('JSON 文件', '*.json')])
        if not p:
            return
        try:
            exporters.export_project(self.doc, p)
            self.current_path = p
            self.title('%s — %s' % (APP_TITLE, os.path.basename(p)))
            self.status('已保存: ' + p)
            self.log('已保存工程: ' + p)
        except Exception as e:
            messagebox.showerror('保存失败', str(e))

    def snapshot(self, four=True):
        """把当前模型渲染成 PNG 存到 ``artifacts/model-shots``。

        用的是内置的纯标准库渲染器, 不依赖显卡/OpenGL, 所以一定能出图。
        """
        polys = getattr(self.viewer, 'polys', None)
        if not polys:
            messagebox.showinfo('模型截图', '当前还没有模型, 先在左边输入一句话生成一个。')
            return
        out_dir = os.path.join(ROOT_DIR, 'artifacts', 'model-shots')
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = os.path.expanduser('~')
        stem = os.path.splitext(os.path.basename(self.current_path or '模型'))[0]
        name = '%s%s%s.png' % (stem, '' if stem == '模型' else '_',
                               time.strftime('%Y%m%d_%H%M%S'))
        path = os.path.join(out_dir, name)
        try:
            if four:
                SR.save_four_view(polys, path)
            else:
                SR.render_to_png(polys, path, int(1100 * self.ui_scale),
                                 int(820 * self.ui_scale))
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror('模型截图', '渲染失败: %s' % exc)
            return
        self.status('截图已保存: %s' % name, TH.OK)
        self.log('模型截图 -> %s' % path)
        if messagebox.askyesno('模型截图', '已保存到\n%s\n\n现在打开这个文件夹吗?' % path):
            try:
                os.startfile(out_dir)
            except Exception:
                pass

    def snapshot_multiview(self, view_set='extended'):
        """Save a shared-scale three-view or seven-view PNG for the current mesh."""
        polys = getattr(self.viewer, 'polys', None)
        if not polys:
            messagebox.showinfo('多视图', '当前还没有模型，请先完成一次本地建模。')
            return
        if view_set not in {'three', 'extended'}:
            view_set = 'extended'
        out_dir = os.path.join(ROOT_DIR, 'artifacts', 'model-shots')
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = os.path.expanduser('~')
        stem = os.path.splitext(os.path.basename(self.current_path or '模型'))[0]
        suffix = '三视图' if view_set == 'three' else '扩展多视图'
        name = '%s_%s_%s.png' % (stem, suffix, time.strftime('%Y%m%d_%H%M%S'))
        path = os.path.join(out_dir, name)
        try:
            if view_set == 'three':
                SR.save_three_view(polys, path)
            else:
                SR.save_multiview(
                    polys, path,
                    views=['iso', 'front', 'right', 'top', 'back', 'left', 'bottom'],
                    tile=(330, 260), columns=3)
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror('多视图', '渲染失败: %s' % exc)
            return
        self.status('多视图已保存: %s' % name, TH.OK)
        self.log('多视图 PNG -> %s' % path)
        if messagebox.askyesno('多视图', '已保存到\n%s\n\n现在打开这个文件夹吗?' % path):
            try:
                os.startfile(out_dir)
            except Exception:
                pass

    def export(self, kind):
        body = self.doc.build()
        if not body:
            messagebox.showinfo('提示', '当前没有可导出的几何体。')
            return
        base = self.doc.name or 'part'
        if kind == 'stl':
            p = filedialog.asksaveasfilename(title='导出 STL', defaultextension='.stl',
                                             initialfile=base + '.stl',
                                             filetypes=[('STL 模型', '*.stl')])
            if p:
                exporters.export_stl(body, p, base)
                self._after_export(p)
        elif kind == 'obj':
            p = filedialog.asksaveasfilename(title='导出 OBJ', defaultextension='.obj',
                                             initialfile=base + '.obj',
                                             filetypes=[('Wavefront OBJ', '*.obj')])
            if p:
                exporters.export_obj(body, p, base)
                self._after_export(p)
        elif kind == 'report':
            p = filedialog.asksaveasfilename(title='导出建模报告', defaultextension='.txt',
                                             initialfile=base + '_建模报告.txt',
                                             filetypes=[('文本文件', '*.txt')])
            if p:
                exporters.export_report(self.doc, p, self.doc.source_prompt)
                self._after_export(p)
        elif kind == 'catia':
            p = filedialog.asksaveasfilename(title='导出 CATIA 脚本', defaultextension='.py',
                                             initialfile='catia_%s.py' % base,
                                             filetypes=[('Python 脚本', '*.py')])
            if p:
                exporters.export_catia_script(self.doc, p)
                self._after_export(p)

    def _after_export(self, path):
        self.status('已导出: ' + path)
        self.log('已导出: ' + path)
        if messagebox.askyesno('导出成功', '已导出到:\n%s\n\n是否打开所在文件夹?' % path):
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass

    # ==================================================================
    # 工具
    # ==================================================================
    def push_catia(self):
        """把当前模型**直接建到 CATIA** 里（cscript + COM，不需要 pywin32）。

        这就是「在助手里输描述 -> 直接出 CATIA 模型」的用法:
        助手负责中文解析与几何内核, CATIA 侧由一段 VBScript 通过 COM 重建特征树。
        """
        try:
            from ai_modeler.catia_backend import catia as catia_mod
        except Exception as e:
            messagebox.showerror('错误', str(e))
            return
        if not self.doc.features:
            messagebox.showinfo('推送到 CATIA',
                                '还没有模型。请在左侧「建模对话」输入需求或添加参考图片后发送。')
            return
        ok, msg = catia_mod.probe()
        if not ok:
            messagebox.showwarning('未检测到 CATIA V5', msg)
            return

        # 不自动保存: 建完直接显示出来, 需要保存时在 CATIA 里手动保存 (Ctrl+S)
        save_path = None

        self.status('正在推送到 CATIA …（没开会自动启动，请稍候）')
        try:
            self.lbl_busy.config(text='推送到 CATIA …')
        except Exception:
            pass

        def work():
            try:
                ok2, msg2, script = catia_mod.push_to_catia(self.doc, save_catpart=save_path)
            except Exception as e:
                ok2, msg2, script = False, '推送失败: %s' % e, None
            self.after(0, lambda: self._push_done(ok2, msg2, script))

        threading.Thread(target=work, daemon=True).start()

    def _push_done(self, ok, msg, script):
        try:
            self.lbl_busy.config(text='')
        except Exception:
            pass
        self.status('已推送到 CATIA' if ok else '推送到 CATIA 失败')
        if ok:
            messagebox.showinfo('推送到 CATIA', msg)
        else:
            messagebox.showerror('推送到 CATIA 失败', msg)

    # ==================================================================
    # 联网搜索
    # ==================================================================
    def online_lookup(self, phrase, auto=False):
        from ai_modeler.agent import online
        if getattr(self, '_online_busy', False):
            return
        self._online_busy = True
        self.status('正在联网搜索: %s …' % phrase[:22])
        q = queue.Queue()

        def work():
            try:
                q.put(('ok', online.ask(phrase)))
            except Exception as e:
                q.put(('err', str(e)))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                kind, data = q.get_nowait()
            except queue.Empty:
                self.after(120, poll)
                return
            self._online_busy = False
            if kind == 'err':
                self.status('联网搜索失败', TH.ERR)
                self.log('联网搜索失败: %s' % data)
                if not auto:
                    messagebox.showerror('联网搜索', '搜索失败: %s' % data)
                return
            self.show_online_result(phrase, data)

        self.after(120, poll)

    def show_online_result(self, phrase, data):
        win = tk.Toplevel(self)
        win.title('联网搜索 —— 本地词库没这个说法')
        win.geometry('760x640')
        win.transient(self)

        head = tk.Frame(win, bg=TH.ACCENT, height=52)
        head.pack(fill='x')
        head.pack_propagate(False)
        tk.Label(head, text='  联网搜索', bg=TH.ACCENT, fg='#ffffff',
                 font=TH.F['h1']).pack(side='left', pady=12)
        tk.Label(head, text=phrase[:60], bg=TH.ACCENT, fg='#d6e6fa',
                 font=TH.F['small']).pack(side='left', padx=6)

        body = tk.Frame(win, bg=TH.PANEL)
        body.pack(fill='both', expand=True)

        if data.get('suggestion'):
            box = tk.Frame(body, bg=TH.ACCENT_SOFT)
            box.pack(fill='x', padx=12, pady=(12, 6))
            tk.Label(box, text='根据网上的资料, 建议这样描述:', bg=TH.ACCENT_SOFT,
                     fg=TH.ACCENT_DK, font=TH.F['body_b']).pack(anchor='w', padx=10, pady=(8, 2))
            tk.Label(box, text=data['suggestion'], bg=TH.ACCENT_SOFT, fg=TH.TEXT,
                     font=TH.F['mono'], wraplength=700, justify='left').pack(
                anchor='w', padx=10)
            tk.Label(box, text='依据: ' + (data.get('reason') or ''), bg=TH.ACCENT_SOFT,
                     fg=TH.TEXT_DIM, font=TH.F['small'], wraplength=700,
                     justify='left').pack(anchor='w', padx=10, pady=(2, 8))
        else:
            tk.Label(body, text='没能从搜索结果里提炼出可用参数。\n' + (data.get('reason') or ''),
                     bg=TH.PANEL, fg=TH.TEXT_DIM, font=TH.F['body'],
                     justify='left').pack(anchor='w', padx=14, pady=12)

        for note in data.get('notes', []):
            tk.Label(body, text='· ' + note, bg=TH.PANEL, fg=TH.TEXT_MUTE,
                     font=TH.F['small'], anchor='w').pack(fill='x', padx=14)

        tk.Label(body, text='搜索结果', bg=TH.PANEL, fg=TH.TEXT,
                 font=TH.F['h2']).pack(anchor='w', padx=14, pady=(10, 4))
        listbox = tk.Text(body, wrap='word', font=TH.F['small'], relief='flat',
                          bg=TH.PANEL_ALT, fg=TH.TEXT_DIM, padx=10, pady=8)
        listbox.pack(fill='both', expand=True, padx=12)
        for i, r in enumerate(data.get('results', []), 1):
            listbox.insert('end', '%d. %s\n' % (i, r.get('title') or ''))
            if r.get('snippet'):
                listbox.insert('end', '   %s\n' % r['snippet'][:180])
            listbox.insert('end', '   %s\n\n' % (r.get('url') or ''))
        listbox.configure(state='disabled')

        bar = tk.Frame(win, bg=TH.PANEL)
        bar.pack(fill='x', padx=12, pady=10)

        def use_it():
            desc = data.get('suggestion')
            if not desc:
                return
            win.destroy()
            self._set_prompt_text(desc)
            self.generate()

        ttk.Button(bar, text='用这条描述建模', style='Accent.TButton',
                   command=use_it, state=('normal' if data.get('suggestion') else 'disabled')
                   ).pack(side='left')
        ttk.Button(bar, text='重新搜索…', style='Ghost.TButton',
                   command=lambda: (win.destroy(), self.online_lookup(phrase))
                   ).pack(side='left', padx=6)
        ttk.Button(bar, text='关闭', style='Ghost.TButton',
                   command=win.destroy).pack(side='right')
        self.status('联网搜索完成: %d 条结果' % len(data.get('results', [])))

    def show_mcp_config(self):
        """MCP 配置: 既显示本插件的 CATIA MCP 服务器, 也显示离线建模工具。"""
        from ai_modeler.runtime import mcp_server
        cfg = mcp_server.client_config()
        catia_cfg = ''
        n_catia = n_off = 0
        try:
            from ai_modeler.catia_mcp import server as mcp_catia
            catia_cfg = mcp_catia.client_config()
            tools, _rep = mcp_catia.all_tools()
            n_catia = len([t for t in tools if t[0].startswith('catia_')])
            n_off = len(tools) - n_catia
        except Exception as e:                                   # noqa: BLE001
            catia_cfg = '（CATIA MCP 模块载入失败: %s）' % e

        win = tk.Toplevel(self)
        win.title('MCP 服务配置')
        win.geometry('820x600')
        ttk.Label(win, text=(
            '★ 推荐用下面第 1 段：这是 CATIA MCP 服务器（工具名对齐开源项目 catia-v5-mcp-server，'
            '共 %d 个 catia_* 工具 + %d 个离线建模工具），AI 客户端可以直接驱动 CATIA。'
            % (n_catia, n_off)),
            font=FONT_UI, wraplength=780, justify='left').pack(anchor='w', padx=12, pady=(8, 2))
        t = tk.Text(win, font=FONT_MONO, wrap='none', height=14)
        t.pack(fill='both', expand=True, padx=12)
        t.insert('1.0', catia_cfg)
        ttk.Label(win, text='第 2 段：只做离线参数化建模（不装 CATIA 也能用）的 MCP 服务。',
                  font=FONT_UI, wraplength=780, justify='left').pack(anchor='w', padx=12, pady=(6, 2))
        t2 = tk.Text(win, font=FONT_MONO, wrap='none', height=8)
        t2.pack(fill='both', expand=True, padx=12)
        t2.insert('1.0', cfg)
        btns = ttk.Frame(win)
        btns.pack(fill='x', padx=12, pady=8)
        ttk.Button(btns, text='复制 CATIA MCP 配置', command=lambda: (
            self.clipboard_clear(), self.clipboard_append(catia_cfg),
            self.status('CATIA MCP 配置已复制'))).pack(side='left')
        ttk.Button(btns, text='复制离线 MCP 配置', command=lambda: (
            self.clipboard_clear(), self.clipboard_append(cfg),
            self.status('离线 MCP 配置已复制'))).pack(side='left', padx=6)
        ttk.Button(btns, text='测试 MCP 服务', command=self.test_mcp).pack(side='left', padx=6)
        ttk.Button(btns, text='打开 MCP 说明', command=self.open_mcp_manual).pack(side='left', padx=6)
        ttk.Button(btns, text='关闭', command=win.destroy).pack(side='right')

    def open_mcp_manual(self):
        """打开 CATIA_MCP说明.md。"""
        import os
        import subprocess
        import sys as _sys
        root = str(PROJECT_ROOT)
        path = os.path.join(root, 'CATIA_MCP说明.md')
        if not os.path.isfile(path):
            messagebox.showinfo('MCP 说明', '未找到 CATIA_MCP说明.md')
            return
        try:
            if _sys.platform.startswith('win'):
                os.startfile(path)                               # noqa: S606
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:                                   # noqa: BLE001
            messagebox.showinfo('MCP 说明', '已生成: %s\n(%s)' % (path, e))

    def test_mcp(self):
        from ai_modeler.runtime import mcp_server
        try:
            n = len(mcp_server.list_tools())
            extra = ''
            try:
                from ai_modeler.catia_mcp import server as mcp_catia
                tools, _rep = mcp_catia.all_tools()
                catia = [t for t in tools if t[0].startswith('catia_')]
                rc = mcp_catia.selftest(verbose=False)
                extra = ('\nCATIA MCP: %d 个 catia_* 工具, 协议自检%s'
                         % (len(catia), '通过' if rc == 0 else '未通过'))
            except Exception as e:                               # noqa: BLE001
                extra = '\nCATIA MCP: 载入失败 %s' % e
            messagebox.showinfo('MCP 服务', '离线 MCP 服务端可正常工作, 共 %d 个建模工具。%s'
                                % (n, extra))
        except Exception as e:
            messagebox.showerror('MCP 服务', str(e))

    # ==================================================================
    # 零件库浏览器
    # ==================================================================
    def refresh_parts(self):
        try:
            from ai_modeler.parts import partlib
        except Exception:
            return
        kw = (self.var_partsrch.get() or '').strip()
        count = partlib.load_kb().get('by_category', {})
        tv = self.tree_parts
        tv.delete(*tv.get_children())
        shown = 0
        for cat, d in partlib.PARTS.items():
            if kw and kw not in cat and not any(kw in a for a in d['aliases']):
                continue
            n = count.get(cat, 0)
            tag = ('手册 %d 条' % n) if n else '参数化'
            node = tv.insert('', 'end', iid='c:' + cat,
                             text='%s   (%s)' % (cat, tag), open=False)
            tv.insert(node, 'end', iid='d:' + cat, text='  ★ 双击填入示例描述')
            for a in d['aliases'][:5]:
                tv.insert(node, 'end', iid='a:%s|%s' % (cat, a), text='     ' + a)
            shown += 1
        if shown == 0:
            tv.insert('', 'end', iid='none', text='（没有匹配的零件）')

    def _on_part_pick(self, _e=None):
        sel = self.tree_parts.selection()
        if not sel or not sel[0].startswith('d:'):
            return
        cat = sel[0][2:]
        try:
            from ai_modeler.parts import partlib
            d = partlib.PARTS.get(cat)
        except Exception:
            return
        if not d:
            return
        sample = SAMPLE_VALUES.get(cat, {})
        bits = []
        for k, aliases in d['params'].items():
            if k not in sample:
                continue
            bits.append('%s%g' % (aliases[0], sample[k]))
        text = ('%s %s' % (cat, ' '.join(bits))).strip()
        self._set_prompt_text(text)
        self.status('已填入: %s —— 点「生成模型」' % text[:34])
        self.log('零件库填入: ' + text)

    def show_online_config(self):
        on = self.cfg.setdefault('online', {'enabled': True, 'timeout': 8})
        win = tk.Toplevel(self)
        win.title('联网搜索设置')
        win.geometry('520x260')
        v_en = tk.BooleanVar(value=bool(on.get('enabled', True)))
        v_to = tk.StringVar(value=str(on.get('timeout', 8)))
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill='both', expand=True)
        tk.Checkbutton(frm, text='本地词库不认识时, 自动联网搜索',
                       variable=v_en, bg=TH.BG, fg=TH.TEXT,
                       activebackground=TH.BG, selectcolor='#ffffff',
                       font=TH.F['body'], bd=0, highlightthickness=0).pack(anchor='w')
        row = tk.Frame(frm, bg=TH.BG)
        row.pack(anchor='w', pady=10)
        tk.Label(row, text='超时(秒)', bg=TH.BG, fg=TH.TEXT_DIM,
                 font=TH.F['body']).pack(side='left')
        ttk.Entry(row, textvariable=v_to, width=6).pack(side='left', padx=6)
        tk.Label(frm, text='搜索走 Bing 网页检索, 不需要 API Key。\n'
                           '查到资料后会给出一条建议描述, 你确认后才建模, 不会自动乱建。\n'
                           '关掉这个开关程序就完全离线, 不产生任何网络请求。',
                 bg=TH.BG, fg=TH.TEXT_MUTE, font=TH.F['small'],
                 justify='left').pack(anchor='w', pady=8)

        def save():
            try:
                to = max(3, min(30, int(float(v_to.get()))))
            except (TypeError, ValueError):
                to = 8
            self.cfg['online'] = {'enabled': bool(v_en.get()), 'timeout': to}
            cfgmod.save(self.cfg)
            self.status('联网搜索设置已保存')
            win.destroy()

        ttk.Button(frm, text='保存', style='Accent.TButton',
                   command=save).pack(anchor='e', pady=6)

    def install_deepseek(self):
        script = os.path.join(ROOT_DIR, 'tools', 'install_deepseek.py')
        if not os.path.isfile(script):
            messagebox.showerror('错误', '未找到 tools/install_deepseek.py')
            return
        ans = messagebox.askyesno(
            '本地 DeepSeek',
            '将在插件目录 local_ai\\ 里安装一套完整的本地 AI:\n\n'
            '  · Ollama 推理引擎  约 1.4 GB\n'
            '  · DeepSeek 模型    约 4.9 GB (也可以选更小的 1.5B)\n\n'
            '全程不需要管理员权限, 也不修改系统。\n'
            '卸载时直接删掉 local_ai 文件夹即可。\n\n'
            '现在开始下载吗? (会打开一个命令行窗口显示进度)')
        if not ans:
            return
        try:
            subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k',
                              'chcp 65001 >nul && "%s" "%s"'
                              % (sys.executable, script)],
                             cwd=ROOT_DIR)
            self.status('已启动本地 DeepSeek 安装程序')
        except Exception as e:
            messagebox.showerror('启动失败', str(e))

    def manual_online(self):
        text = self._current_prompt()
        if not text:
            messagebox.showinfo('提示', '请先在输入框里写下你要查的零件, 例如「M8 内六角螺栓」')
            return
        self.online_lookup(text)

    def show_llm_config(self):
        win = tk.Toplevel(self)
        win.title('多模态 AgentLoop 设置')
        win.geometry('600x340')
        llm = self.cfg.setdefault('llm', {'enabled': False, 'base_url':
                                          'https://api.openai.com/v1', 'model': 'gpt-4o-mini',
                                          'api_key': ''})
        v_en = tk.BooleanVar(value=bool(llm.get('enabled')))
        v_url = tk.StringVar(value=llm.get('base_url', ''))
        v_model = tk.StringVar(value=llm.get('model', ''))
        v_key = tk.StringVar(value=llm.get('api_key', ''))
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill='both', expand=True)
        ttk.Checkbutton(frm, text='启用多模态 AgentLoop',
                        variable=v_en).grid(row=0, column=0, columnspan=2, sticky='w', pady=4)
        for i, (label, var) in enumerate((('接口地址', v_url), ('模型名称', v_model),
                                          ('API Key', v_key)), start=1):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky='w', pady=4)
            ent = ttk.Entry(frm, textvariable=var, width=48, show='*' if label == 'API Key' else '')
            ent.grid(row=i, column=1, sticky='ew', pady=4)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text='启用后，Agent 会先理解本轮对话，再自行选择回答、修改模型或执行完整建模复核。\n'
                            '服务端模型必须同时支持图像输入和工具调用；不支持时会明确报错，不回退到猜测。\n'
                            '关闭时仅使用内置文本规则；图片输入需要启用兼容的多模态模型。',
                  font=('Microsoft YaHei UI', 8), foreground='#7f8c9b',
                  justify='left').grid(row=4, column=0, columnspan=2, sticky='w', pady=8)

        def save():
            self.cfg['llm'] = {'enabled': bool(v_en.get()), 'base_url': v_url.get().strip(),
                               'model': v_model.get().strip(), 'api_key': v_key.get().strip()}
            cfgmod.save(self.cfg)
            self.btn_model.configure(text=self._model_button_text())
            self.status('大模型设置已保存')
            win.destroy()

        ttk.Button(frm, text='保存', style='Accent.TButton', command=save).grid(
            row=5, column=1, sticky='e', pady=6)

    def run_selfcheck(self):
        lines = []
        lines.append('Python      : %s' % sys.version.split()[0])
        lines.append('tkinter     : OK')
        try:
            from ai_modeler.catia_backend import catia
            ok, msg = catia.probe()
            head, _, rest = msg.partition('\n')
            lines.append('CATIA V5    : %s' % (head if ok else '未接入 / ' + head))
            for ln in rest.splitlines():
                if ln.strip():
                    lines.append('               ' + ln.strip())
        except Exception as e:
            lines.append('CATIA V5    : 检测失败 (%s)' % e)
        try:
            from ai_modeler.catia_backend import catialib
            idx = catialib.load_index()
            lines.append('CATIA 模型库: %s 个 catalog 条目' % (idx.get('count', '未建索引')))
        except Exception as e:
            lines.append('CATIA 模型库: 读取失败 (%s)' % e)
        try:
            from ai_modeler.runtime import mcp_server
            lines.append('MCP 工具数  : %d' % len(mcp_server.list_tools()))
        except Exception as e:
            lines.append('MCP         : 失败 (%s)' % e)
        d = DM.Document('T')
        import time as _t
        t0 = _t.time()
        d.add(DM.Feature('box', {'length': 50, 'width': 40, 'height': 20}))
        d.add(DM.Feature('hole', {'diameter': 10, 'through': True, 'pos': 'center'}, DM.CUT))
        b = d.build()
        lines.append('几何内核    : OK (%d 面片, %.3fs)' % (len(b), _t.time() - t0))
        r = NLP.parse('画一个 100×60×20 的长方体, 中心打直径 10 的通孔')
        lines.append('自然语言引擎: %s (%d 条指令)' % ('OK' if r.ok else '异常', r.recognized))
        messagebox.showinfo('系统自检', '\n'.join(lines))

    def show_help(self):
        self.nb.select(self.tab_help)

    def open_manual(self):
        path = os.path.join(ROOT_DIR, '使用说明.md')
        if os.path.isfile(path):
            try:
                os.startfile(path)
                self.status('已打开使用说明')
            except Exception as e:
                messagebox.showerror('无法打开', str(e))
        else:
            messagebox.showinfo('使用说明', '未找到 使用说明.md，改用界面内的「用法」页。')
            self.show_help()

    def open_catia_manual(self):
        """打开 CATIA 连接操作说明（优先 Word 版，其次 Markdown）。"""
        for name in ('操作说明_CATIA连接版.docx', '操作说明_CATIA连接版.md'):
            path = os.path.join(ROOT_DIR, name)
            if os.path.isfile(path):
                try:
                    os.startfile(path)
                    self.status('已打开：%s' % name)
                except Exception as e:
                    messagebox.showerror('无法打开', str(e))
                return
        messagebox.showinfo('操作说明', '未找到 操作说明_CATIA连接版.docx / .md')

    def install_catia_plugin(self):
        script = os.path.join(ROOT_DIR, 'tools', 'install_catia_plugin.py')
        if not os.path.isfile(script):
            messagebox.showerror('错误', '未找到 tools\\install_catia_plugin.py')
            return
        try:
            subprocess.Popen([sys.executable, script],
                             cwd=ROOT_DIR, creationflags=0x00000010)
            self.status('已启动 CATIA 插件安装程序（在新窗口中）')
        except Exception as e:
            messagebox.showerror('启动失败', str(e))

    def connect_catia(self):
        """一键连接 CATIA：探测 -> 写 config.json -> 投放宏 -> 扫描模型库 -> 自检。"""
        script = os.path.join(ROOT_DIR, 'tools', 'connect_catia.py')
        if not os.path.isfile(script):
            messagebox.showerror('错误', '未找到 tools\\connect_catia.py')
            return
        try:
            subprocess.Popen([sys.executable, script],
                             cwd=ROOT_DIR, creationflags=0x00000010)
            self.status('已启动 CATIA 一键连接程序（在新窗口中）')
        except Exception as e:
            messagebox.showerror('启动失败', str(e))

    def open_catia_lib(self):
        """打开 CATIA 模型库 (catalog) 浏览窗口。"""
        try:
            from ai_modeler.catia_backend import catialib
            catialib.open_window(self, status=self.status)
            self.status('已打开 CATIA 模型库')
        except Exception as e:
            messagebox.showerror('打开失败', '无法打开 CATIA 模型库：%s' % e)

    def show_about(self):
        messagebox.showinfo('关于', '%s\n\n自然语言驱动的参数化三维建模工具\n'
                                    '内置 BSP-CSG 几何内核, 纯 Python 标准库实现。\n'
                                    '支持导出 STL / OBJ / 工程文件 / CATIA 自动化脚本,\n'
                                    '并可作为 MCP 服务端供 AI 客户端调用。\n\n'
                                    'Python %s' % (APP_TITLE, sys.version.split()[0]))

    def on_close(self):
        if self.doc.features and not messagebox.askyesno(
                '退出', '确定要退出吗? 未保存的工程会丢失。'):
            return
        # 关掉所有挂起的 after 回调。否则窗口销毁后定时器还会醒来,
        # 触发 generate() 之类的函数, 抛出 Tcl 的 invalid command name。
        self._closing = True
        try:
            for aid in self.tk.call('after', 'info'):
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        except Exception:
            pass
        if self._busy:
            self.cancel_build()
        self.destroy()


HELP_TEXT = '''【三步上手】

① 可直接在输入框粘贴图片（Ctrl+V），也可点「参考图片」选择多张不同视角图。
   剪贴板中的文字仍按普通文字粘贴；图片会显示在输入框上方，发送后以缩略图保留在聊天记录中。

② 输入文字补充目标，例如“按这些图复现零件，允许按比例估算未知尺寸”。
   点「Agent 对话」（或 Ctrl+Enter）后，Agent 会根据本轮指令和当前模型选择回答、修改或新建；
   需要时执行视觉复核。过程预览会实时出现在右侧「Agent」页，完成后的网格显示在中间视口。
   多模态 AgentLoop 需要服务端模型同时支持图像输入与工具调用。

③ 工具栏「多视图 PNG」默认保存扩展视图；「文件」菜单可分别保存三视图或扩展视图。
   AgentLoop 只使用本地网格内核，不会自动创建或同步 CATIA 模型。

④ 在特征树里选中任一特征, 右侧「参数」页可修改尺寸并即时重建;
   也可以在「文件」菜单里导出 STL / OBJ / 建模报告 / CATIA 脚本。

【支持的建模词汇】
基本体: 长方体 / 立方体 / 圆柱 / 圆台 / 圆锥 / 球体 / 齿轮 / 轮廓拉伸 / 回转体 / 放样叶片
切除类: 孔(通孔/盲孔) / 凹槽 / 四角孔 / 均布孔(分度圆)
修饰类: 圆角 R8 / 倒角 C2 / 线性阵列 / 圆周阵列
模板类: 法兰 / 阶梯轴 / 套筒 / 圆管 / 叶盘 / 叶片

【三维操作】
左键拖动 = 旋转      右键或中键拖动 = 平移
滚轮 = 缩放          双击 = 适应窗口
菜单「视图」可切换前/后/左/右/俯/仰/等轴测视图, 以及透视与正交投影。

【MCP 服务】
菜单「工具 → MCP 服务配置」可复制一段 JSON, 粘贴到
Claude / Codex / dsh 等 AI 客户端的 MCP 配置里,
AI 就能直接调用本程序的建模工具(建模、布尔运算、导出等)。
'''


def main():
    enable_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
