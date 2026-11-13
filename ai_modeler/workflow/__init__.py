"""夹具单元协同设计 · 五步 Workflow 领域层。

对外只暴露这一个入口（`from ai_modeler.workflow import ...`），UI 与主程序不要
绕过它去 import 子模块——子模块的划分是内部事，今天写 models/stages，
明天可能拆成 models/state，界面不该跟着改行号。

层次（依赖方向单一，故可整层单测，不需要 tkinter / CATIA）：
    report  ->  stages  ->  rules  ->  models
"""
from __future__ import annotations

from .models import (
    ARM_DATUM,
    ARM_SIDE_BONUS,
    FACT_KEYS,
    REQUIRED_PARTS,
    CheckResult,
    FixtureProject,
    InputFile,
    JointSpec,
    PartSpec,
    PrpPoint,
    RuleSpec,
    TraceEvent,
    build_demo_project,
    find_part,
    find_point,
    find_rule,
    is_number,
    now_iso,
    positive_dim,
)
from .report import (
    SCHEMA,
    export_summary,
    export_unit_record,
    summary_markdown,
    unit_record,
)
from .rules import (
    DEFAULT_ARM_REQUIRED,
    arm_clearance,
    evaluate,
    suggest_arm_length,
)
from .stages import (
    PART_STAGE,
    POINT_STAGE,
    STAGE_NAMES,
    STAGES,
    WorkflowEngine,
    WorkflowError,
    invalidate_from,
    stage_committed,
    stage_meta,
    stage_of_part,
    stage_states,
)

__all__ = [
    # 数据模型
    'FACT_KEYS', 'REQUIRED_PARTS', 'ARM_DATUM', 'ARM_SIDE_BONUS',
    'InputFile', 'PrpPoint', 'RuleSpec', 'PartSpec', 'JointSpec',
    'CheckResult', 'TraceEvent', 'FixtureProject', 'build_demo_project',
    'find_part', 'find_point', 'find_rule', 'is_number', 'positive_dim',
    'now_iso',
    # 状态机
    'STAGES', 'STAGE_NAMES', 'PART_STAGE', 'POINT_STAGE',
    'stage_meta', 'stage_committed', 'stage_states', 'stage_of_part',
    'invalidate_from', 'WorkflowEngine', 'WorkflowError',
    # 工程规则
    'DEFAULT_ARM_REQUIRED', 'evaluate', 'arm_clearance', 'suggest_arm_length',
    # 校核输出
    'SCHEMA', 'unit_record', 'summary_markdown', 'export_unit_record',
    'export_summary',
]
