"""工程规则校验 —— 把「能不能进下一步」变成可以复核的四分结论。

设计意图：

1. 每条检查只回答一个问题，`check_id` 前缀统一 `W-`（Workflow）。
2. 结论四分，且**不允许被合并**：`pass / fail / not_run / not_applicable`。
   设计规范里最要命的就是「一抹绿」——没跑过的也画成通过。
3. `simulated` 恒为 True：本层没有接 CATIA，也没有真实运动仿真，
   任何结论都只能算「按公式算出来的」而不是「实测出来的」（契约 §0.6）。
4. `W-MOTION-01` 恒 not_run、`W-MANUF-01` 恒 not_applicable——这两条是设计规范的红线，
   不管项目数据多完整都不许改成 pass，否则就是在报告里宣布了自己没做的事。
5. `stage` 字段表示「这条结论依赖哪个阶段的数据」，失效传播照着它剪枝。
   取依赖链上**最晚**的阶段，是宁可多作废也不留假绿灯。
"""
from __future__ import annotations

import math

from .models import (
    ARM_DATUM,
    ARM_SIDE_BONUS,
    CheckResult,
    FixtureProject,
    REQUIRED_PARTS,
    find_part,
    find_point,
    find_rule,
    positive_dim,
)

# 无规则表时的兜底阈值。演示公式的来源见 models.ARM_DATUM 注释。
DEFAULT_ARM_REQUIRED = 5.0

# 压臂下料步距与装配余量（mm）。
# 建议值不是「最小可行值」而是「最小可行值向上取步距、再加一个步距」：
# 下料按 5 mm 一档走，装配时压臂前端还要留出可调整的余量，
# 只给刚好 5 mm 的理论值，现场一磨倒角就回到失败区。
ARM_STEP = 5.0
ARM_MARGIN = 5.0


def _layout_flag(layout: str) -> float:
    """把布置方式翻成演示公式里的 s：侧向 = 1，上侧 = 0。

    写死别名是因为参数可能从 CSV / 界面文本进来，认不出就报错，
    不要默默当成上侧——那会让一个侧向方案用上侧的阈值算出「通过」。
    """
    text = str(layout or '').strip().lower()
    if text in ('top', '上侧', 'upper', ''):
        return 0.0
    if text in ('side', '侧向', '侧', 'lateral'):
        return 1.0
    raise ValueError('未知布置方式 %r，只能是 top（上侧）或 side（侧向）' % (layout,))


def arm_clearance(arm_length: float, layout: str) -> float:
    """演示公式：d = L - 62 + 4s（s = 侧向 1 / 上侧 0），返回压臂理论间距 mm。"""
    return float(arm_length) - ARM_DATUM + ARM_SIDE_BONUS * _layout_flag(layout)


def suggest_arm_length(layout: str, required: float) -> float:
    """给出满足 `d >= required` 的压臂建议长度。

    算法（三步，每一步都能在图上量出来）：
      1. 理论下限 L0 = required + 62 - 4s；
      2. 向上取到 ARM_STEP（5 mm）的整数倍，方便下料；
      3. 再加一个步距的装配余量。
    所以 required=5、上侧时：67 → 70 → 75（此时 d = 13 mm）。
    """
    required = float(required)
    if not math.isfinite(required):
        raise ValueError('required 必须是有限数值：%r' % (required,))
    base = required + ARM_DATUM - ARM_SIDE_BONUS * _layout_flag(layout)
    steps = math.ceil(base / ARM_STEP - 1e-9)
    return float(steps) * ARM_STEP + ARM_MARGIN


def _fmt(value: float) -> str:
    """数字进 detail 时去掉多余小数位：3.0 → 3，13.5 → 13.5。"""
    return ('%.2f' % float(value)).rstrip('0').rstrip('.')


def _inputs_check(project: FixtureProject) -> CheckResult:
    """W-INPUT-01：必需资料既要登记路径，也要确认可用。

    缺项必须点名到具体资料——「资料不全」这种话在评审会上等于没说。
    """
    missing_path = []
    missing_loaded = []
    for item in project.inputs:
        if not item.required:
            continue
        if not item.path:
            missing_path.append(item.label)
        elif not item.loaded:
            missing_loaded.append(item.label)

    parts = []
    if missing_path:
        parts.append('缺少必需资料：' + '、'.join(missing_path))
    if missing_loaded:
        parts.append('资料未确认可用：' + '、'.join(missing_loaded))

    if not project.inputs:
        parts.append('未登记任何输入资料')

    required_count = sum(1 for item in project.inputs if item.required)
    status = 'not_run' if not project.inputs else ('fail' if parts else 'pass')
    detail = '；'.join(parts) if parts else '必需资料 %d 项均已确认可用' % required_count
    return CheckResult(
        check_id='W-INPUT-01', title='输入资料齐全性', status=status, detail=detail,
        value=float(required_count - len(missing_path) - len(missing_loaded)),
        limit=float(required_count), unit='项', simulated=True, stage=1,
    )


def _p01_check(project: FixtureProject) -> CheckResult:
    """W-P01-01：P01 固定点偏移必须为 0 且被锁死（契约 §0.2）。

    锁定标记和实际偏移分开判：只锁不置 0、或者置 0 却没锁，都是现场能被改歪的隐患。
    """
    point = find_point(project, 'P01')
    if point is None:
        return CheckResult(
            check_id='W-P01-01', title='P01 固定点偏移锁定', status='fail',
            detail='未定义 P01 固定定位点', value=0.0, limit=0.0, unit='mm',
            simulated=True, rule_id='R-LOCK-01', stage=1,
        )
    problems = []
    if abs(float(point.offset)) > 1e-9:
        problems.append('P01 偏移必须为 0 mm，当前 %s mm' % _fmt(point.offset))
    if not point.offset_locked:
        problems.append('P01 偏移未锁定，任何阶段都不得放开')
    status = 'fail' if problems else 'pass'
    detail = '；'.join(problems) if problems else 'P01 偏移锁定为 0 mm'
    return CheckResult(
        check_id='W-P01-01', title='P01 固定点偏移锁定', status=status, detail=detail,
        value=float(point.offset), limit=0.0, unit='mm',
        simulated=True, rule_id='R-LOCK-01', stage=1,
    )


def _arm_check(project: FixtureProject) -> CheckResult:
    """W-ARM-01：演示公式 d = L - 62 + 4s，要求 d >= 5。

    参数来源固定为两个地方：`cylinder.params['layout']`（布置方式）与
    `clamp_arm.params['length']`（压臂长度）。不从别处索引——
    部件参数是唯一的真相，多一个来源就多一次对不上的机会。
    """
    arm = find_part(project, 'clamp_arm')
    cylinder = find_part(project, 'cylinder')
    layout = cylinder.params.get('layout', 'top') if cylinder is not None else 'top'

    rule = find_rule(project, 'R-GAP-01')
    required = float(rule.target) if rule is not None and rule.target else DEFAULT_ARM_REQUIRED
    source_note = ''
    if rule is not None and not rule.confirmed:
        source_note = '（阈值来源：%s，未经企业确认）' % rule.source

    if arm is None:
        return CheckResult(
            check_id='W-ARM-01', title='压臂最小间距', status='fail',
            detail='缺少部件：压臂，无法校核压臂间距', value=0.0, limit=required,
            unit='mm', simulated=True, rule_id='R-GAP-01', stage=3,
        )
    length = arm.params.get('length')
    if not positive_dim(length):
        return CheckResult(
            check_id='W-ARM-01', title='压臂最小间距', status='fail',
            detail='压臂长度未设置' + source_note, value=0.0, limit=required,
            unit='mm', simulated=True, rule_id='R-GAP-01', stage=3,
        )

    # 布置方式读不出来时给 fail 而不是抛异常：evaluate() 一旦会抛，
    # 界面上一条脏数据就能把整张校核表打断，什么都看不到。
    try:
        layout_flag = _layout_flag(layout)
    except ValueError as exc:
        return CheckResult(
            check_id='W-ARM-01', title='压臂最小间距', status='fail',
            detail='%s，无法按演示公式校核压臂间距' % exc,
            value=0.0, limit=required, unit='mm',
            simulated=True, rule_id='R-GAP-01', stage=3,
        )

    clearance = arm_clearance(length, layout)
    suggestion = suggest_arm_length(layout, required)
    passed = clearance >= required - 1e-9
    layout_text = '上侧' if layout_flag == 0.0 else '侧向'
    if passed:
        detail = '%s布置，L=%s mm → d=%s mm ≥ %s mm%s' % (
            layout_text, _fmt(length), _fmt(clearance), _fmt(required), source_note)
    else:
        detail = '%s布置，L=%s mm → d=%s mm < %s mm；建议压臂长度 ≥ %s mm（此时 d=%s mm）%s' % (
            layout_text, _fmt(length), _fmt(clearance), _fmt(required),
            _fmt(suggestion), _fmt(arm_clearance(suggestion, layout)), source_note)
    return CheckResult(
        check_id='W-ARM-01', title='压臂最小间距',
        status='pass' if passed else 'fail', detail=detail,
        value=clearance, limit=required, unit='mm',
        simulated=True, rule_id='R-GAP-01', stage=3,
    )


def _p02_check(project: FixtureProject) -> CheckResult:
    """W-P02-01：P02 辅助点偏移必须落在它自己的可调范围内。

    这条规则存在的前提是 set_param 允许把越界值写进来（写进来才查得到）。
    如果写入口就拦住，规则永远跑不到，四分结论里就少了一整档真实情况。
    """
    point = find_point(project, 'P02')
    if point is None:
        return CheckResult(
            check_id='W-P02-01', title='P02 辅助点偏移范围', status='fail',
            detail='未定义 P02 辅助定位点', value=0.0, limit=0.0, unit='mm',
            simulated=True, rule_id='R-OFF-01', stage=1,
        )
    low, high = point.offset_range
    offset = float(point.offset)
    passed = low - 1e-9 <= offset <= high + 1e-9
    if passed:
        detail = '偏移 %s mm 在可调范围 [%s, %s] mm 内' % (
            _fmt(offset), _fmt(low), _fmt(high))
    else:
        detail = '偏移 %s mm 超出可调范围 [%s, %s] mm' % (
            _fmt(offset), _fmt(low), _fmt(high))
    return CheckResult(
        check_id='W-P02-01', title='P02 辅助点偏移范围',
        status='pass' if passed else 'fail', detail=detail,
        value=offset, limit=high, unit='mm',
        simulated=True, rule_id='R-OFF-01', stage=1,
    )


def _parts_check(project: FixtureProject) -> CheckResult:
    """W-PART-01：部件齐全 + 关键尺寸 > 0。

    关键尺寸表只列「缺了就没法建模」的那几个；screw 这类标准件只要求存在且型号非空，
    硬给它编一个直径反而会造出一个没人维护的假参数。
    """
    key_dims = {
        'base_plate': ('length', 'width', 'thickness'),
        'support': ('height', 'plate_thickness'),
        'contact_block': ('length', 'width', 'height'),
        'clamp_arm': ('length',),
        'cylinder': ('stroke_deg',),
        'pin': ('diameter', 'length'),
    }
    missing = []
    bad_dims = []
    for key, label in REQUIRED_PARTS:
        part = find_part(project, key)
        if part is None:
            missing.append('%s（%s）' % (label, key))
            continue
        for dim in key_dims.get(key, ()):
            if not positive_dim(part.params.get(dim)):
                bad_dims.append('%s.%s' % (key, dim))

    problems = []
    if missing:
        problems.append('缺少部件：' + '、'.join(missing))
    if bad_dims:
        problems.append('关键尺寸未设置或为 0：' + '、'.join(bad_dims))

    total = len(REQUIRED_PARTS)
    status = 'fail' if problems else 'pass'
    detail = '；'.join(problems) if problems else '%d 个部件齐全，关键尺寸均 > 0' % total
    return CheckResult(
        check_id='W-PART-01', title='单元部件齐全性', status=status, detail=detail,
        value=float(total - len(missing)), limit=float(total), unit='个',
        simulated=True, stage=4,
    )


def _assy_check(project: FixtureProject) -> CheckResult:
    """W-ASSY-01：压臂转动副 + 气缸移动副。

    缺关系时给 not_run 而不是 fail：还没建立装配关系是「还没做」，
    不是「做错了」。这两件事在评审表上必须能分开读。
    """
    revolute = any(j.kind == 'revolute' and 'clamp_arm' in (j.a, j.b) for j in project.joints)
    prismatic = any(j.kind == 'prismatic' and 'cylinder' in (j.a, j.b) for j in project.joints)

    missing = []
    if not revolute:
        missing.append('缺少压臂转动副（revolute）')
    if not prismatic:
        missing.append('缺少气缸移动副（prismatic）')

    if missing:
        detail = '；'.join(missing) + '，装配关系尚未建立'
        status = 'not_run'
    else:
        detail = '压臂转动副 + 气缸移动副均已建立，行程 %s°' % _fmt(
            _max_travel(project, 'prismatic'))
        status = 'pass'
    return CheckResult(
        check_id='W-ASSY-01', title='装配关系与机构联动', status=status, detail=detail,
        value=float(len(project.joints)), limit=2.0, unit='条',
        simulated=True, stage=4,
    )


def _max_travel(project: FixtureProject, kind: str) -> float:
    travels = [float(j.travel) for j in project.joints if j.kind == kind]
    return max(travels) if travels else 0.0


def _motion_check(project: FixtureProject) -> CheckResult:
    """W-MOTION-01：完整运动轨迹——**恒为 not_run**（红线，不许报通过）。

    本层根本没有运动学求解器，干涉检查也没做。所以不管你给多少部件、
    不管装配关系多完整，这条都只能是「未运行」。改成 pass 就等于在设计规范里
    声称做过一套没做过的仿真。
    """
    return CheckResult(
        check_id='W-MOTION-01', title='完整运动轨迹检查', status='not_run',
        detail='本版本未接入运动仿真：无轨迹求逆、无干涉检查，结论按契约恒为 not_run',
        value=0.0, limit=0.0, unit='-', simulated=True, stage=4,
    )


def _manuf_check(project: FixtureProject) -> CheckResult:
    """W-MANUF-01：制造与强度校核——**恒为 not_applicable**（红线）。

    「不适用」和「未运行」是两回事：本工作流不承担强度/工艺校核，
    硬给它一个 not_run 会让人以为「跑一下就会通过」，那是更危险的误导。
    """
    return CheckResult(
        check_id='W-MANUF-01', title='制造与强度校核', status='not_applicable',
        detail='制造与强度校核不在本工作流范围内，恒为 not_applicable，需另行送校',
        value=0.0, limit=0.0, unit='-', simulated=True, stage=5,
    )


def evaluate(project: FixtureProject) -> list:
    """重算全部检查结论。顺序固定，UI 表头不用二次排序。

    恒返回 8 条（契约 §4 全表）。其中 W-MOTION-01 与 W-MANUF-01 不读项目数据，
    是常量结论——它们表达的是能力边界，不是方案好坏。
    """
    return [
        _inputs_check(project),
        _p01_check(project),
        _arm_check(project),
        _p02_check(project),
        _parts_check(project),
        _assy_check(project),
        _motion_check(project),
        _manuf_check(project),
    ]
