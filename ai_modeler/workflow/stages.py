"""五阶段状态机与失效传播。

设计意图：

1. **前置不满足不进下一步**：每个阶段都有显式门禁 `can_enter()`，
   并且缺项说明必须能直接抄进评审记录（「缺少必需资料：工件数模」这种），
   不是一句「条件不满足」。
2. **提交记事件，不记布尔量**：阶段是否完成从 `history` 里推，
   这样「参数改了 → 下游复核勾选作废」只需要追加一个 invalidate 事件，
   历史本身不被篡改（可追溯的证据链不能被 reset 以外的操作抹掉）。
3. **参数一变，下游结论全部失效**：`invalidate_from()` 只把结论打成 not_run，
   不重算。重算是规则层的事，混在一起做会让人分不清「结论过期」和「重新算过」。
4. **红线**：失效传播不碰 `not_applicable`。W-MANUF-01 是「不适用」，
   把它打成 not_run 等于暗示「这个以后会跑」——设计规范里不能这么写。
5. 本文件不 import tkinter / CATIA，UI 只是消费者。
"""
from __future__ import annotations

import math

from .models import (
    FixtureProject,
    TraceEvent,
    REQUIRED_PARTS,
    find_part,
    find_point,
    now_iso,
    positive_dim,
)
from .rules import evaluate

# 五阶段定义（契约 §3）：(编号, 名称, 副标题)
STAGES = [
    (1, '工程输入', '工件数模与定位基准'),
    (2, '单元规划', '结构布局与任务分解'),
    (3, '参数建模', '机加工件与接口构建'),
    (4, '关联装配', '标准件配置与机构联动'),
    (5, '校核输出', '几何记录与方案复核'),
]

STAGE_NAMES = {index: name for index, name, _subtitle in STAGES}

# 每阶段「交出去的东西」。UI 左侧步骤列表、报告摘要都从这里取，
# 避免界面上一句、报告里一句，两边对不上。
_STAGE_OUTPUTS = {
    1: ['工件数模（已确认）', '定位基准 P01（锁定）', '定位夹紧点 PRP', '工程规则表'],
    2: ['结构布局方案', '单元任务分解', '压臂布置方式与长度初值'],
    3: ['机加工件参数（基板 / 支撑座 / 接触块 / 压臂）', 'P01 锁定复查记录'],
    4: ['装配关系（转动副 / 移动副）', '标准件配置（气缸 / 定位销 / 螺钉）'],
    5: ['检查记录（四分结论）', '单元记录 JSON', '方案摘要 Markdown'],
}

# 部件属于哪个阶段改的。失效传播据此决定「从哪里开始作废」：
# 机加工件（3）变了，装配与校核（4、5）必须一起作废；标准件（4）变了，只作废 4、5。
PART_STAGE = {
    'base_plate': 3,
    'support': 3,
    'contact_block': 3,
    'clamp_arm': 3,
    'cylinder': 4,
    'pin': 4,
    'screw': 4,
}

# 定位点属于第 1 阶段：基准一变，后面全都要重来。
POINT_STAGE = 1

# 失效传播时保留原样的结论：这些表达的是能力边界，不是方案数据的结果。
_PROTECTED_STATUSES = ('not_applicable',)


class WorkflowError(Exception):
    """阶段门禁未通过。missing 里是可以直接贴进评审记录的具体缺项。"""

    def __init__(self, message: str, missing: list):
        self.message = message
        self.missing = list(missing)
        super().__init__(self._text())

    def _text(self) -> str:
        if not self.missing:
            return self.message
        return '%s：%s' % (self.message, '；'.join(self.missing))

    def __str__(self) -> str:
        return self._text()


def stage_meta(stage: int) -> dict:
    """取一个阶段的元信息。编号越界直接报错——静默返回空字典会让界面画出一片空白。"""
    for index, name, subtitle in STAGES:
        if index == stage:
            return {
                'index': index,
                'name': name,
                'subtitle': subtitle,
                'outputs': list(_STAGE_OUTPUTS.get(index, [])),
            }
    raise ValueError('阶段编号必须在 1..5 之间：%r' % (stage,))


def stage_of_part(part_key: str) -> int:
    """部件归属阶段。未知部件按第 3 阶段算：
    宁可多作废下游，也不要留一条没人负责的过期结论。
    """
    return PART_STAGE.get(str(part_key), 3)


def stage_committed(project: FixtureProject, stage: int) -> bool:
    """从事件流推出「某阶段是否算已完成」。

    判定规则：commit(s) 记完成，invalidate(N) 冲掉所有 N 及之后阶段的完成标记
    （invalidate 的含义就是「从这里开始的工作要重做」）。
    按时间顺序扫一遍，最后落到的状态就是答案。
    """
    committed = False
    for event in project.history:
        if event.action == 'commit' and event.stage == stage:
            committed = True
        elif event.action == 'invalidate' and event.stage <= stage:
            committed = False
    return committed


def stage_states(project: FixtureProject) -> list:
    """五阶段的完成状态，供报告与 UI 复用（避免两处各写一遍推演逻辑）。"""
    return [
        {'index': index, 'name': name, 'committed': stage_committed(project, index)}
        for index, name, _subtitle in STAGES
    ]


def invalidate_from(project: FixtureProject, stage: int) -> int:
    """把 stage 及之后阶段的检查结论作废，并撤回相应阶段的完成标记。

    返回**真正发生变化**的条数（本来就是 not_run 的不重复计数）——
    这个数字要拿去写日志说话，虚高就等于谎报工作量。

    revision 不变：版本号只在参数真的改了时 +1（契约 §3 的明确分工）。
    """
    stage = int(stage)
    if stage < 1 or stage > 5:
        raise ValueError('阶段编号必须在 1..5 之间：%r' % (stage,))

    changed = 0
    for check in project.checks:
        if check.stage < stage:
            continue
        if check.status in _PROTECTED_STATUSES:
            # 红线：not_applicable 是「不适用」，不能被改写成「未运行」。
            continue
        if check.status != 'not_run':
            check.status = 'not_run'
            # 保留 value / limit（复核时还想知道上一次算到多少），只换说明。
            check.detail = '已作废：第 %d 阶段（%s）参数或前置条件变更' % (
                stage, STAGE_NAMES[stage])
            changed += 1

    if project.stage >= stage:
        # 状态要自洽：当前阶段不能停在已被作废的结论之后。
        project.stage = stage

    project.history.append(TraceEvent(
        ts=now_iso(), stage=stage, action='invalidate',
        detail='自第 %d 阶段（%s）起作废 %d 条检查结论' % (stage, STAGE_NAMES[stage], changed),
        revision=project.revision,
    ))
    return changed


def blocking_failures(project: FixtureProject) -> list:
    """返回阻断「进入复核」的未通过项。

    设计规范 5.2：默认任务在检查步骤暂停、禁止进入复核；6.2：接受修正后才继续。
    所以复核门槛不能只看某一条规则——任何 block 级未通过都算。
    规则表里查不到的检查项（资料缺失、部件缺失这类）一律按 block 处理：
    宁可多拦一道，也不能让有未通过项的方案进复核。
    """
    severity = {rule.rule_id: rule.severity for rule in project.rules}
    blocked = []
    for check in evaluate(project):
        if check.status != 'fail':
            continue
        if severity.get(check.rule_id, 'block') == 'warn':
            continue
        blocked.append(check)
    return blocked


class WorkflowEngine:
    """五步向导的状态机。所有改动都走这里，UI 不直接改 project 的字段。"""

    def __init__(self, project: FixtureProject):
        self._project = project if project is not None else FixtureProject()

    @property
    def project(self) -> FixtureProject:
        return self._project

    # ------------------------------------------------------------------
    # 门禁
    # ------------------------------------------------------------------
    def can_enter(self, stage: int) -> tuple:
        """(是否可进入, 缺项说明列表)。

        stage=1 永远可进：它是起点，没有前置。
        其余阶段要求「上一阶段已完成」+ 本阶段自己的数据前提。
        """
        stage = int(stage)
        if stage < 1 or stage > 5:
            raise ValueError('阶段编号必须在 1..5 之间：%r' % (stage,))
        project = self._project
        if stage == 1:
            return True, []

        missing = []
        prev = stage - 1
        if not stage_committed(project, prev):
            missing.append('上一阶段（%d %s）尚未完成' % (prev, STAGE_NAMES[prev]))

        if stage == 2:
            for item in project.inputs:
                if not item.required:
                    continue
                if not item.path:
                    missing.append('缺少必需资料：%s' % item.label)
                elif not item.loaded:
                    missing.append('资料未确认可用：%s' % item.label)
            if not any(point.role == 'primary' for point in project.points):
                missing.append('缺少主定位点 P01')
        elif stage == 3:
            p01 = find_point(project, 'P01')
            if p01 is None:
                missing.append('缺少 P01 固定定位点')
            else:
                if not p01.offset_locked:
                    missing.append('P01 偏移必须锁定为 0')
                if abs(float(p01.offset)) > 1e-9:
                    missing.append('P01 偏移必须为 0 mm，当前 %s mm' % _fmt(p01.offset))
        elif stage == 4:
            arm = find_part(project, 'clamp_arm')
            if arm is None:
                missing.append('缺少压臂部件（clamp_arm）')
            elif not positive_dim(arm.params.get('length')):
                missing.append('压臂长度未设置')
            support = find_part(project, 'support')
            if support is None:
                missing.append('缺少支撑座部件（support）')
            elif not positive_dim(support.params.get('height')):
                missing.append('支撑座高度未设置')
            block = find_part(project, 'contact_block')
            if block is None:
                missing.append('缺少接触块部件（contact_block）')
            elif not positive_dim(block.params.get('height')):
                missing.append('接触块高度未设置')
        elif stage == 5:
            joints = project.joints
            if not any(j.kind == 'revolute' and 'clamp_arm' in (j.a, j.b) for j in joints):
                missing.append('缺少压臂转动副（revolute）')
            if not any(j.kind == 'prismatic' and 'cylinder' in (j.a, j.b) for j in joints):
                missing.append('缺少气缸移动副（prismatic）')
            absent = [label for key, label in REQUIRED_PARTS
                      if find_part(project, key) is None]
            if absent:
                missing.append('缺少部件：' + '、'.join(absent))
            # 有未通过的检查就不能进复核（设计规范 5.2 / 6.2 的硬要求）。
            # 这一步放在最后：先报"缺什么"，再报"哪条没过"，缺项顺序才对得上。
            for failed in blocking_failures(project):
                missing.append('检查未通过：%s（%s）' % (failed.title, failed.detail))
        return (not missing), missing

    def enter(self, stage: int) -> None:
        """进入某阶段：门禁不过就抛错，过了才记事件并落 stage。"""
        stage = int(stage)
        ok, missing = self.can_enter(stage)
        if not ok:
            raise WorkflowError('第 %d 阶段（%s）前置条件未满足'
                                % (stage, STAGE_NAMES[stage]), missing)
        self._log('enter', stage, '进入第 %d 阶段（%s）' % (stage, STAGE_NAMES[stage]))
        self._project.stage = stage

    def commit(self, stage: int) -> None:
        """完成某阶段。

        只允许提交当前阶段——「跳过第 2 步直接交第 3 步」在评审上没有依据，
        所以顺序提交是硬规则，不是建议。
        提交第 5 阶段时顺带重算全部检查：校核输出阶段的意义就是把结论算出来。
        """
        stage = int(stage)
        current = self._project.stage
        if stage != current:
            raise WorkflowError(
                '只能顺序提交：当前处于第 %d 阶段（%s），不能提交第 %d 阶段'
                % (current, STAGE_NAMES.get(current, '?'), stage),
                ['请先完成第 %d 阶段' % current],
            )
        ok, missing = self.can_enter(stage)
        if not ok:
            raise WorkflowError('第 %d 阶段（%s）前置条件未满足'
                                % (stage, STAGE_NAMES[stage]), missing)
        self._log('commit', stage, '提交第 %d 阶段（%s）输出' % (stage, STAGE_NAMES[stage]))
        if stage < 5:
            self._project.stage = stage + 1
        else:
            self._project.checks = evaluate(self._project)

    # ------------------------------------------------------------------
    # 参数
    # ------------------------------------------------------------------
    def set_param(self, part_key: str, name: str, value: float) -> list:
        """改一个部件参数（或一个定位点的 offset），返回被作废的检查项标题。

        几条硬规则，每条都有现场理由：
        - P01 的 offset 谁都不能改（固定基准，契约 §0.2）；
        - 文本参数（material / layout / spec）不能按数值改，否则提示词拼接会拿到 20.0；
        - 负数 / NaN / 非数字直接拒；**越界的 offset 允许写进来**，
          交给 W-P02-01 如实报 fail——写入口全拦住，规则就永远跑不到；
        - 值没变就不算一次变更：不 +1 revision，也不制造一场假失效。
        """
        project = self._project
        pid = _point_id(part_key)

        if pid:
            point = find_point(project, pid)
            if point is None:
                raise KeyError('未知定位点：%s' % part_key)
            if str(name) != 'offset':
                raise ValueError('定位点 %s 只有 offset 可调，不是 %r' % (pid, name))
            if pid == 'P01' or point.offset_locked:
                raise ValueError(
                    '%s 是固定定位基准，偏移量锁定为 0 mm，任何阶段都不得修改' % pid)
            new_value = _numeric(name, value)
            if abs(point.offset - new_value) < 1e-9:
                return []
            old_value = point.offset
            point.offset = new_value
            project.revision += 1
            self._log('param_change', POINT_STAGE,
                      '%s.offset %s → %s mm' % (pid, _fmt(old_value), _fmt(new_value)))
            return self._invalidate_and_titles(POINT_STAGE)

        part = find_part(project, str(part_key))
        if part is None:
            raise KeyError('未知部件：%s' % part_key)
        if name not in part.params:
            raise ValueError('部件 %s 没有参数 %r' % (part.key, name))
        current = part.params[name]
        if isinstance(current, str) or isinstance(current, bool):
            raise ValueError('参数 %s.%s 是文本参数，不能按数值修改' % (part.key, name))
        new_value = _numeric(name, value)
        if abs(float(current) - new_value) < 1e-9:
            return []
        stage = stage_of_part(part.key)
        old_value = current
        part.params[name] = new_value
        project.revision += 1
        self._log('param_change', stage, '%s.%s %s → %s' % (
            part.key, name, _fmt(old_value), _fmt(new_value)))
        return self._invalidate_and_titles(stage)

    def invalidate_from(self, stage: int) -> int:
        """作废第 stage 及之后阶段的检查结论（委托给模块级函数，供 UI 直接调用）。"""
        return invalidate_from(self._project, stage)

    def run_checks(self) -> list:
        """重算并写回检查结论。UI 在参数修正后要显式调它——
        参数改动本身只负责「作废」，不负责「重算」。"""
        self._project.checks = evaluate(self._project)
        return self._project.checks

    def reset(self) -> None:
        """回到第 1 阶段，清空派生状态。

        保留用户填的资料与参数：清空「已确认的数模路径」这种手工输入，
        只会逼人重新填一遍，还容易漏填。
        """
        project = self._project
        project.stage = 1
        project.checks = []
        project.history = []
        project.revision += 1

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _invalidate_and_titles(self, stage: int) -> list:
        titles = [check.title for check in self._project.checks
                  if check.stage >= stage and check.status not in ('not_run', 'not_applicable')]
        invalidate_from(self._project, stage)
        return titles

    def _log(self, action: str, stage: int, detail: str) -> None:
        self._project.history.append(TraceEvent(
            ts=now_iso(), stage=int(stage), action=action, detail=detail,
            revision=self._project.revision,
        ))


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _fmt(value: float) -> str:
    return ('%.2f' % float(value)).rstrip('0').rstrip('.')


def _numeric(name: str, value) -> float:
    """把输入收成有限非负 float。bool 单独挡：True 会被当成 1.0 静默写进尺寸。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('参数 %s 的值必须是数字：%r' % (name, value))
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('参数 %s 的值必须是有限数值：%r' % (name, value))
    if number < 0.0:
        raise ValueError('参数 %s 不能为负：%r' % (name, value))
    return number


def _point_id(part_key: str) -> str:
    """识别「定位点」寻址：'P01' / 'point:P01' / 'prp:P01'。

    为什么允许三种写法：UI 的表格行、命令行脚本、导入的历史数据各用各的，
    在入口统一收，比在每个调用点各写一遍判断可靠。
    """
    text = str(part_key or '').strip()
    low = text.lower()
    for prefix in ('point:', 'prp:', 'pt:'):
        if low.startswith(prefix):
            return text[len(prefix):].strip().upper()
    if low.startswith('p') and low[1:].isdigit():
        return low.upper()
    return ''
