export type PendingFindingAdjudication = {
  finding_id: string
  item?: string
  severity?: string
  finding_status: string
  previous_evidence_id: string
  previous_observation_id: string
  reference_evidence?: string
  model_evidence?: string
  affected_feature_ids: string[]
  candidate_evidence_id: string
  candidate_observation_id: string
  candidate_reference_evidence?: string
  candidate_source_evidence_id?: string
  binding: {
    reference_id: string
    source_sha256: string
    user_target_fingerprint: string
    view: string
    evidence_id: string
    observation_id: string
  }
  expected_generation: number
  expected_revision: number
}

export function adjudicationRequest(row: PendingFindingAdjudication): Record<string, unknown>
export function pendingAdjudications(state: unknown): PendingFindingAdjudication[]
