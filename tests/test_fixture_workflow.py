"""夹具单元协同设计 · 领域层单测。

覆盖契约 §8 的验收点：
  - 状态机阻断（前置不满足不进下一步、不能跳步提交）
  - P01 锁定（任何写法都改不动 offset）
  - 参数失效（revision +1，下游结论变 not_run）
  - 四分统计（pass / fail / not_run / not_applicable 各有一份真实样本）
  - 序列化往返（to_dict -> json -> from_dict -> to_dict 逐字节一致）

外加两条红线：W-MOTION-01 恒 not_run、W-MANUF-01 恒 not_applicable。
这两条不管项目数据多干净、不管参数怎么改，都不许变成 pass——
它们在研制报告里代表的是「没做的事」，不是「做得好的事」。
"""
import json
from collections import Counter

import pytest

from ai_modeler.workflow import (
    FACT_KEYS,
    CheckResult,
    FixtureProject,
    InputFile,
    PrpPoint,
    TraceEvent,
    WorkflowEngine,
    WorkflowError,
    arm_clearance,
    build_demo_project,
    evaluate,
    export_summary,
    export_unit_record,
    find_part,
    find_point,
    invalidate_from,
    stage_committed,
    stage_meta,
    suggest_arm_length,
    summary_markdown,
    unit_record,
)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _run(project):
    """重算检查并把结果写回项目，返回 check_id -> CheckResult 的字典。"""
    project.checks = evaluate(project)
    return {check.check_id: check for check in project.checks}


def _statuses(project):
    project.checks = evaluate(project)
    return Counter(check.status for check in project.checks)


# --------------------------------------------------------------------------
# 阶段定义
# --------------------------------------------------------------------------
def test_stages_define_five_steps():
    for stage in (1, 2, 3, 4, 5):
        meta = stage_meta(stage)
        assert meta['index'] == stage
        assert meta['name']
        assert meta['subtitle']
        assert meta['outputs'], '第 %d 阶段没有输出清单，界面会画出空白' % stage
    assert [stage_meta(i)['name'] for i in range(1, 6)] == [
        '工程输入', '单元规划', '参数建模', '关联装配', '校核输出']
    with pytest.raises(ValueError):
        stage_meta(0)
    with pytest.raises(ValueError):
        stage_meta(6)


# --------------------------------------------------------------------------
# 默认案例
# --------------------------------------------------------------------------
def test_demo_project_matches_case():
    project = build_demo_project()
    assert project.unit_id == 'UN200'
    assert project.unit_name == '气动夹紧单元'
    assert project.stage == 1
    # 结论不预置：参数一改就会留下一排过期绿灯。
    assert project.checks == []

    arm = find_part(project, 'clamp_arm')
    assert arm.params['length'] == 65.0, '默认案例必须给 65，才能触发 d=3 的失败分支'
    cylinder = find_part(project, 'cylinder')
    assert cylinder.params['layout'] == 'top'
    assert cylinder.params['stroke_deg'] == 65.0

    p01 = find_point(project, 'P01')
    assert p01.offset == 0.0 and p01.offset_locked is True
    assert p01.offset_range == (0.0, 0.0)
    p02 = find_point(project, 'P02')
    assert p02.offset == 2.0
    assert p02.offset_range == (0.0, 4.0)

    assert len(project.parts) == 7
    for key, label in (('base_plate', '基板'), ('support', '支撑座'),
                       ('contact_block', '接触块'), ('clamp_arm', '压臂'),
                       ('cylinder', '气缸'), ('pin', '定位销'), ('screw', '内六角螺钉')):
        part = find_part(project, key)
        assert part is not None, '缺少默认部件 %s' % key
        assert part.name == label
    # 主程序按这些 key 拼提示词，改名会静默降级成一句「气动夹紧单元」。
    assert {'length', 'width', 'thickness', 'material'} <= set(
        find_part(project, 'base_plate').params)
    assert {'height', 'plate_thickness'} <= set(find_part(project, 'support').params)
    assert {'length', 'width', 'height'} <= set(
        find_part(project, 'contact_block').params)
    assert {'diameter', 'length'} <= set(find_part(project, 'pin').params)
    assert find_part(project, 'screw').params['spec'] == 'M8x20'
    assert 'length' in find_part(project, 'clamp_arm').params
    assert {'stroke_deg', 'bore', 'layout'} <= set(find_part(project, 'cylinder').params)

    # 四个必需资料都要已确认，否则演示连第 2 步都走不进去。
    assert [item.key for item in project.inputs] == ['workpiece', 'prp', 'rules', 'std_parts']
    assert all(item.loaded and item.path for item in project.inputs if item.required)

    # 事实字段默认：全模拟、未连 CAD。
    assert project.facts() == {
        'simulated': True,
        'cad_connected': False,
        'model_api_called': False,
        'real_cad_files_created': False,
    }


# --------------------------------------------------------------------------
# 演示公式与建议值
# --------------------------------------------------------------------------
def test_arm_clearance_formula():
    assert arm_clearance(65, 'top') == 3.0
    assert arm_clearance(65, 'side') == 7.0
    assert arm_clearance(65.0, '上侧') == 3.0
    with pytest.raises(ValueError):
        arm_clearance(65, '斜上')


def test_suggest_arm_length_meets_requirement():
    suggested = suggest_arm_length('top', 5)
    assert suggested >= 75, '建议值要留装配余量，不能只给理论下限 67'
    assert arm_clearance(suggested, 'top') == 13.0
    assert arm_clearance(suggested, 'top') >= 5

    side_suggested = suggest_arm_length('side', 5)
    assert side_suggested >= 67
    assert arm_clearance(side_suggested, 'side') >= 5


# --------------------------------------------------------------------------
# 四分统计
# --------------------------------------------------------------------------
def test_evaluate_returns_eight_checks_in_fixed_order():
    project = build_demo_project()
    checks = evaluate(project)
    assert len(checks) == 8
    assert [check.check_id for check in checks] == [
        'W-INPUT-01', 'W-P01-01', 'W-ARM-01', 'W-P02-01',
        'W-PART-01', 'W-ASSY-01', 'W-MOTION-01', 'W-MANUF-01',
    ]
    # 同一份数据重算两遍结果一致（界面刷新不该让结论跳变）。
    assert [c.to_dict() for c in evaluate(project)] == [c.to_dict() for c in evaluate(project)]


def test_demo_counts_split_into_four_statuses():
    project = build_demo_project()
    counts = _statuses(project)
    assert counts['pass'] == 5
    assert counts['fail'] == 1, '默认案例应只有压臂间距一条失败'
    assert counts['not_run'] == 1
    assert counts['not_applicable'] == 1
    assert sum(counts.values()) == 8

    by_id = {check.check_id: check for check in project.checks}
    arm = by_id['W-ARM-01']
    assert arm.status == 'fail'
    assert arm.value == 3.0 and arm.limit == 5.0
    assert '75' in arm.detail, '失败时必须给出建议压臂长度'
    assert by_id['W-ASSY-01'].status == 'pass'
    # 全部结论都是「算出来的」，不是实测的。
    assert all(check.simulated is True for check in project.checks)


def test_motion_and_manufacturing_are_red_lines():
    project = build_demo_project()
    # 先把压臂间距检查过掉：研制报告 5.2 要求「有未通过项禁止进入复核」，
    # 不先修正就走不到第 5 阶段，而这条测试要验的正是走完之后的红线。
    find_part(project, 'clamp_arm').params['length'] = 75.0
    engine = WorkflowEngine(project)
    engine.run_checks()

    def status(check_id):
        return {c.check_id: c for c in evaluate(project)}[check_id].status

    assert status('W-MOTION-01') == 'not_run'
    assert status('W-MANUF-01') == 'not_applicable'

    # 走完五步、改过压臂、改过标准件之后依然如此。
    for stage in (1, 2, 3, 4, 5):
        engine.commit(stage)
    engine.set_param('clamp_arm', 'length', 75.0)
    engine.set_param('pin', 'diameter', 10.0)
    engine.run_checks()
    assert status('W-MOTION-01') == 'not_run'
    assert status('W-MANUF-01') == 'not_applicable'
    # 失效传播也不许把「不适用」改写成「未运行」。
    engine.invalidate_from(1)
    assert status('W-MANUF-01') == 'not_applicable'


def test_assembly_missing_joint_is_not_run_not_fail():
    project = build_demo_project()
    project.joints = [j for j in project.joints if j.kind != 'prismatic']
    check = _run(project)['W-ASSY-01']
    assert check.status == 'not_run', '缺关系是「还没做」，不是「做错了」'
    assert 'prismatic' in check.detail


def test_parts_check_detects_missing_part_and_zero_dim():
    project = build_demo_project()
    project.parts = [p for p in project.parts if p.key != 'pin']
    check = _run(project)['W-PART-01']
    assert check.status == 'fail'
    assert '定位销' in check.detail

    project = build_demo_project()
    find_part(project, 'base_plate').params['thickness'] = 0.0
    check = _run(project)['W-PART-01']
    assert check.status == 'fail'
    assert 'base_plate.thickness' in check.detail


def test_p01_check_fails_when_unlocked_or_offset():
    project = build_demo_project()
    find_point(project, 'P01').offset_locked = False
    assert _run(project)['W-P01-01'].status == 'fail'


def test_p02_offset_out_of_range_fails():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.run_checks()
    # 越界值允许写进来，由规则如实报 fail（写入口全拦住，规则就永远跑不到）。
    engine.set_param('P02', 'offset', 6.0)
    check = _run(project)['W-P02-01']
    assert check.status == 'fail'
    assert '[0, 4]' in check.detail
    assert project.stage == 1, '定位点属于第 1 阶段，改它要回到第 1 阶段重走'


def test_unknown_layout_reports_fail_instead_of_raising():
    project = build_demo_project()
    find_part(project, 'cylinder').params['layout'] = '斜向'
    # evaluate() 一旦会抛，一条脏数据就能把整张校核表打断。
    check = _run(project)['W-ARM-01']
    assert check.status == 'fail'
    assert '斜向' in check.detail


# --------------------------------------------------------------------------
# 状态机
# --------------------------------------------------------------------------
def test_state_machine_blocks_stage_jump():
    project = build_demo_project()
    engine = WorkflowEngine(project)

    ok, missing = engine.can_enter(3)
    assert ok is False
    assert missing, '门禁要给出具体缺项'
    assert any('尚未完成' in item for item in missing)

    with pytest.raises(WorkflowError) as info:
        engine.commit(3)
    assert info.value.missing, 'WorkflowError 必须带得出缺项清单'
    assert project.stage == 1, '校验不过不得推进阶段'

    # 顺序走完之后才放行。
    engine.commit(1)
    assert project.stage == 2
    ok, missing = engine.can_enter(2)
    assert ok is True and missing == []
    assert [event.action for event in project.history] == ['commit'], '提交要留可追溯事件'


def test_can_enter_reports_concrete_missing_items():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.commit(1)

    project.inputs[0].loaded = False
    ok, missing = engine.can_enter(2)
    assert ok is False
    assert '资料未确认可用：工件数模' in missing

    project.inputs[0].path = ''
    ok, missing = engine.can_enter(2)
    assert ok is False
    assert '缺少必需资料：工件数模' in missing

    project.inputs[0].path = 'assets/demo/un200_workpiece.step'
    project.inputs[0].loaded = True
    assert engine.can_enter(2) == (True, [])


def test_can_enter_stage3_requires_p01_locked():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.commit(1)
    engine.commit(2)
    find_point(project, 'P01').offset_locked = False
    ok, missing = engine.can_enter(3)
    assert ok is False
    assert 'P01 偏移必须锁定为 0' in missing


def test_can_enter_stage4_requires_arm_length():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    for stage in (1, 2, 3):
        engine.commit(stage)
    find_part(project, 'clamp_arm').params['length'] = 0.0
    ok, missing = engine.can_enter(4)
    assert ok is False
    assert '压臂长度未设置' in missing


def test_full_walkthrough_runs_checks_at_stage5():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    for stage in (1, 2, 3, 4):
        engine.commit(stage)
        assert project.stage == min(stage + 1, 5)

    # 研制报告 5.2：默认案例压臂间距不通过，此时**禁止进入复核**。
    # 这不是缺陷，是验收要求——所以「走完五步」必须先接受修正。
    ok, missing = engine.can_enter(5)
    assert ok is False
    assert any('压臂最小间距' in item for item in missing)

    engine.set_param('clamp_arm', 'length', 75.0)
    assert project.stage == 3, '改第 3 阶段参数要把阶段回退到第 3 阶段'
    for stage in (3, 4, 5):
        engine.commit(stage)
    assert project.stage == 5

    assert len(project.checks) == 8, '第 5 阶段提交即产出校核结论'
    assert [stage for stage in (1, 2, 3, 4, 5) if stage_committed(project, stage)] == [1, 2, 3, 4, 5]
    # 这一路走过来既有提交、也有作废，历史要能还原出发生过什么。
    actions = {e.action for e in project.history}
    assert {'commit', 'param_change', 'invalidate'} <= actions


def test_review_gate_blocks_failed_check():
    """研制报告 5.2 / 6.2 的硬要求：检查未通过不能进复核。"""
    project = build_demo_project()
    engine = WorkflowEngine(project)
    for stage in (1, 2, 3, 4):
        engine.commit(stage)

    ok, missing = engine.can_enter(5)
    assert ok is False
    assert any('W' in item or '压臂' in item for item in missing)

    # 未通过项被修掉之后，门禁必须自己放开，不需要额外开关。
    engine.set_param('clamp_arm', 'length', 75.0)
    for stage in (3, 4):
        engine.commit(stage)
    ok, missing = engine.can_enter(5)
    assert ok is True, '修正后应放行，实际缺项：%s' % missing


# --------------------------------------------------------------------------
# 参数改动与失效传播
# --------------------------------------------------------------------------
def test_p01_offset_cannot_be_changed():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    revision_before = project.revision
    for key in ('P01', 'point:P01', 'prp:P01'):
        with pytest.raises(ValueError):
            engine.set_param(key, 'offset', 1.0)
    # 就算有人先把锁定标记改成 False，P01 这个 pid 本身也不能改。
    find_point(project, 'P01').offset_locked = False
    with pytest.raises(ValueError):
        engine.set_param('P01', 'offset', 0.5)

    assert find_point(project, 'P01').offset == 0.0
    assert project.revision == revision_before
    assert project.history == []


@pytest.mark.parametrize('part_key, name, value', [
    ('clamp_arm', 'length', -1.0),          # 负尺寸
    ('clamp_arm', 'length', '75'),          # 字符串
    ('clamp_arm', 'length', float('nan')),  # NaN
    ('clamp_arm', 'length', True),          # bool 会被当成 1.0 静默写进尺寸
    ('clamp_arm', 'no_such_dim', 10.0),     # 参数名不存在
    ('cylinder', 'layout', 1.0),            # 文本参数不能按数值改
])
def test_set_param_rejects_illegal_values(part_key, name, value):
    project = build_demo_project()
    engine = WorkflowEngine(project)
    with pytest.raises(ValueError):
        engine.set_param(part_key, name, value)
    assert project.revision == 1


def test_set_param_unknown_part_raises_keyerror():
    engine = WorkflowEngine(build_demo_project())
    with pytest.raises(KeyError):
        engine.set_param('gripper', 'length', 10.0)


def test_param_change_bumps_revision_and_invalidates_downstream():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.run_checks()
    revision_before = project.revision

    titles = engine.set_param('clamp_arm', 'length', 75.0)

    assert project.revision == revision_before + 1
    assert find_part(project, 'clamp_arm').params['length'] == 75.0
    assert set(titles) == {'压臂最小间距', '单元部件齐全性', '装配关系与机构联动'}

    by_id = {check.check_id: check for check in project.checks}
    assert by_id['W-ARM-01'].status == 'not_run'
    assert by_id['W-PART-01'].status == 'not_run'
    assert by_id['W-ASSY-01'].status == 'not_run'
    # 第 1 阶段的结论不跟着失效：资料和定位基准都没动。
    assert by_id['W-INPUT-01'].status == 'pass'
    assert by_id['W-P01-01'].status == 'pass'
    # 红线项不参与失效传播。
    assert by_id['W-MANUF-01'].status == 'not_applicable'
    # 失效之后要显式重算，才算重新有了结论。
    engine.run_checks()
    assert {c.check_id: c for c in project.checks}['W-ARM-01'].status == 'pass'


def test_same_value_is_not_a_change():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.run_checks()
    revision_before = project.revision
    assert engine.set_param('clamp_arm', 'length', 65.0) == []
    assert project.revision == revision_before
    assert {c.check_id: c for c in project.checks}['W-ARM-01'].status == 'fail'


def test_fix_branch_arm_75_passes():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.run_checks()
    engine.set_param('clamp_arm', 'length', 75.0)
    engine.run_checks()

    by_id = {check.check_id: check for check in project.checks}
    assert by_id['W-ARM-01'].status == 'pass'
    assert by_id['W-ARM-01'].value == 13.0
    counts = _statuses(project)
    assert counts['fail'] == 0
    assert counts['pass'] == 6


def test_standard_part_change_invalidates_from_stage4():
    project = build_demo_project()
    # 先过复核门禁（报告要求有未通过项不许进第 5 阶段），
    # 这条测试要验的是「改标准件把第 4 阶段及之后打回」。
    find_part(project, 'clamp_arm').params['length'] = 75.0
    engine = WorkflowEngine(project)
    for stage in (1, 2, 3, 4, 5):
        engine.commit(stage)

    engine.set_param('cylinder', 'bore', 40.0)
    assert project.stage == 4, '标准件属于第 4 阶段，要回到第 4 阶段重走'
    assert stage_committed(project, 3) is True
    assert stage_committed(project, 4) is False
    assert stage_committed(project, 5) is False


def test_invalidate_from_counts_only_real_changes():
    project = build_demo_project()
    engine = WorkflowEngine(project)
    engine.run_checks()
    project.stage = 5

    assert invalidate_from(project, 3) == 3, '只有压臂间距 / 部件齐全 / 装配关系从有结论变成无结论'
    assert project.stage == 3
    assert {c.check_id: c for c in project.checks}['W-MANUF-01'].status == 'not_applicable'

    revision_before = project.revision
    assert invalidate_from(project, 3) == 0
    assert project.revision == revision_before, '作废不涨版本号'
    with pytest.raises(ValueError):
        invalidate_from(project, 6)


def test_reset_keeps_user_data_but_clears_derived_state():
    project = build_demo_project()
    # 同上：先让压臂间距过关，才走得进第 5 阶段。
    find_part(project, 'clamp_arm').params['length'] = 75.0
    engine = WorkflowEngine(project)
    for stage in (1, 2, 3, 4, 5):
        engine.commit(stage)
    engine.set_param('clamp_arm', 'length', 80.0)
    assert project.checks and project.history

    engine.reset()
    assert project.stage == 1
    assert project.checks == []
    assert project.history == []
    # 手工填的参数不该被 reset 清掉。
    assert find_part(project, 'clamp_arm').params['length'] == 80.0
    assert find_point(project, 'P02').offset == 2.0


# --------------------------------------------------------------------------
# 序列化往返
# --------------------------------------------------------------------------
def test_project_serialization_roundtrip():
    project = build_demo_project()
    project.checks = evaluate(project)
    project.history.append(TraceEvent('2026-01-01T00:00:00', 1, 'commit', '提交第 1 阶段', 1))

    payload = project.to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    restored = FixtureProject.from_dict(json.loads(text))

    assert restored.to_dict() == payload
    assert isinstance(restored.points[0].offset_range, tuple), 'JSON 读回来必须是元组'
    assert restored.points[0].offset_range == (0.0, 0.0)
    assert isinstance(restored.checks[0], CheckResult)
    assert isinstance(restored.inputs[0], InputFile)
    assert restored.checks[0].status == 'pass'
    assert restored.facts() == project.facts()


def test_single_record_serialization_roundtrip():
    project = build_demo_project()
    project.checks = evaluate(project)
    samples = [project.inputs[0], project.points[1], project.rules[0],
               project.parts[0], project.joints[1], project.checks[0],
               TraceEvent('2026-01-01T00:00:00', 3, 'param_change', 'x', 2)]
    for sample in samples:
        cls = type(sample)
        plain = sample.to_dict()
        text = json.dumps(plain, ensure_ascii=False)
        assert cls.from_dict(json.loads(text)).to_dict() == plain


def test_point_offset_range_must_be_a_pair():
    with pytest.raises(ValueError):
        PrpPoint('P09', 'assist', 0.0, 0.0, 0.0, offset_range=(0.0,))


# --------------------------------------------------------------------------
# 校核输出
# --------------------------------------------------------------------------
def test_unit_record_contract_keys():
    project = build_demo_project()
    project.checks = evaluate(project)
    record = unit_record(project)

    assert record['schema'] == 'fixture-unit/1'
    assert set(record['facts']) == set(FACT_KEYS)
    assert record['facts']['simulated'] is True
    assert record['facts']['cad_connected'] is False
    assert record['revision'] == project.revision
    assert record['unit_id'] == 'UN200'
    assert record['stage'] == project.stage
    for key in ('points', 'parts', 'joints', 'history', 'rules', 'inputs'):
        assert key in record

    assert len(record['stages']) == 5
    assert set(record['stages'][0]) == {'index', 'name', 'committed'}
    assert [state['index'] for state in record['stages']] == [1, 2, 3, 4, 5]

    counts = record['counts']
    assert set(counts) == {'pass', 'fail', 'not_run', 'not_applicable'}
    assert sum(counts.values()) == len(record['checks']) == 8
    for check in record['checks']:
        assert {'status', 'simulated', 'rule_id', 'stage'} <= set(check)


def test_summary_markdown_does_not_pretty_up_conclusions():
    project = build_demo_project()
    project.checks = evaluate(project)
    text = summary_markdown(project)
    for status in ('pass', 'fail', 'not_run', 'not_applicable'):
        assert status in text
    assert 'W-ARM-01' in text and 'W-MOTION-01' in text and 'W-MANUF-01' in text
    assert '事实声明' in text
    assert 'revision' in text


def test_exports_write_utf8_files(tmp_path):
    project = build_demo_project()
    project.checks = evaluate(project)

    json_path = export_unit_record(project, tmp_path / 'out' / 'un200.json')
    md_path = export_summary(project, tmp_path / 'out' / 'un200.md')

    assert json_path.exists() and md_path.exists()
    payload = json.loads(json_path.read_text(encoding='utf-8'))
    assert payload['schema'] == 'fixture-unit/1'
    assert payload['unit_id'] == 'UN200'
    markdown = md_path.read_text(encoding='utf-8')
    assert '气动夹紧单元' in markdown

    # 导出本身也是可追溯动作。
    assert [event.action for event in project.history[-2:]] == ['export', 'export']


def test_export_accepts_string_path(tmp_path):
    project = build_demo_project()
    project.checks = evaluate(project)
    target = export_summary(project, str(tmp_path / 'a.md'))
    assert target.exists()
