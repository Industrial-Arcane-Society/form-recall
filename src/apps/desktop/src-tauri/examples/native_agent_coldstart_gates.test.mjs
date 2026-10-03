import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer, createConnection } from 'node:net'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readdirSync, readFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { join, relative } from 'node:path'
import { tmpdir } from 'node:os'
import { createAuditedToolProxy, createTerminalUpdateHmac, evaluateColdstartGates, parseEvidenceJsonl, parsePostcheckState, readRunArtifacts, summarizeAcpTerminalUpdateDiagnostics, summarizeCordisBindingDiagnostics, toolArgumentsFingerprint } from './native_agent_coldstart_gates.mjs'
import { buildSafeAcceptanceSummary, writeSafeAcceptanceArtifacts } from './native_agent_coldstart_publication.mjs'
import { removeScratchDirectory, safeScratchCleanupStatus, terminateChildrenAndWait, waitThenTerminateChild } from './native_agent_coldstart_lifecycle.mjs'

class FakeChild extends EventEmitter {
  constructor({ exitAfterKillMs = null } = {}) {
    super()
    this.pid = 123
    this.exitCode = null
    this.signalCode = null
    this.killed = false
    this.exitAfterKillMs = exitAfterKillMs
  }

  kill() {
    this.killed = true
    if (this.exitAfterKillMs !== null) {
      setTimeout(() => {
        this.exitCode = null
        this.signalCode = 'SIGTERM'
        this.emit('exit', null, 'SIGTERM')
      }, this.exitAfterKillMs)
    }
    return true
  }
}

test('bounded child timeout waits for termination exit before reporting success', async () => {
  const child = new FakeChild({ exitAfterKillMs: 2 })
  assert.equal(await waitThenTerminateChild(child, 2, 50), true)
  assert.equal(child.killed, true)
  assert.equal(child.signalCode, 'SIGTERM')
})

test('failure shutdown waits for fake child exit and reports cleanup failure without details', async () => {
  const child = new FakeChild({ exitAfterKillMs: 2 })
  assert.equal(await terminateChildrenAndWait([child], 50), true)

  const cleanup = removeScratchDirectory('private-scratch-marker', () => {
    throw new Error('private-path-or-error-marker')
  })
  assert.equal(cleanup, 'failed')
  assert.equal(JSON.stringify({ cleanup }).includes('private-'), false)
  assert.equal(safeScratchCleanupStatus(true, cleanup), 'failed')
  assert.equal(safeScratchCleanupStatus(false, 'removed'), 'failed')
  assert.equal(safeScratchCleanupStatus(true, 'removed'), 'removed')
})

test('bounded child timeout reports when termination does not exit', async () => {
  const child = new FakeChild()
  assert.equal(await waitThenTerminateChild(child, 2, 2), false)
  assert.equal(child.killed, true)
})

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

test('ACP result updates without call IDs fail identity without coalescing as duplicate terminals', () => {
  const input = fixture()
  delete input.toolEvents[1].tool_call_id
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3000 })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.gates.acp_cad_tool_results.reasons.includes('acp_cad_tool_call_id_missing'))
  assert.ok(result.gates.acp_cad_tool_results.reasons.includes('acp_cad_tool_result_completion_missing'))
  assert.equal(result.gates.acp_cad_tool_results.reasons.includes('acp_cad_tool_result_terminal_update_duplicated'), false)
})

test('two terminal ACP updates with the same valid call ID remain a hard duplicate failure', () => {
  const input = fixture()
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3000 })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.gates.acp_cad_tool_results.reasons.includes('acp_cad_tool_result_terminal_update_duplicated'))
})

test('ACP terminal projection uses per-run full-update HMACs and an exact tool-name allowlist', () => {
  const marker = 'private-event-id-or-payload-marker', key = randomBytes(32), digestByEvent = new WeakMap()
  const makeTerminal = (id, rawOutput, at, status = 'completed') => {
    const update = { sessionUpdate: 'tool_call_update', toolCallId: id, status, rawOutput,
      content: { image: { data: 'private-image-payload-marker' } }, error: 'private-acp-error-marker', title: 'private-title-marker' }
    const row = { kind: 'tool_call_update', tool_call_id: id, status, at_ms: at }
    digestByEvent.set(row, createTerminalUpdateHmac(update, key))
    return row
  }
  const start = (id, title, at) => ({ kind: 'tool_call', tool_call_id: id, title, status: 'in_progress', at_ms: at })
  const repeatedId = 'private-acp-id-repeated', distinctId = 'private-acp-id-distinct', mixedId = 'private-acp-id-mixed'
  const identical = { text: marker, nested: { z: 2, a: 1 } }
  const rows = [
    start(repeatedId, 'cad_finish_model', 1),
    makeTerminal(repeatedId, identical, 2),
    makeTerminal(repeatedId, identical, 3),
    start(distinctId, 'cad_inspect_references', 4),
    makeTerminal(distinctId, identical, 5),
    makeTerminal(distinctId, { text: 'different-private-payload' }, 6),
    start(mixedId, 'cad_update_feature', 7),
    makeTerminal(mixedId, { text: marker }, 8),
    makeTerminal(mixedId, { text: marker }, 9, 'failed'),
    start('private-web-id', 'web_search', 10),
    start('private-unknown-id', 'cad_private_tool_name', 11),
    { kind: 'tool_call_update', status: 'failed', content: marker, error: marker },
  ]
  const hmacA = createTerminalUpdateHmac({ b: 2, a: 1 }, key)
  const hmacB = createTerminalUpdateHmac({ a: 1, b: 2 }, key)
  assert.equal(hmacA, hmacB)
  assert.notEqual(hmacA, createTerminalUpdateHmac({ a: 1, b: 2 }, randomBytes(32)))
  const projection = summarizeAcpTerminalUpdateDiagnostics(rows, { terminalUpdateHmacByEvent: digestByEvent })
  assert.equal(projection.schema, 'acp_terminal_update_projection_v2')
  assert.deepEqual(projection.call_group_terminal_distribution, [
    { tool_name: 'cad_finish_model', terminal_category: 'repeated_same_status_identical_update', count: 1 },
    { tool_name: 'cad_inspect_references', terminal_category: 'repeated_same_status_distinct_updates', count: 1 },
    { tool_name: 'cad_update_feature', terminal_category: 'repeated_mixed_terminal_statuses', count: 1 },
    { tool_name: 'unknown', terminal_category: 'no_terminal', count: 1 },
    { tool_name: 'web_search', terminal_category: 'no_terminal', count: 1 },
  ])
  assert.deepEqual(projection.call_group_terminal_status_counts, [
    { tool_name: 'cad_finish_model', terminal_status: 'completed', count: 1 },
    { tool_name: 'cad_inspect_references', terminal_status: 'completed', count: 1 },
    { tool_name: 'cad_update_feature', terminal_status: 'mixed', count: 1 },
    { tool_name: 'unknown', terminal_status: 'none', count: 1 },
    { tool_name: 'web_search', terminal_status: 'none', count: 1 },
  ])
  assert.equal(projection.same_terminal_event_pair_count, 1)
  assert.equal(projection.different_terminal_event_pair_count, 2)
  assert.equal(projection.missing_tool_call_id_event_count, 1)
  assert.ok(projection.tool_status_counts.some(row => row.tool_name === 'cad_finish_model'
    && row.kind === 'update' && row.status === 'completed' && row.count === 2))
  assert.ok(projection.tool_status_counts.some(row => row.tool_name === 'unknown'
    && row.kind === 'start' && row.status === 'in_progress' && row.count === 1))
  const serialized = JSON.stringify(projection)
  for (const secret of [marker, 'different-private-payload', repeatedId, distinctId, mixedId,
    'private-web-id', 'private-unknown-id', 'cad_private_tool_name', 'private-image-payload-marker',
    'private-acp-error-marker', 'private-title-marker', hmacA, key.toString('hex')]) {
    assert.equal(serialized.includes(secret), false)
  }
})

test('Cordis binding projection reports exact static reason counts and never leaks identities or bodies', () => {
  const input = fixture()
  const projection = summarizeCordisBindingDiagnostics(input)
  assert.deepEqual(projection, { schema: 'cordis_binding_projection_v1', counts: [
    { tool_name: 'cad_finish_model', stage: 'acp_to_bridge', outcome: 'matched', count: 1 },
    { tool_name: 'cad_finish_model', stage: 'bridge_to_audit', outcome: 'matched', count: 1 },
  ] })
  const failed = fixture()
  failed.toolBridgeCalls[0].success = false
  failed.toolBridgeCalls[0].error = 'private-error-body-marker'
  failed.nativeAudit[0].error = 'private-audit-marker'
  failed.toolBridgeCalls[0].arguments_sha256_24 = 'd'.repeat(24)
  const rejected = summarizeCordisBindingDiagnostics(failed)
  assert.ok(rejected.counts.some(row => row.tool_name === 'cad_finish_model'
    && row.stage === 'bridge_to_audit' && row.outcome === 'bridge_execution_rejected' && row.count === 1))
  const hidden = fixture()
  hidden.toolBridgeCalls[0].tool = 'cad_private_tool_name'
  hidden.toolEvents[0].title = 'cad_private_tool_name'
  hidden.nativeAudit[0].tool = 'cad_private_tool_name'
  hidden.toolBridgeCalls[0].error = 'private-error-body-marker'
  const hiddenProjection = summarizeCordisBindingDiagnostics(hidden)
  const serialized = JSON.stringify(hiddenProjection)
  for (const secret of ['cad_private_tool_name', 'private-error-body-marker', '11111111-1111-4111-8111-111111111111',
    'acp-call', hidden.toolBridgeCalls[0].arguments_sha256_24]) assert.equal(serialized.includes(secret), false)
  assert.ok(hiddenProjection.counts.every(row => ['matched', 'bridge_call_missing', 'acp_call_missing', 'tool_name_not_allowlisted',
    'correlation_fingerprint_missing', 'invocation_count_mismatch', 'identity_pairing_ambiguous', 'acp_identity_missing',
    'run_scope_mismatch', 'bridge_call_id_missing', 'bridge_scope_rejected', 'bridge_time_invalid',
    'bridge_execution_rejected', 'schema_missing', 'registration_missing', 'native_audit_missing',
    'native_audit_tool_mismatch', 'native_audit_out_of_run', 'native_audit_rejected'].includes(row.outcome)))
})

test('Cordis binding reasons distinguish missing ACP/audit rows from explicit rejection', () => {
  const outcome = (projection, stage, code) => projection.counts.find(row => row.stage === stage && row.outcome === code)?.count || 0
  const noAcp = fixture()
  noAcp.toolEvents = []
  assert.equal(outcome(summarizeCordisBindingDiagnostics(noAcp), 'acp_to_bridge', 'acp_call_missing'), 1)
  const noAudit = fixture()
  noAudit.nativeAudit = []
  assert.equal(outcome(summarizeCordisBindingDiagnostics(noAudit), 'bridge_to_audit', 'native_audit_missing'), 1)
  const auditRejected = fixture()
  auditRejected.nativeAudit[0].success = false
  assert.equal(outcome(summarizeCordisBindingDiagnostics(auditRejected), 'bridge_to_audit', 'native_audit_rejected'), 1)
  const foreignScope = fixture()
  foreignScope.toolBridgeCalls[0].session_id = 'foreign-session-marker'
  const scopeProjection = summarizeCordisBindingDiagnostics(foreignScope)
  assert.equal(outcome(scopeProjection, 'acp_to_bridge', 'run_scope_mismatch'), 1)
  assert.equal(outcome(scopeProjection, 'bridge_to_audit', 'bridge_scope_rejected'), 1)
  assert.equal(JSON.stringify(scopeProjection).includes('foreign-session-marker'), false)
})

test('published cold-start files contain only safe gates and aggregate projections', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-safe-publication-'))
  const output = join(root, 'run')
  const secrets = ['sentinel-acp-call-id', 'sentinel-native-uuid', 'sentinel-answer-body',
    'sentinel-thought-body', 'sentinel-tool-payload', 'sentinel-argument-value', 'sentinel-scope',
    'sentinel-runtime', 'sentinel-reason', 'sentinel-evidence-error', 'sentinel-gate-name']
  try {
    const key = randomBytes(32), digestByEvent = new WeakMap()
    const start = { kind: 'tool_call', tool_call_id: secrets[0], title: 'cad_finish_model', status: 'in_progress', at_ms: 1 }
    const update = { sessionUpdate: 'tool_call_update', toolCallId: secrets[0], status: 'completed',
      rawOutput: { text: secrets[2], payload: secrets[4] }, thought: secrets[3], arguments: secrets[5] }
    const terminal = { kind: 'tool_call_update', tool_call_id: secrets[0], status: 'completed', at_ms: 2 }
    digestByEvent.set(terminal, createTerminalUpdateHmac(update, key))
    const events = [start, terminal]
    const acpProjection = summarizeAcpTerminalUpdateDiagnostics(events, { terminalUpdateHmacByEvent: digestByEvent })
    acpProjection.raw_trace = secrets.join('|')
    const bindingProjection = summarizeCordisBindingDiagnostics({
      ...fixture(),
      toolEvents: events.map(event => ({ ...event, run_id: 'temporary-run-id', session_id: secrets[1], turn_id: 'temporary-turn-id' })),
    })
    bindingProjection.debug_payload = secrets.join('|')
    const strict = {
      status: 'passed', scope: secrets[6],
      successful_image_request_ids: [secrets[1]], matched_native_call_ids: [secrets[1]],
      answer: secrets[2], thought: secrets[3], payload: secrets[4], arguments: secrets[5],
      failure_reasons: [secrets[8]], evidence_errors: [secrets[9]], gates: {
        run_scope: { status: 'passed', reasons: [secrets[8]] },
        acp_cad_tool_results: { status: 'passed', reasons: [] }, [secrets[10]]: { status: 'passed', reasons: [] },
      },
    }
    const summary = buildSafeAcceptanceSummary({ strict, transportStatus: 'completed', modelStatus: secrets[7],
      runtime: { node: secrets[7], dsh: secrets[7], directory: secrets[0] },
      source: { bytes: 72309, sha256: 'b'.repeat(64), unchanged: true, reference_id: secrets[1] },
      scratchCleanupStatus: secrets[7] })
    writeSafeAcceptanceArtifacts(output, { summary, acpProjection, bindingProjection,
      buildStatus: { status: 'passed', exit_code: 0, elapsed_ms: 12, stdout_bytes: 0, stderr_bytes: 0, raw: secrets[4] } })
    const listFiles = directory => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
      const path = join(directory, entry.name)
      return entry.isDirectory() ? listFiles(path) : [relative(output, path)]
    })
    const files = listFiles(output).sort()
    assert.deepEqual(files, ['build-status.json', 'summary.json', 'tool-result-diagnostics.jsonl'])
    const serialized = files.map(path => readFileSync(join(output, path), 'utf8')).join('\n')
    for (const secret of secrets) assert.equal(serialized.includes(secret), false)
    assert.equal(summary.status, 'passed')
    assert.equal(summary.strict_gates.gates.run_scope.status, 'passed')
    assert.equal(summary.strict_gates.gates.run_scope.reason_count, 1)
    assert.equal(summary.strict_gates.failure_reason_count, 1)
    assert.equal(summary.strict_gates.evidence_error_count, 1)
    assert.equal(summary.status_scope, 'scope_unverified')
    assert.equal(summary.runtime.node, 'unverified')
    assert.equal(summary.scratch_cleanup_status, 'not_created')
    assert.deepEqual(Object.keys(summary.strict_gates.gates), [
      'run_scope', 'dsh', 'trace_integrity', 'original_image', 'main_image_wire', 'cordis_cad_tools',
      'acp_cad_tool_results', 'current_geometry', 'artifacts', 'holes', 'visual', 'finish',
    ])
    const knownValues = buildSafeAcceptanceSummary({
      strict: { status: 'passed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
        gates: { run_scope: { status: 'passed', reasons: [] } } },
      transportStatus: 'completed', modelStatus: 'accepted_by_strict_cli_gate',
      runtime: { node: 'v22.23.3', dsh: '0.2.0-rc.2' },
      source: { bytes: 72309, sha256: 'b'.repeat(64), unchanged: true }, scratchCleanupStatus: 'removed',
    })
    assert.equal(knownValues.status_scope, 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance')
    assert.deepEqual(knownValues.runtime, { node: 'v22.23.3', dsh: '0.2.0-rc.2' })
    assert.equal(knownValues.model_status, 'accepted_by_strict_cli_gate')
    assert.equal(knownValues.scratch_cleanup_status, 'removed')
    assert.equal(buildSafeAcceptanceSummary({ scratchCleanupStatus: 'failed' }).scratch_cleanup_status, 'failed')
    const projections = readFileSync(join(output, 'tool-result-diagnostics.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
    assert.equal(projections[0].schema, 'acp_terminal_update_projection_v2')
    assert.deepEqual(projections[0].call_group_terminal_distribution,
      [{ tool_name: 'cad_finish_model', terminal_category: 'completed_once', count: 1 }])
    assert.equal(projections[1].schema, 'cordis_binding_projection_v1')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
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
const criticalGatePredicateFlips = [
  ['visual coverage: finish reference coverage missing', f => { f.domain.finish_summary.reference_coverage = false }, 'visual_coverage_incomplete_or_conflicting'],
  ['visual coverage: all references covered missing', f => { f.domain.finish_summary.visual_evidence.all_references_covered = false; f.computedState.visual_evidence_summary.all_references_covered = false }, 'visual_coverage_incomplete_or_conflicting'],
  ['visual coverage: independent review pair count zero', f => { f.domain.finish_summary.independent_review_pair_count = 0 }, 'visual_coverage_incomplete_or_conflicting'],
  ['visual coverage: Rust complete flag missing', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.visual_coverage_complete = false }, 'visual_coverage_incomplete_or_conflicting'],
  ['visual coverage: reference manifest invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.manifest_valid = false }, 'visual_coverage_incomplete_or_conflicting'],
  ['visual coverage: mismatch count nonzero', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.mismatch_count = 1 }, 'visual_coverage_incomplete_or_conflicting'],
  ['original binding: inventory row missing', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources = [] }, 'visual_gate_original_reference_binding_missing'],
  ['original binding: duplicate inventory rows', f => { const refs = f.domain.finish_summary.overall_geometry_review.result.evidence.references; refs.original_reference_sources.push({ ...refs.original_reference_sources[0] }) }, 'visual_gate_original_reference_binding_missing'],
  ['original binding: reference id wrong', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].id = 'sha256:' + 'd'.repeat(64) }, 'visual_gate_original_reference_binding_missing'],
  ['original binding: source hash wrong', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].sha256 = 'd'.repeat(64) }, 'visual_gate_original_reference_binding_missing'],
  ['original binding: coverage false', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].covered = false }, 'visual_gate_original_reference_binding_missing'],
  ['original binding: inventory invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].inventory_valid = false }, 'visual_gate_original_reference_binding_missing'],
  ['host finish: domain finished false', f => { f.domain.finished = false }, 'host_finish_not_accepted'],
  ['host finish: domain state not accepted', f => { f.domain.finish_state = 'candidate' }, 'host_finish_not_accepted'],
  ['host finish: summary state not accepted', f => { f.domain.finish_summary.finish_state = 'candidate' }, 'host_finish_not_accepted'],
  ['host finish: summary acceptance false', f => { f.domain.finish_summary.accepted = false }, 'host_finish_not_accepted'],
  ['host finish: geometry review acceptance false', f => { f.domain.finish_summary.overall_geometry_review_accepted = false }, 'host_finish_not_accepted'],
  ['host finish: review issuer wrong', f => { f.domain.finish_summary.overall_geometry_review.issuer = 'other' }, 'host_finish_not_accepted'],
  ['host finish: review decision not accept', f => { f.domain.finish_summary.overall_geometry_review.result.decision = 'reject' }, 'host_finish_not_accepted'],
]
for (const [label, modify, reason] of criticalGatePredicateFlips) test(`critical gate predicate flip: ${label}`, () => {
  const accepted = evaluateColdstartGates(fixture())
  assert.equal(accepted.status, 'passed', 'all predicates satisfied must pass')
  assert.equal(accepted.gates.visual.status, 'passed')
  assert.equal(accepted.gates.finish.status, 'passed')

  const input = fixture()
  modify(input)
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.equal(result.gates[reason.startsWith('host_finish') ? 'finish' : 'visual'].status, 'failed')
  assert.ok(result.failure_reasons.includes(reason), JSON.stringify(result.failure_reasons))
})
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
