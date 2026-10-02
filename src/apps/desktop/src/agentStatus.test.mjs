import test from 'node:test'
import assert from 'node:assert/strict'
import { modelAcceptanceView, turnStatusView } from './agentStatus.mjs'

test('ACP turn completion never promotes a candidate model to accepted', () => {
  const candidate = modelAcceptanceView({
    exists: true,
    status: 'candidate',
    finish_status: 'candidate_holes_unverified',
    finish_summary: { accepted: false },
    geometric: { status: 'passed' },
    visual: { status: 'not_reviewed' },
  })
  assert.equal(turnStatusView('completed').key, 'completed')
  assert.equal(candidate.outcome, 'candidate')
  assert.equal(candidate.geometry, '通过')
  assert.equal(candidate.visual, '未复核')
})

test('only an accepted current model summary is shown as accepted', () => {
  const accepted = modelAcceptanceView({
    exists: true,
    status: 'accepted',
    finish_status: 'accepted',
    finish_summary: { accepted: true },
    geometric: { status: 'passed' },
    visual: { status: 'covered' },
  })
  assert.equal(accepted.outcome, 'accepted')
  assert.equal(accepted.outcomeLabel, '已验收')
  assert.equal(accepted.visual, '已覆盖')
})

test('cancelled and stopped turns stay distinct from completion', () => {
  assert.deepEqual(turnStatusView('cancelled'), { key: 'cancelled', text: '本轮已取消' })
  assert.deepEqual(turnStatusView('stopped', 'max_turns'), { key: 'stopped', text: '本轮已停止（max_turns）' })
  assert.equal(turnStatusView('ended').key, 'ended')
})

test('missing and unavailable model summaries stay explicit', () => {
  assert.equal(modelAcceptanceView(null).outcomeLabel, '尚无 CADPlan 模型')
  assert.equal(modelAcceptanceView({ status: 'unavailable' }).outcomeLabel, '状态不可读取')
})
