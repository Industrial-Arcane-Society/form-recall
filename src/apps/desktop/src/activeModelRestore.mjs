/**
 * Merge a host-recomputed active-model acceptance projection into the Vue
 * document only while every identity boundary still refers to that snapshot.
 * A missing or stale projection always hydrates as a candidate.
 *
 * @param {any} active
 * @param {number} [currentGeneration]
 */
export function restoreActiveModelDocument(active, currentGeneration = 0) {
  const document = active?.document
  if (!document || typeof document !== 'object') return null

  const generation = active?.generation
  if (!Number.isInteger(generation) || generation < currentGeneration) return null
  if (document.model_generation !== generation || document.revision !== generation) return null

  const projection = active.acceptance_projection
  const projectedModel = projection?.model
  const projectionMatches = projection?.active_generation === generation
    && projection?.revision === document.cad_revision
    && projection?.geometry_fingerprint === document.geometry_fingerprint
    && projectedModel?.revision === document.cad_revision
    && projectedModel?.geometry_fingerprint === document.geometry_fingerprint
  const finish = projectionMatches ? projectedModel?.finish_summary : null
  const finishCurrent = finish?.revision === document.cad_revision
    && finish?.geometry_fingerprint === document.geometry_fingerprint
  const acceptanceConfirmed = projectionMatches
    && projectedModel?.acceptance_confirmed === true
    && projectedModel?.status === 'accepted'
    && projectedModel?.finish_status === 'accepted'
    && finishCurrent
    && finish?.accepted === true
    && finish?.finish_state === 'accepted'
  const exposesFinishSummary = projectionMatches
    && finishCurrent
    && (acceptanceConfirmed || (finish?.accepted !== true && finish?.finish_state !== 'accepted'))
  const finishStatus = projectionMatches && finishCurrent ? projectedModel?.finish_status || 'not_finished' : 'not_finished'

  return {
    ...document,
    acceptance_confirmed: acceptanceConfirmed,
    acceptance_source: acceptanceConfirmed ? 'native_host_active_model_restore' : '',
    finish_summary: exposesFinishSummary ? finish : null,
    finish_status: acceptanceConfirmed ? 'accepted' : finishStatus === 'accepted' ? 'not_finished' : finishStatus,
    geometric_status: projectionMatches ? projectedModel?.geometric?.status || 'unknown' : 'unknown',
    visual_status: projectionMatches ? projectedModel?.visual?.status || 'unknown' : 'unknown',
  }
}
