# -*- coding: utf-8 -*-
"""夹具单元协同设计 —— 五步向导窗口。

为什么要单独一个 Toplevel：主窗口(app.py)是"自由建模"的节奏——对话、三视图、
特征树并排，随时可改；五步向导是"顺序推进"的节奏——前置不满足就不许进下一步。
两种节奏塞进同一个窗口，用户分不清什么时候"该等校验"，所以这里做成独立窗口，
主程序只负责打开它。

和领域层的关系（见 docs/planning/夹具工作流_接口契约.md）：
    本文件只消费契约 §1/§3/§4/§6 公布的名字与签名，不发明 API。
    所有会走到领域层的动作都套了 try/except 并落到状态条——Tk 回调里抛异常会
    直接打断事件循环，界面上表现为"点了没反应"，比直接报错更难查。

契约红线（本文件刻意遵守，改动前先看契约）：
    · P01 固定点偏移恒为 0，输入框直接 disabled；
    · 参数一变，下游检查由 engine.set_param 作废，状态条如实报"已作废 N 项"；
    · 检查结论四分显示，绝不合并成一个绿灯；
    · simulated / cad_connected 等事实字段只读，UI 不提供任何改写入口。
"""
from __future__ import annotations

import functools
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from ai_modeler.ui import theme as TH

# ---------------------------------------------------------------------------
# 契约接线（§1 包结构 / §3 状态机 / §4 规则 / §6 校核输出）
# ---------------------------------------------------------------------------
from ai_modeler.workflow import FixtureProject, STAGES, WorkflowEngine
from ai_modeler.workflow.stages import WorkflowError, stage_meta
from ai_modeler.workflow.rules import evaluate
from ai_modeler.workflow.report import export_unit_record

# 「某阶段是否已完成」必须扫 history 里的 commit/invalidate 事件才准（作废要能
# 冲掉完成标记）。契约 §3 的签名表里没有它，但实现方在 stages.py 里提供了
# stage_committed 并注明「供报告与 UI 复用」——两边各推一遍必然对不上，所以取它。
# 万一后续改名，退回本文件自带的推演（见 _committed_stages）。
try:
    from ai_modeler.workflow.stages import stage_committed
except ImportError:  # pragma: no cover
    stage_committed = None  # type: ignore

# §5 说 build_demo_project 放 models.py 或 __init__.py 二选一，所以两条路都试；
# 它只用于"没传 project 时给个默认案例"，拿不到就退回空项目，不阻断窗口打开。
try:
    from ai_modeler.workflow import build_demo_project
except ImportError:  # pragma: no cover - 取决于实现者把它放在哪
    try:
        from ai_modeler.workflow.models import build_demo_project  # type: ignore
    except ImportError:
        build_demo_project = None  # type: ignore

# 摘要导出是加分项(契约 §6 有 export_summary)，实现方若暂未提供就隐藏按钮。
try:
    from ai_modeler.workflow.report import export_summary
except ImportError:  # pragma: no cover
    try:
        from ai_modeler.workflow import export_summary  # type: ignore
    except ImportError:
        export_summary = None  # type: ignore


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _get(obj, name, default=None):
    """读字段：领域模型可能是 dataclass，也可能是 from_dict 出来的 dict。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set(obj, name, value):
    """写字段：理由同 _get（个别控件没有引擎 setter，只能写数据模型公布的字段）。"""
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _as_float(raw):
    """把输入框内容转 float；失败返回 None 由调用方提示，不在这里抛。"""
    if isinstance(raw, bool):
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _fmt(value):
    """数值去掉多余的 .0，让表格读起来像工程图上的标注。"""
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, float):
        text = '%.2f' % value
        text = text.rstrip('0').rstrip('.')
        return text or '0'
    return '' if value is None else str(value)


def _check_label(status):
    return {
        'pass': '通过',
        'fail': '未通过',
        'not_run': '未运行',
        'not_applicable': '不适用',
    }.get(status, status or '未知')


def _check_fg(status):
    """四分色。

    刻意在每次调用时现取 TH.OK 等——theme.set_mode() 会重灌模块级颜色，
    若在 import 时把它们焊进常量，切深色模式后这里就是过期颜色。
    """
    if status == 'pass':
        return TH.OK
    if status == 'fail':
        return TH.ERR
    if status == 'not_run':
        return TH.WARN
    return TH.TEXT_MUTE


KIND_LABEL = {
    'fix': '固定',
    'revolute': '转动副',
    'prismatic': '移动副',
    'contact': '接触',
}

ROLE_LABEL = {'primary': '主定位', 'assist': '辅助支撑'}


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

class FixtureWorkflowWindow(tk.Toplevel):
    """五步「夹具单元协同设计」向导窗口。

    参数：
        master         父窗口（Tk 或 Toplevel）
        project        FixtureProject；None 时用契约 §5 的默认案例 UN200
        on_push_model  可选回调，第 3/4 步把建模结果交回主程序；
                       None 表示"只演示"，此时推送按钮禁用（不假装能推）
    """

    def __init__(self, master, project=None, on_push_model=None):
        super().__init__(master)

        self.project = project if project is not None else self._default_project()
        self.engine = WorkflowEngine(self.project)
        self.on_push_model = on_push_model

        stage_list = list(STAGES)
        self._stage_ids = [int(item[0]) for item in stage_list]
        self._stage_names = {int(item[0]): str(item[1]) for item in stage_list}
        self._stage_subtitles = {
            int(item[0]): (str(item[2]) if len(item) > 2 else '') for item in stage_list
        }
        self.current_stage = self._clamp_stage(_get(self.project, 'stage', 1) or 1)

        # 需要跨刷新保留的控件引用
        self._step_rows = {}
        self._pages = {}
        self._page_hints = {}
        self._push_buttons = []
        self._input_tree = None
        self._part_tree = None
        self._joint_tree = None
        self._check_tree = None
        self._points_grid = None
        self._params_host = None
        self._unit_info_labels = {}
        self._count_labels = {}
        self._offset_entries = {}
        self._param_entries = []
        self._ui_font = None
        self._button_summary = None
        # 重建控件期间会触发 <FocusOut>，用这个旗标挡掉递归提交。
        self._rebuilding = False
        self._auto_eval_done = False

        try:
            self.transient(master)
        except tk.TclError:
            pass  # 无头/独立调试时没有真正的主窗口，忽略即可

        self.title('夹具单元协同设计 · 五步向导')
        self.geometry('1360x860')
        self.minsize(1000, 640)

        self._ensure_theme()
        self._build_header()
        # 先建底栏再建主体：pack 按顺序分配空间，若主体(expand)先占满，
        # 窗口一旦压到最小高度，最后 pack 的底栏就会被挤出可视区。
        self._build_bottom()
        self._build_body()
        self.refresh()

        ok, missing = self._can_enter(self.current_stage)
        if ok:
            self._status('就绪：当前第 %d 阶段「%s」，前置校验已通过。'
                         % (self.current_stage, self._stage_name(self.current_stage)), 'ok')
        else:
            self._status('当前第 %d 阶段「%s」还不能推进，缺 %d 项：%s'
                         % (self.current_stage, self._stage_name(self.current_stage),
                            len(missing), '；'.join(missing)), 'warn')

    # -- 初始化 -----------------------------------------------------------------

    @staticmethod
    def _default_project():
        """不要默认案例时也要能开窗：宁可空项目，也不让窗口建不出来。"""
        if build_demo_project is not None:
            try:
                return build_demo_project()
            except Exception:  # noqa: BLE001
                pass
        return FixtureProject()

    def _ensure_theme(self):
        """主窗口通常已经 apply 过样式；单独调起本窗口(或无头测试)时补一次。

        apply() 是幂等的，只补控件样式，不改配色。
        """
        try:
            if not ttk.Style(self).lookup('Card.TFrame', 'background'):
                TH.apply(self)
        except Exception:  # noqa: BLE001 - 样式补不上也不该阻断窗口
            pass

    # -- 阶段索引 -----------------------------------------------------------------

    def _clamp_stage(self, stage):
        try:
            value = int(stage)
        except (TypeError, ValueError):
            value = self._stage_ids[0]
        return max(self._stage_ids[0], min(self._stage_ids[-1], value))

    def _last_stage(self):
        return self._stage_ids[-1]

    def _stage_name(self, stage):
        return self._stage_names.get(stage, '第 %d 阶段' % stage)

    def _stage_subtitle(self, stage):
        return self._stage_subtitles.get(stage, '')

    def _stage_meta(self, stage):
        """契约 §3 的 stage_meta 只用来补"阶段产出"提示，拿不到就算了。"""
        try:
            meta = stage_meta(stage)
            return meta if isinstance(meta, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    # -- 校验与历史 ---------------------------------------------------------------

    def _can_enter(self, stage):
        """进入某阶段前必须问引擎；顺手把异常翻译成缺项，别让按钮点不动还没说法。"""
        try:
            ok, missing = self.engine.can_enter(stage)
            return bool(ok), [str(item) for item in (missing or [])]
        except Exception as exc:  # noqa: BLE001
            return False, ['前置校验失败：%s' % exc]

    def _committed_stages(self):
        """哪些阶段已完成。

        不能只看"有没有 commit 事件"：契约原则 3 要求参数一变下游全部失效，
        作废要能把完成标记一起冲掉。实现方在 stages.py 的 stage_committed 里
        已经按时间顺序推演过一遍（commit 记完成，invalidate 冲掉），这里直接用它；
        拿不到时退回本文件的史扫描（只认 commit，够用但不处理作废）。
        """
        done = set()
        for stage in self._stage_ids:
            try:
                if stage_committed is not None:
                    if stage_committed(self.project, stage):
                        done.add(stage)
                else:
                    for event in (_get(self.project, 'history', []) or []):
                        if (_get(event, 'action') == 'commit'
                                and _get(event, 'stage') == stage):
                            done.add(stage)
                            break
            except Exception:  # noqa: BLE001 - 推演失败就当没完成
                continue
        return done

    def _sync_stage(self):
        """让界面跟着引擎的当前阶段走。

        引擎会自己改 project.stage：提交后 +1、失效传播后回退（基准一变退回第 1 阶段）。
        界面若不同步，就会出现"显示第 2 阶段、引擎认为在第 1 阶段"，点提交直接被
        顺序规则拒掉——用户会觉得按钮坏了。
        """
        try:
            value = self._clamp_stage(_get(self.project, 'stage', self.current_stage)
                                      or self.current_stage)
        except Exception:  # noqa: BLE001
            return
        self.current_stage = value

    # -- 顶部 ---------------------------------------------------------------------

    def _build_header(self):
        # 顶栏高度按实际字高算：写死 58px 在 150% 缩放下会把副标题挤到边缘。
        try:
            title_h = tkfont.Font(font=TH.F['brand'], root=self).metrics('linespace')
            sub_h = tkfont.Font(font=TH.F['small'], root=self).metrics('linespace')
            height = title_h + sub_h + 16
        except Exception:  # noqa: BLE001
            height = 62

        head = tk.Frame(self, bg=TH.ACCENT, height=height)
        head.pack(fill='x')
        head.pack_propagate(False)

        left = tk.Frame(head, bg=TH.ACCENT)
        left.pack(side='left', padx=16, pady=4)
        tk.Label(left, text='夹具单元协同设计', bg=TH.ACCENT, fg=TH.ON_ACCENT,
                 font=TH.F['brand']).pack(anchor='w')
        tk.Label(left, text='五步向导 · 前置不满足不进下一步', bg=TH.ACCENT,
                 fg=TH.HEAD_SUB, font=TH.F['small']).pack(anchor='w')

        right = tk.Frame(head, bg=TH.ACCENT)
        right.pack(side='right', padx=16, pady=4)
        self._build_fact_chips(right)
        self.lbl_head_unit = tk.Label(right, text='', bg=TH.ACCENT, fg=TH.HEAD_SUB,
                                      font=TH.F['small'])
        self.lbl_head_unit.pack(anchor='e')

    def _build_fact_chips(self, parent):
        """事实字段只读展示。

        契约原则 5/6：simulated / cad_connected 是事实，不能被勾选改写，
        所以这里只有 Label，没有 Checkbutton。
        """
        row = tk.Frame(parent, bg=TH.ACCENT)
        row.pack(anchor='e')
        simulated = bool(_get(self.project, 'simulated', True))
        cad = bool(_get(self.project, 'cad_connected', False))
        specs = [
            ('模拟数据' if simulated else '真实模型',
             TH.ACCENT_DK, TH.WARN if simulated else TH.OK),
            ('CATIA 已连接' if cad else 'CATIA 未连接',
             TH.ACCENT_DK, TH.HEAD_SUB),
        ]
        for text, bg, fg in specs:
            tk.Label(row, text=text, bg=bg, fg=fg, font=TH.F['tiny'],
                     padx=7, pady=1).pack(side='left', padx=(6, 0))

    # -- 主体 ---------------------------------------------------------------------

    def _build_body(self):
        panes = ttk.PanedWindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10, pady=(10, 6))
        self._panes = panes

        side_outer, side = TH.card(panes, width=272)
        panes.add(side_outer, weight=0)
        self._build_sidebar(side)

        content_outer, content = TH.card(panes)
        panes.add(content_outer, weight=1)
        self._build_content(content)

    def _build_sidebar(self, inner):
        TH.section(inner, '设计流程', '共 %d 步' % len(self._stage_ids))

        for stage in self._stage_ids:
            row = tk.Frame(inner, bg=TH.PANEL, cursor='hand2')
            row.pack(fill='x', padx=8, pady=1)

            bar = tk.Frame(row, bg=TH.PANEL, width=3, height=44)
            bar.pack(side='left', fill='y')
            bar.pack_propagate(False)

            num = tk.Label(row, text=str(stage), bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                           font=TH.F['body_b'], width=3, pady=3)
            num.pack(side='left', padx=(8, 8), pady=8)

            text = tk.Frame(row, bg=TH.PANEL)
            text.pack(side='left', fill='x', expand=True)
            name = tk.Label(text, text=self._stage_name(stage), bg=TH.PANEL,
                            fg=TH.TEXT, font=TH.F['body_b'], anchor='w')
            name.pack(fill='x')
            sub = tk.Label(text, text=self._stage_subtitle(stage), bg=TH.PANEL,
                           fg=TH.TEXT_MUTE, font=TH.F['tiny'], anchor='w')
            sub.pack(fill='x')

            mark = tk.Label(row, text='·', bg=TH.PANEL, fg=TH.DISABLED,
                            font=TH.F['body_b'], width=2)
            mark.pack(side='right', padx=(0, 6))

            for widget in (row, bar, num, text, name, sub, mark):
                widget.bind('<Button-1>', lambda _e, s=stage: self._on_step_click(s))

            self._step_rows[stage] = {
                'row': row, 'bar': bar, 'num': num, 'text': text,
                'name': name, 'sub': sub, 'mark': mark,
            }

        TH.hairline(inner)
        legend = tk.Frame(inner, bg=TH.PANEL)
        legend.pack(fill='x', padx=14, pady=(0, 12))
        for text, color in (('● 当前阶段', TH.ACCENT), ('✓ 已完成', TH.OK),
                            ('○ 可进入', TH.TEXT_MUTE), ('· 未解锁', TH.DISABLED)):
            tk.Label(legend, text=text, bg=TH.PANEL, fg=color,
                     font=TH.F['tiny'], anchor='w').pack(fill='x')

    def _build_content(self, content):
        container = tk.Frame(content, bg=TH.PANEL)
        container.pack(fill='both', expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self._page_container = container

        for stage in self._stage_ids:
            page = self._build_page(container, stage)
            page.grid(row=0, column=0, sticky='nsew')
            page.grid_remove()
            self._pages[stage] = page

    # -- 单个阶段页 ---------------------------------------------------------------

    def _build_page(self, parent, stage):
        page = tk.Frame(parent, bg=TH.PANEL)

        head = TH.section(page, '%d · %s' % (stage, self._stage_name(stage)),
                          self._stage_subtitle(stage))
        # 第 3/4 步的"交主程序"按钮挂在页头右侧：都属于"这一页的产出要交出去"。
        if stage in (3, 4):
            btn = ttk.Button(head, text='▶ 交主程序建模', style='Ghost.TButton',
                             command=self._on_push_model)
            btn.pack(side='right', padx=(8, 0))
            self._push_buttons.append(btn)

        hint = tk.Label(page, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                        font=TH.F['small'], anchor='w', justify='left',
                        wraplength=760)
        hint.pack(fill='x', padx=14, pady=(0, 6))
        # 折行宽度跟内容区走：写死的话窗口一窄，提示就被右边界切掉。
        _bind_wrap(page, hint, pad=60)
        self._page_hints[stage] = hint

        wrap = tk.Frame(page, bg=TH.PANEL)
        wrap.pack(fill='both', expand=True)
        inner = self._scrollable(wrap)

        builder = {
            1: self._build_stage_inputs,
            2: self._build_stage_points,
            3: self._build_stage_params,
            4: self._build_stage_joints,
            5: self._build_stage_checks,
        }.get(stage)
        if builder is not None:
            builder(inner)
        return page

    def _scrollable(self, parent):
        """可滚动的内层容器：五步里任何一页在 1280×720 都可能超出一屏。"""
        canvas = tk.Canvas(parent, bg=TH.PANEL, bd=0, highlightthickness=0)
        bar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')

        inner = tk.Frame(canvas, bg=TH.PANEL)
        window = canvas.create_window((0, 0), window=inner, anchor='nw')

        def _wheel(event):
            try:
                canvas.yview_scroll(int(-event.delta / 120) or -1, 'units')
            except tk.TclError:
                pass  # 画布正在销毁

        def _sync(event=None):
            try:
                box = canvas.bbox('all')
                if box:
                    canvas.configure(scrollregion=box)
            except tk.TclError:
                pass

        def _fit(event):
            try:
                canvas.itemconfigure(window, width=event.width)
            except tk.TclError:
                pass

        inner.bind('<Configure>', _sync)
        canvas.bind('<Configure>', _fit)
        canvas.bind('<MouseWheel>', _wheel)
        inner.bind('<MouseWheel>', _wheel)
        return inner

    def _make_table(self, parent, columns, height=7, pad=(12, 0, 12, 8)):
        """统一建一张带横竖滚动条的表格，返回 Treeview。

        columns 里每项是 (key, 表头, 最长样例文本, 对齐)。

        为什么列宽不写死像素：本机是 150% 缩放，"W-INPUT-01" 实际占 110px，
        按 96dpi 估的 88px 会把编号切成 "W-INPUT-"。所以按当前 DPI 实测字宽来定，
        表头比样例长时取表头。窗口窄到放不下就出横向滚动条——宁可让用户滚，
        也不要在窗口边缘把字硬切掉。
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

        box.pack(fill='x', padx=(pad_l, pad_r), pady=(pad_t, pad_b))
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

    # -- 第 1 阶段：工程输入 -------------------------------------------------------

    def _build_stage_inputs(self, parent):
        TH.section(parent, '工程输入资料', '双击一行切换「已确认可用」')

        tree = self._make_table(parent, (
            ('label', '资料', '定位夹紧方案（PRP）', 'w'),
            ('version', '版本', '2026-A', 'center'),
            ('state', '状态', '已确认', 'center'),
            ('path', '文件路径', 'assets/demo/un200_workpiece.step', 'w')),
            height=7, pad=(12, 0, 12, 10))
        tree.bind('<Double-1>', self._on_toggle_input)
        self._input_tree = tree

        TH.section(parent, '单元信息')
        grid = tk.Frame(parent, bg=TH.PANEL)
        grid.pack(fill='x', padx=14, pady=(0, 12))
        for row, key in enumerate(('单元编号', '单元名称', '典型案例', '当前阶段', '修订')):
            tk.Label(grid, text=key, bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                     anchor='w').grid(row=row, column=0, sticky='nw', pady=2)
            value = tk.Label(grid, text='', bg=TH.PANEL, fg=TH.TEXT, font=TH.F['body'],
                             anchor='w', justify='left', wraplength=620)
            value.grid(row=row, column=1, sticky='w', padx=(18, 0), pady=2)
            _bind_wrap(grid, value, pad=110)
            self._unit_info_labels[key] = value

    # -- 第 2 阶段：单元规划 -------------------------------------------------------

    def _build_stage_points(self, parent):
        TH.section(parent, '定位夹紧点', 'P01 为固定点，偏移锁定为 0（契约红线）')
        self._points_grid = tk.Frame(parent, bg=TH.PANEL)
        self._points_grid.pack(fill='x', padx=12, pady=(0, 8))

        tk.Label(parent, text='主定位点只能看不能改；辅助点偏移改完，下游检查会被作废。',
                 bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                 anchor='w').pack(fill='x', padx=14, pady=(0, 8))

        TH.hairline(parent)
        TH.section(parent, '任务分解', '部件按用途分组')
        self._part_tree = self._make_table(parent, (
            ('name', '部件', '接触块', 'w'),
            ('category', '类别', '标准件', 'center'),
            ('spec', '型号', 'GB/T 2203 φ12×40', 'w'),
            ('params', '尺寸参数', 'length=600，width=380，thickness=20 …', 'w')),
            height=7, pad=(12, 0, 12, 12))

    # -- 第 3 阶段：参数建模 -------------------------------------------------------

    def _build_stage_params(self, parent):
        TH.section(parent, '部件参数', '数值项可直接改；改完下游检查自动作废')
        tk.Label(parent, text='回车或移开焦点即提交。被引擎判为非法时自动还原并说明原因。',
                 bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                 anchor='w').pack(fill='x', padx=14, pady=(0, 8))

        host = tk.Frame(parent, bg=TH.PANEL)
        host.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self._params_host = host

    # -- 第 4 阶段：关联装配 -------------------------------------------------------

    def _build_stage_joints(self, parent):
        TH.section(parent, '装配关系', '转动副压臂 + 移动副气缸')
        self._joint_tree = self._make_table(parent, (
            ('a', '部件 A', 'contact_block', 'w'),
            ('b', '部件 B', 'contact_block', 'w'),
            ('kind', '约束类型', '转动副', 'center'),
            ('axis', '轴向', 'Y', 'center'),
            ('travel', '行程 / 转角', '65 °', 'center')), height=8, pad=(12, 0, 12, 10))

        note = tk.Label(parent,
                        text='装配关系只描述机构怎么动；没有真实运动仿真的项，'
                             '校核页会显示「未运行」，不会被算成通过。',
                        bg=TH.PANEL, fg=TH.TEXT_MUTE, font=TH.F['small'],
                        anchor='w', justify='left', wraplength=780)
        note.pack(fill='x', padx=14, pady=(0, 12))
        _bind_wrap(parent, note, pad=32)

    # -- 第 5 阶段：校核输出 -------------------------------------------------------

    def _build_stage_checks(self, parent):
        head = TH.section(parent, '检查结果', '四分结论：通过 / 未通过 / 未运行 / 不适用')
        ttk.Button(head, text='↻ 重新校核', style='Ghost.TButton',
                   command=self._on_run_checks).pack(side='right', padx=(8, 0))
        # 摘要导出是加分项：领域层没提供就禁用，不假装有。
        self._btn_summary = ttk.Button(head, text='导出摘要', style='Ghost.TButton',
                                       command=self._on_export_summary)
        self._btn_summary.pack(side='right', padx=(8, 0))
        if export_summary is None:
            self._btn_summary.configure(state='disabled')

        chips = tk.Frame(parent, bg=TH.PANEL)
        chips.pack(fill='x', padx=14, pady=(0, 8))
        for key in ('pass', 'fail', 'not_run', 'not_applicable'):
            label = tk.Label(chips, text='%s 0' % _check_label(key), bg=TH.PANEL_ALT,
                             fg=_check_fg(key), font=TH.F['tiny'], padx=8, pady=2)
            label.pack(side='left', padx=(0, 6))
            self._count_labels[key] = label

        self._check_tree = self._make_table(parent, (
            ('check', '编号', 'W-INPUT-01', 'w'),
            ('title', '检查项', '完整运动轨迹检查', 'w'),
            ('status', '结论', '未通过', 'center'),
            ('value', '实测 / 限值', '3 / 5 mm', 'center'),
            ('rule', '来源规则', 'R-LOCK-01', 'center'),
            ('stage', '阶段', '第 5 阶段', 'center')), height=9, pad=(12, 0, 12, 8))

        TH.hairline(parent)
        TH.section(parent, '这套流程凭什么能复用', '三件事要分开说')

        blocks = tk.Frame(parent, bg=TH.PANEL)
        blocks.pack(fill='x', padx=12, pady=(0, 14))
        blocks.grid_columnconfigure(0, weight=1, uniform='blk')
        blocks.grid_columnconfigure(1, weight=1, uniform='blk')
        blocks.grid_columnconfigure(2, weight=1, uniform='blk')

        items = (
            ('流程可复用',
             '五阶段的顺序与进入校验都由引擎给出，换一个单元只需替换输入资料和点位。'),
            ('设计可追溯',
             '进入、提交、改参、作废都写进历史并递增修订号，能回放到任一版本。'),
            ('结果可复核',
             '四种结论分别落表并随记录导出，未运行和不适用不会被合并成一盏绿灯。'),
        )
        for column, (title, desc) in enumerate(items):
            outer, inner = TH.card(blocks, bg=TH.PANEL_ALT)
            outer.grid(row=0, column=column, sticky='nsew',
                       padx=(0, 8) if column < 2 else 0, pady=0)
            tk.Label(inner, text=title, bg=TH.PANEL_ALT, fg=TH.TEXT,
                     font=TH.F['body_b'], anchor='w').pack(fill='x', padx=10, pady=(8, 2))
            label = tk.Label(inner, text=desc, bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                             font=TH.F['small'], anchor='w', justify='left',
                             wraplength=230)
            label.pack(fill='x', padx=10, pady=(0, 9))
            _bind_wrap(inner, label, pad=26)

    # -- 底部 ---------------------------------------------------------------------

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

        row = tk.Frame(bottom, bg=TH.PANEL)
        row.pack(fill='x', pady=(8, 0))

        self.lbl_progress = tk.Label(row, text='', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                                     font=TH.F['small'])
        self.lbl_progress.pack(side='left', padx=(2, 0))

        # 视觉顺序(左→右): 上一步 / 完成本阶段 / 导出校核记录
        self.btn_export = ttk.Button(row, text='导出校核记录', style='Ghost.TButton',
                                     command=self._on_export)
        self.btn_export.pack(side='right')
        self.btn_next = ttk.Button(row, text='完成本阶段 ›', style='Accent.TButton',
                                   command=self._on_commit)
        self.btn_next.pack(side='right', padx=(0, 6))
        self.btn_prev = ttk.Button(row, text='‹ 上一步', style='Ghost.TButton',
                                   command=self._on_prev)
        self.btn_prev.pack(side='right', padx=(0, 6))

    def _on_strip_configure(self, event):
        try:
            self.lbl_status.configure(wraplength=max(320, event.width - 60))
        except Exception:  # noqa: BLE001
            pass

    # -- 状态条 -------------------------------------------------------------------

    def _status(self, text, level='info'):
        color = {'ok': TH.OK, 'warn': TH.WARN, 'err': TH.ERR}.get(level, TH.TEXT_DIM)
        try:
            self.lbl_status.configure(text=str(text), fg=color)
            self._status_dot.configure(fg=color)
        except tk.TclError:
            pass  # 窗口正在销毁时刷新到这里，忽略

    # -- 刷新 ---------------------------------------------------------------------

    def refresh(self):
        """整窗重算。任何一环失败都只落到状态条，不让界面停在半截。"""
        try:
            self._sync_stage()
            self._refresh_header()
            self._refresh_steps()
            self._refresh_pages()
            self._refresh_buttons()
        except Exception as exc:  # noqa: BLE001
            self._status('界面刷新失败：%s' % exc, 'err')

    def _refresh_header(self):
        unit_id = _get(self.project, 'unit_id', '') or ''
        unit_name = _get(self.project, 'unit_name', '') or ''
        revision = _get(self.project, 'revision', 1)
        text = '%s · %s · 修订 r%s' % (unit_id, unit_name, revision)
        case_note = _get(self.project, 'case_note', '') or ''
        if case_note:
            text += ' · ' + case_note
        try:
            self.lbl_head_unit.configure(text=text)
            self.title('夹具单元协同设计 · 五步向导 —— %s' % (unit_id or 'UN200'))
        except tk.TclError:
            pass

    def _refresh_steps(self):
        committed = self._committed_stages()
        for stage, widgets in self._step_rows.items():
            if stage == self.current_stage:
                bg, name_fg, sub_fg = TH.ACCENT_SOFT, TH.ACCENT, TH.ACCENT_DK
                mark, mark_fg, bar_bg = '●', TH.ACCENT, TH.ACCENT
                num_bg, num_fg = TH.ACCENT, TH.ON_ACCENT
            elif stage in committed:
                bg, name_fg, sub_fg = TH.PANEL, TH.TEXT, TH.TEXT_DIM
                mark, mark_fg, bar_bg = '✓', TH.OK, TH.OK
                num_bg, num_fg = TH.PANEL_ALT, TH.TEXT_DIM
            else:
                ok, _missing = self._can_enter(stage)
                if ok:
                    bg, name_fg, sub_fg = TH.PANEL, TH.TEXT_DIM, TH.TEXT_MUTE
                    mark, mark_fg, bar_bg = '○', TH.TEXT_MUTE, TH.PANEL
                    num_bg, num_fg = TH.PANEL_ALT, TH.TEXT_DIM
                else:
                    bg, name_fg, sub_fg = TH.PANEL, TH.DISABLED, TH.DISABLED
                    mark, mark_fg, bar_bg = '·', TH.DISABLED, TH.PANEL
                    num_bg, num_fg = TH.BTN_OFF, TH.BTN_OFF_FG

            try:
                widgets['row'].configure(bg=bg)
                widgets['text'].configure(bg=bg)
                widgets['name'].configure(bg=bg, fg=name_fg)
                widgets['sub'].configure(bg=bg, fg=sub_fg)
                widgets['mark'].configure(bg=bg, fg=mark_fg, text=mark)
                widgets['bar'].configure(bg=bar_bg)
                widgets['num'].configure(bg=num_bg, fg=num_fg)
            except tk.TclError:
                pass

    def _refresh_pages(self):
        for stage, page in self._pages.items():
            try:
                if stage == self.current_stage:
                    page.grid()
                else:
                    page.grid_remove()
            except tk.TclError:
                pass

        hint = self._page_hints.get(self.current_stage)
        if hint is not None:
            ok, missing = self._can_enter(self.current_stage)
            try:
                hint.configure(text=self._stage_hint_text(self.current_stage, ok, missing),
                               fg=TH.OK if ok else TH.ERR)
            except tk.TclError:
                pass

        handler = {
            1: self._refresh_inputs,
            2: self._refresh_points,
            3: self._refresh_params,
            4: self._refresh_joints,
            5: self._refresh_checks,
        }.get(self.current_stage)
        if handler is not None:
            handler()

    def _stage_hint_text(self, stage, ok, missing):
        if ok:
            pieces = ['前置校验通过，可以完成本阶段']
            outputs = self._stage_meta(stage).get('outputs') or []
            if outputs:
                pieces.append('阶段产出：' + '、'.join(str(item) for item in outputs))
            return ' · '.join(pieces)
        return '前置校验未通过（缺 %d 项）：%s' % (len(missing), '；'.join(missing))

    def _refresh_buttons(self):
        stage = self.current_stage
        committed = stage in self._committed_stages()
        ok, _missing = self._can_enter(stage)

        try:
            self.btn_prev.configure(
                state='normal' if stage > self._stage_ids[0] else 'disabled')
            # 已提交过的阶段允许重提（改了参数之后确实需要再提交一次）。
            self.btn_next.configure(
                text='重新提交本阶段 ›' if committed else '完成本阶段 ›',
                state='normal' if ok else 'disabled')
            self.btn_export.configure(state='normal')
        except tk.TclError:
            pass

        for btn in self._push_buttons:
            try:
                btn.configure(state='disabled' if self.on_push_model is None else 'normal')
            except tk.TclError:
                pass

        try:
            self.lbl_progress.configure(
                text='第 %d / %d 阶段「%s」 · 已完成 %d 步 · 修订 r%s'
                     % (stage, self._last_stage(), self._stage_name(stage),
                        len(self._committed_stages()), _get(self.project, 'revision', 1)))
        except tk.TclError:
            pass

    def _clear(self, parent):
        for child in list(parent.winfo_children()):
            child.destroy()

    # -- 各页数据刷新 ---------------------------------------------------------------

    def _refresh_inputs(self):
        tree = self._input_tree
        if tree is not None:
            try:
                tree.delete(*tree.get_children())
                # 颜色每次刷新重设：theme.set_mode() 会重灌 TH 的颜色。
                # 只有异常行才染色——整行绿会让人以为「路径」是可点的链接。
                tree.tag_configure('ok', foreground=TH.TEXT)
                tree.tag_configure('miss', foreground=TH.ERR)
                tree.tag_configure('warn', foreground=TH.WARN)
                for index, item in enumerate(_get(self.project, 'inputs', []) or []):
                    loaded = bool(_get(item, 'loaded', False))
                    required = bool(_get(item, 'required', True))
                    path = str(_get(item, 'path', '') or '')
                    if loaded:
                        state, tag = '已确认', 'ok'
                    elif required:
                        state, tag = '缺失', 'miss'
                    else:
                        state, tag = '待补充', 'warn'
                    tree.insert(
                        '', 'end', iid='in:%d' % index, tags=(tag,),
                        values=(str(_get(item, 'label', '') or ''),
                                str(_get(item, 'version', '') or '—'),
                                state,
                                path or '—'))
            except tk.TclError:
                pass

        info = {
            '单元编号': str(_get(self.project, 'unit_id', '') or '—'),
            '单元名称': str(_get(self.project, 'unit_name', '') or '—'),
            '典型案例': str(_get(self.project, 'case_note', '') or '（未填写）'),
            '当前阶段': '第 %d 阶段 · %s' % (self.current_stage,
                                           self._stage_name(self.current_stage)),
            '修订': 'r%s' % (_get(self.project, 'revision', 1),),
        }
        for key, label in self._unit_info_labels.items():
            try:
                label.configure(text=info.get(key, ''))
            except tk.TclError:
                pass

    def _refresh_points(self):
        grid = self._points_grid
        if grid is None:
            return
        self._rebuilding = True
        try:
            self._clear(grid)
            self._offset_entries = {}
            headers = ('点位', '角色', 'X', 'Y', 'Z', '偏移 (mm)', '可调范围')
            for column, title in enumerate(headers):
                tk.Label(grid, text=title, bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                         font=TH.F['small'], padx=8, pady=4,
                         anchor='center' if column else 'w').grid(
                    row=0, column=column, sticky='ew', padx=(0, 1), pady=(0, 1))

            points = list(_get(self.project, 'points', []) or [])
            if not points:
                tk.Label(grid, text='暂无点位数据', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                         font=TH.F['small'], anchor='w').grid(
                    row=1, column=0, columnspan=len(headers), sticky='w',
                    padx=8, pady=8)
            for row, point in enumerate(points, start=1):
                self._build_point_row(grid, point, row)
        finally:
            self._rebuilding = False
        self._refresh_part_tree()

    def _refresh_part_tree(self):
        """任务分解表：让用户在规划阶段就能看到"这套单元由哪些件组成"。"""
        tree = self._part_tree
        if tree is None:
            return
        try:
            tree.delete(*tree.get_children())
            for part in (_get(self.project, 'parts', []) or []):
                params = _get(part, 'params', {}) or {}
                items = list(params.items())
                summary = '，'.join('%s=%s' % (key, _fmt(value))
                                    for key, value in items[:3])
                if len(items) > 3:
                    summary += ' …'
                tree.insert('', 'end', values=(
                    str(_get(part, 'name', '') or _get(part, 'key', '')),
                    str(_get(part, 'category', '') or '—'),
                    str(_get(part, 'spec', '') or '—'),
                    summary or '—'))
        except tk.TclError:
            pass

    def _build_point_row(self, grid, point, row):
        pid = str(_get(point, 'pid', '') or '')
        offset = float(_get(point, 'offset', 0.0) or 0.0)
        locked = (bool(_get(point, 'offset_locked', False))
                  or pid.upper() == 'P01'
                  or _offset_range(point) == (0.0, 0.0))
        low, high = _offset_range(point)

        cells = (
            (pid, 'w'),
            (ROLE_LABEL.get(_get(point, 'role', ''), str(_get(point, 'role', '') or '—')), 'w'),
            (_fmt(_get(point, 'x', 0.0)), 'center'),
            (_fmt(_get(point, 'y', 0.0)), 'center'),
            (_fmt(_get(point, 'z', 0.0)), 'center'),
        )
        for column, (text, anchor) in enumerate(cells):
            tk.Label(grid, text=text, bg=TH.PANEL, fg=TH.TEXT,
                     font=TH.F['body'], padx=8, pady=4, anchor=anchor).grid(
                row=row, column=column, sticky='ew', padx=(0, 1), pady=(0, 1))

        entry = ttk.Entry(grid, width=10, font=TH.F['num'], justify='center')
        entry.insert(0, _fmt(offset))
        entry.grid(row=row, column=5, sticky='ew', padx=(0, 1), pady=(0, 1))
        # 锁定的点也放进引用表：刷新时能把状态复查一遍，也方便自检断言它是 disabled。
        self._offset_entries[pid] = entry
        if locked:
            # 契约红线：P01 固定点偏移恒为 0，输入框直接禁用。
            entry.configure(state='disabled')
        else:
            entry.bind('<Return>', lambda _e, p=pid: self._commit_offset(p))
            entry.bind('<FocusOut>', lambda _e, p=pid: self._commit_offset(p))

        rng_text = '锁定' if locked else '%s ~ %s' % (_fmt(low), _fmt(high))
        tk.Label(grid, text=rng_text, bg=TH.PANEL,
                 fg=TH.DISABLED if locked else TH.TEXT_DIM,
                 font=TH.F['small'], padx=8, pady=4, anchor='center').grid(
            row=row, column=6, sticky='ew', pady=(0, 1))

    def _refresh_params(self):
        host = self._params_host
        if host is None:
            return
        self._rebuilding = True
        try:
            self._clear(host)
            self._param_entries = []
            parts = list(_get(self.project, 'parts', []) or [])
            if not parts:
                tk.Label(host, text='暂无部件数据', bg=TH.PANEL, fg=TH.TEXT_MUTE,
                         font=TH.F['small'], anchor='w').pack(fill='x', padx=4, pady=8)
                return
            for part in parts:
                self._build_part_block(host, part)
        finally:
            self._rebuilding = False

    def _build_part_block(self, host, part):
        outer, inner = TH.card(host, bg=TH.PANEL_ALT)
        outer.pack(fill='x', pady=(0, 6))

        key = str(_get(part, 'key', '') or '')
        name = str(_get(part, 'name', '') or key)
        category = str(_get(part, 'category', '') or '')
        spec = str(_get(part, 'spec', '') or '')

        top = tk.Frame(inner, bg=TH.PANEL_ALT)
        top.pack(fill='x', padx=12, pady=(9, 2))
        tk.Label(top, text=name, bg=TH.PANEL_ALT, fg=TH.TEXT,
                 font=TH.F['body_b']).pack(side='left')
        meta = ' · '.join(piece for piece in (category, key) if piece)
        if meta:
            tk.Label(top, text=meta, bg=TH.PANEL_ALT, fg=TH.TEXT_MUTE,
                     font=TH.F['tiny']).pack(side='left', padx=(8, 0))
        if spec:
            tk.Label(top, text='型号 ' + spec, bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                     font=TH.F['small']).pack(side='right')

        grid = tk.Frame(inner, bg=TH.PANEL_ALT)
        grid.pack(fill='x', padx=12, pady=(2, 10))

        params = _get(part, 'params', {}) or {}
        if not params:
            tk.Label(grid, text='无尺寸参数', bg=TH.PANEL_ALT, fg=TH.TEXT_MUTE,
                     font=TH.F['small'], anchor='w').grid(row=0, column=0, sticky='w')
            return

        for index, (param, value) in enumerate(sorted(params.items())):
            param = str(param)
            # 两列并排：七种部件全竖着排要滚三屏，横着并一列刚好一屏看完。
            row, slot = divmod(index, 2)
            name_col, value_col = slot * 2, slot * 2 + 1
            tk.Label(grid, text=param, bg=TH.PANEL_ALT, fg=TH.TEXT_DIM,
                     font=TH.F['small'], anchor='w', width=13).grid(
                row=row, column=name_col, sticky='w', pady=1, padx=(0, 2))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                var = tk.StringVar(value=_fmt(float(value)))
                entry = ttk.Entry(grid, textvariable=var, width=10, font=TH.F['num'])
                entry.grid(row=row, column=value_col, sticky='w', padx=(6, 26), pady=1)
                # 闭包里带默认参数，避免循环变量晚绑定；提交后刷新会重建输入框。
                entry.bind('<Return>',
                           lambda _e, v=var, k=key, n=param: self._commit_param(k, n, v))
                entry.bind('<FocusOut>',
                           lambda _e, v=var, k=key, n=param: self._commit_param(k, n, v))
                self._param_entries.append((key, param, var, entry))
            else:
                tk.Label(grid, text=_fmt(value), bg=TH.PANEL_ALT, fg=TH.TEXT,
                         font=TH.F['body'], anchor='w').grid(
                    row=row, column=value_col, sticky='w', padx=(6, 26), pady=1)

    def _refresh_joints(self):
        tree = self._joint_tree
        if tree is None:
            return
        try:
            tree.delete(*tree.get_children())
            for joint in (_get(self.project, 'joints', []) or []):
                kind = str(_get(joint, 'kind', '') or '')
                axis = str(_get(joint, 'axis', '') or '—')
                travel = _get(joint, 'travel', 0.0)
                unit = '°' if kind == 'revolute' else 'mm'
                tree.insert('', 'end', values=(
                    str(_get(joint, 'a', '') or ''),
                    str(_get(joint, 'b', '') or ''),
                    KIND_LABEL.get(kind, kind or '—'),
                    axis,
                    '%s %s' % (_fmt(travel), unit)))
        except tk.TclError:
            pass

    def _refresh_checks(self):
        tree = self._check_tree
        if tree is None:
            return

        checks = list(_get(self.project, 'checks', []) or [])
        if not checks and not self._auto_eval_done:
            # 第一次进校核页先算一遍，否则用户看到的是一张空表。
            self._auto_eval_done = True
            checks = self._evaluate_checks()

        counts = {'pass': 0, 'fail': 0, 'not_run': 0, 'not_applicable': 0}
        try:
            tree.delete(*tree.get_children())
            for status in counts:
                # 四分色每次刷新重设（理由同 _check_fg）。
                tree.tag_configure(status, foreground=_check_fg(status))
            for check in checks:
                status = str(_get(check, 'status', 'not_run') or 'not_run')
                counts[status] = counts.get(status, 0) + 1
                limit = _get(check, 'limit')
                value = _get(check, 'value')
                unit = str(_get(check, 'unit', '') or '')
                if limit:
                    value_text = '%s / %s %s' % (_fmt(value), _fmt(limit), unit)
                else:
                    value_text = '%s %s' % (_fmt(value), unit) if value != 0 else '—'
                tree.insert('', 'end', tags=(status,), values=(
                    str(_get(check, 'check_id', '') or ''),
                    str(_get(check, 'title', '') or ''),
                    _check_label(status),
                    value_text.strip(),
                    str(_get(check, 'rule_id', '') or '—'),
                    '第 %s 阶段' % (_get(check, 'stage', 0) or 0)))
        except tk.TclError:
            return

        for key, label in self._count_labels.items():
            try:
                label.configure(text='%s %d' % (_check_label(key), counts.get(key, 0)))
            except tk.TclError:
                pass

    def _evaluate_checks(self):
        """跑一遍规则校验，结果写回 project.checks（契约 §2 就是它的落点）。

        优先用引擎的 run_checks：实现方在 WorkflowEngine 里明确写了「参数改动只负责
        作废，不负责重算，UI 在参数修正后要显式调它」。引擎没这个口子时退回契约 §4
        的 evaluate，由本文件负责写回。
        """
        try:
            runner = getattr(self.engine, 'run_checks', None)
            if callable(runner):
                results = list(runner() or [])
            else:
                results = list(evaluate(self.project) or [])
                self.project.checks = results
        except Exception as exc:  # noqa: BLE001
            self._status('校核失败：%s' % exc, 'err')
            return []
        return results

    # -- 交互：导航 ----------------------------------------------------------------

    @_guarded
    def _on_step_click(self, stage):
        self._goto(stage)

    def _goto(self, stage):
        stage = self._clamp_stage(stage)
        if stage == self.current_stage:
            return

        # 契约原则 1：进任何一步之前先问引擎。
        ok, missing = self._can_enter(stage)
        committed = stage in self._committed_stages()

        if not ok and not committed:
            self._status('不能进入第 %d 阶段「%s」，缺 %d 项：%s'
                         % (stage, self._stage_name(stage), len(missing),
                            '；'.join(missing)), 'err')
            return

        if ok:
            try:
                # 走引擎的 enter：它记账(history)并落 project.stage，UI 不绕过状态机。
                self.engine.enter(stage)
            except WorkflowError as exc:
                detail = getattr(exc, 'missing', None) or [str(exc)]
                self._status('进入被拒：%s' % '；'.join(str(x) for x in detail), 'err')
                return
            except Exception as exc:  # noqa: BLE001
                self._status('进入第 %d 阶段失败：%s' % (stage, exc), 'err')
                return
        else:
            # 已提交过的阶段允许回看；此时它自己的前置可能已被作废，
            # 引擎的 enter 会拦，所以只落 stage 不记 enter 事件（回看不该改历史）。
            try:
                self.project.stage = stage
            except Exception:  # noqa: BLE001
                pass

        self.current_stage = stage

        if not ok:
            self._status('第 %d 阶段已提交，仅供查看；当前缺项（%d）：%s'
                         % (stage, len(missing), '；'.join(missing)), 'warn')
        else:
            self._status('已进入第 %d 阶段「%s」。'
                         % (stage, self._stage_name(stage)), 'ok')
        self.refresh()

    @_guarded
    def _on_prev(self):
        if self.current_stage <= self._stage_ids[0]:
            self._status('已经是第一步了。', 'warn')
            return
        self._goto(self.current_stage - 1)

    # ---- 工序记忆：把每次提交的结局自动学进去 -----------------------------

    def _process_store(self):
        """惰性加载工序记忆，并把工序库灌进去（只刷新向量，不动历史统计）。

        必须先 learn_steps：ProcessMemory.record 对未知工序号会直接拒绝，
        不先导入工序库，第一笔记录就会失败。
        """
        if getattr(self, '_proc_mem', None) is None:
            try:
                import os
                from ai_modeler.meta import ProcessMemory
                from ai_modeler.workflow import load_process
                from ai_modeler.paths import PROJECT_ROOT
                mem = ProcessMemory(os.path.join(
                    str(PROJECT_ROOT), 'kb', 'process_memory.json'))
                lib = load_process()
                mem.learn_steps([
                    {'step_id': s.step_id, 'stage': s.stage,
                     'text': s.search_text(), 'status': s.status}
                    for s in lib.steps])
                self._proc_mem = mem
                self._proc_steps = {no: [s.step_id for s in steps]
                                    for no, steps in lib.by_stage().items()}
            except Exception:
                self._proc_mem = False
        return self._proc_mem or None

    def _process_learn(self, stage, ok, reason=''):
        """把这一阶段的提交结局写进工序记忆。

        三条分寸：
        1. 成功记 success、被拦记 fail——被拦也是数据，而且是**最有用**的那种，
           它直接回答「这道工序常栽在哪」；
        2. 一个阶段对应几道工序就记几条，不合并成一条，否则成功率没法用；
        3. 任何异常都吞掉：学习是增强，不能让记忆的问题卡住流程。
        """
        try:
            mem = self._process_store()
            if mem is None:
                return
            step_ids = (getattr(self, '_proc_steps', None) or {}).get(stage) or []
            if not step_ids:
                return
            note = '' if ok else (reason or '前置条件未满足')
            recorded = 0
            for sid in step_ids:
                try:
                    mem.record(sid, 'success' if ok else 'fail', note)
                    recorded += 1
                except Exception:
                    continue
            if recorded:
                mem.save()
        except Exception:
            pass

    @_guarded
    def _on_commit(self):
        stage = self.current_stage
        ok, missing = self._can_enter(stage)
        if not ok:
            # 前置不满足就不许完成——按钮也已禁用，这里再挡一次（键盘/脚本触发）。
            self._status('第 %d 阶段还不能完成，缺 %d 项：%s'
                         % (stage, len(missing), '；'.join(missing)), 'err')
            # 被门禁拦下也是经验：记一笔失败，下次查这道工序就知道它常缺什么。
            self._process_learn(stage, False, '；'.join(missing))
            self.refresh()
            return

        try:
            self.engine.commit(stage)
        except WorkflowError as exc:
            detail = getattr(exc, 'missing', None) or [str(exc)]
            self._status('提交被拒：%s' % '；'.join(str(item) for item in detail), 'err')
            self._process_learn(stage, False, '；'.join(str(item) for item in detail))
            self.refresh()
            return

        # 提交成功：这一阶段的工序算过了一遍，自动写进工序记忆。
        self._process_learn(stage, True)

        nxt = stage + 1
        if nxt > self._last_stage():
            self._status('五阶段全部完成，可以导出校核记录。', 'ok')
            self.refresh()
            return

        ok_next, missing_next = self._can_enter(nxt)
        if ok_next:
            # 引擎的 commit 已经把 project.stage 推到 nxt，这里只负责跟上并报一句。
            self.current_stage = nxt
            self._status('第 %d 阶段已提交，自动进入第 %d 阶段「%s」。'
                         % (stage, nxt, self._stage_name(nxt)), 'ok')
        else:
            # 提交成功但不能自动往下滚：把下一阶段缺什么说清楚，别让用户盲点。
            self._status('第 %d 阶段已提交；进入第 %d 阶段还需：%s'
                         % (stage, nxt, '；'.join(missing_next)), 'warn')
        self.refresh()

    # -- 交互：资料 / 点位 / 参数 ---------------------------------------------------

    @_guarded
    def _on_toggle_input(self, event):
        tree = self._input_tree
        if tree is None:
            return
        item = tree.identify_row(event.y)
        if not item:
            return
        try:
            index = int(item.split(':')[1])
        except (IndexError, ValueError):
            return

        inputs = list(_get(self.project, 'inputs', []) or [])
        if not (0 <= index < len(inputs)):
            return
        target = inputs[index]
        label = str(_get(target, 'label', '') or '资料')

        if not bool(_get(target, 'loaded', False)) and not str(_get(target, 'path', '') or ''):
            # 没有文件却点"已确认"，等于伪造齐全，W-INPUT-01 就成了摆设。
            self._status('资料「%s」还没有文件路径，先补路径再确认。' % label, 'warn')
            return

        loaded = not bool(_get(target, 'loaded', False))
        # 说明：引擎没有提供"资料确认"的接口，而 W-INPUT-01 要求 loaded，
        # 卡在这一项上用户就没法往下走。这是本窗口唯一直接写 project 字段的地方，
        # 失效传播仍然走引擎的 invalidate_from，不自己造状态。
        _set(target, 'loaded', loaded)
        try:
            invalidated = self.engine.invalidate_from(1)
        except Exception:  # noqa: BLE001
            invalidated = 0
        self._status('资料「%s」已标记为%s；已作废 %d 项检查'
                     % (label, '已确认' if loaded else '未确认', int(invalidated or 0)),
                     'warn')
        self.after_idle(self.refresh)

    def _find_point(self, pid):
        for point in (_get(self.project, 'points', []) or []):
            if str(_get(point, 'pid', '')) == str(pid):
                return point
        return None

    @_guarded
    def _commit_offset(self, pid):
        if self._rebuilding:
            return
        point = self._find_point(pid)
        if point is None:
            return

        current = float(_get(point, 'offset', 0.0) or 0.0)
        entry = self._offset_entries.get(pid)

        # P01 的输入框本应 disabled；这里再挡一次，防止程序化 set 进来。
        if bool(_get(point, 'offset_locked', False)) or str(pid).upper() == 'P01':
            self._status('P01 是固定定位点，偏移锁定为 0，不能修改（契约红线）。', 'err')
            return

        if entry is None:
            return
        raw = entry.get()
        value = _as_float(raw)
        if value is None:
            self._status('偏移「%s」不是有效数字，已还原为 %s。' % (raw, _fmt(current)), 'err')
            entry.delete(0, 'end')
            entry.insert(0, _fmt(current))
            return

        if abs(value - current) < 1e-9:
            return

        stage_before = _get(self.project, 'stage')
        # 点位 offset 也走契约 §3 的 set_param（实现里按 'P02' 这样的 id 寻址）：
        # 由引擎负责 P01 拦截、revision +1、失效传播和史记录，UI 只负责提示。
        try:
            invalidated = self.engine.set_param(pid, 'offset', value)
        except (ValueError, KeyError) as exc:
            self._status('偏移被拒：%s（已还原）' % exc, 'err')
            entry.delete(0, 'end')
            entry.insert(0, _fmt(current))
            return

        titles = [str(item) for item in (invalidated or [])]
        message = '点位 %s 偏移已改为 %s mm' % (pid, _fmt(value))
        if titles:
            message += '；已作废 %d 项检查' % len(titles)
        low, high = _offset_range(point)
        if not (low - 1e-6 <= value <= high + 1e-6):
            # 越界不拦：W-P02-01 要能如实报 fail。入口全拦住，规则就永远跑不到。
            message += '；超出可调范围 [%s, %s]，校核会判不通过' % (_fmt(low), _fmt(high))
        if _get(self.project, 'stage') != stage_before:
            # 失效传播会把当前阶段回退(基准一变，后面全要重来)，得说清楚
            message += '；基准变更，已退回第 %s 阶段' % (_get(self.project, 'stage'),)
        self._status(message, 'warn')
        self.after_idle(self.refresh)

    def _part_value(self, part_key, name):
        for part in (_get(self.project, 'parts', []) or []):
            if str(_get(part, 'key', '')) == str(part_key):
                return (_get(part, 'params', {}) or {}).get(name)
        return None

    @_guarded
    def _commit_param(self, part_key, name, var):
        if self._rebuilding:
            return
        current = self._part_value(part_key, name)
        raw = var.get()
        value = _as_float(raw)

        if value is None:
            self._status('「%s」不是有效数字：%s，已还原。' % (name, raw), 'err')
            var.set(_fmt(current) if current is not None else '')
            return

        if current is not None and abs(value - float(current)) < 1e-9:
            var.set(_fmt(value))
            return

        try:
            invalidated = self.engine.set_param(part_key, name, value)
        except (ValueError, KeyError) as exc:
            # 引擎判定非法（锁死 / 文本参数 / 非数字 / 未知部件）→ 还原并说清原因
            self._status('参数被拒：%s（已还原）' % exc, 'err')
            var.set(_fmt(current) if current is not None else '')
            return

        titles = [str(item) for item in (invalidated or [])]
        message = '%s.%s 已改为 %s' % (part_key, name, _fmt(value))
        if titles:
            message += '；已作废 %d 项检查' % len(titles)
        self._status(message, 'warn' if titles else 'ok')
        # 刷新会重建输入框，正在处理 <FocusOut> 时不能同步销毁自己。
        self.after_idle(self.refresh)

    # -- 交互：校核 / 导出 / 推送 ---------------------------------------------------

    @_guarded
    def _on_run_checks(self):
        results = self._evaluate_checks()
        self._auto_eval_done = True
        if not results:
            self._status('校核已执行，但没有产生任何检查项。', 'warn')
        else:
            self._status('校核完成，共 %d 项结论。' % len(results), 'ok')
        self.refresh()

    @_guarded
    def _on_export(self):
        if not (_get(self.project, 'checks', []) or []):
            self._evaluate_checks()

        unit_id = str(_get(self.project, 'unit_id', '') or 'UN200')
        revision = _get(self.project, 'revision', 1)
        path = filedialog.asksaveasfilename(
            parent=self,
            title='导出校核记录',
            defaultextension='.json',
            initialfile='单元记录_%s_r%s.json' % (unit_id, revision),
            filetypes=[('单元记录 JSON', '*.json'), ('全部文件', '*.*')])
        if not path:
            self._status('已取消导出。', 'warn')
            return

        try:
            written = export_unit_record(self.project, path)
        except Exception as exc:  # noqa: BLE001
            self._status('导出失败：%s' % exc, 'err')
            return

        self._status('校核记录已导出：%s' % (written or path), 'ok')

    @_guarded
    def _on_export_summary(self):
        if export_summary is None:
            self._status('当前领域层还没有提供摘要导出（契约 §6 未实现）。', 'warn')
            return
        unit_id = str(_get(self.project, 'unit_id', '') or 'UN200')
        path = filedialog.asksaveasfilename(
            parent=self,
            title='导出摘要',
            defaultextension='.md',
            initialfile='单元摘要_%s.md' % unit_id,
            filetypes=[('Markdown', '*.md'), ('全部文件', '*.*')])
        if not path:
            self._status('已取消导出。', 'warn')
            return
        written = export_summary(self.project, path)
        self._status('摘要已导出：%s' % (written or path), 'ok')

    @_guarded
    def _on_push_model(self):
        if self.on_push_model is None:
            # 不假装能推：明确告诉用户这是演示窗口。
            self._status('没有接主程序建模入口，本窗口只走演示流程。', 'warn')
            return
        parts = len(_get(self.project, 'parts', []) or [])
        joints = len(_get(self.project, 'joints', []) or [])
        self.on_push_model(self.engine.project)
        self._status('第 %d 阶段的建模结果已交给主程序（%d 个部件 / %d 条装配关系）。'
                     % (self.current_stage, parts, joints), 'ok')


def _offset_range(point):
    """PrpPoint.offset_range 是 tuple，序列化后可能变成 list；统一成两个 float。"""
    raw = _get(point, 'offset_range', (0.0, 0.0)) or (0.0, 0.0)
    try:
        low, high = float(raw[0]), float(raw[1])
    except (IndexError, TypeError, ValueError):
        low, high = 0.0, 0.0
    return low, high


__all__ = ['FixtureWorkflowWindow']
