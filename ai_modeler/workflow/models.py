"""夹具单元协同设计 —— 数据模型与默认案例。

设计意图（为什么先有这一层）：

1. 五步向导 UI、规则校验、导出记录共用同一份结构，**字段名即 JSON 键名**，
   界面显示 / 单元记录 / 单测断言都不需要再做名字映射——少一层翻译就少一类错。
2. 本文件**只依赖标准库**（dataclasses / json / datetime / math / pathlib 都不用到这里）。
   于是 rules / stages / report 可以在没有 tkinter、没有 CATIA 的机器上被单独 import 和测试
   （契约 §1 的硬要求，也是能写纯单测的前提）。
3. `simulated / cad_connected / model_api_called / real_cad_files_created` 这四个「事实字段」
   放在 FixtureProject 上、由 `facts()` 统一吐出，而不是散在 UI 变量里：
   导出记录里的这四个值只有一个来源，界面上的勾选框就改不动它们（契约 §0.5）。
4. `to_dict` 走一个通用的递归转换（`_to_plain`），而不是 7 个类各写一遍循环：
   新增字段时不会漏序列化——漏掉一个字段的导出记录是没法复核的。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from typing import Any

# 事实字段：不得由 UI 勾选改写（契约 §0.5）
FACT_KEYS = (
    'simulated',
    'cad_connected',
    'model_api_called',
    'real_cad_files_created',
)

# 单元必需部件（key -> 中文名）。放在这里，规则校验和阶段门禁共用一份，
# 避免「规则说缺件、门禁说齐全」这种自相矛盾。
REQUIRED_PARTS = (
    ('base_plate', '基板'),
    ('support', '支撑座'),
    ('contact_block', '接触块'),
    ('clamp_arm', '压臂'),
    ('cylinder', '气缸'),
    ('pin', '定位销'),
    ('screw', '内六角螺钉'),
)

# 演示公式常数：d = L - 62 + 4s（s = 侧向 1 / 上侧 0）
# 注意：62 是设计规范 §5 演示案例的几何常数，**不是通用工程常数**，
# 所以每条用到它的结论都带 rule_id / detail 说明来源，不能当成通用校核公式对外宣称。
ARM_DATUM = 62.0
ARM_SIDE_BONUS = 4.0


def now_iso() -> str:
    """ISO 时间串。只到秒：追溯记录读起来够用，又不会让同一秒的两条事件对不齐。"""
    return datetime.now().isoformat(timespec='seconds')


def _to_plain(value: Any) -> Any:
    """把 dataclass / tuple 递归转成 JSON 能表达的结构。

    tuple 单独处理成 list：json 不认 tuple，而 `offset_range` 就是 tuple，
    不转的话导出会直接抛 TypeError。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    return value


class _Serializable:
    """统一提供 to_dict()。from_dict() 各自实现——反序列化的类型收窄规则每个类都不同。"""

    def to_dict(self) -> dict:
        return _to_plain(self)


@dataclass
class InputFile(_Serializable):
    """工程输入资料。path 为空表示缺失，loaded 表示「已确认可用」，两者不能混为一谈：
    文件路径填了但没人核过，照样不能进下一步（契约 §0.1）。"""

    key: str
    label: str
    path: str = ''
    required: bool = True
    version: str = ''
    loaded: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> 'InputFile':
        data = dict(data or {})
        return cls(
            key=str(data.get('key', '')),
            label=str(data.get('label', '')),
            path=str(data.get('path', '')),
            required=bool(data.get('required', True)),
            version=str(data.get('version', '')),
            loaded=bool(data.get('loaded', False)),
        )


@dataclass
class PrpPoint(_Serializable):
    """定位夹紧点。offset_locked=True 表示偏移被锁死（P01 必为 True）。

    为什么 offset_range 在 __post_init__ 里强制收成 tuple：
    JSON 读回来是 list，list 和 tuple 比较不相等，范围校验写 `lo <= v <= hi` 时
    更怕的是时而是 list 时而是 tuple 的不确定性，所以构造即定型。
    """

    pid: str
    role: str
    x: float
    y: float
    z: float
    nx: float = 0.0
    ny: float = 0.0
    nz: float = 1.0
    offset: float = 0.0
    offset_locked: bool = False
    offset_range: tuple = (0.0, 0.0)

    def __post_init__(self) -> None:
        bounds = tuple(self.offset_range or (0.0, 0.0))
        if len(bounds) != 2:
            raise ValueError('offset_range 必须是 (最小值, 最大值)：%r' % (self.offset_range,))
        self.offset_range = (float(bounds[0]), float(bounds[1]))

    @classmethod
    def from_dict(cls, data: dict) -> 'PrpPoint':
        data = dict(data or {})
        return cls(
            pid=str(data.get('pid', '')),
            role=str(data.get('role', 'primary')),
            x=float(data.get('x', 0.0)),
            y=float(data.get('y', 0.0)),
            z=float(data.get('z', 0.0)),
            nx=float(data.get('nx', 0.0)),
            ny=float(data.get('ny', 0.0)),
            nz=float(data.get('nz', 1.0)),
            offset=float(data.get('offset', 0.0)),
            offset_locked=bool(data.get('offset_locked', False)),
            offset_range=tuple(data.get('offset_range', (0.0, 0.0))),
        )


@dataclass
class RuleSpec(_Serializable):
    """工程规则条目。

    confirmed 单独存在，是因为设计规范里反复强调的一条：未经企业确认的阈值只能叫
    「示例约定」。规则没确认时检查照样跑（不然演示走不下去），但结论里必须写清来源，
    不能拿它冒充企业标准。
    """

    rule_id: str
    title: str
    source: str
    applies_to: str
    unit: str
    method: str
    target: float = 0.0
    target_max: float = 0.0
    formula: str = ''
    severity: str = 'block'
    confirmed: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> 'RuleSpec':
        data = dict(data or {})
        return cls(
            rule_id=str(data.get('rule_id', '')),
            title=str(data.get('title', '')),
            source=str(data.get('source', '')),
            applies_to=str(data.get('applies_to', '')),
            unit=str(data.get('unit', '')),
            method=str(data.get('method', 'min')),
            target=float(data.get('target', 0.0)),
            target_max=float(data.get('target_max', 0.0)),
            formula=str(data.get('formula', '')),
            severity=str(data.get('severity', 'block')),
            confirmed=bool(data.get('confirmed', False)),
        )


@dataclass
class PartSpec(_Serializable):
    """单元部件。params 里允许出现字符串（如 material / layout / spec），
    因为主程序要拿它拼提示词；规则校验只对数值型参数做 > 0 判断，不会去 float() 字符串。"""

    key: str
    name: str
    category: str
    params: dict = field(default_factory=dict)
    spec: str = ''

    @classmethod
    def from_dict(cls, data: dict) -> 'PartSpec':
        data = dict(data or {})
        return cls(
            key=str(data.get('key', '')),
            name=str(data.get('name', '')),
            category=str(data.get('category', '自制件')),
            params=dict(data.get('params') or {}),
            spec=str(data.get('spec', '')),
        )


@dataclass
class JointSpec(_Serializable):
    """装配关系。kind='contact' 表示面接触约束，不算运动副；
    运动副只有 revolute / prismatic 两种，规则校验靠这个区分说话。"""

    a: str
    b: str
    kind: str
    axis: str = ''
    travel: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> 'JointSpec':
        data = dict(data or {})
        return cls(
            a=str(data.get('a', '')),
            b=str(data.get('b', '')),
            kind=str(data.get('kind', 'fix')),
            axis=str(data.get('axis', '')),
            travel=float(data.get('travel', 0.0)),
        )


@dataclass
class CheckResult(_Serializable):
    """检查结论。status 四分，绝不合并成一个绿灯（契约 §0.4）：
    pass / fail / not_run / not_applicable。后两者含义不同——
    「还没算」和「不适用」，混成一个就会在设计规范里撒谎。"""

    check_id: str
    title: str
    status: str
    detail: str = ''
    value: float = 0.0
    limit: float = 0.0
    unit: str = 'mm'
    simulated: bool = True
    rule_id: str = ''
    stage: int = 0

    STATUSES = ('pass', 'fail', 'not_run', 'not_applicable')

    @classmethod
    def from_dict(cls, data: dict) -> 'CheckResult':
        data = dict(data or {})
        return cls(
            check_id=str(data.get('check_id', '')),
            title=str(data.get('title', '')),
            status=str(data.get('status', 'not_run')),
            detail=str(data.get('detail', '')),
            value=float(data.get('value', 0.0)),
            limit=float(data.get('limit', 0.0)),
            unit=str(data.get('unit', 'mm')),
            simulated=bool(data.get('simulated', True)),
            rule_id=str(data.get('rule_id', '')),
            stage=int(data.get('stage', 0)),
        )


@dataclass
class TraceEvent(_Serializable):
    """可追溯事件。revision 记录的是事件发生时的数据版本，
    这样复核时能对上「这条记录对应哪一版参数」。"""

    ts: str
    stage: int
    action: str
    detail: str = ''
    revision: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> 'TraceEvent':
        data = dict(data or {})
        return cls(
            ts=str(data.get('ts', '')),
            stage=int(data.get('stage', 0)),
            action=str(data.get('action', '')),
            detail=str(data.get('detail', '')),
            revision=int(data.get('revision', 0)),
        )


@dataclass
class FixtureProject(_Serializable):
    """一个夹具单元方案的全部状态。

    revision 只在**参数实际发生变化**时 +1；invalidate_from 不动它。
    这样「第几版数据」和「失效传播」是两件事，复核时不会因为多跑了几次
    作废逻辑就把版本号数乱。
    """

    unit_id: str = 'UN200'
    unit_name: str = '气动夹紧单元'
    case_note: str = ''
    inputs: list = field(default_factory=list)
    points: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    joints: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    history: list = field(default_factory=list)
    stage: int = 1
    revision: int = 1
    # 事实字段：不得由 UI 勾选改写
    simulated: bool = True
    cad_connected: bool = False
    model_api_called: bool = False
    real_cad_files_created: bool = False

    def facts(self) -> dict:
        """四个事实字段原样吐出，供导出记录使用（只读语义，不提供 setter）。"""
        return {key: bool(getattr(self, key)) for key in FACT_KEYS}

    def find_part(self, key: str):
        return find_part(self, key)

    def find_point(self, pid: str):
        return find_point(self, pid)

    @classmethod
    def from_dict(cls, data: dict) -> 'FixtureProject':
        data = dict(data or {})
        project = cls(
            unit_id=str(data.get('unit_id', 'UN200')),
            unit_name=str(data.get('unit_name', '气动夹紧单元')),
            case_note=str(data.get('case_note', '')),
            stage=int(data.get('stage', 1)),
            revision=int(data.get('revision', 1)),
            simulated=bool(data.get('simulated', True)),
            cad_connected=bool(data.get('cad_connected', False)),
            model_api_called=bool(data.get('model_api_called', False)),
            real_cad_files_created=bool(data.get('real_cad_files_created', False)),
        )
        project.inputs = [InputFile.from_dict(x) for x in data.get('inputs') or []]
        project.points = [PrpPoint.from_dict(x) for x in data.get('points') or []]
        project.rules = [RuleSpec.from_dict(x) for x in data.get('rules') or []]
        project.parts = [PartSpec.from_dict(x) for x in data.get('parts') or []]
        project.joints = [JointSpec.from_dict(x) for x in data.get('joints') or []]
        project.checks = [CheckResult.from_dict(x) for x in data.get('checks') or []]
        project.history = [TraceEvent.from_dict(x) for x in data.get('history') or []]
        return project


# --------------------------------------------------------------------------
# 纯数据查询（models 只依赖标准库，rules 与 stages 共用这几个函数）
# --------------------------------------------------------------------------
def find_part(project: FixtureProject, key: str):
    """按 key 取部件；取不到返回 None——调用方要自己决定「缺件」算 fail 还是 not_run。"""
    for part in project.parts:
        if part.key == key:
            return part
    return None


def find_point(project: FixtureProject, pid: str):
    for point in project.points:
        if point.pid == pid:
            return point
    return None


def find_rule(project: FixtureProject, rule_id: str):
    for rule in project.rules:
        if rule.rule_id == rule_id:
            return rule
    return None


def is_number(value: Any) -> bool:
    """true 数值才算数值：bool 是 int 的子类，`True > 0` 会静默通过尺寸校验，必须排除。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive_dim(value: Any) -> bool:
    """关键尺寸有效 = 是数值且 > 0。缺失 / 字符串 / 0 / 负数都算「未设置」。"""
    return is_number(value) and float(value) > 0.0


# --------------------------------------------------------------------------
# 默认案例（UN200，与示例 / 设计规范 §5 一致）
# --------------------------------------------------------------------------
def build_demo_project() -> FixtureProject:
    """构造默认案例：一套气动夹紧单元。

    刻意设计成「只有一条失败」：压臂 L=65、上侧布置 → d = 65-62 = 3 < 5，
    W-ARM-01 必 fail，用来触发修正分支（L=75 → d=13）。
    其余检查全部通过，这样四分统计里 pass / fail / not_run / not_applicable 各能看到真实样本。

    本函数**不预置 checks**：结论必须由 rules.evaluate() 现算。
    预置一份结论放在项目里，等参数一改就会留下一排过期绿灯——这正是设计原则 3 要防的事。
    """
    project = FixtureProject(
        unit_id='UN200',
        unit_name='气动夹紧单元',
        case_note='一套气动夹紧单元的协同设计',
    )

    # 第 1 阶段：工程输入。四项都已确认可用，否则演示连第 2 步都走不进去。
    project.inputs = [
        InputFile('workpiece', '工件数模', 'assets/demo/un200_workpiece.step',
                  True, '2026-A', True),
        InputFile('prp', '定位夹紧方案（PRP）', 'assets/demo/un200_prp.xlsx',
                  True, '2026-A', True),
        InputFile('rules', '工程规则表', 'assets/demo/un200_rules.csv',
                  True, '2026-A', True),
        InputFile('std_parts', '标准件库', 'assets/demo/std_parts.csv',
                  True, '2026-A', True),
    ]

    # 定位夹紧点：P01 主基准锁死，P02 辅助点可在 [0, 4] mm 内调。
    project.points = [
        PrpPoint('P01', 'primary', 300.0, 190.0, 20.0,
                 nx=0.0, ny=0.0, nz=1.0,
                 offset=0.0, offset_locked=True, offset_range=(0.0, 0.0)),
        PrpPoint('P02', 'assist', 120.0, 60.0, 20.0,
                 nx=0.0, ny=0.0, nz=1.0,
                 offset=2.0, offset_locked=False, offset_range=(0.0, 4.0)),
    ]

    # 工程规则：全部标注「示例约定」，没有一条写成已确认的企业标准。
    project.rules = [
        RuleSpec(
            rule_id='R-GAP-01', title='压臂最小间距', source='企业示例约定 · 待确认',
            applies_to='clamp_unit', unit='mm', method='formula',
            target=5.0, formula='d = L - 62 + 4s',
            severity='block', confirmed=False,
        ),
        RuleSpec(
            rule_id='R-OFF-01', title='辅助点偏移范围', source='企业示例约定 · 待确认',
            applies_to='prp_point', unit='mm', method='range',
            target=0.0, target_max=4.0, severity='block', confirmed=False,
        ),
        RuleSpec(
            rule_id='R-LOCK-01', title='固定点偏移锁定', source='固定基准锁定 · 硬约束',
            applies_to='prp_point', unit='mm', method='max',
            target=0.0, severity='block', confirmed=True,
        ),
    ]

    # 部件参数名与主程序拼提示词的键一致，改名会静默降级成一句「气动夹紧单元」。
    project.parts = [
        PartSpec('base_plate', '基板', '自制件', {
            'length': 600.0, 'width': 380.0, 'thickness': 20.0,
            'material': '2mm 钣金件',
        }),
        PartSpec('support', '支撑座', '自制件', {
            'height': 120.0, 'plate_thickness': 15.0,
        }),
        PartSpec('contact_block', '接触块', '自制件', {
            'length': 80.0, 'width': 50.0, 'height': 30.0,
        }),
        # 故意给 65：上侧布置下 d = 3 mm < 5 mm，触发修正分支。
        PartSpec('clamp_arm', '压臂', '自制件', {'length': 65.0}),
        PartSpec('cylinder', '气缸', '外购件', {
            'stroke_deg': 65.0, 'bore': 32.0, 'layout': 'top',
        }, spec='SDA32×65'),
        PartSpec('pin', '定位销', '标准件', {'diameter': 12.0, 'length': 40.0},
                 spec='GB/T 2203 φ12×40'),
        PartSpec('screw', '内六角螺钉', '标准件', {'spec': 'M8x20'}, spec='M8x20'),
    ]

    # 装配关系：压臂转动副 + 气缸移动副是 W-ASSY-01 的两条判据。
    project.joints = [
        JointSpec('base_plate', 'support', 'fix'),
        JointSpec('support', 'contact_block', 'contact'),
        JointSpec('support', 'clamp_arm', 'revolute', axis='Y', travel=65.0),
        JointSpec('base_plate', 'cylinder', 'fix'),
        JointSpec('cylinder', 'clamp_arm', 'prismatic', axis='Z', travel=65.0),
        JointSpec('contact_block', 'pin', 'fix'),
        JointSpec('clamp_arm', 'screw', 'fix'),
    ]

    return project
