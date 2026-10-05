import test from 'node:test'
import assert from 'node:assert/strict'
import { restoreActiveModelDocument } from './activeModelRestore.mjs'

function acceptedSnapshot(generation = 8) {
  const fingerprint = 'restore-fingerprint'
  const document = {
    revision: generation,
    model_generation: generation,
    cad_revision: 7,
    geometry_fingerprint: fingerprint,
    features: [{ name: 'Body' }],
    finish_summary: { accepted: true, finish_state: 'accepted', revision: 7, geometry_fingerprint: fingerprint },
  }
  return {
    generation,
    document,
    acceptance_projection: {
      active_generation: generation,
      revision: 7,
      geometry_fingerprint: fingerprint,
      model: {
        exists: true,
        status: 'accepted',
        acceptance_confirmed: true,
        revision: 7,
        geometry_fingerprint: fingerprint,
        finish_status: 'accepted',
        finish_summary: document.finish_summary,
        geometric: { status: 'passed' },
        visual: { status: 'covered' },
      },
    },
  }
}

test('active-model startup restores accepted only from a current host projection', () => {
  const document = restoreActiveModelDocument(acceptedSnapshot())
  assert.equal(document.acceptance_confirmed, true)
  assert.equal(document.acceptance_source, 'native_host_active_model_restore')
  assert.equal(document.finish_status, 'accepted')
})

test('missing, stale, mismatched, and candidate host projections cannot retain acceptance', () => {
  const missing = acceptedSnapshot()
  delete missing.acceptance_projection
  const missingDocument = restoreActiveModelDocument(missing)
  assert.equal(missingDocument.acceptance_confirmed, false)
  assert.equal(missingDocument.finish_summary, null)

  const stale = acceptedSnapshot()
  stale.acceptance_projection.active_generation -= 1
  const staleDocument = restoreActiveModelDocument(stale)
  assert.equal(staleDocument.acceptance_confirmed, false)
  assert.equal(staleDocument.finish_summary, null)

  const mismatch = acceptedSnapshot()
  mismatch.acceptance_projection.geometry_fingerprint = 'different-fingerprint'
  const mismatchDocument = restoreActiveModelDocument(mismatch)
  assert.equal(mismatchDocument.acceptance_confirmed, false)
  assert.equal(mismatchDocument.finish_summary, null)

  const candidate = acceptedSnapshot()
  candidate.acceptance_projection.model.status = 'candidate'
  candidate.acceptance_projection.model.acceptance_confirmed = false
  const candidateDocument = restoreActiveModelDocument(candidate)
  assert.equal(candidateDocument.acceptance_confirmed, false)
  assert.equal(candidateDocument.finish_summary, null)
  assert.equal(candidateDocument.finish_status, 'not_finished')
})

test('projection from an older generation cannot overwrite a newer Vue document', () => {
  const staleProjection = acceptedSnapshot(8)
  assert.equal(restoreActiveModelDocument(staleProjection, 9), null)
})
