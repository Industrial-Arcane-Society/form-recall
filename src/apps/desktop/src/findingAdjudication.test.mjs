import assert from 'node:assert/strict'
import test from 'node:test'
import { adjudicationRequest, pendingAdjudications } from './findingAdjudication.mjs'

const pending = {
  finding_id: 'visual-old',
  finding_status: 'awaiting_verification',
  previous_evidence_id: 'ref:old',
  previous_observation_id: 'obs-1',
  affected_feature_ids: ['hole-a'],
  candidate_evidence_id: 'ref:new',
  candidate_observation_id: 'obs-2',
  binding: {
    evidence_id: 'ref:new',
    observation_id: 'obs-2',
    reference_id: 'ref',
    source_sha256: 'source-sha',
    user_target_fingerprint: 'target-sha',
    view: 'front',
  },
  expected_generation: 9,
  expected_revision: 7,
}

test('pending list presents only rows with a complete human-confirmable binding', () => {
  assert.deepEqual(pendingAdjudications({ pending: [pending, { finding_id: 'bad' }, null] }), [pending])
  assert.deepEqual(pendingAdjudications({ pending: [] }), [])
})

test('confirmation request binds the clicked finding and exact candidate with CAS versions', () => {
  assert.deepEqual(adjudicationRequest(pending), {
    expected_generation: 9,
    expected_revision: 7,
    finding_id: 'visual-old',
    candidate_evidence_id: 'ref:new',
    candidate_observation_id: 'obs-2',
    reference_id: 'ref',
    source_sha256: 'source-sha',
    user_target_fingerprint: 'target-sha',
    view: 'front',
    affected_feature_ids: ['hole-a'],
  })
})

test('malformed, stale-shape, or mismatched candidate rows cannot produce a request', () => {
  assert.throws(() => adjudicationRequest({ ...pending, expected_generation: Number.MAX_SAFE_INTEGER + 1 }), /generation\/revision/)
  assert.throws(() => adjudicationRequest({ ...pending, affected_feature_ids: ['hole-a', 'hole-a'] }), /范围无效/)
  assert.throws(() => adjudicationRequest({ ...pending, candidate_evidence_id: 'other' }), /binding 不一致/)
  assert.throws(() => adjudicationRequest({ ...pending, binding: { ...pending.binding, source_sha256: '' } }), /source_sha256/)
})
