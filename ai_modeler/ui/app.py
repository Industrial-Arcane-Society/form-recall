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
import uuid
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
        self._modeling_mode = str(self.cfg.get('modeling_mode') or 'agent').strip().lower()
        if self._modeling_mode not in {'quick', 'agent'}:
            self._modeling_mode = 'agent'
        self._mode_switch_buttons = []
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
        self._dsh_session_id = 'ai-modeler-' + uuid.uuid4().hex
        self._agent_context_summary = ''
        self._agent_summary_through = 0
        self._agent_total_tokens = 0
        self._agent_total_cost = 0.0
        self._last_plan = None
        self._pending_agent_plan = None
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
        self._append_chat('assistant', self._mode_welcome_message())
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
        self.var_edges = tk.BooleanVar(value=False)
        self.var_grid = tk.BooleanVar(value=False)
        self.var_axes = tk.BooleanVar(value=True)
        self.var_persp = tk.BooleanVar(value=True)
        vm.add_checkbutton(label='显示特征边线', variable=self.var_edges, command=self._sync_view)
        vm.add_checkbutton(label='显示参考网格', variable=self.var_grid, command=self._sync_view)
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
        ttk.Checkbutton(bar, text='辅助线', variable=self.var_edges,
                        command=self._sync_view).pack(side='left', padx=(9, 2))
        ttk.Checkbutton(bar, text='网格', variable=self.var_grid,
                        command=self._sync_view).pack(side='left', padx=(2, 1))

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
        left_card, left = TH.card(panes, width=int(485 * self.ui_scale))
        panes.add(left_card, weight=0)

        head = TH.section(left, '建模对话')
        ttk.Button(head, text='新对话', style='Ghost.TButton',
                   command=self.new_chat).pack(side='right', padx=(8, 0))
        self.cmb_sample = ttk.Combobox(head, values=[s[0] for s in SAMPLES],
                                       state='readonly', width=9)
        self.cmb_sample.set('示例…')
        self.cmb_sample.pack(side='right', padx=(5, 0))
        self.cmb_sample.bind('<<ComboboxSelected>>', self._on_sample)

        # The two modeling routes are explicit UI modes. The model viewport and
        # feature tree stay shared; only the conversation page's execution route
        # changes.
        self.mode_switch = tk.Frame(left, bg=TH.BORDER, bd=0)
        self.mode_switch.pack(fill='x', padx=12, pady=(0, 8))
        mode_inner = tk.Frame(self.mode_switch, bg=TH.PANEL_ALT, bd=0)
        mode_inner.pack(fill='x', padx=1, pady=1)
        self.btn_quick_mode = ttk.Button(
            mode_inner, text='⚡ 快速模式', command=lambda: self._switch_modeling_mode('quick'))
        self.btn_quick_mode.pack(side='left', fill='x', expand=True, padx=(1, 2), pady=1)
        self.btn_agent_mode = ttk.Button(
            mode_inner, text='✦ Agent 创造模式', command=lambda: self._switch_modeling_mode('agent'))
        self.btn_agent_mode.pack(side='left', fill='x', expand=True, padx=(2, 1), pady=1)
        self._mode_switch_buttons = [self.btn_quick_mode, self.btn_agent_mode]
        self.lbl_mode_description = tk.Label(
            left, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
            font=TH.F['small'], anchor='w')
        self.lbl_mode_description.pack(fill='x', padx=13, pady=(0, 6))
        self._refresh_modeling_mode_switch()

        history_wrap = tk.Frame(left, bg=TH.PANEL)
        history_wrap.pack(fill='both', expand=True, padx=12, pady=(0, 7))
        self.chat_canvas = tk.Canvas(
            history_wrap, bg=TH.PANEL, bd=0, highlightthickness=0,
            yscrollincrement=24)
        self.chat_canvas.pack(side='left', fill='both', expand=True)
        chat_scroll = ttk.Scrollbar(history_wrap, command=self.chat_canvas.yview)
        chat_scroll.pack(side='right', fill='y', padx=(5, 0))
        self.chat_canvas.configure(yscrollcommand=chat_scroll.set)
        self.chat_messages = tk.Frame(self.chat_canvas, bg=TH.PANEL)
        self._chat_window = self.chat_canvas.create_window(
            (0, 0), window=self.chat_messages, anchor='nw')
        self.chat_history = self.chat_messages  # legacy name retained for UI reset code
        self._chat_cards = []
        self.chat_messages.bind('<Configure>', self._sync_chat_scrollregion)
        self.chat_canvas.bind('<Configure>', self._resize_chat_messages)
        self.chat_canvas.bind('<MouseWheel>', self._scroll_chat)
        self.chat_messages.bind('<MouseWheel>', self._scroll_chat)

        self.composer = tk.Frame(
            left, bg=TH.PANEL, bd=0, highlightthickness=1,
            highlightbackground=TH.BORDER, highlightcolor=TH.ACCENT)
        self.composer.pack(fill='x', padx=12, pady=(0, 10))

        self.chat_activity = tk.Frame(
            left, bg=TH.PANEL_ALT, highlightthickness=1,
            highlightbackground=TH.BORDER)
        self.chat_activity_spinner = ttk.Progressbar(
            self.chat_activity, mode='indeterminate', length=56)
        self.chat_activity_spinner.pack(side='left', padx=(10, 8), pady=8)
        activity_text = tk.Frame(self.chat_activity, bg=TH.PANEL_ALT)
        activity_text.pack(side='left', fill='x', expand=True, padx=(0, 10), pady=5)
        self.chat_activity_title = tk.Label(
            activity_text, text='', bg=TH.PANEL_ALT, fg=TH.TEXT,
            font=TH.F['body_b'], anchor='w')
        self.chat_activity_title.pack(fill='x')
        self.chat_activity_detail = tk.Label(
            activity_text, text='', bg=TH.PANEL_ALT, fg=TH.TEXT_MUTE,
            font=TH.F['tiny'], anchor='w', justify='left', wraplength=350)
        self.chat_activity_detail.pack(fill='x')
        self._chat_activity_after_id = None
        self._chat_activity_visible = False
        self._chat_activity_running = False

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
        self._input_placeholder = self._mode_input_placeholder()
        self._input_placeholder_active = False
        self._restore_prompt_placeholder()
        self.txt.bind('<FocusIn>', self._clear_prompt_placeholder)
        self.txt.bind('<FocusOut>', self._restore_prompt_placeholder)
        self.txt.bind('<KeyRelease>', self._resize_prompt)
        self.txt.bind('<Return>', self._on_return)
        self.txt.bind('<Shift-Return>', lambda e: None)
        self.txt.bind('<<Paste>>', self._paste_clipboard_image)
        self.after_idle(self._resize_prompt)

        self.chat_activity.pack_forget()

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
        # 发送/停止双态按钮：空闲=粉色发送 ↑；AgentLoop 运行中=红色停止 ■。
        # 点击行为随状态切换（见 _on_send_button）；回车键仍走 generate()。
        self.btn_send = tk.Canvas(
            composer_footer, width=34, height=34, bg=TH.PANEL,
            highlightthickness=0, bd=0, cursor='hand2')
        self.btn_send.pack(side='right', padx=(5, 0))
        self._send_mode = None
        self._draw_send_button('send')
        self.btn_send.bind('<Button-1>', self._on_send_button)
        self.btn_send.bind('<Enter>', lambda _event: self._send_hover(True))
        self.btn_send.bind('<Leave>', lambda _event: self._send_hover(False))
        self.btn_model = ttk.Button(
            composer_footer, text=self._model_button_text(), style='Ghost.TButton',
            command=self.show_llm_config)
        self.btn_model.pack(side='right')
        self._refresh_modeling_mode_switch()

        TH.hairline(left)

        TH.section(left, '特征树', '双击启用 / 停用')
        tvbox = tk.Frame(left, bg=TH.PANEL)
        tvbox.pack(fill='both', expand=False, padx=12)
        tvbox.configure(height=int(190 * self.ui_scale))
        tvbox.pack_propagate(False)
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
        # Agent progress now lives in the conversation pane; keep this legacy
        # backing frame out of the right inspector's notebook.
        self.lbl_token = tk.Label(
            self.tab_agent, text='Token 估算：—', bg=TH.PANEL, fg=TH.TEXT_MUTE,
            font=TH.F['small'], anchor='w', justify='left', wraplength=270)
        self.lbl_token.pack(fill='x', padx=12, pady=(8, 0))
        self._agent_thinking_expanded = False
        self._agent_thinking_title = '模型思考（待机）'
        self.agent_thinking_header = tk.Frame(self.tab_agent, bg=TH.PANEL)
        self.agent_thinking_header.pack(fill='x', padx=10, pady=(8, 2))
        self.btn_agent_thinking = tk.Button(
            self.agent_thinking_header, text='▶ 模型思考（待机）',
            command=self._toggle_agent_thinking_panel,
            bg=TH.PANEL, fg=TH.TEXT_DIM, activebackground=TH.PANEL,
            activeforeground=TH.TEXT, relief='flat', bd=0,
            anchor='w', padx=4, pady=3, font=TH.F['body_b'], cursor='hand2')
        self.btn_agent_thinking.pack(fill='x')
        self.agent_thinking_body = tk.Frame(self.tab_agent, bg=TH.PANEL)
        self.lbl_agent_stage = tk.Label(
            self.agent_thinking_body, text='等待任务', bg=TH.PANEL, fg=TH.TEXT_DIM,
            font=TH.F['body_b'], anchor='w', justify='left', wraplength=270)
        self.lbl_agent_stage.pack(fill='x', padx=12, pady=(2, 4))
        self.lbl_agent_detail = tk.Label(
            self.agent_thinking_body, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
            font=TH.F['small'], anchor='nw', justify='left', wraplength=270)
        self.lbl_agent_detail.pack(fill='x', padx=12, pady=(2, 8))
        self.lbl_agent_preview = tk.Label(
            self.tab_agent, text='建模过程中生成的预览会显示在这里。',
            bg=TH.VIEW_BOT, fg=TH.TEXT_MUTE, font=TH.F['small'],
            justify='center', wraplength=270, compound='top')
        self.lbl_agent_preview.pack(fill='x', padx=10, pady=6)
        self.agent_thinking_body.pack(
            fill='x', padx=2, pady=(0, 2), before=self.lbl_agent_preview)
        self.agent_thinking_body.pack_forget()
        self.txt_agent_events = tk.Text(
            self.agent_thinking_body, height=7, width=26, wrap='word',
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
        if not hasattr(self, 'chat_messages'):
            return
        labels = {'user': '你', 'assistant': 'AI Modeler Agent', 'system': 'AgentLoop'}
        tag = role if role in {'user', 'assistant', 'system'} else 'system'
        body = str(text or '').strip()
        user_message = tag == 'user'
        system_message = tag == 'system'
        row = tk.Frame(self.chat_messages, bg=TH.PANEL)
        row.pack(fill='x', padx=10, pady=(4, 7))
        bubble_bg = (TH.ACCENT_SOFT if user_message else
                     TH.PANEL_ALT if system_message else TH.PANEL)
        bubble = tk.Frame(
            row, bg=bubble_bg, highlightthickness=1,
            highlightbackground=TH.BORDER if user_message or system_message else TH.PANEL)
        bubble.pack(side='right' if user_message else 'left',
                    anchor='e' if user_message else 'w',
                    padx=(44, 2) if user_message else (2, 44))
        for scroll_target in (row, bubble):
            scroll_target.bind('<MouseWheel>', self._scroll_chat)
        max_width = max(220, int(max(300, self.chat_canvas.winfo_width() - 72)
                                 * (0.76 if user_message else 0.96)))
        header = tk.Frame(bubble, bg=bubble_bg)
        header.pack(fill='x', padx=11, pady=(7, 2))
        role_label = tk.Label(
            header, text=labels.get(tag, 'AgentLoop'), bg=bubble_bg,
            fg=TH.ACCENT_DK if user_message else TH.TEXT_DIM,
            font=TH.F['small'] if system_message else TH.F['body_b'], anchor='w')
        role_label.pack(side='left')
        for scroll_target in (header, role_label):
            scroll_target.bind('<MouseWheel>', self._scroll_chat)
        tk.Label(header, text=time.strftime('%H:%M'), bg=bubble_bg,
                 fg=TH.TEXT_MUTE, font=TH.F['tiny']).pack(side='right')

        image_paths = list(images or [])
        image_limit = (200, 150) if len(image_paths) <= 1 else (128, 104)
        for path in image_paths:
            photo = self._load_thumbnail(path, image_limit)
            if photo is None:
                tk.Label(bubble, text='图片无法预览：%s' % os.path.basename(path),
                         bg=bubble_bg, fg=TH.TEXT_MUTE, font=TH.F['small'],
                         anchor='w', justify='left').pack(
                             fill='x', padx=11, pady=(2, 5))
                continue
            self._chat_photo_refs.append(photo)
            image_label = tk.Label(bubble, image=photo, bg=bubble_bg,
                                   bd=0, highlightthickness=0)
            image_label.pack(anchor='w' if not user_message else 'e',
                             padx=11, pady=(2, 5))
            image_label.bind('<MouseWheel>', self._scroll_chat)
        message_label = tk.Label(
            bubble, text=body or '（空消息）', bg=bubble_bg, fg=TH.TEXT,
            font=TH.F['small'] if system_message else TH.F['body'],
            justify='left', anchor='w', wraplength=max_width)
        message_label.pack(fill='x', padx=11, pady=(2, 9))
        message_label.bind('<MouseWheel>', self._scroll_chat)
        self._chat_cards.append((message_label, 0.76 if user_message else 0.96))
        self.chat_canvas.update_idletasks()
        self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox('all'))
        self.chat_canvas.yview_moveto(1.0)

    def _sync_chat_scrollregion(self, _event=None):
        if hasattr(self, 'chat_canvas'):
            self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox('all'))

    def _resize_chat_messages(self, event):
        if not hasattr(self, '_chat_window'):
            return
        self.chat_canvas.itemconfigure(self._chat_window, width=max(1, event.width))
        self.after_idle(self._resize_chat_card_text)

    def _resize_chat_card_text(self):
        if not hasattr(self, 'chat_canvas'):
            return
        usable = max(280, self.chat_canvas.winfo_width() - 72)
        for label, ratio in list(getattr(self, '_chat_cards', [])):
            try:
                label.configure(wraplength=max(200, int(usable * ratio)))
            except tk.TclError:
                pass

    def _scroll_chat(self, event):
        if getattr(event, 'delta', 0):
            self.chat_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'

    def _set_chat_activity(self, title, detail='', active=True, hide_after_ms=5500):
        """Show current DSH/build activity beside the composer, where it stays visible."""
        if not hasattr(self, 'chat_activity'):
            return
        if self._chat_activity_after_id:
            try:
                self.after_cancel(self._chat_activity_after_id)
            except Exception:
                pass
            self._chat_activity_after_id = None
        self.chat_activity_title.configure(text=str(title or ''))
        self.chat_activity_detail.configure(text=str(detail or ''))
        if not self._chat_activity_visible:
            self.chat_activity.pack(fill='x', padx=12, pady=(0, 8), before=self.composer)
            self._chat_activity_visible = True
        if active and not self._chat_activity_running:
            self.chat_activity_spinner.start(12)
            self._chat_activity_running = True
        elif not active and self._chat_activity_running:
            self.chat_activity_spinner.stop()
            self._chat_activity_running = False
        if not active:
            if hide_after_ms:
                self._chat_activity_after_id = self.after(
                    hide_after_ms, self._hide_chat_activity)

    def _hide_chat_activity(self):
        self._chat_activity_after_id = None
        if getattr(self, '_chat_activity_visible', False):
            if self._chat_activity_running:
                self.chat_activity_spinner.stop()
                self._chat_activity_running = False
            self.chat_activity.pack_forget()
            self._chat_activity_visible = False

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
        if getattr(self, '_modeling_mode', 'agent') == 'quick':
            return '⚡ 本地规则 ▾'
        llmcfg = self.cfg.get('llm', {}) if isinstance(self.cfg.get('llm'), dict) else {}
        if llmcfg.get('enabled'):
            from ai_modeler.agent import providers
            profile = providers.active_profile(llmcfg) or {}
            model = (profile.get('model') or llmcfg.get('model') or '').strip()
            name = (profile.get('name') or '').strip()
            short = model.rsplit('/', 1)[-1] if model else '模型未填写'
            text = ('%s·%s' % (name, short)) if name else (model or '模型未填写')
        else:
            text = '本地规则'
        if len(text) > 24:
            text = text[:23] + '…'
        return '⚡ %s ▾' % text

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
            messagebox.showinfo('AgentLoop 正在运行',
                                '请先点输入框旁的停止按钮结束当前任务，再新建对话。')
            return
        try:
            from ai_modeler.agent import dsh_agent
            dsh_agent.close_session(getattr(self, '_dsh_session_id', None))
        except Exception:
            pass
        self._dsh_session_id = 'ai-modeler-' + uuid.uuid4().hex
        self._agent_context = []
        self._agent_context_summary = ''
        self._agent_summary_through = 0
        self._agent_total_tokens = 0
        self._agent_total_cost = 0.0
        self._last_plan = None
        self._pending_agent_plan = None
        self.reference_images = []
        self._clear_agent_live_preview()
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.delete('1.0', 'end')
        self.txt_agent_events.configure(state='disabled')
        self._update_reference_label()
        for child in self.chat_messages.winfo_children():
            child.destroy()
        self._chat_cards = []
        self._chat_photo_refs = []
        self._hide_chat_activity()
        self._append_chat(
            'assistant',
            self._mode_welcome_message(new_chat=True))
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

    def _refresh_modeling_mode_switch(self):
        selected = getattr(self, '_modeling_mode', 'agent')
        if hasattr(self, 'btn_quick_mode'):
            self.btn_quick_mode.configure(
                style='Accent.TButton' if selected == 'quick' else 'Ghost.TButton')
        if hasattr(self, 'btn_agent_mode'):
            self.btn_agent_mode.configure(
                style='Accent.TButton' if selected == 'agent' else 'Ghost.TButton')
        if hasattr(self, 'lbl_mode_description'):
            self.lbl_mode_description.configure(text=(
                '内置规则 · 本地快速建模 · 文字输入'
                if selected == 'quick' else
                'DSH 多模态 Agent · 支持参考图、工具调用与连续修改'))
        if hasattr(self, 'txt'):
            self._input_placeholder = self._mode_input_placeholder()
            if getattr(self, '_input_placeholder_active', False):
                self.txt.delete('1.0', 'end')
                self.txt.insert('1.0', self._input_placeholder)
                self.txt.configure(fg=TH.TEXT_MUTE)
        if hasattr(self, 'btn_model'):
            self.btn_model.configure(text=self._model_button_text())

    def _mode_input_placeholder(self):
        if getattr(self, '_modeling_mode', 'agent') == 'quick':
            return '快速模式：输入文字描述；参考图请切换到 Agent 创造模式…'
        return '描述要建的零件，或直接粘贴参考图片…'

    def _mode_welcome_message(self, new_chat=False):
        prefix = '新对话已开始。' if new_chat else ''
        if getattr(self, '_modeling_mode', 'agent') == 'quick':
            return (prefix + '快速模式使用原有内置规则解析并本地建模，不调用 LLM。'
                    '请输入文字描述；图片建模请切换到 Agent 创造模式。')
        return (prefix + 'Agent 创造模式由 DSH 处理文字和参考图，支持连续修改、'
                '工具调用与模型预览。')

    def _set_modeling_mode_controls_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for button in getattr(self, '_mode_switch_buttons', []):
            try:
                button.configure(state=state)
            except Exception:
                pass

    def _switch_modeling_mode(self, mode):
        mode = str(mode or '').strip().lower()
        if mode not in {'quick', 'agent'} or self._busy:
            return
        if self._modeling_mode == mode:
            return
        self._modeling_mode = mode
        self.cfg['modeling_mode'] = mode
        self._refresh_modeling_mode_switch()
        try:
            cfgmod.save(self.cfg)
        except Exception as exc:
            self.log('保存建模模式失败: %s' % exc)
        if mode == 'quick':
            self.status('已切换到快速模式：使用原有内置规则，不调用 LLM。')
        else:
            self.status('已切换到 Agent 创造模式：由 DSH Agent 处理文字和参考图。')

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
        mode = getattr(self, '_modeling_mode', 'agent')
        if mode == 'quick' and images:
            self._append_chat(
                'assistant',
                '快速模式只处理文字，参考图片仍保留在输入区；切换到「Agent 创造模式」后再发送。')
            self.status('快速模式不支持图片输入', TH.WARN)
            return
        profile = {}
        try:
            from ai_modeler.agent import providers
            profile = providers.active_profile(llmcfg) or {}
        except Exception:
            pass
        effective_llm = profile or llmcfg
        llm_ready = bool(
            llmcfg.get('enabled') and
            all(str(effective_llm.get(key) or '').strip()
                for key in ('base_url', 'model', 'api_key')))
        if mode == 'agent' and not llm_ready:
            if llmcfg.get('enabled'):
                reason = '大模型已启用，但当前供应商的接口地址、模型名称或 API Key 不完整。'
            else:
                reason = 'Agent 创造模式需要启用并配置支持 tools 的大模型。'
            self._append_chat(
                'assistant',
                reason + ' 输入内容仍保留；请检查「工具 → AI 大模型设置」，或切换到「快速模式」。')
            self.status('Agent 创造模式尚未配置 LLM', TH.ERR)
            return
        self._append_chat('user', text or '请根据参考图建立模型。', images)
        self._set_prompt_text('')
        self.clear_reference_images()

        if mode == 'quick':
            self._last_plan = None
            self._pending_agent_plan = None
            self._append_chat(
                'assistant',
                '快速模式：使用原有内置规则解析并本地建模，不调用大模型。')
            self._finish_generate(text, None)
            return

        if mode == 'agent':
            self._agent_context.append({
                'role': 'user',
                'content': (text or '请根据参考图建立模型。') +
                           (('\n参考图：' + '、'.join(os.path.basename(p) for p in images))
                            if images else ''),
                'image_paths': list(images),
            })
            self._agent_generate(text, images)
            return

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
        self.txt_agent_events.see('end')
        self.txt_agent_events.configure(state='disabled')

    def _append_agent_reasoning(self, status_text):
        """Surface receipt of DSH reasoning events without dumping raw chain text."""
        if not status_text:
            return
        self._agent_reasoning_stream_seen = True
        status_text = str(status_text).strip()
        if status_text.startswith('已接收增量'):
            self._agent_stream_detail = '已收到模型思考流增量'
            if '累计约' in status_text:
                raw_count = status_text.split('累计约', 1)[1].split('字符', 1)[0]
                digits = ''.join(ch for ch in raw_count if ch.isdigit())
                if digits:
                    self._agent_reasoning_chars = int(digits)
                    self._agent_stream_detail = '已收到模型思考流增量 · 累计约 %s 字符' % format(
                        self._agent_reasoning_chars, ',')
        else:
            # Backward-compatible DSH events may carry the raw delta in this
            # field; intentionally discard it and display only stream activity.
            self._agent_stream_detail = '已收到模型思考流增量（内容不展开）'
        if getattr(self, '_agent_wait_started', None) is not None:
            self._refresh_agent_wait_clock()
        else:
            self._set_chat_activity(
                '已收到模型思考流', self._agent_stream_detail, active=True)

    def _finish_agent_reasoning_block(self):
        if not getattr(self, '_agent_reasoning_block_open', False):
            return
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.insert('end', '\n')
        self.txt_agent_events.see('end')
        self.txt_agent_events.configure(state='disabled')
        self._agent_reasoning_block_open = False

    def _set_agent_thinking_panel(self, expanded, title=None):
        """Show live thinking activity while a model request is in flight."""
        if title is not None:
            self._agent_thinking_title = str(title)
        self._agent_thinking_expanded = bool(expanded)
        if self._agent_thinking_expanded:
            if self.agent_thinking_body.winfo_manager() != 'pack':
                self.agent_thinking_body.pack(
                    fill='x', padx=2, pady=(0, 2), before=self.lbl_agent_preview)
            arrow = '▼ '
        else:
            self.agent_thinking_body.pack_forget()
            arrow = '▶ '
        self.btn_agent_thinking.configure(
            text=arrow + self._agent_thinking_title)

    def _toggle_agent_thinking_panel(self):
        self._set_agent_thinking_panel(
            not self._agent_thinking_expanded,
            self._agent_thinking_title)

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
        if hasattr(self, 'lbl_token'):
            self.lbl_token.configure(text='Token 估算：—')
        if hasattr(self, 'btn_agent_thinking'):
            self._set_agent_thinking_panel(False, '模型思考（待机）')

    @staticmethod
    def _fmt_tokens(n):
        return '{:,}'.format(int(n or 0))

    def _total_cost_cny(self, cost):
        """把累计美元金额换算成人民币（汇率取单价表里的 usd_to_cny）。"""
        try:
            basis = cost.get('basis') if isinstance(cost, dict) else None
            rate = float((basis or {}).get('usd_to_cny') or 7.2)
        except (TypeError, ValueError, AttributeError):
            rate = 7.2
        return round(float(getattr(self, '_agent_total_cost', 0.0)) * rate, 4)

    def _usage_label(self, run_tokens, cost, total_cost):
        """状态栏文字：本轮/累计的 token 与估算费用。金额一律带"≈"与来源提示。"""
        from ai_modeler.agent import usage_cost as _usage_cost

        def money(item):
            if not isinstance(item, dict) or not item.get('priced'):
                return None
            basis = item.get('basis') or {}
            return _usage_cost.fmt_money(item.get('amount'), item.get('cny'),
                                         str(basis.get('currency') or 'USD'))

        txt = '用量：本轮 %s tokens' % self._fmt_tokens(run_tokens)
        cur = money(cost)
        if cur:
            txt += ' ≈ ' + cur
        txt += ' · 累计 %s tokens' % self._fmt_tokens(self._agent_total_tokens)
        tot = money(total_cost)
        if tot:
            txt += ' ≈ ' + tot
        if cur is None and run_tokens:
            txt += '（未配单价：填 config.json 的 pricing 即显示金额）'
        return txt

    def _show_agent_live_preview(self, payload):
        """Append a worker-produced preview to the conversation as soon as it is ready."""
        payload = payload if isinstance(payload, dict) else {}
        preview = payload.get('preview') if isinstance(payload.get('preview'), dict) else {}
        path = preview.get('preview_image')
        stage = str(payload.get('stage') or 'preview')
        if path and os.path.isfile(path):
            self._last_agent_preview_path = path
        dims = preview.get('dimensions') if isinstance(preview.get('dimensions'), dict) else {}
        size = dims.get('size') or []
        size_text = ' × '.join('%.2f' % float(value) for value in size) + ' mm' if size else '尺寸待计算'
        views = '、'.join(preview.get('preview_image_views') or [])
        message = '实时模型预览 · %s\n外形尺寸：%s\n视图：%s' % (
            stage, size_text, views or '—')
        if path and os.path.isfile(path):
            self._append_chat('assistant', message, [path])
        else:
            self._append_chat('assistant', message + '\n预览图暂时无法显示：' + str(path or ''))
        self._set_chat_activity('模型预览已更新', message, active=True)
        self._append_agent_event('预览已更新：%s；%s' % (stage, size_text))

    def _agent_generate(self, text, images):
        from ai_modeler.agent import dsh_agent
        self._busy = True
        self._set_modeling_mode_controls_enabled(False)
        self._t0 = time.time()
        self._agent_wait_started = None
        self._agent_wait_detail = ''
        self._agent_stream_detail = ''
        self._agent_wait_seconds = -1
        self._agent_wait_rendered_stream = None
        self._agent_reasoning_request_count = 0
        self._agent_reasoning_request_detail = ''
        self._agent_reasoning_block_open = False
        self._agent_reasoning_stream_seen = False
        self._agent_reasoning_chars = 0
        agent_cfg = self.cfg.get('agentloop', {}) if isinstance(self.cfg.get('agentloop'), dict) else {}
        llm_cfg = self.cfg.get('llm', {}) if isinstance(self.cfg.get('llm'), dict) else {}
        try:
            from ai_modeler.agent import providers
            active_profile = providers.active_profile(llm_cfg) or {}
        except Exception:
            active_profile = {}
        effort = str(active_profile.get('reasoning_effort')
                     or llm_cfg.get('reasoning_effort') or 'none').strip().lower()
        self._agent_effort_display = effort
        self._agent_reasoning_enabled = effort not in {'', 'none', 'off', 'disabled'}
        try:
            self._budget_s = max(60.0, min(1800.0,
                                           float(agent_cfg.get('total_timeout_sec', 1800))))
        except (TypeError, ValueError):
            self._budget_s = 1800.0
        self._cancel = threading.Event()
        self.lbl_busy.configure(text='AgentLoop 运行中…')
        try:
            self.btn_stop.configure(state='normal')
        except Exception:
            pass
        self._set_send_mode('stop')
        self.progress.configure(value=0)
        self.status('正在准备建模请求…')
        self._set_chat_activity(
            '正在准备建模请求',
            '整理对话上下文和 %d 张参考图；启动后会持续显示等待时间与工具活动。' % len(images),
            active=True)
        self.lbl_agent_stage.configure(text='准备本轮上下文…')
        self.lbl_agent_detail.configure(text='正在整理对话与参考图片；模型请求开始后会显示等待时间。')
        self._set_agent_thinking_panel(False, '正在准备模型请求')
        self.txt_agent_events.configure(state='normal')
        self.txt_agent_events.delete('1.0', 'end')
        self.txt_agent_events.configure(state='disabled')
        self._q = q = queue.Queue()
        request = {'text': text, 'images': list(images), 'id': time.monotonic_ns()}
        self._active_request = request
        context = [dict(item) for item in self._agent_context[:-1]]
        context_summary = self._agent_context_summary
        summary_through = self._agent_summary_through
        resumable_plan = self._pending_agent_plan or self._last_plan
        current_plan = json.loads(json.dumps(resumable_plan)) if resumable_plan else None
        current_document = None
        if self._pending_agent_plan is None and hasattr(self.doc, 'features'):
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
                result = dsh_agent.run_dsh_agent(
                    text, image_paths=images, cfg=cfg_snapshot,
                    session_id=self._dsh_session_id,
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

    def _refresh_agent_wait_clock(self):
        """Keep the model request visibly alive while the worker is blocked on HTTP."""
        started = getattr(self, '_agent_wait_started', None)
        if started is None:
            return
        elapsed = max(0, int(time.monotonic() - started))
        stream_detail = getattr(self, '_agent_stream_detail', '')
        if (elapsed == getattr(self, '_agent_wait_seconds', -1)
                and stream_detail == getattr(self, '_agent_wait_rendered_stream', None)):
            return
        self._agent_wait_seconds = elapsed
        self._agent_wait_rendered_stream = stream_detail
        minutes, seconds = divmod(elapsed, 60)
        clock = '%02d:%02d' % (minutes, seconds)
        if getattr(self, '_agent_reasoning_stream_seen', False):
            stage = '模型正在思考'
        elif '生成答复' in stream_detail:
            stage = '模型正在生成答复'
        elif '工具调用' in stream_detail:
            stage = '模型正在准备工具调用'
        elif getattr(self, '_agent_reasoning_enabled', False):
            stage = '等待模型思考流'
        else:
            stage = '模型响应等待中'
        self.lbl_agent_stage.configure(text=stage + ' · ' + clock)
        detail_lines = [self._agent_wait_detail or '正在等待模型处理本轮输入']
        detail_lines.append('推理强度：%s' % getattr(self, '_agent_effort_display', '未记录'))
        if not getattr(self, '_agent_reasoning_enabled', False):
            detail_lines.append('当前推理关闭，服务通常不会返回思考流；模型响应、工具活动和预览仍会持续显示。')
        if stream_detail:
            detail_lines.append(stream_detail)
        detail_lines.append('已等待 %s；点输入框旁的停止按钮可当场中断本次模型请求。' % clock)
        self._set_chat_activity(stage + ' · ' + clock, '\n'.join(detail_lines), active=True)
        self.lbl_agent_detail.configure(
            text='\n'.join(detail_lines))
        self._set_agent_thinking_panel(
            self._agent_thinking_expanded,
            '%s · %s' % (stage, clock))

    def _poll_agent(self):
        if not self._q:
            return
        try:
            while True:
                kind, value, detail = self._q.get_nowait()
                if kind == 'agent_progress':
                    value = str(value)
                    if value.startswith('模型等待中：'):
                        self._finish_agent_reasoning_block()
                        self._agent_reasoning_request_count += 1
                        self._agent_reasoning_request_detail = value[len('模型等待中：'):].strip()
                        self._agent_wait_started = time.monotonic()
                        self._agent_wait_detail = value[len('模型等待中：'):].strip()
                        self._agent_stream_detail = ''
                        self._agent_wait_seconds = -1
                        self._agent_wait_rendered_stream = None
                        self._set_agent_thinking_panel(True, '模型正在思考')
                        self._append_agent_event(value)
                        self._refresh_agent_wait_clock()
                        self.status('模型正在处理当前请求…', TH.WARN)
                        continue
                    if value.startswith('模型思考链增量：'):
                        self._append_agent_reasoning(
                            value[len('模型思考链增量：'):])
                        continue
                    if value.startswith('模型思考流：'):
                        self._append_agent_reasoning(
                            value[len('模型思考流：'):].strip())
                        continue
                    if value.startswith('模型流式状态：'):
                        self._agent_stream_detail = value[len('模型流式状态：'):].strip()
                        self._refresh_agent_wait_clock()
                        continue
                    if value.startswith('模型进展摘要：'):
                        summary = value[len('模型进展摘要：'):].strip()
                        self.lbl_agent_detail.configure(text=summary[:1200])
                        self._set_agent_thinking_panel(False, '模型判断摘要（已收起）')
                        self._append_agent_event('模型判断摘要：' + summary)
                        self._append_chat('system', '阶段摘要：' + summary[:700])
                        self._set_chat_activity('模型阶段摘要', summary[:700], active=True)
                        continue
                    if value.startswith('Agent 决策摘要：'):
                        summary = value[len('Agent 决策摘要：'):].strip()
                        self.lbl_agent_detail.configure(text=summary[:1200])
                        self._set_agent_thinking_panel(False, '下一步：' + summary[:56])
                        self._append_agent_event('下一步：' + summary)
                        self._append_chat('system', '下一步：' + summary[:700])
                        self._set_chat_activity('Agent 正在继续', summary[:700], active=True)
                        continue
                    if value.startswith('模型响应已返回：'):
                        self._finish_agent_reasoning_block()
                        self._agent_wait_started = None
                        self._agent_stream_detail = ''
                        self._agent_wait_seconds = -1
                        self._agent_wait_rendered_stream = None
                        self.lbl_agent_stage.configure(text='模型已返回 · 正在处理结果…')
                        self.lbl_agent_detail.configure(
                            text='思考状态已自动收起。\n' +
                                 value[len('模型响应已返回：'):].strip())
                        self._set_agent_thinking_panel(False, '思考已完成（已收起）')
                        self._append_agent_event(value)
                        self._set_chat_activity(
                            '模型已返回 · 正在处理结果',
                            value[len('模型响应已返回：'):].strip(), active=True)
                        continue
                    self._agent_wait_started = None
                    self._agent_stream_detail = ''
                    self._set_agent_thinking_panel(False, '思考阶段结束')
                    self._append_chat('system', value)
                    self.lbl_agent_stage.configure(text=str(value))
                    self._append_agent_event(value)
                    self._set_chat_activity(
                        '正在执行建模步骤' if ('工具' in value or '特征' in value) else 'AgentLoop 正在运行',
                        value, active=True)
                    self.status(value)
                    continue
                if kind == 'agent_preview':
                    self._finish_agent_reasoning_block()
                    self._agent_wait_started = None
                    self._agent_stream_detail = ''
                    self._set_agent_thinking_panel(False, '正在查看建模预览')
                    request = self._active_request or {}
                    if value.get('request_id') == request.get('id'):
                        self._show_agent_live_preview(value)
                        self.status('建模过程预览已更新', TH.WARN)
                    continue
                self._agent_wait_started = None
                self._finish_agent_reasoning_block()
                self._agent_stream_detail = ''
                self._set_agent_thinking_panel(False, '正在整理本轮结果')
                self._set_chat_activity('本轮即将完成', '正在整理模型与运行结果…', active=True)
                self._unlock_ui()
                request = self._active_request or {'text': '', 'images': []}
                text = request['text']
                if kind == 'agent_ok':
                    result = value
                    self._agent_context_summary = result.get(
                        'conversation_summary', self._agent_context_summary)
                    self._agent_summary_through = result.get(
                        'summary_through', self._agent_summary_through)
                    usage = result.get('llm_usage') or {}
                    cost = result.get('llm_cost') or {}
                    run_tokens = int(float(usage.get('total_tokens') or 0) or 0)
                    if run_tokens:
                        self._agent_total_tokens += run_tokens
                    run_cost = cost.get('amount') if cost.get('priced') else None
                    if run_cost:
                        self._agent_total_cost = round(
                            float(getattr(self, '_agent_total_cost', 0.0)) + float(run_cost), 6)
                    total_cost = None
                    if getattr(self, '_agent_total_cost', 0):
                        total_cost = {'priced': True, 'amount': self._agent_total_cost,
                                      'cny': self._total_cost_cny(cost), 'basis': cost.get('basis')}
                    self.lbl_token.configure(
                        text=self._usage_label(run_tokens, cost, total_cost))
                    reply = result.get('reply', 'AgentLoop 已结束。')
                    self._append_chat('assistant', reply)
                    if run_tokens:
                        # 用量小结放在回答之后：先看结果，再看这一轮花了多少
                        from ai_modeler.agent import usage_cost as _usage_cost
                        self._append_chat('system', _usage_cost.format_chat_lines(
                            usage, cost, total_cost))
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
                    request_efforts = []
                    for call in result.get('model_calls', []):
                        if not isinstance(call, dict):
                            continue
                        effort = call.get('reasoning_effort')
                        thinking = call.get('thinking')
                        if effort:
                            label = str(effort)
                        elif isinstance(thinking, dict) and thinking.get('type'):
                            label = 'thinking:%s' % thinking['type']
                        else:
                            label = '服务商默认'
                        if label not in request_efforts:
                            request_efforts.append(label)
                    request_effort_text = '、'.join(request_efforts) or '未记录'
                    self.log('AgentLoop 状态：%s；复核轮次：%s；自动返修：%s；函数工具：%s；LLM 请求：%s；截断续接：%s；请求思考强度：%s；请求图像块：%s；预览：%s' %
                             (result.get('state'), result.get('review_rounds', 0),
                              result.get('repair_cycles', 0), result.get('tool_calls', 0),
                              result.get('model_call_count', 0),
                              result.get('truncated_responses', 0),
                              request_effort_text,
                              result.get('image_parts_in_request', 0),
                              result.get('preview_calls', 0)))
                    if result.get('preview', {}).get('preview_image'):
                        self.log('本地复核图：' + result['preview']['preview_image'])
                    if result.get('run_record'):
                        self.log('AgentLoop 运行记录：' + result['run_record'])
                    for label, path in (result.get('output_paths') or {}).items():
                        if path:
                            self.log('本地%s：%s' % (label, path))
                    if result.get('output_warning'):
                        self.log(result['output_warning'])
                    routing = result.get('routing') or {}
                    self.lbl_agent_stage.configure(
                        text='本轮完成 · %s' % (routing.get('action') or result.get('state')))
                    self._set_agent_thinking_panel(False, '思考已完成（已收起）')
                    self.lbl_agent_detail.configure(
                        text='工作流：%s\n复核：%s（%s 轮）\n函数工具：%s · LLM 请求：%s · 截断续接：%s\n请求思考强度：%s\n请求图像块：%s · 预览：%s\n%s' % (
                            routing.get('action', '—'), routing.get('review_mode', '—'),
                            result.get('review_rounds', 0), result.get('tool_calls', 0),
                            result.get('model_call_count', 0),
                            result.get('truncated_responses', 0), request_effort_text,
                            result.get('image_parts_in_request', 0),
                            result.get('preview_calls', 0), routing.get('reason', '')))
                    if result.get('state') == 'conversation':
                        self._append_agent_event('连续对话已回复；当前模型与特征树保持不变。')
                        self.status('连续对话已回复')
                    elif result.get('state') == 'accepted':
                        try:
                            from ai_modeler.sdk import cadplan
                            self._agent_candidate_notice = None
                            self._pending_agent_plan = None
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
                        self._pending_agent_plan = None
                        candidate_shown = self._show_agent_candidate(text, result)
                        if candidate_shown and result.get('state') == 'reviewed':
                            self.status('多视图检查已通过；未运行独立相似度审查', TH.WARN)
                        elif candidate_shown:
                            self.status('计划已校验并预览；当前是未复核候选', TH.WARN)
                        else:
                            self.status('AgentLoop 已生成结果，但候选无法显示', TH.ERR)
                    else:
                        pending_plan = result.get('plan')
                        pending_validation = result.get('validation') or {}
                        if (isinstance(pending_plan, dict)
                                and pending_validation.get('state')
                                in {'valid', 'needs_confirmation'}):
                            self._pending_agent_plan = pending_plan
                        candidate_shown = self._show_agent_candidate(text, result)
                        if (candidate_shown and
                                result.get('state') == 'candidate_with_open_findings'):
                            self.status('待确认候选已保存；视觉复核仍有未解决项', TH.WARN)
                        elif candidate_shown:
                            self.status('AgentLoop 尚未接受；当前仅显示候选模型', TH.WARN)
                        else:
                            self.status('AgentLoop 已停止：需要补充信息或仍有待处理偏差', TH.WARN)
                    elapsed = max(0, int(time.time() - getattr(self, '_t0', time.time())))
                    minutes, seconds = divmod(elapsed, 60)
                    self._set_chat_activity(
                        '本轮已完成 · %02d:%02d' % (minutes, seconds),
                        '状态：%s · 推理强度：%s · 思考流：%s' % (
                            result.get('state', 'unknown'),
                            getattr(self, '_agent_effort_display', '未记录'),
                            ('已接收约 %s 字符' % format(self._agent_reasoning_chars, ',')
                             if self._agent_reasoning_stream_seen else
                             '未返回（推理关闭或服务未提供）')),
                        active=False)
                elif kind == 'agent_err':
                    if self._cancel is not None and self._cancel.is_set():
                        # 模型流被「停止」当场中断：按取消收尾，不按失败告警。
                        self._agent_wait_started = None
                        self._agent_stream_detail = ''
                        self._finish_agent_reasoning_block()
                        self._set_agent_thinking_panel(False, '本轮已停止')
                        if hasattr(self, '_q'):
                            self._q = queue.Queue()      # 丢弃同行线程后续消息
                        self._active_request = None
                        self._unlock_ui()
                        self._append_chat('assistant', '已停止当前 AgentLoop（模型请求已当场中断）。')
                        self._set_chat_activity('本轮已停止', '已中断模型请求。', active=False)
                        self.status('AgentLoop 已停止', TH.WARN)
                        self._resume_pending_request()
                        return
                    message = 'AgentLoop 失败：' + str(value)
                    self._append_chat('assistant', message)
                    self._agent_context.append({'role': 'assistant', 'content': message})
                    self.log(message)
                    self.log(detail or '')
                    self.lbl_agent_stage.configure(text='本轮失败')
                    self.lbl_agent_detail.configure(text=str(value)[:1200])
                    self._set_agent_thinking_panel(False, '本轮失败（思考已收起）')
                    self._append_agent_event(message)
                    self._set_chat_activity('AgentLoop 执行失败', str(value)[:500], active=False)
                    self.status('AgentLoop 未完成', TH.ERR)
                self._active_request = None
                if not self._busy:
                    self._resume_pending_request()
                return
        except queue.Empty:
            pass
        self._refresh_agent_wait_clock()
        if self._cancel is not None and self._cancel.is_set():
            self._agent_wait_started = None
            self._agent_stream_detail = ''
            self._finish_agent_reasoning_block()
            self._set_agent_thinking_panel(False, '本轮已取消')
            self._q = queue.Queue()
            self._active_request = None
            self._unlock_ui()
            self._append_chat('assistant', '已取消当前 AgentLoop。')
            self._set_chat_activity('本轮已取消', '当前请求已停止。', active=False)
            self.status('AgentLoop 已取消', TH.WARN)
            self._resume_pending_request()
            return
        if time.time() - getattr(self, '_t0', time.time()) > self._budget_s + 15:
            self._agent_wait_started = None
            self._agent_stream_detail = ''
            self._finish_agent_reasoning_block()
            self._set_agent_thinking_panel(False, '本轮超时（思考已收起）')
            if self._cancel is not None:
                self._cancel.set()
            self._q = queue.Queue()
            self._active_request = None
            self._unlock_ui()
            message = 'AgentLoop 超过总时限，已停止等待；没有回退到规则猜测。'
            self._append_chat('assistant', message)
            self._agent_context.append({'role': 'assistant', 'content': message})
            self._set_chat_activity('AgentLoop 已超时', message, active=False)
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
        self._set_modeling_mode_controls_enabled(False)
        self._t0 = time.time()
        self._budget_s = self._budget()
        self._cancel = threading.Event()
        self.lbl_busy.configure(text='正在计算…')
        try:
            self.btn_stop.configure(state='normal')
        except Exception:
            pass
        self._set_send_mode('stop')
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

    # ---- 发送/停止双态按钮 -------------------------------------------------

    def _draw_send_button(self, mode):
        """按模式重画输入框旁的圆形按钮：send=发送 ↑，stop=停止 ■。"""
        canvas = self.btn_send
        canvas.delete('all')
        if mode == 'stop':
            self._send_circle = canvas.create_oval(
                1, 1, 33, 33, fill=TH.ERR, outline='')
            canvas.create_rectangle(11, 11, 23, 23, fill='#ffffff', outline='')
        else:
            self._send_circle = canvas.create_oval(
                1, 1, 33, 33, fill='#e85b9f', outline='')
            canvas.create_text(
                17, 16, text='↑', fill='#ffffff', font=(TH.UI, 15, 'bold'))
        self._send_mode = mode

    def _set_send_mode(self, mode):
        """忙碌时把发送按钮切成停止按钮，空闲时还原；幂等可安全重入。"""
        try:
            if getattr(self, '_send_mode', None) != mode:
                self._draw_send_button(mode)
        except Exception:
            pass

    def _on_send_button(self, _event=None):
        # 运行中点击 = 停止当前 AgentLoop；空闲点击 = 发送。
        # 回车键不经过这里：运行中回车仍旧把消息排队到本轮之后。
        if getattr(self, '_send_mode', '') == 'stop' and self._busy:
            self.cancel_build()
        else:
            self.generate()

    def _send_hover(self, entering):
        circle = getattr(self, '_send_circle', None)
        if circle is None:
            return
        if getattr(self, '_send_mode', '') == 'stop':
            hover = '#ff6f68' if TH.is_dark() else '#b91c1c'
            fill = hover if entering else TH.ERR
        else:
            fill = '#d94d91' if entering else '#e85b9f'
        self.btn_send.itemconfigure(circle, fill=fill)

    def cancel_build(self):
        """用户在计算过程中点「停止」。

        几何构建阶段：由 doc.build 的 deadline/cancel 检查中止（BuildAborted）。
        AgentLoop 阶段：置位事件后，飞行中的模型流式请求会在下一个分片处
        被当场断开（llm.ModelRequestCancelled），不再等整条响应跑完烧 token；
        本页 UI 由 _poll_agent 的取消分支收尾。
        """
        if not self._busy:
            return
        if self._cancel is not None:
            self._cancel.set()
        self.status('正在停止…')
        self.lbl_busy.configure(text='正在停止…')
        self._agent_wait_started = None
        if getattr(self, '_agent_stream_detail', None) is not None:
            self._agent_stream_detail = ''
        try:
            self._finish_agent_reasoning_block()
            self._set_agent_thinking_panel(False, '正在停止…')
        except Exception:
            pass

    def _unlock_ui(self):
        self._busy = False
        self._set_modeling_mode_controls_enabled(True)
        self.lbl_busy.configure(text='')
        try:
            self.btn_stop.configure(state='disabled')
        except Exception:
            pass
        self._set_send_mode('send')

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
        self._pending_agent_plan = None
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
            self._pending_agent_plan = None
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
        from ai_modeler.agent import providers

        win = tk.Toplevel(self)
        win.title('多模态 AgentLoop 设置 · 多供应商')
        win.geometry('680x560')
        base_cfg = self.cfg.get('llm') if isinstance(
            self.cfg.get('llm'), dict) else None
        llm = providers.ensure_providers(base_cfg or {
            'enabled': False, 'base_url': 'https://api.openai.com/v1',
            'model': 'gpt-4o-mini', 'api_key': ''})
        profiles = llm['providers']                 # 列表内元素就地编辑
        model_lists = {}                            # 档案id → 已拉取的模型列表
        try:
            start_idx = [p['id'] for p in profiles].index(llm['active_provider_id'])
        except ValueError:
            start_idx = 0

        v_en = tk.BooleanVar(value=bool(llm.get('enabled')))
        v_name = tk.StringVar()
        v_url = tk.StringVar()
        v_model = tk.StringVar()
        v_key = tk.StringVar()
        v_reasoning = tk.StringVar()
        prov_var = tk.StringVar()

        frm = ttk.Frame(win, padding=14)
        frm.pack(fill='both', expand=True)
        frm.columnconfigure(1, weight=1)
        ttk.Checkbutton(frm, text='启用多模态 AgentLoop',
                        variable=v_en).grid(row=0, column=0, columnspan=3, sticky='w', pady=4)

        ttk.Label(frm, text='供应商').grid(row=1, column=0, sticky='w', pady=4)
        prov_combo = ttk.Combobox(frm, textvariable=prov_var, state='readonly', width=34)
        prov_combo.grid(row=1, column=1, sticky='w', pady=4)
        btn_col = ttk.Frame(frm)
        btn_col.grid(row=1, column=2, sticky='e', pady=4)
        ttk.Button(btn_col, text='新建…', width=8,
                   command=lambda: open_new()).pack(side='left', padx=2)
        ttk.Button(btn_col, text='删除', width=8,
                   command=lambda: delete_current()).pack(side='left', padx=2)

        # row2 显示名称 / row3 接口地址（跨两列）
        for row, label, var in ((2, '显示名称', v_name), (3, '接口地址', v_url)):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Entry(frm, textvariable=var, width=50).grid(
                row=row, column=1, columnspan=2, sticky='ew', pady=4)

        # row4 模型名称（可编辑下拉 + 拉取服务端列表）
        ttk.Label(frm, text='模型名称').grid(row=4, column=0, sticky='w', pady=4)
        model_combo = ttk.Combobox(frm, textvariable=v_model, width=48)
        model_combo.grid(row=4, column=1, sticky='ew', pady=4)
        ttk.Button(frm, text='获取模型列表', width=12,
                   command=lambda: do_fetch()).grid(row=4, column=2, sticky='e', pady=4)

        # row5 API Key（掩码 + 显示切换）
        ttk.Label(frm, text='API Key').grid(row=5, column=0, sticky='w', pady=4)
        key_entry = ttk.Entry(frm, textvariable=v_key, width=48, show='*')
        key_entry.grid(row=5, column=1, sticky='ew', pady=4)

        def toggle_key():
            key_entry.configure(show='' if str(key_entry.cget('show')) == '*' else '*')
        ttk.Button(frm, text='显示', width=6, command=toggle_key).grid(
            row=5, column=2, sticky='e', pady=4)

        # row6 思考强度
        ttk.Label(frm, text='思考强度').grid(row=6, column=0, sticky='w', pady=4)
        ttk.Combobox(frm, textvariable=v_reasoning, values=providers.EFFORTS,
                     state='readonly', width=14).grid(row=6, column=1, sticky='w', pady=4)

        ttk.Label(frm, justify='left', font=('Microsoft YaHei UI', 8), foreground='#7f8c9b',
                  text=('· 下拉切换供应商档案；改完按「保存」才写入 config.json 并生效（保存的是当前选中档案）。\n'
                        '· 「新建…」按模板创建（SiliconFlow / DeepSeek 官方 / OpenAI / 本地 llama.cpp / 自定义）；\n'
                        '  「获取模型列表」实时调用该服务的 /models 接口填充模型下拉（需先填接口地址和密钥）。\n'
                        '· 思考强度（DeepSeek 官方）：默认 high；none=关闭思考；low/high/max 对应官方 effort。\n'
                        '  思考强度（SiliconFlow，如 Kimi-K2.7-Code）：low/high=开启思考；max=开启+最大思考预算 32768；\n'
                        '  none=不发送开关，按服务端默认（Kimi 等推理模型默认会思考）；思考过程将流式实时显示。\n'
                        '· 每份档案独立保存密钥与思考强度；其他 OpenAI 兼容服务请按其参数支持情况选择。')
                  ).grid(row=7, column=0, columnspan=3, sticky='w', pady=(8, 4))

        state = {'idx': start_idx, 'labels': []}

        def stash():
            """把界面上的值写回当前档案（切换/保存/删除/拉取前调用）。"""
            p = profiles[state['idx']]
            p['name'] = v_name.get().strip()
            p['base_url'] = v_url.get().strip()
            p['model'] = v_model.get().strip()
            p['api_key'] = v_key.get().strip()
            effort = (v_reasoning.get() or 'none').strip().lower()
            p['reasoning_effort'] = effort if effort in providers.EFFORTS else 'none'

        def load(idx):
            state['idx'] = idx
            p = profiles[idx]
            v_name.set(p.get('name', ''))
            v_url.set(p.get('base_url', ''))
            v_model.set(p.get('model', ''))
            v_key.set(p.get('api_key', ''))
            effort = str(p.get('reasoning_effort') or 'none').strip().lower()
            v_reasoning.set(effort if effort in providers.EFFORTS else 'none')
            model_lists.setdefault(p['id'], [])
            model_combo['values'] = model_lists[p['id']]
            prov_var.set(state['labels'][idx] if state['labels']
                          else providers.profile_label(p))

        def refresh(select=None):
            labels, index = [], {}
            for i, p in enumerate(profiles):
                label = providers.profile_label(p)
                origin, n = label, 2
                while label in index:
                    label = '%s (%d)' % (origin, n)
                    n += 1
                index[label] = i
                labels.append(label)
            state['labels'] = labels
            prov_combo['values'] = labels
            if select is None:
                select = state['idx']
            select = max(0, min(select, len(profiles) - 1))
            prov_var.set(labels[select])
            load(select)

        def on_pick(*_args):
            label = prov_var.get()
            idx = state['labels'].index(label) if label in state['labels'] else state['idx']
            if idx == state['idx']:
                return
            stash()
            load(idx)

        prov_combo.bind('<<ComboboxSelected>>', on_pick)

        def delete_current():
            if len(profiles) <= 1:
                messagebox.showinfo('提示', '至少保留一个供应商档案。', parent=win)
                return
            stash()
            removed = profiles.pop(state['idx'])
            model_lists.pop(removed.get('id'), None)
            refresh(select=0)
            self.status('已删除供应商档案「%s」' % (removed.get('name') or '未命名'))

        def do_fetch():
            stash()
            p = profiles[state['idx']]
            win.configure(cursor='watch')
            win.update_idletasks()
            try:
                ok, payload = providers.fetch_models(p['base_url'], p['api_key'])
            finally:
                win.configure(cursor='')
            if not ok:
                messagebox.showwarning('获取模型列表失败', payload, parent=win)
                return
            model_lists[p['id']] = payload
            model_combo['values'] = payload
            self.status('已获取 %d 个模型（%s）'
                        % (len(payload), p['name'] or providers.host_label(p['base_url'])))

        def open_new():
            chooser = tk.Toplevel(win)
            chooser.title('新建供应商 — 选择模板')
            chooser.transient(win)
            chooser.resizable(False, False)
            chooser.grab_set()
            body = ttk.Frame(chooser, padding=12)
            body.pack(fill='both', expand=True)
            ttk.Label(body, text='按模板创建一份新档案，创建后可自由修改：').pack(anchor='w', pady=(0, 6))

            def pick(key, chooser_ref):
                profile = providers.template_profile(key)
                profile['name'] = providers.unique_name(profiles, profile['name'])
                profiles.append(profile)
                chooser_ref.destroy()
                refresh(select=len(profiles) - 1)
                self.status('已新建供应商「%s」；填好密钥后按保存' % profile['name'])

            for key, label in providers.template_labels():
                ttk.Button(body, text=label, width=46,
                           command=lambda k=key, c=chooser: pick(k, c)
                           ).pack(fill='x', pady=2)
            ttk.Button(body, text='取消', width=46,
                       command=chooser.destroy).pack(fill='x', pady=(8, 0))

        def save():
            stash()
            for p in profiles:
                p['name'] = (p['name'] or providers.host_label(p['base_url'])
                             or '未命名').strip()
            llm_cfg = providers.ensure_providers(dict(base_cfg or {}))
            llm_cfg['enabled'] = bool(v_en.get())
            llm_cfg['providers'] = [dict(p) for p in profiles]
            llm_cfg['active_provider_id'] = profiles[state['idx']]['id']
            llm_cfg = providers.mirror_active(llm_cfg)
            self.cfg['llm'] = llm_cfg
            cfgmod.save(self.cfg)
            self.btn_model.configure(text=self._model_button_text())
            self.status('大模型设置已保存 · 当前供应商：%s'
                        % providers.profile_label(profiles[state['idx']]))
            win.destroy()

        refresh(select=start_idx)
        ttk.Button(frm, text='保存', style='Accent.TButton', command=save).grid(
            row=8, column=2, sticky='e', pady=6)


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
        try:
            from ai_modeler.agent import dsh_agent
            session_id = getattr(self, '_dsh_session_id', None)
            threading.Thread(target=dsh_agent.close_session,
                             args=(session_id,), daemon=False).start()
        except Exception:
            pass
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
