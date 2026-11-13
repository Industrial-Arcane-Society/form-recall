# -*- coding: utf-8 -*-
"""元增量学习状态面板 —— 把「后台在悄悄学什么」摊开给人看。

为什么单独一个窗口：元学习是后台机制，主窗口的对话区只看得见「这一句生成了什么模型」，
看不见「哪些族已经形成原型、候选池堆了多少、评测到底是原型法赢还是 1-NN 赢」。
没有这个窗口，这套机制在用户眼里就只是 README 里的一句话，验收时也没法解释。

和核心算法的关系（见 docs/architecture/元增量学习核心.md）：
    只消费契约 §4 的 MetaMemory（families / stats）与 §5 的 load_dataset /
    evaluate_holdout，不发明 API、不改核心包。核心包是另一条线并行实现的，
    写这个文件时它还没落地 —— 所以 import、构造、每一次方法调用都留了兜底：
    核心不在时窗口照样能开，表格显示「还没有学到任何族」，状态条写明原因；
    等它就绪后同一个文件不用改就能接上。

契约红线（改动前先看 §6）：
    · 颜色一律取 TH，本文件不出现 #rrggbb 字面量；
    · 评测必须同时报「原型法」和「1-NN 基线」两个数，只报一个没有说服力；
    · 回调一律 try/except 落到状态条 —— Tk 回调里抛异常会直接打断事件循环，
      界面上表现为「点了没反应」，比直接报错更难查；
    · 冷启动（空库）是正常状态，不是错误。
"""
from __future__ import annotations

import functools
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

from ai_modeler.ui import theme as TH

# ---------------------------------------------------------------------------
# 契约接线（§4 记忆 / §5 评测）
# ---------------------------------------------------------------------------
# 核心包可能还没写完：import 失败就降级成 None，绝不让界面跟着 import 不进来。
# 这里刻意捕获 Exception 而不是只接 ImportError —— 并行开发的包经常是"半截代码"，
# 语法错误抛的是 SyntaxError，只接 ImportError 的话整个面板会跟着 import 不进来。
_CORE_ERROR = ''
try:
    from ai_modeler.meta import DEFAULT_STORE, MetaEvaluator, MetaMemory
except Exception as _exc:  # noqa: BLE001 - 核心未就绪不是界面该崩的理由
    _CORE_ERROR = '%s: %s' % (type(_exc).__name__, _exc)
    DEFAULT_STORE = None  # type: ignore
    MetaEvaluator = None  # type: ignore
    MetaMemory = None  # type: ignore

# §2 说 load_dataset 由 __init__ 导出；万一只放在 evaluate.py 里，两条路都试。
try:
    from ai_modeler.meta import load_dataset
except Exception:  # noqa: BLE001
    try:
        from ai_modeler.meta.evaluate import load_dataset  # type: ignore
    except Exception:  # noqa: BLE001
        load_dataset = None  # type: ignore

# evaluate_holdout 在 §2 的导出清单里没有，正常从 evaluate.py 取。
try:
    from ai_modeler.meta import evaluate_holdout
except Exception:  # noqa: BLE001
    try:
        from ai_modeler.meta.evaluate import evaluate_holdout  # type: ignore
    except Exception:  # noqa: BLE001
        evaluate_holdout = None  # type: ignore


PROTO_MIN_VERIFIED = 3   # 契约 §4：同族累计 ≥3 条 verified 才建立原型
EVENT_LIMIT = 200        # 事件区只留最近这些条，堆多了没人看
POLL_MS = 150            # 评测后台线程的回主线程轮询间隔


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _get(obj, name, default=None):
    """读字段：核心层可能给 dict，也可能给 dataclass/namedtuple。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pick(obj, *names, **kw):
    """按候选名找第一个非 None 的字段。

    契约只钉死了 families() 里的 name/n_samples/n_verified/n_candidates 四个，
    「最近学习时间」「原型是否已建立」被 §6 要求显示、§4 却没给字段名。这里不猜死
    一个名字，实现方用 last_at / last_seen / updated_at 任何一个都能接上；
    全都没有就显示「—」——宁可不显示，也不编一个时间出来。
    """
    default = kw.get('default')
    for name in names:
        value = _get(obj, name, None)
        if value is not None:
            return value
    return default


def _as_int(raw, default=0):
    if isinstance(raw, bool):
        return int(raw)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _as_float(raw):
    """转 float，失败返回 None（由调用方决定怎么提示，不在这里抛）。"""
    if isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fmt_int(raw):
    """整数加千分位：1156 → 1,156；拿不到就显示「—」。"""
    if raw is None or isinstance(raw, bool):
        return '—' if raw is None else ('是' if raw else '否')
    try:
        return '{:,}'.format(int(raw))
    except (TypeError, ValueError):
        return str(raw)


def _fmt_time(raw):
    """时间字段归一成「YYYY-MM-DD HH:MM」。

    实现方可能存 ISO 字符串、时间戳，或者根本没这个字段 —— 全都要能显示，
    实在认不出来就把原文放出来，总比写个「—」丢掉线索好。
    """
    if raw is None or raw == '':
        return '—'
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(raw)))
        except (OSError, ValueError, OverflowError):
            return str(raw)
    text = str(raw)
    # ISO 的 'T' 换成空格，秒去掉：面板上不需要到秒的精度。
    if len(text) >= 16 and text[4] == '-' and text[10] in 'T ':
        text = text.replace('T', ' ')[:16]
    return text


def _short_path(text, limit=46):
    """把长路径压成「…\\尾段\\文件名」，并且只在目录分隔处下刀。

    顶栏右侧还要放两个 chip，全路径在 900 宽下会把标题挤掉；但按字符数硬截会
    出现 "…ocal\\Temp\\..." 这种从词中间切断的样子，看着像乱码。所以按目录段
    从后往前拼，超了就不再加。
    """
    text = str(text)
    if len(text) <= limit:
        return text
    segments = text.replace('/', '\\').split('\\')
    tail = segments[-1]
    for segment in reversed(segments[:-1]):
        candidate = segment + '\\' + tail
        if len(candidate) + 1 > limit:
            break
        tail = candidate
    return '…\\' + tail


def _bind_wrap(host, label, pad=28, minimum=160):
    """让一段说明文字随容器宽度折行。

    说明文字写死 wraplength 是最容易翻车的地方之一：窗口一窄，文字就被卡片
    右边界硬切掉，看着像界面坏了。绑在容器 <Configure> 上才跟得上窗口缩放。
    """
    def _resize(event, widget=label, pad=pad, minimum=minimum):
        try:
            widget.configure(wraplength=max(minimum, event.width - pad))
        except tk.TclError:
            pass

    try:
        host.bind('<Configure>', _resize)
    except tk.TclError:
        pass


def _guarded(method):
    """把 Tk 回调包一层：异常一律转成状态条消息，绝不让它冲出事件循环。"""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 这是界面最后一道兜底
            try:
                self._status('%s 失败：%s' % (method.__name__, exc), 'err')
            except Exception:
                pass  # 状态条自己都坏了就别再抛了
            return None

    return wrapper


# ---------------------------------------------------------------------------
# 窗口
# ---------------------------------------------------------------------------

class MetaPanelWindow(tk.Toplevel):
    """元增量学习状态面板（契约 §6）。

    参数：
        master   父窗口（Tk 或 Toplevel）
        memory   契约 §4 的 MetaMemory；None 时自己开一个 kb/meta_memory.json。
                 也允许传一个鸭子类型的替身（只要有 families()/stats()），
                 无头自检就是靠这个把数据路径跑一遍的。

    三块内容（上→下）：
        1. 总览：族数 / 已验证样本 / 候选样本 / 特征维度 / 最近更新，key-value 式；
        2. 族表格：族名、已验证数、候选数、原型状态、最近学习时间，点表头排序；
        3. 事件区：最近学习事件 + 「跑一次评测」（后台线程，不卡界面）。
    """

    def __init__(self, master, memory=None):
        super().__init__(master)

        self._store_error = ''
        self.memory = memory if memory is not None else self._make_memory()

        # 需要跨刷新保留的控件引用
        self._ui_font = None
        self._stat_labels = {}
        self._family_tree = None
        self._event_tree = None
        self._chip_core = None
        self._chip_store = None
        self.lbl_store = None
        self.lbl_status = None
        self._status_dot = None
        self.btn_eval = None
        self.btn_refresh = None

        # 事件区：契约 §4 没有「学习事件流」这个 API，所以主体是两次刷新之间
        # 比对 stats()/families() 推出来的变化（见 _collect_events）。
        self._events = []            # [(时刻, 类型, 内容)]，新的在前
        self._family_snap = None     # 上一次快照 {族名: (已验证数, 候选数)}
        self._event_source = 'diff'

        # 排序：默认按已验证样本数降序（"哪个族学得最多"是第一眼要看的）
        self._sort_key = 'n_verified'
        self._sort_desc = True

        # 评测：后台线程 + 主线程轮询，界面绝不 join 等结果
        self._eval_thread = None
        self._eval_running = False
        self._eval_result = None
        self._eval_error = None
        self._closed = False

        try:
            self.transient(master)
        except tk.TclError:
            pass  # 无头/独立调试时没有真正的主窗口，忽略即可

        # 卡片之间会露出窗口底色那几像素缝。不设的话是系统默认的浅灰，
        # 深色模式下整窗像镶了一圈白边。
        try:
            self.configure(bg=TH.BG)
        except tk.TclError:
            pass

        self.title('元增量学习 · 状态面板')
        self.geometry('1100x720')
        self.minsize(900, 600)

        self._ensure_theme()
        self._build_header()
        # 先建底栏再建主体：pack 按顺序分配空间，若主体(expand)先占满，
        # 窗口一旦压到最小高度，最后 pack 的底栏就会被挤出可视区。
        self._build_bottom()
        self._build_body()
        self.refresh()
        self._initial_status()

    # -- 初始化 -----------------------------------------------------------------

    def _make_memory(self):
        """没传 memory 时自己开一个（契约 §4）。

        kb/meta_memory.json 是相对路径，依赖当前工作目录。工作目录若不是项目根，
        这里会悄悄建出一个空库，用户看到「一个族都没学到」还以为是学习坏了。
        所以优先用契约保证的 DEFAULT_STORE（项目根下的绝对路径），拿不到再退回
        相对路径 —— 两条都失败就返回 None，由界面按「核心未就绪」处理。
        """
        if MetaMemory is None:
            self._store_error = _CORE_ERROR or 'ai_modeler.meta 里没有 MetaMemory'
            return None
        candidates = []
        if isinstance(DEFAULT_STORE, str) and DEFAULT_STORE:
            candidates.append(DEFAULT_STORE)
        candidates.append('kb/meta_memory.json')
        last = ''
        for path in candidates:
            try:
                return MetaMemory(path)
            except Exception as exc:  # noqa: BLE001 - 换下一个候选路径
                last = '%s（%s）' % (path, exc)
        self._store_error = last or 'MetaMemory 构造失败'
        return None

    def _ensure_theme(self):
        """主窗口通常已经 apply 过样式；单独调起本窗口（或无头测试）时补一次。

        apply() 是幂等的，只补控件样式，不改配色。
        """
        try:
            if not ttk.Style(self).lookup('Card.TFrame', 'background'):
                TH.apply(self)
        except Exception:  # noqa: BLE001 - 样式补不上也不该阻断窗口
            pass

    def _core_ready(self):
        """核心包能不能用：导入了、或者调用方直接塞了 memory 对象。"""
        return self.memory is not None

    # -- 顶部 ---------------------------------------------------------------------

    def _build_header(self):
        # 顶栏高度按实际字高算：写死 58px 在 150% 缩放下会把副标题挤到边缘。
        try:
            title_h = tkfont.Font(font=TH.F['brand'], root=self).metrics('linespace')
            sub_h = tkfont.Font(font=TH.F['small'], root=self).metrics('linespace')
            height = title_h + sub_h + 10
        except Exception:  # noqa: BLE001
            height = 62

        head = tk.Frame(self, bg=TH.ACCENT, height=height)
        head.pack(fill='x')
        head.pack_propagate(False)

        left = tk.Frame(head, bg=TH.ACCENT)
        left.pack(side='left', padx=16, pady=4)
        tk.Label(left, text='元增量学习', bg=TH.ACCENT, fg=TH.ON_ACCENT,
                 font=TH.F['brand']).pack(anchor='w')
        tk.Label(left, text='原型网络 · 增量 EMA · exemplar 回放——学新的不忘旧的',
                 bg=TH.ACCENT, fg=TH.HEAD_SUB, font=TH.F['small']).pack(anchor='w')

        right = tk.Frame(head, bg=TH.ACCENT)
        right.pack(side='right', padx=16, pady=4)

        chips = tk.Frame(right, bg=TH.ACCENT)
        chips.pack(anchor='e')
        # 只读事实：这两个标签描述的是状态，不放任何改写入口。
        self._chip_core = tk.Label(chips, text='核心包未就绪', bg=TH.ACCENT_DK,
                                   fg=TH.WARN, font=TH.F['tiny'], padx=7, pady=1)
        self._chip_core.pack(side='left', padx=(6, 0))
        self._chip_store = tk.Label(chips, text='记忆库 —', bg=TH.ACCENT_DK,
                                    fg=TH.HEAD_SUB, font=TH.F['tiny'], padx=7, pady=1)
        self._chip_store.pack(side='left', padx=(6, 0))

        self.lbl_store = tk.Label(right, text='', bg=TH.ACCENT, fg=TH.HEAD_SUB,
                                  font=TH.F['tiny'], anchor='e')
        self.lbl_store.pack(anchor='e')

    def _refresh_header(self, stats, families):
        ready = self._core_ready()
        store = self._store_label()
        try:
            self._chip_core.configure(
                text='核心包就绪' if ready else '核心包未就绪',
                fg=TH.OK if ready else TH.WARN)
            count = len(families)
            self._chip_store.configure(
                text='记忆库 · 空库' if count == 0 else '记忆库 · %d 个族' % count,
                fg=TH.HEAD_SUB)
            self.lbl_store.configure(text=store)
        except tk.TclError:
            pass

    def _store_label(self):
        """顶栏那行小字：记忆库到底存在哪个文件里，验收时要能一眼说清。"""
        if self.memory is None:
            if self._store_error:
                return '记忆库未打开：%s' % self._store_error
            return '记忆库未打开：元增量核心包还没就绪'
        path = _get(self.memory, 'store_path', None) or _get(self.memory, 'path', None)
        if not path:
            return '记忆库已加载（实现方未公布存储路径）'
        text = str(path)
        # 顶栏右侧还要放两个 chip，路径太长在 900 宽下会把标题挤掉，按目录段压短。
        return _short_path(text)

    # -- 主体 ---------------------------------------------------------------------

    def _build_body(self):
        # 顺序讲究：事件区先 pack 到 side='bottom' 占住位置，再让族表格(expand)吃
        # 剩余高度。反过来的话，窗口压到 900×600 时最后 pack 的事件区会被挤出屏幕。
        self._build_events_section()
        self._build_overview()
        self._build_family_section()

    def _slim_head(self, parent, title, hint=None):
        """TH.section 的紧凑版：同样的主色竖条 + 标题，只是上下留白更省。

        为什么要省这几像素：契约要求三块竖着排（总览/族表/事件区），窗口最小
        900×600。三个 TH.section 各吃 58px 头顶留白，族表格就只剩两行——
        卡片标题是装饰，表格才是主体，所以这里挪一点留白还给它。
        颜色和字体仍全部取自 TH，不引入新样式。
        """
        head = tk.Frame(parent, bg=TH.PANEL)
        head.pack(fill='x', padx=12, pady=(8, 4))
        tk.Frame(head, bg=TH.ACCENT, width=3, height=14).pack(side='left', padx=(0, 8))
        tk.Label(head, text=title, bg=TH.PANEL, fg=TH.TEXT,
                 font=TH.F['h2']).pack(side='left')
        if hint:
            label = tk.Label(head, text=hint, bg=TH.PANEL, fg=TH.TEXT_MUTE,
                             font=TH.F['small'])
            label.pack(side='right')
            # 卡片一窄，右侧提示会先被挤掉；绑上折行，宁可换行也别被切成半句。
            _bind_wrap(head, label, pad=240, minimum=90)
        return head

    def _build_overview(self):
        """契约 §6 的上半：key-value 式总览。"""
        outer, inner = TH.card(self)
        outer.pack(fill='x', padx=10, pady=(8, 0))

        head = self._slim_head(inner, '记忆总览', '数字直接来自 MetaMemory.stats()')
        # 刷新按钮放这儿：它是"读"，和"跑评测"那种"算"分开，别混在一起。
        self.btn_refresh = ttk.Button(head, text='↻ 刷新', style='Ghost.TButton',
                                      command=self._on_refresh)
        self.btn_refresh.pack(side='right')

        grid = tk.Frame(inner, bg=TH.PANEL)
        grid.pack(fill='x', padx=12, pady=(0, 8))

        specs = (
            ('families', '族数'),
            ('samples', '已验证样本'),
            ('candidates', '候选样本'),
            ('dim', '特征维度'),
            ('updated_at', '最近更新'),
        )
        # 单行 key-value：键小字、值大字，横着排。
        # 为什么不做成五张竖卡片——那样一屏 720 里光总览就吃掉 150px，
        # 族表格会被压成两行；900×600 的最小尺寸下更是直接看不见表。
        for column, (key, label) in enumerate(specs):
            cell = tk.Frame(grid, bg=TH.PANEL)
            cell.grid(row=0, column=column, sticky='w', padx=(0, 26))
            tk.Label(cell, text=label, bg=TH.PANEL, fg=TH.TEXT_MUTE,
                     font=TH.F['small'], anchor='w').pack(side='left', pady=(4, 0))
            # 时间那一格是长文本，用小一号字，免得把这一行撑破。
            value_font = TH.F['body_b'] if key == 'updated_at' else TH.F['h1']
            value = tk.Label(cell, text='—', bg=TH.PANEL, fg=TH.TEXT,
                             font=value_font, anchor='w')
            value.pack(side='left', padx=(7, 0))
            self._stat_labels[key] = value

        # 最后一格（时间）吃掉剩余宽度，时间戳不会被挤到下一行。
        grid.grid_columnconfigure(len(specs) - 1, weight=1)

    def _build_family_section(self):
        """契约 §6 的中部：族表格。"""
        outer, inner = TH.card(self)
        outer.pack(fill='both', expand=True, padx=10, pady=(6, 0))

        self._slim_head(inner, '族记忆', '点表头按样本数排序')

        tree = self._make_table(inner, (
            ('name', '族名', '内六角螺钉', 'w'),
            ('n_verified', '已验证数', '1,156', 'center'),
            ('n_candidates', '候选数', '32', 'center'),
            ('proto', '原型状态', '待积累（还需 2 条）', 'center'),
            ('last', '最近学习时间', '2026-11-13 18:25', 'center')),
            height=3, pad=(12, 0, 12, 8), expand=True)
        # 表头点击排序。契约 §6 只要求"支持按样本数排序"，这里顺手把候选数也开了，
        # 族名保持不排序（字符串排序对中文族名没意义）。
        tree.heading('n_verified', command=self._on_sort_verified)
        tree.heading('n_candidates', command=self._on_sort_candidates)
        self._family_tree = tree

    def _build_events_section(self):
        """契约 §6 的下半：最近学习事件 + 跑评测。"""
        outer, inner = TH.card(self)
        outer.pack(side='bottom', fill='x', padx=10, pady=(6, 4))

        head = TH.section(inner, '最近学习事件', '事件来自两次刷新之间的记忆变化')
        self.btn_eval = ttk.Button(head, text='跑一次评测', style='Accent.TButton',
                                   command=self._on_evaluate)
        self.btn_eval.pack(side='right')

        tree = self._make_table(inner, (
            ('at', '时间', '18:25:31', 'center'),
            ('kind', '类型', '评测', 'center'),
            ('text', '内容', '原型法 acc=0.836 · 1-NN 基线 acc=0.791（+4.5pp）', 'w')),
            height=3, pad=(12, 0, 12, 8))
        self._event_tree = tree

    # -- 建表 / 量宽 ---------------------------------------------------------------

    def _make_table(self, parent, columns, height=7, pad=(12, 0, 12, 8), expand=False):
        """统一建一张带横竖滚动条的表格，返回 Treeview。

        columns 里每项是 (key, 表头, 最长样例文本, 对齐)。

        为什么列宽不写死像素：本机是 150% 缩放，"W-INPUT-01" 实际占 110px，
        按 96dpi 估的 88px 会把编号切成 "W-INPUT-"。所以按当前 DPI 实测字宽来定，
        表头比样例长时取表头。窗口窄到放不下就出横向滚动条——宁可让用户滚，
        也不要在窗口边缘把字硬切掉。

        expand=True 时这张表吃满父容器的剩余高度（族表要跟着窗口长），
        False 时按 height 行固定（事件区不该因为窗口变大而变长）。
        """
        pad_l, pad_t, pad_r, pad_b = pad
        box = tk.Frame(parent, bg=TH.PANEL)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(box, columns=[spec[0] for spec in columns],
                            show='headings', height=height, selectmode='browse')
        for key, title, sample, anchor in columns:
            tree.heading(key, text=title)
            width = self._column_px(title, sample)
            tree.column(key, width=width, minwidth=width, anchor=anchor, stretch=True)

        vsb = ttk.Scrollbar(box, orient='vertical', command=tree.yview)
        hsb = ttk.Scrollbar(box, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        box.pack(fill='both' if expand else 'x', expand=expand,
                 padx=(pad_l, pad_r), pady=(pad_t, pad_b))
        return tree

    def _column_px(self, header, sample, padding=26):
        """列宽 = max(表头, 最长样例) 的实测像素 + 边距。字体拿不到时退回一个保底值。"""
        try:
            if self._ui_font is None:
                self._ui_font = tkfont.Font(font=TH.F['body'], root=self)
            measure = self._ui_font.measure
            return max(48, max(measure(header), measure(sample)) + padding)
        except Exception:  # noqa: BLE001 - 字体量不出来就按字数粗估
            return max(48, 12 * max(len(header), len(sample)) + padding)

    # -- 底部状态条 -----------------------------------------------------------------

    def _build_bottom(self):
        bottom = tk.Frame(self, bg=TH.PANEL)
        bottom.pack(side='bottom', fill='x', padx=10, pady=(0, 10))

        strip = tk.Frame(bottom, bg=TH.PANEL_ALT, highlightthickness=1,
                         highlightbackground=TH.BORDER)
        strip.pack(fill='x')
        self._status_dot = tk.Label(strip, text='●', bg=TH.PANEL_ALT,
                                    fg=TH.TEXT_MUTE, font=TH.F['small'])
        self._status_dot.pack(side='left', padx=(10, 5), pady=6)
        self.lbl_status = tk.Label(strip, text='', bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                                   font=TH.F['small'], anchor='w', justify='left',
                                   wraplength=820)
        self.lbl_status.pack(side='left', fill='x', expand=True, padx=(0, 10), pady=6)
        strip.bind('<Configure>', self._on_strip_configure)

    def _on_strip_configure(self, event):
        try:
            self.lbl_status.configure(wraplength=max(320, event.width - 60))
        except Exception:  # noqa: BLE001
            pass

    def _status(self, text, level='info'):
        """状态条。窗口销毁过程中的刷新到这里就静静忽略，不再抛。"""
        if self._closed:
            return
        color = {'ok': TH.OK, 'warn': TH.WARN, 'err': TH.ERR}.get(level, TH.TEXT_DIM)
        try:
            self.lbl_status.configure(text=str(text), fg=color)
            self._status_dot.configure(fg=color)
        except (tk.TclError, AttributeError):
            pass

    def _initial_status(self):
        """窗口刚开时先交代清楚：连上了什么、空库是不是正常、缺什么。"""
        if self.memory is None:
            if self._store_error:
                self._status('记忆库打不开（%s）：面板先按空库显示。'
                             % self._store_error, 'warn')
            elif _CORE_ERROR:
                self._status('元增量核心包还没就绪（%s）：面板先按空库显示，'
                             '核心落地后重开即可。' % _CORE_ERROR, 'warn')
            else:
                self._status('元增量核心包还没就绪（ai_modeler.meta 里没有 MetaMemory）：'
                             '面板先按空库显示，核心落地后重开即可。', 'warn')
            return

        stats = self._read_stats()
        families = self._read_families()
        verified, candidates = self._overview_counts(stats, families)
        if stats or families:
            self._status('已连接记忆库：%d 个族 / %s 条已验证样本 / %s 条候选。'
                         % (len(families), _fmt_int(verified),
                            _fmt_int(candidates)), 'ok')
        else:
            self._status('记忆库是空的（冷启动是正常状态）：让主程序 observe 一条经验，'
                         '或点「跑一次评测」看核心能不能跑通。', 'warn')

    # -- 读记忆库 -------------------------------------------------------------------

    def _read_stats(self):
        """stats() 契约 §4 保证返回 dict；实现方给别的东西也不该让界面崩。"""
        if self.memory is None:
            return {}
        reader = getattr(self.memory, 'stats', None)
        if not callable(reader):
            return {}
        try:
            data = reader()
        except Exception:  # noqa: BLE001 - 读不到就是空总览
            return {}
        return data if isinstance(data, dict) else {}

    def _read_families(self):
        """families() → 归一化后的 [{name, n_verified, n_candidates, ready, last}]。"""
        if self.memory is None:
            return []
        reader = getattr(self.memory, 'families', None)
        if not callable(reader):
            return []
        try:
            raw_rows = reader() or []
        except Exception:  # noqa: BLE001
            return []
        rows = []
        try:
            for raw in raw_rows:
                rows.append(self._norm_family(raw))
        except Exception:  # noqa: BLE001 - 半路遇到怪行就返回已解析的
            pass
        return rows

    def _norm_family(self, raw):
        """把一条族记录抹平成表格要的五个字段。"""
        name = str(_pick(raw, 'name', 'family', 'category', default='') or '').strip()
        # n_samples 是兜底：契约里 n_verified 与 n_samples 都给了，缺前者时用它。
        verified = _as_int(_pick(raw, 'n_verified', 'verified', 'n_samples', default=0))
        candidates = _as_int(_pick(raw, 'n_candidates', 'candidates', default=0))
        proto = _pick(raw, 'prototype_ready', 'has_prototype', 'prototype', default=None)
        last = _pick(raw, 'last_at', 'last_seen', 'last_learned',
                     'updated_at', 'last_update', default=None)
        # 实现方如果直接报了"原型是否建立"就信它；没报就按 §4 的 ≥3 条门槛推。
        ready = bool(proto) if proto is not None else verified >= PROTO_MIN_VERIFIED
        return {
            'name': name or '（未命名族）',
            'n_verified': verified,
            'n_candidates': candidates,
            'ready': ready,
            'last': _fmt_time(last),
        }

    # -- 刷新 ---------------------------------------------------------------------

    def refresh(self):
        """整窗重算。任何一环失败都只落到状态条，不让界面停在半截。"""
        if self._closed:
            return
        try:
            stats = self._read_stats()
            families = self._read_families()
        except Exception as exc:  # noqa: BLE001
            self._status('读取记忆库失败：%s' % exc, 'err')
            self._refresh_overview({}, [])
            self._refresh_table([])
            self._refresh_events()
            return

        try:
            self._refresh_overview(stats, families)
            self._refresh_table(families)
            self._refresh_header(stats, families)
            self._collect_events(stats, families)
            self._refresh_events()
        except Exception as exc:  # noqa: BLE001
            self._status('界面刷新失败：%s' % exc, 'err')

    def _refresh_overview(self, stats, families):
        verified, candidates = self._overview_counts(stats, families)
        values = {
            'families': _fmt_int(len(families)),
            'samples': _fmt_int(verified),
            'candidates': _fmt_int(candidates),
            'dim': _fmt_int(stats.get('dim')),
            'updated_at': _fmt_time(stats.get('updated_at')),
        }
        for key, label in self._stat_labels.items():
            try:
                label.configure(text=values.get(key, '—'))
            except tk.TclError:
                pass

    def _overview_counts(self, stats, families):
        """已验证 / 候选样本数：从族表汇总，而不是直接搬 stats 里的同名字段。

        为什么要自己加：契约 §4 只写了 stats 有 'samples'/'candidates'，没规定口径。
        真实实现里 'samples' 是「已经折进原型的条数」，没过 3 条门槛的族计 0——
        直接搬就会出现「总览说 0 条已验证、下面表格却列着 5 条」的自相矛盾。
        族表上的 n_verified/n_candidates 口径明确，两块对得上比省一行代码重要。
        """
        verified = sum(_as_int(row.get('n_verified')) for row in families)
        candidates = sum(_as_int(row.get('n_candidates')) for row in families)
        if not families:
            # 族表空的时候退回 stats 的汇总：实现方若暂未提供 families()，
            # 别把明明有样本的库显示成一串 0。
            verified = _as_int(_pick(stats, 'verified_total', 'samples', default=verified))
            candidates = _as_int(_pick(stats, 'candidates', default=candidates))
        return verified, candidates

    def _refresh_table(self, families):
        tree = self._family_tree
        if tree is None:
            return

        try:
            tree.delete(*tree.get_children())
            # 颜色每次刷新重设：theme.set_mode() 会重灌模块级颜色，
            # 焊在 import 时的常量到了深色模式就是过期颜色。
            tree.tag_configure('ready', foreground=TH.TEXT)
            tree.tag_configure('pending', foreground=TH.TEXT_MUTE)
            tree.tag_configure('empty', foreground=TH.TEXT_MUTE)
            # 排序指示画在表头上，用户才知道现在按哪一列排、升序还是降序。
            arrows = {'n_verified': '已验证数', 'n_candidates': '候选数'}
            for key, base in arrows.items():
                mark = ''
                if key == self._sort_key:
                    mark = ' ▼' if self._sort_desc else ' ▲'
                tree.heading(key, text=base + mark)
        except tk.TclError:
            return

        rows = self._sorted_rows(families)
        try:
            if not rows:
                # 冷启动不是错误：说清"为什么空"比一句"无数据"有用得多。
                tree.insert('', 'end', iid='empty', tags=('empty',), values=(
                    self._empty_text(), '—', '—', '—', '—'))
                return
            for index, row in enumerate(rows):
                tag = 'ready' if row['ready'] else 'pending'
                if row['ready']:
                    proto_text = '已建立'
                else:
                    need = max(0, PROTO_MIN_VERIFIED - row['n_verified'])
                    proto_text = '待积累（还需 %d 条）' % need if need else '待积累'
                tree.insert('', 'end', iid='fam:%d' % index, tags=(tag,), values=(
                    row['name'],
                    _fmt_int(row['n_verified']),
                    _fmt_int(row['n_candidates']),
                    proto_text,
                    row['last']))
        except tk.TclError:
            pass

    def _sorted_rows(self, families):
        """按当前排序键排；排序失败（数据里混进怪值）就保持原序，不炸。"""
        key = self._sort_key
        try:
            if key == 'name':
                return sorted(families, key=lambda row: str(row.get('name', '')),
                              reverse=self._sort_desc)
            return sorted(families, key=lambda row: _as_int(row.get(key, 0)),
                          reverse=self._sort_desc)
        except Exception:  # noqa: BLE001
            return list(families)

    def _empty_text(self):
        if self.memory is None:
            return '还没有学到任何族（核心包未就绪，先显示空壳）'
        return '还没有学到任何族（首次 observe 之后才会出现第一行）'

    # -- 事件区 -------------------------------------------------------------------

    def _collect_events(self, stats, families):
        """把"这一轮刷新看到的记忆变化"变成事件。

        契约 §4 没有「学习事件流」这个 API，所以主体只能自己推：
        每次刷新比对 families() 的 (已验证数, 候选数)，谁变了就记一条。
        如果实现方额外提供了 recent_events()/events 属性，优先用它的真实记录
        ——那是有时间戳的原始日志，比这里推出来的准。取不到就安静回退，
        不因为"没有这个口子"报错。
        """
        external = self._memory_events()
        if external is not None:
            self._event_source = 'memory'
            self._events = external[:EVENT_LIMIT]
            return

        self._event_source = 'diff'
        snap = {row['name']: (row['n_verified'], row['n_candidates']) for row in families}

        if self._family_snap is None:
            # 第一次只立基线，不凭空编事件。
            self._family_snap = snap
            if snap:
                verified_total, _candidates = self._overview_counts(stats, families)
                self._add_event('快照', '打开面板时记忆库已有 %d 个族 / %s 条已验证样本'
                                % (len(snap), _fmt_int(verified_total)))
            else:
                self._add_event('快照', '记忆库为空（冷启动），等待第一条经验写入')
            return

        old = self._family_snap
        for name, (verified, candidates) in snap.items():
            prev = old.get(name)
            if prev is None:
                self._add_event('新族', '「%s」进入记忆库（已验证 %d / 候选 %d）'
                                % (name, verified, candidates))
                continue
            if verified != prev[0]:
                self._add_event('学习', '「%s」已验证 %d → %d（%+d）'
                                % (name, prev[0], verified, verified - prev[0]))
            if candidates != prev[1]:
                self._add_event('候选', '「%s」候选 %d → %d（%+d）'
                                % (name, prev[1], candidates, candidates - prev[1]))
        self._family_snap = snap

    def _memory_events(self):
        """实现方若额外给了事件列表就用它，否则返回 None 表示"没有这个口子"。"""
        if self.memory is None:
            return None
        for name in ('recent_events', 'events', 'history'):
            attr = getattr(self.memory, name, None)
            if attr is None:
                continue
            try:
                data = attr() if callable(attr) else attr
            except Exception:  # noqa: BLE001 - 有口子但读不出来，当没有
                continue
            if not isinstance(data, (list, tuple)):
                continue
            events = []
            for item in data:
                text = _pick(item, 'text', 'message', 'msg', 'summary', 'detail', default='')
                kind = _pick(item, 'kind', 'type', 'event', default='学习')
                at = _fmt_time(_pick(item, 'at', 'time', 'ts', 'when', 'updated_at',
                                     default=None))
                if not text:
                    continue
                events.append((at, str(kind), str(text)))
            events.reverse()  # 契约没规定方向，按"新的在前"统一
            return events
        return None

    def _add_event(self, kind, text):
        self._events.insert(0, (time.strftime('%H:%M:%S'), kind, text))
        del self._events[EVENT_LIMIT:]

    def _refresh_events(self):
        tree = self._event_tree
        if tree is None:
            return
        tags = {'学习': 'ok', '新族': 'ok', '候选': 'warn', '评测': 'info',
                '快照': 'info', '失败': 'err'}
        try:
            tree.delete(*tree.get_children())
            tree.tag_configure('ok', foreground=TH.OK)
            tree.tag_configure('warn', foreground=TH.WARN)
            tree.tag_configure('err', foreground=TH.ERR)
            tree.tag_configure('info', foreground=TH.TEXT_DIM)
            if not self._events:
                tree.insert('', 'end', iid='ev:none', tags=('info',), values=(
                    '—', '等待',
                    '这个窗口打开后还没看到记忆变化；主程序 observe 一条，或点「跑一次评测」。'))
                return
            for index, (at, kind, text) in enumerate(self._events):
                tree.insert('', 'end', iid='ev:%d' % index,
                            tags=(tags.get(kind, 'info'),), values=(at, kind, text))
        except tk.TclError:
            pass

    # -- 交互：刷新与排序 -------------------------------------------------------------

    @_guarded
    def _on_refresh(self):
        self.refresh()
        stats = self._read_stats()
        families = self._read_families()
        verified, candidates = self._overview_counts(stats, families)
        self._status('已刷新：%d 个族 / %s 条已验证样本 / %s 条候选。'
                     % (len(families), _fmt_int(verified),
                        _fmt_int(candidates)), 'ok')

    def _toggle_sort(self, key):
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key, self._sort_desc = key, True
        self.refresh()

    @_guarded
    def _on_sort_verified(self):
        self._toggle_sort('n_verified')
        self._status('已按已验证样本数%s排序。'
                     % ('降序' if self._sort_desc else '升序'), 'ok')

    @_guarded
    def _on_sort_candidates(self):
        self._toggle_sort('n_candidates')
        self._status('已按候选样本数%s排序。'
                     % ('降序' if self._sort_desc else '升序'), 'ok')

    # -- 跑评测（后台线程） -----------------------------------------------------------

    @_guarded
    def _on_evaluate(self):
        if self._eval_running:
            self._status('上一次评测还在跑，等它回来。', 'warn')
            return

        if load_dataset is None and evaluate_holdout is None and MetaEvaluator is None:
            self._status('元增量核心包还没就绪（ai_modeler.meta 里没有 '
                         'load_dataset / evaluate_holdout），暂时跑不了评测。', 'warn')
            return

        self._eval_running = True
        self._eval_result = None
        self._eval_error = None
        try:
            self.btn_eval.configure(state='disabled', text='评测中…')
        except (tk.TclError, AttributeError):
            pass

        self._status('正在后台跑 holdout 评测（原型法 vs 1-NN 基线），界面不会卡…', 'info')
        self._eval_thread = threading.Thread(target=self._eval_worker, name='meta-eval',
                                             daemon=True)
        self._eval_thread.start()
        # 用 after 轮询而不是 join：主线程一旦阻塞，整个窗口就冻住了。
        try:
            self.after(POLL_MS, self._poll_eval)
        except tk.TclError:
            pass

    def _eval_worker(self):
        """后台线程体：只算，不碰 Tk 控件（Tk 不是线程安全的）。"""
        try:
            self._eval_result = self._evaluate_blocking()
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常要带回主线程
            self._eval_error = exc

    def _evaluate_blocking(self):
        """真正跑评测的地方。

        优先用契约 §5 的两个自由函数（签名是定死的）；只有它们拿不到时才退回
        §2 导出的 MetaEvaluator 实例，并按 §5 的函数名找方法 —— 那个类的方法签名
        契约没写，所以不敢直接当主路走。
        """
        rows = None
        if load_dataset is not None:
            rows = load_dataset() or []
        if evaluate_holdout is not None:
            if rows is None:
                raise RuntimeError('取不到数据集（ai_modeler.meta 没有 load_dataset），'
                                   '没法跑 holdout 评测')
            return evaluate_holdout(rows)
        if MetaEvaluator is not None:
            evaluator = MetaEvaluator()
            for name in ('evaluate_holdout', 'evaluate'):
                method = getattr(evaluator, name, None)
                if not callable(method):
                    continue
                try:
                    return method(rows if rows is not None else [])
                except TypeError:
                    return method()
            raise RuntimeError('MetaEvaluator 上没有 evaluate_holdout / evaluate 方法')
        raise RuntimeError('元增量核心包未就绪')

    def _poll_eval(self):
        """主线程这边每 POLL_MS 问一次：线程完了就把结果落进状态条。"""
        if self._closed:
            return
        try:
            alive = self._eval_thread is not None and self._eval_thread.is_alive()
        except Exception:  # noqa: BLE001
            alive = False
        if alive:
            try:
                self.after(POLL_MS, self._poll_eval)
            except tk.TclError:
                pass
            return

        self._eval_running = False
        self._eval_thread = None
        error = self._eval_error
        result = self._eval_result
        self._eval_error = None
        self._eval_result = None

        try:
            if self.btn_eval is not None:
                self.btn_eval.configure(state='normal', text='跑一次评测')
        except (tk.TclError, AttributeError):
            pass

        if error is not None:
            message = '评测失败：%s' % error
            self._add_event('失败', message)
            self._refresh_events()
            self._status(message, 'err')
            return

        text = self._format_eval(result)
        # 评测结果同时进状态条和事件列表：状态条会随下一次操作被覆盖，
        # 事件列表是留痕的，回头还能看到"上一次评测赢了多少"。
        self._add_event('评测', text)
        self._refresh_events()
        level = 'warn' if result is None or not isinstance(result, dict) else 'ok'
        self._status(text, level)

    def _format_eval(self, result):
        """把评测结果写成一句话。

        契约 §5 要求「原型法」和「1-NN 基线」同时给出——只报一个数没有说服力，
        所以基线缺失时这里会明确标出来，而不是悄悄只报准确率。
        """
        if not isinstance(result, dict):
            return ('评测完成，但返回的不是契约 §5 的 dict（拿到了 %r）。'
                    % (type(result).__name__,))

        accuracy = _as_float(result.get('accuracy'))
        baseline = _as_float(result.get('baseline_nearest'))
        macro_f1 = _as_float(result.get('macro_f1'))
        n_train = result.get('n_train')
        n_test = result.get('n_test')

        parts = []
        if accuracy is not None:
            parts.append('原型法 acc=%.3f' % accuracy)
        else:
            parts.append('原型法 acc 缺失')
        if macro_f1 is not None:
            parts.append('macro-F1=%.3f' % macro_f1)
        if baseline is not None:
            parts.append('1-NN 基线 acc=%.3f' % baseline)
        else:
            parts.append('1-NN 基线缺失（契约 §5 要求必给 baseline_nearest）')
        if accuracy is not None and baseline is not None:
            parts.append('比基线 %+.1f 个百分点' % ((accuracy - baseline) * 100.0))
        if n_train is not None or n_test is not None:
            parts.append('训练 %s / 测试 %s 条' % (_fmt_int(n_train), _fmt_int(n_test)))
        return '评测完成：' + ' · '.join(parts)

    # -- 关闭 ---------------------------------------------------------------------

    def destroy(self):
        """标记关闭：后台线程和 after 轮询回来时看到旗标就静静退出。"""
        self._closed = True
        try:
            super().destroy()
        except tk.TclError:
            pass


__all__ = ['MetaPanelWindow']
