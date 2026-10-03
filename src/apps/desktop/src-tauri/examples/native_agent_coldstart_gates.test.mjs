import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer, createConnection } from 'node:net'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createAuditedToolProxy, evaluateColdstartGates, parseEvidenceJsonl, parsePostcheckState, readRunArtifacts, toolArgumentsFingerprint } from './native_agent_coldstart_gates.mjs'

// Synthetic contract fixture: no provider credentials, image bytes, geometry
// execution or model API are used. It is not scientific acceptance evidence.
function fixture() {
  const fp = 'a'.repeat(64), image = 'b'.repeat(64), rid = 'sha256:' + image
  const nativeCallId = '11111111-1111-4111-8111-111111111111'
  const argsFingerprint = toolArgumentsFingerprint({ fixture: true })
  const run = { id: 'run-new', turn_id: 'turn-new', acp_session_id: 'acp-new', tool_session_id: 'bridge-new', started_at_ms: 1000, ended_at_ms: 5000 }
  const checks = { execution_engine: 'rust_c_abi_occt', state: 'passed', valid: true, closed_solid_count: 1,
    validation: { state: 'valid' }, revision: 2, geometry_fingerprint: fp, hole_measurement_complete: true,
    hole_measurements_verified: true, host_attestation: { schema_version: 1, issuer: 'native_cad_session.complete_preview' } }
  const visual = { all_references_covered: true, comparable_review_pairs: 1 }
  const review = { issuer: 'native_review_gate', revision: 2, geometry_fingerprint: fp, result: {
    decision: 'accept', evidence: { geometry: { binding_valid: true, passed: true, host_attestation_valid: true, checks },
      holes: { measurement_complete: true }, references: { manifest_valid: true, visual_coverage_complete: true,
        mismatch_count: 0, original_reference_sources: [{ id: rid, sha256: image, covered: true, inventory_valid: true }] } } } }
  const plan = { features: [{ id: 'body', kind: 'box' }, { id: 'hole', kind: 'hole' }] }
  const finish = { accepted: true, finish_state: 'accepted', revision: 2, geometry_fingerprint: fp,
    overall_geometry_review_accepted: true, overall_geometry_review: review, hole_measurement: { complete: true },
    reference_coverage: true, visual_evidence: visual, independent_review_pair_count: 1, construction_complete: true,
    open_finding_count: 0, unresolved_findings: [], required_external_facts: { required_count: 0, verified_count: 0, failed_count: 0, unknown_count: 0 } }
  const { host_attestation, ...report } = checks
  return structuredClone({ run, provider: { model: 'fixture-model', host: 'fixture.invalid' },
    source: { sha256: image, sha256_after: image, bytes: 72309, reference_id: rid },
    references: { session_id: run.tool_session_id, images: [{ id: rid, sha256: image }] },
    userTurns: { session_id: run.tool_session_id, turns: [{ turn_id: run.turn_id, reference_ids: [rid] }] },
    activeTurn: { session_id: run.tool_session_id, turn_id: run.turn_id },
    dsh: { status: 'completed', exit_code: 0 },
    registration: { phase: 'registered', registeredToolNames: ['cad_finish_model'] },
    schema: { schemaVersion: 1, tools: [{ name: 'cad_finish_model' }] },
    streamEvents: [
      { kind: 'provider_wire_request', capture_layer: 'node_global_fetch', wire_observed: true, request_id: 'image-request', turn_id: run.turn_id,
        at: 2000, model: 'fixture-model', provider_host: 'fixture.invalid', images: [{ source_sha256: image, transport_sha256: image, byte_length: 72309, source_reference_ids: [rid] }] },
      { kind: 'provider_wire_response_headers', request_id: 'image-request', turn_id: run.turn_id, at: 2100, status_code: 200 },
      { kind: 'provider_wire_response_complete', request_id: 'image-request', turn_id: run.turn_id, at: 2200, response_terminator: 'sse_done' },
    ],
    toolEvents: [
      { run_id: run.id, session_id: run.acp_session_id, turn_id: run.turn_id, at_ms: 2500, tool_call_id: 'acp-call', title: 'cad_finish_model', status: 'in_progress', kind: 'tool_call', arguments_sha256_24: argsFingerprint },
      { run_id: run.id, session_id: run.acp_session_id, turn_id: run.turn_id, at_ms: 2900, tool_call_id: 'acp-call', status: 'completed', kind: 'tool_call_update' },
    ],
    toolBridgeCalls: [{ run_id: run.id, session_id: run.tool_session_id, turn_id: run.turn_id, call_id: nativeCallId, tool: 'cad_finish_model', authenticated_scope: true,
      started_at_ms: 2600, completed_at_ms: 2800, success: true, arguments_sha256_24: argsFingerprint }],
    nativeAudit: [{ call_id: nativeCallId, tool: 'cad_finish_model', at: 2, success: true, revision_before: 2, revision_after: 2 }],
    computedState: { revision: 2, geometry_signature: fp, visual_evidence_summary: visual },
    domain: { plan, finished: true, finish_state: 'accepted', revision: 2, preview_revision: 2, latest_geometry_review: review,
      last_preview: { revision: 2, geometry_fingerprint: fp, execution_engine: 'rust_c_abi_occt', local_geometry_checks: checks, preview_sha256: 'c'.repeat(64) }, finish_summary: finish },
    artifacts: { failures: [], geometry_report: report, cadplan: plan, preview_sha256: 'c'.repeat(64) },
  })
}

test('complete same-run evidence is passed; host acceptance alone is insufficient', () => {
  const result = evaluateColdstartGates(fixture())
  assert.equal(result.status, 'passed')
  assert.deepEqual(result.successful_image_request_ids, ['image-request'])
  assert.deepEqual(result.matched_native_call_ids, ['11111111-1111-4111-8111-111111111111'])
})

test('missing ACP terminal update is distinguished from a failed terminal update', () => {
  const input = fixture()
  input.toolEvents = [input.toolEvents[0]]
  const result = evaluateColdstartGates(input)
  assert.equal(result.gates.cordis_cad_tools.status, 'passed')
  assert.equal(result.gates.acp_cad_tool_results.status, 'failed')
  assert.deepEqual(result.gates.acp_cad_tool_results.reasons, ['acp_cad_tool_result_completion_missing'])
  assert.deepEqual(result.matched_native_call_ids, ['11111111-1111-4111-8111-111111111111'])
})

test('ACP failed terminal update has its own reason and remains a hard failure', () => {
  const input = fixture()
  input.toolEvents[1].status = 'failed'
  const result = evaluateColdstartGates(input)
  assert.equal(result.gates.cordis_cad_tools.status, 'failed')
  assert.equal(result.gates.acp_cad_tool_results.status, 'failed')
  assert.deepEqual(result.gates.acp_cad_tool_results.reasons, ['acp_cad_tool_result_failed'])
  assert.equal(result.status, 'failed')
})

test('normalized argument fingerprints ignore object key order and reveal no argument text', () => {
  const left = toolArgumentsFingerprint({ z: 2, nested: { b: true, a: 1 } })
  const right = toolArgumentsFingerprint({ nested: { a: 1, b: true }, z: 2 })
  assert.equal(left, right)
  assert.match(left, /^[a-f0-9]{24}$/)
  assert.equal(toolArgumentsFingerprint({ z: 3, nested: { b: true, a: 1 } }) === left, false)
  assert.equal(toolArgumentsFingerprint(undefined), null)
})

test('serial repeated identical calls pair only through unique complete temporal intervals', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 3100, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3500, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, started_at_ms: 3200, completed_at_ms: 3400 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const result = evaluateColdstartGates(input)
  assert.equal(result.gates.cordis_cad_tools.status, 'passed')
  assert.deepEqual(result.matched_native_call_ids.sort(), [
    '11111111-1111-4111-8111-111111111111', nativeCallId,
  ])
})

test('overlapping repeated-call intervals stay ambiguous', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents[1].at_ms = 3200
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 2700, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3500, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls[0].completed_at_ms = 3200
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, started_at_ms: 2800, completed_at_ms: 3000 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.failure_reasons.includes('cordis_tool_arguments_or_identity_missing_or_unmatched'))
})

test('foreign repeated-call scope cannot be paired by matching fingerprints or timestamps', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 3100, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3500, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, session_id: 'foreign-session', started_at_ms: 3200, completed_at_ms: 3400 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.failure_reasons.includes('cad_tool_rejected_unbound_or_unaudited'))
  assert.ok(result.failure_reasons.includes('cordis_tool_arguments_or_identity_missing_or_unmatched'))
})

test('missing repeated-call terminal interval remains ambiguous', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 3100, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, started_at_ms: 3200, completed_at_ms: 3400 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.failure_reasons.includes('cordis_tool_arguments_or_identity_missing_or_unmatched'))
})

const negativeFixtures = [
  ['different request successful response', f => { f.streamEvents[1].request_id = 'other'; f.streamEvents[2].request_id = 'other' }, 'same_image_request_successful_response_missing'],
  ['missing response completion', f => f.streamEvents.pop(), 'same_image_request_successful_response_missing'],
  ['failed protocol completion', f => { f.streamEvents[2].response_terminator = 'response.failed' }, 'same_image_request_successful_response_missing'],
  ['truncated provider finish', f => { f.streamEvents[2].finish_reason = 'length' }, 'same_image_request_successful_response_missing'],
  ['stream body ends without a successful terminal marker', f => {
    f.streamEvents[0].request_fields = { stream: true }; f.streamEvents[2].response_terminator = 'body_end'
  }, 'same_image_request_successful_response_missing'],
  ['malformed evidence trace', f => { f.evidenceErrors = ['stream_invalid_record_line_7'] }, 'evidence_trace_missing_or_malformed'],
  ['response from old turn', f => { f.streamEvents[1].turn_id = 'old' }, 'same_image_request_successful_response_missing'],
  ['only dedicated vision payload capture', f => { f.streamEvents[0].capture_layer = 'rust_native_vision' }, 'main_model_original_image_wire_missing'],
  ['wrong provider model', f => { f.streamEvents[0].model = 'other' }, 'main_model_original_image_wire_missing'],
  ['changed original bytes', f => { f.source.sha256_after = 'd'.repeat(64) }, 'original_image_changed_or_invalid'],
  ['missing Cordis tools', f => { f.toolBridgeCalls = []; f.nativeAudit = []; f.toolEvents = [] }, 'successful_cordis_cad_call_and_native_audit_missing'],
  ['action log alone is not Cordis proof', f => { f.toolBridgeCalls = []; f.domain.actions = [{ tool: 'cad_finish_model' }] }, 'successful_cordis_cad_call_and_native_audit_missing'],
  ['native tool rejected', f => { f.nativeAudit[0].success = false }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['successful call followed by rejected CAD call', f => f.toolBridgeCalls.push({ ...f.toolBridgeCalls[0], call_id: 'rejected', success: false }), 'cad_tool_rejected_unbound_or_unaudited'],
  ['rejected native web tool also fails', f => f.toolBridgeCalls.push({ ...f.toolBridgeCalls[0], tool: 'web_search', call_id: 'rejected', success: false }), 'tool_call_rejected_or_failed'],
  ['one ACP invocation cannot prove two native calls', f => {
    f.toolBridgeCalls.push({ ...f.toolBridgeCalls[0], call_id: '22222222-2222-4222-8222-222222222222' })
    f.nativeAudit.push({ ...f.nativeAudit[0], call_id: '22222222-2222-4222-8222-222222222222' })
  }, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
  ['duplicate native UUID cannot represent two invocations', f => {
    f.toolEvents.push({ ...f.toolEvents[0], tool_call_id: 'second-acp-call' })
    f.toolBridgeCalls.push({ ...f.toolBridgeCalls[0] })
    f.nativeAudit.push({ ...f.nativeAudit[0] })
  }, 'native_calls_not_individually_observed_or_duplicate'],
  ['ACP call with different arguments cannot prove bridge call', f => {
    f.toolEvents[0].arguments_sha256_24 = toolArgumentsFingerprint({ fixture: false })
  }, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
  ['unbound ACP CAD call is rejected', f => {
    f.toolEvents.push({ ...f.toolEvents[0], tool_call_id: 'extra-call|44444444-4444-4444-8444-444444444444' })
  }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['ACP tool name mismatch cannot prove bridge call', f => { f.toolEvents.forEach(event => { event.title = 'cad_preview_model' }) }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['ACP failed update remains a hard failure after Rust success', f => { f.toolEvents[1].status = 'failed' }, 'tool_call_rejected_or_failed'],
  ['missing ACP argument fingerprint fails closed', f => { delete f.toolEvents[0].arguments_sha256_24 }, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
  ['foreign bridge session', f => { f.toolBridgeCalls[0].session_id = 'old' }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['audit from unrelated UUID', f => { f.nativeAudit[0].call_id = 'other' }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['direct CLI postcheck is not DSH execution', f => { f.nativeAudit[0].call_id = 'probe-0'; f.toolBridgeCalls = [] }, 'successful_cordis_cad_call_and_native_audit_missing'],
  ['DSH nonzero exit', f => { f.dsh.exit_code = 1 }, 'dsh_not_completed_or_nonzero_exit'],
  ['host accepted false', f => { f.domain.finish_summary.accepted = false }, 'host_finish_not_accepted'],
  ['preview geometry stale', f => { f.domain.last_preview.geometry_fingerprint = 'd'.repeat(64) }, 'preview_geometry_finish_revision_or_fingerprint_stale'],
  ['current plan fingerprint differs from preview', f => { f.computedState.geometry_signature = 'd'.repeat(64) }, 'preview_geometry_finish_revision_or_fingerprint_stale'],
  ['geometry report stale', f => { f.artifacts.geometry_report.revision = 1 }, 'geometry_report_file_stale'],
  ['holes incomplete', f => { f.domain.finish_summary.hole_measurement.complete = false }, 'holes_incomplete_or_unverified'],
  ['host says holes complete but backend does not', f => { f.domain.last_preview.local_geometry_checks.hole_measurement_complete = false }, 'holes_incomplete_or_unverified'],
  ['visual coverage incomplete', f => { f.domain.finish_summary.reference_coverage = false }, 'visual_coverage_incomplete_or_conflicting'],
  ['stale visual summary', f => { f.computedState.visual_evidence_summary = { ...f.computedState.visual_evidence_summary, all_references_covered: false } }, 'visual_evidence_not_current_rust_summary'],
  ['attestation invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.geometry.host_attestation_valid = false }, 'geometry_attestation_or_gate_binding_invalid'],
  ['missing preview artifact', f => { f.artifacts.failures = ['artifact_preview_missing'] }, 'current_run_artifacts_missing_or_invalid'],
  ['missing mandatory fields', f => { delete f.domain.finish_summary; delete f.computedState }, 'host_finish_not_accepted'],
]
for (const [label, modify, reason] of negativeFixtures) test(label, () => {
  const input = fixture(); modify(input)
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.failure_reasons.includes(reason), JSON.stringify(result.failure_reasons))
})

test('postcheck parses only the Rust cad_get_state result', () => {
  assert.deepEqual(parsePostcheckState({ ok: true, result: { payload: { tool: 'cad_get_state', isError: false }, text: 'AI_MODELER_PROGRESS:local\n{"revision":2}' } }), { revision: 2 })
  assert.equal(parsePostcheckState({ ok: false }), null)
})

test('JSONL corruption fails closed without returning raw secret-bearing lines', () => {
  const parsed = parseEvidenceJsonl('{"kind":"safe"}\nfixture-private-marker\nnull\n', 'stream')
  assert.deepEqual(parsed.records, [{ kind: 'safe' }])
  assert.deepEqual(parsed.errors, ['stream_invalid_record_line_2', 'stream_invalid_record_line_3'])
  assert.ok(!JSON.stringify(parsed).includes('private-marker'))
})

test('artifacts outside the fresh run cannot be reused', () => {
  const root = mkdtempSync(join(tmpdir(), 'f7-g8-artifacts-'))
  try {
    const run = join(root, 'run'); mkdirSync(run)
    const old = join(root, 'old.png'); writeFileSync(old, 'not an acceptance image')
    const result = readRunArtifacts(run, { last_preview: { output_paths: { preview: old } } })
    assert.ok(result.failures.includes('artifact_preview_missing_invalid_or_outside_run'))
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('loopback bridge records exact native UUID and scope without token or bodies', async () => {
  const native = createServer(socket => {
    let text = ''
    socket.on('data', chunk => {
      text += chunk
      if (!text.includes('\n')) return
      const request = JSON.parse(text.slice(0, text.indexOf('\n')))
      socket.end(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {
        payload: { isError: false, execution_engine: 'rust', tool: request.params.name }, text: 'private-result-marker' } }) + '\n')
    })
  })
  await new Promise(done => native.listen(0, '127.0.0.1', done))
  const proxy = await createAuditedToolProxy({ address: `127.0.0.1:${native.address().port}`, token: 'fixture-token-marker',
    sessionId: 'native-session', turnId: 'turn', runId: 'run' })
  try {
    const port = Number(proxy.address.split(':')[1])
    await new Promise((yes, no) => {
      const socket = createConnection({ host: '127.0.0.1', port })
      socket.on('error', no); socket.on('data', () => {}); socket.on('end', yes)
      socket.on('connect', () => socket.write(JSON.stringify({ id: 'rpc', method: 'tools/call', params: {
        token: 'fixture-token-marker', sessionId: 'native-session', turnId: 'turn', toolCallId: 'native-uuid', name: 'cad_finish_model',
        arguments: { private: 'private-argument-marker' } } }) + '\n'))
    })
    assert.equal(proxy.records.length, 1)
    assert.equal(proxy.records[0].success, true)
    assert.equal(proxy.records[0].authenticated_scope, true)
    assert.equal(proxy.records[0].call_id, 'native-uuid')
    assert.equal(proxy.records[0].arguments_sha256_24, toolArgumentsFingerprint({ private: 'private-argument-marker' }))
    const serialized = JSON.stringify(proxy.records)
    for (const secret of ['fixture-token-marker', 'private-argument-marker', 'private-result-marker']) assert.equal(serialized.includes(secret), false)
  } finally { await proxy.close(); await new Promise(done => native.close(done)) }
})
