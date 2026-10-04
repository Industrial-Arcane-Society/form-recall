const REQUEST_FIELDS = [
  'reference_id',
  'source_sha256',
  'user_target_fingerprint',
  'view',
]

export function adjudicationRequest(row) {
  const binding = row?.binding
  if (!row?.finding_id || !row?.candidate_evidence_id || !row?.candidate_observation_id || !binding) {
    throw new TypeError('人工确认候选缺少稳定 finding/evidence binding')
  }
  const features = Array.isArray(row.affected_feature_ids) ? [...row.affected_feature_ids] : []
  if (!features.length || new Set(features).size !== features.length) {
    throw new TypeError('人工确认候选的受影响特征范围无效')
  }
  for (const field of REQUEST_FIELDS) {
    if (typeof binding[field] !== 'string' || !binding[field]) {
      throw new TypeError(`人工确认候选缺少 ${field}`)
    }
  }
  if (binding.evidence_id !== row.candidate_evidence_id || binding.observation_id !== row.candidate_observation_id) {
    throw new TypeError('人工确认候选与当前 binding 不一致')
  }
  if (!Number.isSafeInteger(row.expected_generation) || !Number.isSafeInteger(row.expected_revision)) {
    throw new TypeError('人工确认候选的 generation/revision 无效')
  }
  return {
    expected_generation: row.expected_generation,
    expected_revision: row.expected_revision,
    finding_id: row.finding_id,
    candidate_evidence_id: row.candidate_evidence_id,
    candidate_observation_id: row.candidate_observation_id,
    reference_id: binding.reference_id,
    source_sha256: binding.source_sha256,
    user_target_fingerprint: binding.user_target_fingerprint,
    view: binding.view,
    affected_feature_ids: features,
  }
}

export function pendingAdjudications(state) {
  if (!Array.isArray(state?.pending)) return []
  return state.pending.filter(row => row?.finding_id && row?.candidate_evidence_id && row?.binding)
}
