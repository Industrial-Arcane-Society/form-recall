"""校核输出 —— 单元记录 JSON 与人读摘要 Markdown。

设计意图：

1. **记录要能被别人复核**：`unit_record()` 把事实字段、四分结论、事件流、参数
   全塞进一个 JSON，评审的人不用问「这个数是怎么来的」。
2. **摘要不美化**：pass 之外的三类结论照样占版面，失败项带建议值。
   把 `not_run` 写成「待定」、把 `not_applicable` 从表里删掉，都是在骗读报告的人。
3. **事实字段原样输出**：`simulated / cad_connected / model_api_called /
   real_cad_files_created` 只从 project 上读，本文件不提供任何改写入口。
4. 导出会追一条 `export` 事件（契约 §2 的 action 之一），便于回答「这份文件是哪一版导出的」。
   事件在文件内容生成之后才追加，所以文件里记录的是导出**前**的完整历史。
5. 依赖方向：report -> stages -> rules -> models。contract 里说的「report 依赖 models」
   指的是不依赖 UI / 真实 CAD，这里并不引入任何外部包。
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import FACT_KEYS, FixtureProject, TraceEvent, now_iso
from .stages import STAGE_NAMES, stage_states

SCHEMA = 'fixture-unit/1'

_COUNT_KEYS = ('pass', 'fail', 'not_run', 'not_applicable')

_STATUS_TEXT = {
    'pass': '通过',
    'fail': '未通过',
    'not_run': '未运行',
    'not_applicable': '不适用',
}


def _counts(checks: list) -> dict:
    """四分统计。四个键永远都在（哪怕是 0），这样下游取数不用判空。"""
    counts = {key: 0 for key in _COUNT_KEYS}
    for check in checks:
        if check.status in counts:
            counts[check.status] += 1
    return counts


def _checks_by_status(checks: list, status: str) -> list:
    return [check for check in checks if check.status == status]


def unit_record(project: FixtureProject) -> dict:
    """完整、可复核的单元记录。键名即 JSON 键名，不做二次映射。"""
    checks = list(project.checks)
    record = {
        'schema': SCHEMA,
        'generated_at': now_iso(),
        'unit_id': project.unit_id,
        'unit_name': project.unit_name,
        'case_note': project.case_note,
        'revision': int(project.revision),
        'stage': int(project.stage),
        'stage_name': STAGE_NAMES.get(int(project.stage), ''),
        # 四个事实字段原样输出（契约 §6），只读语义。
        'facts': project.facts(),
        'stages': stage_states(project),
        'inputs': [item.to_dict() for item in project.inputs],
        'points': [point.to_dict() for point in project.points],
        'rules': [rule.to_dict() for rule in project.rules],
        'parts': [part.to_dict() for part in project.parts],
        'joints': [joint.to_dict() for joint in project.joints],
        'checks': [check.to_dict() for check in checks],
        'counts': _counts(checks),
        'history': [event.to_dict() for event in project.history],
        # 能力边界写进记录本身：读报告的人不必再问「运动仿真跑了吗」。
        'limits': {
            'motion_simulation': False,
            'manufacturing_check': False,
            'real_cad_files_created': bool(project.real_cad_files_created),
        },
    }
    return record


def summary_markdown(project: FixtureProject) -> str:
    """人读摘要：五阶段状态 + 四分统计 + 需要看的结论 + 事实声明。"""
    checks = list(project.checks)
    counts = _counts(checks)
    states = stage_states(project)
    facts = project.facts()

    lines = []
    lines.append('# %s（%s）· 方案校核摘要' % (project.unit_name, project.unit_id))
    lines.append('')
    lines.append('- 生成时间：%s' % now_iso())
    lines.append('- 数据版本：revision %d · 当前阶段：%d %s'
                 % (project.revision, project.stage,
                    STAGE_NAMES.get(project.stage, '')))
    if project.case_note:
        lines.append('- 案例：%s' % project.case_note)
    lines.append('')

    lines.append('## 一、五阶段状态')
    lines.append('')
    lines.append('| 阶段 | 名称 | 状态 |')
    lines.append('| --- | --- | --- |')
    for state in states:
        lines.append('| %d | %s | %s |' % (
            state['index'], state['name'],
            '已完成' if state['committed'] else '未完成'))
    lines.append('')

    lines.append('## 二、检查四分统计')
    lines.append('')
    lines.append('| 结论 | 条数 |')
    lines.append('| --- | --- |')
    for key in _COUNT_KEYS:
        lines.append('| %s（%s） | %d |' % (key, _STATUS_TEXT[key], counts[key]))
    lines.append('| 合计 | %d |' % len(checks))
    lines.append('')
    if not checks:
        lines.append('> 尚未运行校核：检查结论为空，不代表方案合格。')
        lines.append('')

    lines.append('## 三、需要看的结论')
    lines.append('')
    for status in ('fail', 'not_run', 'not_applicable'):
        picked = _checks_by_status(checks, status)
        lines.append('### %s（%s）' % (_STATUS_TEXT[status], status))
        lines.append('')
        if not picked:
            lines.append('- 无')
        else:
            for check in picked:
                lines.append('- %s %s：%s' % (check.check_id, check.title, check.detail))
        lines.append('')

    lines.append('## 四、事实声明')
    lines.append('')
    lines.append('- simulated（模拟建模）：%s' % _yes_no(facts['simulated']))
    lines.append('- cad_connected（已连真实 CATIA）：%s' % _yes_no(facts['cad_connected']))
    lines.append('- model_api_called（已调用模型 API）：%s' % _yes_no(facts['model_api_called']))
    lines.append('- real_cad_files_created（已生成真实 CAD 文件）：%s'
                 % _yes_no(facts['real_cad_files_created']))
    lines.append('')
    lines.append('> 以上四项由程序写入，界面上任何勾选都改不动它们。')
    lines.append('')

    lines.append('## 五、可追溯事件（最近 10 条）')
    lines.append('')
    if not project.history:
        lines.append('- 无')
    else:
        for event in project.history[-10:]:
            lines.append('- %s · 阶段 %d · %s · %s'
                         % (event.ts, event.stage, event.action, event.detail))
    lines.append('')
    lines.append('> 本摘要自动生成。「未运行 / 不适用」两类结论不得改写为通过：')
    lines.append('> 前者是没做的事，后者不是本工作流的范围。')
    return '\n'.join(lines) + '\n'


def _yes_no(value: bool) -> str:
    return '是' if value else '否'


def _write(path, text: str) -> Path:
    """统一出口：UTF-8、带 BOM 不写（BOM 会让 json 解析器在部分工具里失败）、父目录自动建。"""
    target = Path(path)
    if str(target.parent) not in ('', '.'):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return target


def _log_export(project: FixtureProject, detail: str) -> None:
    """导出也算一次可追溯动作；事件在文件内容生成之后追加，不进本次文件。"""
    project.history.append(TraceEvent(
        ts=now_iso(), stage=int(project.stage), action='export',
        detail=detail, revision=int(project.revision),
    ))


def export_unit_record(project: FixtureProject, path) -> Path:
    """把单元记录写成 JSON，返回实际落盘路径。"""
    payload = unit_record(project)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    target = _write(path, text + '\n')
    _log_export(project, '导出单元记录：%s' % target.name)
    return target


def export_summary(project: FixtureProject, path) -> Path:
    """把摘要写成 Markdown，返回实际落盘路径。"""
    text = summary_markdown(project)
    target = _write(path, text)
    _log_export(project, '导出方案摘要：%s' % target.name)
    return target


__all__ = [
    'FACT_KEYS',
    'SCHEMA',
    'export_summary',
    'export_unit_record',
    'summary_markdown',
    'unit_record',
]
