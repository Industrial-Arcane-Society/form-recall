import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer, createConnection } from 'node:net'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readdirSync, readFileSync, symlinkSync, existsSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { join, relative } from 'node:path'
import { tmpdir } from 'node:os'
import { createAuditedToolProxy, createTerminalUpdateHmac, evaluateColdstartGates, parseEvidenceJsonl, parsePostcheckState, readRunArtifacts, summarizeAcpTerminalUpdateDiagnostics, summarizeCordisBindingDiagnostics, summarizeVisualReviewDiagnostics, toolArgumentsFingerprint } from './native_agent_coldstart_gates.mjs'
import { buildSafeAcceptanceSummary, buildSafeFailureDiagnostic, replacePublishedSafeFailureSummary,
  SAFE_PUBLICATION_PHASE, writeSafeAcceptanceArtifacts, writeSafeFailureSummary } from './native_agent_coldstart_publication.mjs'
import { runOfflineCargoBuild } from './native_agent_coldstart_build.mjs'
import { removeScratchDirectory, safeScratchCleanupStatus, terminateChildrenAndWait, waitThenTerminateChild } from './native_agent_coldstart_lifecycle.mjs'
import { COLDSTART_SCRATCH_MAX_AGE_MS, COLDSTART_SCRATCH_PREFIX, createColdstartScratchDirectory,
  recoverStaleColdstartScratch } from './native_agent_coldstart_scratch.mjs'
import { apply as applyNativeCordisTools } from '../../../../legacy/ai_modeler/agent/dsh_native_tools.mjs'

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

function scratchFixture(t, { pid = 71, ageMs = COLDSTART_SCRATCH_MAX_AGE_MS + 1000, nonceByte = 'a' } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-scratch-recovery-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const directory = createColdstartScratchDirectory({ root, pid, now: 2_000_000,
    random: () => Buffer.from(nonceByte.repeat(64), 'hex') })
  return { root, directory, now: 2_000_000 + ageMs }
}

test('stale scratch with ESRCH owner is removed and only fixed counts are returned', t => {
  const fixture = scratchFixture(t)
  writeFileSync(join(fixture.directory, 'sentinel'), 'private-payload-sentinel')
  const summary = recoverStaleColdstartScratch({ root: fixture.root, now: fixture.now,
    processIsAlive: () => { const error = new Error('private pid'); error.code = 'ESRCH'; throw error } })
  assert.equal(summary.status, 'complete')
  assert.equal(summary.removed_count, 1)
  assert.equal(summary.scanned_count, 1)
  assert.equal(JSON.stringify(summary).includes('private'), false)
  assert.equal(readdirSync(fixture.root).length, 0)
})

test('live and unknown owner PIDs preserve stale scratch', t => {
  const live = scratchFixture(t, { nonceByte: 'b' })
  const liveSummary = recoverStaleColdstartScratch({ root: live.root, now: live.now, processIsAlive: () => 'alive' })
  assert.equal(liveSummary.preserved_count, 1)
  assert.equal(readdirSync(live.root).length, 1)

  const unknown = scratchFixture(t, { nonceByte: 'c' })
  const unknownSummary = recoverStaleColdstartScratch({ root: unknown.root, now: unknown.now, processIsAlive: () => 'unknown' })
  assert.equal(unknownSummary.unknown_owner_count, 1)
  assert.equal(unknownSummary.removed_count, 0)
  assert.equal(readdirSync(unknown.root).length, 1)
})

test('fresh scratch is preserved regardless of dead owner', t => {
  const fixture = scratchFixture(t, { ageMs: COLDSTART_SCRATCH_MAX_AGE_MS - 1, nonceByte: 'd' })
  const summary = recoverStaleColdstartScratch({ root: fixture.root, now: fixture.now, processIsAlive: () => 'dead' })
  assert.equal(summary.preserved_count, 1)
  assert.equal(summary.removed_count, 0)
})

test('recovery rejects invalid marker, nonmatching prefix, nested path and symlink target', t => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-scratch-reject-'))
  const outside = mkdtempSync(join(tmpdir(), 'coldstart-scratch-outside-'))
  t.after(() => { rmSync(root, { recursive: true, force: true }); rmSync(outside, { recursive: true, force: true }) })
  const link = join(root, COLDSTART_SCRATCH_PREFIX + 'e'.repeat(64))
  symlinkSync(outside, link, 'junction')
  const wrongPrefix = join(root, 'other-private-' + 'f'.repeat(64))
  mkdirSync(wrongPrefix)
  const nestedParent = join(root, 'nested')
  mkdirSync(nestedParent)
  const nested = join(nestedParent, COLDSTART_SCRATCH_PREFIX + '1'.repeat(64))
  mkdirSync(nested)
  const invalid = join(root, COLDSTART_SCRATCH_PREFIX + '2'.repeat(64))
  mkdirSync(invalid)
  writeFileSync(join(invalid, '.aimodeler-scratch-owner.json'), JSON.stringify({ schema: 'wrong' }))

  const summary = recoverStaleColdstartScratch({ root, now: Date.now() + COLDSTART_SCRATCH_MAX_AGE_MS + 1,
    processIsAlive: () => 'dead' })
  assert.equal(summary.rejected_count, 2)
  assert.equal(summary.scanned_count, 2)
  assert.equal(readdirSync(outside).length, 0)
  assert.equal(existsSync(link), true)
  assert.equal(existsSync(wrongPrefix), true)
  assert.equal(existsSync(nested), true)
})

test('recovery rejects a symlink temp root without scanning its external target', t => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-scratch-root-'))
  const alias = join(tmpdir(), 'coldstart-scratch-root-link-' + randomBytes(8).toString('hex'))
  t.after(() => { rmSync(root, { recursive: true, force: true }); rmSync(alias, { recursive: true, force: true }) })
  symlinkSync(root, alias, 'junction')
  const summary = recoverStaleColdstartScratch({ root: alias, now: Date.now() + COLDSTART_SCRATCH_MAX_AGE_MS + 1,
    processIsAlive: () => 'dead' })
  assert.equal(summary.status, 'partial')
  assert.equal(summary.failed_count, 1)
  assert.equal(summary.scanned_count, 0)
  assert.equal(readdirSync(root).length, 0)
})

test('scratch deletion failure reports safe state without path or error text', t => {
  const fixture = scratchFixture(t, { nonceByte: '3' })
  const summary = recoverStaleColdstartScratch({ root: fixture.root, now: fixture.now,
    processIsAlive: () => 'dead', remove: () => { throw new Error('private-path-and-error-sentinel') } })
  assert.equal(summary.status, 'partial')
  assert.equal(summary.failed_count, 1)
  const output = JSON.stringify(summary)
  assert.equal(output.includes(fixture.directory), false)
  assert.equal(output.includes('private-path'), false)
  assert.deepEqual(Object.keys(summary), ['status', 'scanned_count', 'removed_count', 'preserved_count',
    'rejected_count', 'unknown_owner_count', 'failed_count'])
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
    dsh: { status: 'completed', exit_code: 0, child_exited: true, failure: { method: 'unknown', error_class: 'none' } },
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
    toolBridgeCalls: [{ run_id: run.id, session_id: run.tool_session_id, turn_id: run.turn_id, call_id: nativeCallId, acp_call_id: 'acp-call', tool: 'cad_finish_model', authenticated_scope: true,
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

test('missing ACP terminal update blocks strict pairing and is distinguished from a failed terminal update', () => {
  const input = fixture()
  input.toolEvents = [input.toolEvents[0]]
  const result = evaluateColdstartGates(input)
  assert.equal(result.gates.cordis_cad_tools.status, 'failed')
  assert.equal(result.gates.acp_cad_tool_results.status, 'failed')
  assert.deepEqual(result.gates.acp_cad_tool_results.reasons, ['acp_cad_tool_result_completion_missing'])
  assert.deepEqual(result.matched_native_call_ids, [])
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

test('duplicate same-status terminal snapshots are idempotent for strict identity pairing', () => {
  const input = fixture()
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3000 })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'passed')
  assert.equal(result.gates.cordis_cad_tools.status, 'passed')
  assert.equal(result.gates.acp_cad_tool_results.status, 'passed')
  assert.deepEqual(result.matched_native_call_ids, ['11111111-1111-4111-8111-111111111111'])
})

test('repeated identical calls pair by exact ACP identity despite same arguments', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 3100, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3500, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, acp_call_id: 'second-acp-call', started_at_ms: 3200, completed_at_ms: 3400 })
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

test('same-name same-argument overlapping calls pair by exact Cordis exec callId', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents[1].at_ms = 2700
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 2500, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 2700, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls[0].started_at_ms = 2600
  input.toolBridgeCalls[0].completed_at_ms = 2800
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, acp_call_id: 'second-acp-call', started_at_ms: 2600, completed_at_ms: 2800 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const result = evaluateColdstartGates(input)
  assert.equal(result.gates.cordis_cad_tools.status, 'passed')
  assert.equal(result.status, 'passed')
})

test('native Cordis execute, bridge request, ACP updates, and strict gate share one exact call identity', async () => {
  const root = mkdtempSync(join(tmpdir(), 'cordis-acp-callid-e2e-'))
  const nodeModules = join(root, 'node_modules')
  mkdirSync(join(nodeModules, '@deepseek-ai', 'dsh'), { recursive: true })
  mkdirSync(join(nodeModules, '@deepseek-ai', 'dsh-tools'), { recursive: true })
  writeFileSync(join(nodeModules, '@deepseek-ai', 'dsh', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh', version: 'fixture' }))
  writeFileSync(join(nodeModules, '@deepseek-ai', 'dsh-tools', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: 'fixture', main: 'index.cjs' }))
  writeFileSync(join(nodeModules, '@deepseek-ai', 'dsh-tools', 'index.cjs'), 'exports.defineTool = value => value\n')
  const schemasPath = join(root, 'schemas.json'), turnPath = join(root, 'turn.json')
  writeFileSync(schemasPath, JSON.stringify({ schemaVersion: 1, tools: [{ name: 'cad_finish_model', description: 'finish', inputSchema: {
    type: 'object', properties: { fixture: { type: 'boolean' } }, required: ['fixture'], additionalProperties: false,
  } }] }))
  writeFileSync(turnPath, JSON.stringify({ turn_id: 'turn-new' }))
  const envKeys = ['AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE', 'AI_MODELER_TOOL_BRIDGE_ADDR', 'AI_MODELER_TOOL_BRIDGE_TOKEN',
    'AI_MODELER_TOOL_SESSION_ID', 'AI_MODELER_ACTIVE_TURN_FILE', 'AI_MODELER_DSH_NODE_MODULES', 'AI_MODELER_NATIVE_TOOLS_FILE']
  const previous = Object.fromEntries(envKeys.map(key => [key, process.env[key]]))
  const params = []
  const upstream = createServer(socket => {
    let data = ''
    socket.on('data', chunk => {
      data += chunk.toString()
      const end = data.indexOf('\n')
      if (end < 0) return
      const request = JSON.parse(data.slice(0, end))
      params.push(request.params)
      socket.end(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { text: 'ok', payload: {
        isError: false, execution_engine: 'rust', tool: request.params.name,
      } } }) + '\n')
    })
  })
  await new Promise((resolveListen, reject) => { upstream.once('error', reject); upstream.listen(0, '127.0.0.1', resolveListen) })
  try {
    process.env.AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE = schemasPath
    process.env.AI_MODELER_TOOL_BRIDGE_ADDR = `127.0.0.1:${upstream.address().port}`
    process.env.AI_MODELER_TOOL_BRIDGE_TOKEN = 'temporary-secret'
    process.env.AI_MODELER_TOOL_SESSION_ID = 'bridge-new'
    process.env.AI_MODELER_ACTIVE_TURN_FILE = turnPath
    process.env.AI_MODELER_DSH_NODE_MODULES = nodeModules
    process.env.AI_MODELER_NATIVE_TOOLS_FILE = join(root, 'registration.json')
    const registered = []
    applyNativeCordisTools({ tools: { register(tool) { registered.push(tool) } }, attachments: { async saveImages() { return [] } } })
    await registered[0].execute({ fixture: true }, { callId: 'acp-call', signal: new AbortController().signal })
    assert.equal(params.length, 1)
    assert.equal(params[0].acpCallId, 'acp-call')
    const input = fixture(), nativeId = params[0].toolCallId
    input.toolEvents.forEach(event => { event.tool_call_id = params[0].acpCallId })
    input.toolBridgeCalls[0].call_id = nativeId
    input.toolBridgeCalls[0].acp_call_id = params[0].acpCallId
    input.nativeAudit[0].call_id = nativeId
    const result = evaluateColdstartGates(input)
    assert.equal(result.gates.cordis_cad_tools.status, 'passed')
    assert.equal(result.status, 'passed')
    assert.deepEqual(result.matched_native_call_ids, [nativeId])
  } finally {
    for (const key of envKeys) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
    await new Promise(resolveClose => upstream.close(resolveClose))
    rmSync(root, { recursive: true, force: true })
  }
})

test('missing or mismatched Cordis exec callId remains fail closed', () => {
  for (const mutate of [call => { delete call.acp_call_id }, call => { call.acp_call_id = 'wrong-acp-call' }]) {
    const input = fixture()
    mutate(input.toolBridgeCalls[0])
    const result = evaluateColdstartGates(input)
    assert.equal(result.gates.cordis_cad_tools.status, 'failed')
    assert.ok(result.failure_reasons.includes('cordis_tool_arguments_or_identity_missing_or_unmatched'))
  }
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
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, acp_call_id: 'second-acp-call', started_at_ms: 3200, completed_at_ms: 3400 })
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

test('repeated failed terminal snapshots remain failed without a duplicate-notification failure', () => {
  const input = fixture()
  input.toolEvents[1].status = 'failed'
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3000 })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.deepEqual(result.gates.acp_cad_tool_results.reasons, ['acp_cad_tool_result_failed'])
})

test('conflicting terminal ACP states remain a hard failure', () => {
  const input = fixture()
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3000, status: 'failed' })
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.deepEqual(result.gates.acp_cad_tool_results.reasons, [
    'acp_cad_tool_result_failed', 'acp_cad_tool_result_terminal_status_invalid',
  ])
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
  assert.equal(projection.schema, 'acp_terminal_update_projection_v4')
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
  assert.deepEqual(projection.repeated_argument_fingerprint_counts, [])
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

test('ACP lifecycle sequence diagnostics classify ordered call status without publishing identities or payloads', () => {
  const id = name => `private-acp-call-id-${name}`
  const start = (name, tool, status = 'in_progress') => ({ kind: 'tool_call', tool_call_id: id(name), title: tool,
    status, arguments_sha256_24: 'a'.repeat(24), arguments: { secret: `private-argument-${name}` } })
  const update = (name, status, extra = {}) => ({ kind: 'tool_call_update', tool_call_id: id(name), status,
    content: `private-content-${name}`, error: `private-error-${name}`, at_ms: 1700000000000, ...extra })
  const events = [
    start('complete', 'cad_add_feature'), update('complete', 'completed'),
    start('failed', 'cad_update_feature'), update('failed', 'failed'),
    start('open', 'cad_get_state'),
    start('complete-no-progress', 'cad_finish_model', 'other'), update('complete-no-progress', 'completed'),
    start('failed-no-progress', 'cad_validate_model', 'other'), update('failed-no-progress', 'failed'),
    start('after-terminal', 'cad_preview_model'), update('after-terminal', 'completed'), update('after-terminal', 'in_progress'),
    start('repeat', 'cad_add_hole_array'), update('repeat', 'completed'), update('repeat', 'completed'),
    start('conflict', 'cad_remove_feature'), update('conflict', 'completed'), update('conflict', 'failed'),
    start('unknown-title', 'cad_private_tool_name'), update('unknown-title', 'completed'),
  ]
  const projection = summarizeAcpTerminalUpdateDiagnostics(events)
  assert.deepEqual(projection.call_group_status_sequences, [
    { tool_name: 'cad_add_feature', sequence: 'progress_then_completed', count: 1 },
    { tool_name: 'cad_add_hole_array', sequence: 'repeated_same_terminal_status', count: 1 },
    { tool_name: 'cad_finish_model', sequence: 'completed_without_progress', count: 1 },
    { tool_name: 'cad_get_state', sequence: 'no_terminal_status', count: 1 },
    { tool_name: 'cad_preview_model', sequence: 'update_after_terminal', count: 1 },
    { tool_name: 'cad_remove_feature', sequence: 'conflicting_terminal_statuses', count: 1 },
    { tool_name: 'cad_update_feature', sequence: 'progress_then_failed', count: 1 },
    { tool_name: 'cad_validate_model', sequence: 'failed_without_progress', count: 1 },
    { tool_name: 'unknown', sequence: 'progress_then_completed', count: 1 },
  ])
  const serialized = JSON.stringify(projection)
  for (const secret of ['private-acp-call-id-', 'private-argument-', 'private-content-', 'private-error-',
    'cad_private_tool_name', '1700000000000', 'a'.repeat(24)]) {
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
  hidden.toolBridgeCalls[0].acp_call_id = 'sentinel-acp-call-identity'
  const hiddenProjection = summarizeCordisBindingDiagnostics(hidden)
  const serialized = JSON.stringify(hiddenProjection)
  for (const secret of ['cad_private_tool_name', 'private-error-body-marker', '11111111-1111-4111-8111-111111111111',
    'acp-call', 'sentinel-acp-call-identity', hidden.toolBridgeCalls[0].arguments_sha256_24]) assert.equal(serialized.includes(secret), false)
  assert.ok(hiddenProjection.counts.every(row => ['matched', 'bridge_call_missing', 'acp_call_missing', 'tool_name_not_allowlisted',
    'correlation_fingerprint_missing', 'invocation_count_mismatch', 'identity_pairing_ambiguous', 'acp_identity_missing',
    'run_scope_mismatch', 'bridge_call_id_missing', 'bridge_scope_rejected', 'bridge_time_invalid',
    'bridge_execution_rejected', 'schema_missing', 'registration_missing', 'native_audit_missing',
    'native_audit_tool_mismatch', 'native_audit_out_of_run', 'native_audit_rejected', 'bridge_dispatch_failed'].includes(row.outcome)))
})

test('bridge dispatch diagnostics retain fixed failure stage and code only', () => {
  const input = fixture()
  const call = input.toolBridgeCalls[0]
  call.success = false
  call.dispatch_stage = 'pre_executor_deadline'
  call.dispatch_failure_code = 'deadline_exceeded'
  call.call_id = 'private-call-id-sentinel'
  call.arguments_sha256_24 = 'a'.repeat(24)
  call.error = 'private-error-body-sentinel'
  const projection = summarizeCordisBindingDiagnostics(input)
  const row = projection.counts.find(item => item.stage === 'bridge_dispatch')
  assert.deepEqual(row, { tool_name: 'cad_finish_model', stage: 'bridge_dispatch', outcome: 'bridge_dispatch_failed',
    dispatch_stage: 'pre_executor_deadline', failure_code: 'deadline_exceeded', count: 1 })
  call.dispatch_stage = 'raw-stage-sentinel'
  call.dispatch_failure_code = 'raw-error-code-sentinel'
  const sanitized = summarizeCordisBindingDiagnostics(input).counts.find(item => item.stage === 'bridge_dispatch')
  assert.equal(sanitized.dispatch_stage, 'unknown')
  assert.equal(sanitized.failure_code, 'unknown')
  const serialized = JSON.stringify(projection)
  for (const secret of ['private-call-id-sentinel', 'private-error-body-sentinel', 'a'.repeat(24)]) assert.equal(serialized.includes(secret), false)
})

test('Cordis binding projection uses exact ACP identities for repeated identical calls', () => {
  const input = fixture(), nativeCallId = '22222222-2222-4222-8222-222222222222'
  input.toolEvents.push({ ...input.toolEvents[0], at_ms: 3100, tool_call_id: 'second-acp-call' })
  input.toolEvents.push({ ...input.toolEvents[1], at_ms: 3500, tool_call_id: 'second-acp-call' })
  input.toolBridgeCalls.push({ ...input.toolBridgeCalls[0], call_id: nativeCallId, acp_call_id: 'second-acp-call', started_at_ms: 3200, completed_at_ms: 3400 })
  input.nativeAudit.push({ ...input.nativeAudit[0], call_id: nativeCallId })
  const projection = summarizeCordisBindingDiagnostics(input)
  assert.deepEqual(projection.counts.filter(row => row.stage === 'acp_to_bridge'), [
    { tool_name: 'cad_finish_model', stage: 'acp_to_bridge', outcome: 'matched', count: 2 },
  ])
})

test('ACP diagnostics count repeated tool argument fingerprints without publishing fingerprints', () => {
  const events = [
    { kind: 'tool_call', tool_call_id: 'a', title: 'cad_get_reference_image', arguments_sha256_24: 'a'.repeat(24) },
    { kind: 'tool_call', tool_call_id: 'a', title: 'cad_get_reference_image', arguments_sha256_24: 'a'.repeat(24) },
    { kind: 'tool_call', tool_call_id: 'b', title: 'cad_get_reference_image', arguments_sha256_24: 'a'.repeat(24) },
    { kind: 'tool_call', tool_call_id: 'c', title: 'cad_get_reference_image', arguments_sha256_24: 'b'.repeat(24) },
    { kind: 'tool_call', tool_call_id: 'd', title: 'cad_get_reference_image', arguments_sha256_24: 'b'.repeat(24) },
    { kind: 'tool_call', tool_call_id: 'e', title: 'cad_get_reference_image', arguments_sha256_24: 'b'.repeat(24) },
  ]
  const projection = summarizeAcpTerminalUpdateDiagnostics(events)
  assert.deepEqual(projection.repeated_argument_fingerprint_counts,
    [{ tool_name: 'cad_get_reference_image', repeated_call_count: 3 }])
  const serialized = JSON.stringify(projection)
  assert.equal(serialized.includes('a'.repeat(24)), false)
  assert.equal(serialized.includes('b'.repeat(24)), false)
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

test('failed bridge call reports a matching timed native audit rejection without leaking call data', () => {
  const input = fixture()
  const call = input.toolBridgeCalls[0]
  call.success = false
  call.error = 'private-bridge-error-marker'
  call.arguments = { diameter: 'private-argument-marker' }
  input.nativeAudit[0].success = false
  input.nativeAudit[0].error = { type: 'argument_validation', message: 'private-native-error-marker' }
  const projection = summarizeCordisBindingDiagnostics(input)
  const outcomeCount = code => projection.counts.find(row => row.stage === 'bridge_to_audit' && row.outcome === code)?.count || 0
  assert.equal(outcomeCount('native_audit_rejected'), 1)
  assert.equal(outcomeCount('bridge_execution_rejected'), 0)
  assert.equal(projection.counts.find(row => row.stage === 'bridge_to_audit' && row.outcome === 'native_audit_rejected')?.error_class,
    'argument_validation')
  const serialized = JSON.stringify(projection)
  for (const secret of [call.call_id, call.acp_call_id, call.arguments_sha256_24, call.error,
    call.arguments.diameter, input.nativeAudit[0].error.message]) assert.equal(serialized.includes(secret), false)
  for (const errorType of ['state_conflict', 'execution', 'private-type-sentinel']) {
    input.nativeAudit[0].error.type = errorType
    const safe = JSON.stringify(summarizeCordisBindingDiagnostics(input))
    assert.equal(safe.includes(errorType === 'private-type-sentinel' ? errorType : 'private-native-error-marker'), false)
    const classes = summarizeCordisBindingDiagnostics(input).counts
      .filter(row => row.stage === 'bridge_to_audit' && row.outcome === 'native_audit_rejected')
      .map(row => row.error_class)
    assert.deepEqual(classes, [errorType === 'private-type-sentinel' ? 'unknown' : errorType])
  }
})

test('failed bridge call without a native audit row remains a bridge rejection', () => {
  const input = fixture()
  const call = input.toolBridgeCalls[0]
  call.success = false
  call.error = 'private-bridge-error-marker'
  call.arguments = { diameter: 'private-argument-marker' }
  input.nativeAudit = []
  const projection = summarizeCordisBindingDiagnostics(input)
  assert.ok(projection.counts.some(row => row.stage === 'bridge_to_audit'
    && row.outcome === 'bridge_execution_rejected' && row.count === 1))
  const serialized = JSON.stringify(projection)
  for (const secret of [call.call_id, call.acp_call_id, call.arguments_sha256_24, call.error,
    call.arguments.diameter]) assert.equal(serialized.includes(secret), false)
})

test('failed bridge call with a successful native audit remains a bridge rejection', () => {
  const input = fixture()
  const call = input.toolBridgeCalls[0]
  call.success = false
  call.error = 'private-bridge-error-marker'
  call.arguments = { diameter: 'private-argument-marker' }
  input.nativeAudit[0].success = true
  input.nativeAudit[0].error = 'private-native-error-marker'
  const projection = summarizeCordisBindingDiagnostics(input)
  assert.ok(projection.counts.some(row => row.stage === 'bridge_to_audit'
    && row.outcome === 'bridge_execution_rejected' && row.count === 1))
  assert.equal(projection.counts.some(row => row.stage === 'bridge_to_audit' && row.outcome === 'native_audit_rejected'), false)
  const serialized = JSON.stringify(projection)
  for (const secret of [call.call_id, call.acp_call_id, call.arguments_sha256_24, call.error,
    call.arguments.diameter, input.nativeAudit[0].error]) assert.equal(serialized.includes(secret), false)
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
    acpProjection.call_group_status_sequences.push(
      { tool_name: secrets[10], sequence: 'progress_then_completed', count: 42 },
      { tool_name: 'cad_finish_model', sequence: secrets[9], count: 42 },
      { tool_name: 'cad_finish_model', sequence: 'progress_then_completed', count: secrets[8] },
    )
    acpProjection.repeated_argument_fingerprint_counts = [
      { tool_name: 'cad_finish_model', repeated_call_count: 4 },
      { tool_name: secrets[10], repeated_call_count: 99 },
      { tool_name: 'cad_get_reference_image', repeated_call_count: secrets[8] },
    ]
    const bindingProjection = summarizeCordisBindingDiagnostics({
      ...fixture(),
      toolEvents: events.map(event => ({ ...event, run_id: 'temporary-run-id', session_id: secrets[1], turn_id: 'temporary-turn-id' })),
    })
    bindingProjection.debug_payload = secrets.join('|')
    const strict = {
      status: 'failed', scope: secrets[6],
      successful_image_request_ids: [secrets[1]], matched_native_call_ids: [secrets[1]],
      answer: secrets[2], thought: secrets[3], payload: secrets[4], arguments: secrets[5],
      failure_reasons: [secrets[8]], evidence_errors: [secrets[9]], gates: {
        run_scope: { status: 'failed', reasons: ['run_identity_missing', secrets[8]] },
        acp_cad_tool_results: { status: 'passed', reasons: [] }, [secrets[10]]: { status: 'passed', reasons: [] },
      },
    }
    const summary = buildSafeAcceptanceSummary({ strict, transportStatus: 'completed', modelStatus: secrets[7],
      runtime: { node: secrets[7], dsh: secrets[7], directory: secrets[0] },
      source: { bytes: 72309, sha256: 'b'.repeat(64), unchanged: true, reference_id: secrets[1] },
      scratchCleanupStatus: secrets[7], dshDiagnostic: { method: secrets[10], error_class: secrets[8], message: secrets[0] } })
    writeSafeAcceptanceArtifacts(output, { summary, acpProjection, bindingProjection,
      visionProjection: summarizeVisualReviewDiagnostics({ reference_similarity_history: [{
        review_diagnostics: { required_evidence_count: 2, mapped_evidence_count: 2, unmapped_evidence_count: 0,
          missing_evidence_count: 0, unsupported_evidence_count: 0, out_of_scope_evidence_count: 0,
          mismatch_evidence_count: 0, unknown_evidence_count: 0,
          view_comparable: true, coverage_class: 'verified', stage_eligible: true, secret: secrets[0] },
        summary: secrets[1], evidence: [{ evidence_id: secrets[2], reference: secrets[3] }],
      }] }),
      buildStatus: { status: 'passed', exit_code: 0, elapsed_ms: 12, stdout_bytes: 0, stderr_bytes: 0, raw: secrets[4] } })
    const listFiles = directory => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
      const path = join(directory, entry.name)
      return entry.isDirectory() ? listFiles(path) : [relative(output, path)]
    })
    const files = listFiles(output).sort()
    assert.deepEqual(files, ['build-status.json', 'summary.json', 'tool-result-diagnostics.jsonl'])
    const serialized = files.map(path => readFileSync(join(output, path), 'utf8')).join('\n')
    for (const secret of secrets) assert.equal(serialized.includes(secret), false)
    assert.equal(summary.status, 'failed')
    assert.equal(summary.strict_gates.gates.run_scope.status, 'failed')
    assert.equal(summary.strict_gates.gates.run_scope.reason_count, 2)
    assert.deepEqual(summary.strict_gates.gates.run_scope.reason_codes, ['run_identity_missing'])
    assert.deepEqual(summary.strict_gates.gates.acp_cad_tool_results.reason_codes, [])
    assert.equal(summary.strict_gates.failure_reason_count, 1)
    assert.equal(summary.strict_gates.evidence_error_count, 1)
    assert.equal(summary.status_scope, 'scope_unverified')
    assert.equal(summary.runtime.node, 'unverified')
    assert.equal(summary.scratch_cleanup_status, 'not_created')
    assert.deepEqual(summary.dsh_diagnostic, { schema: 'dsh_rpc_failure_diagnostic_v1', stage: 'unknown', method: 'unknown', error_class: 'unknown', request_sequence: null, rpc_error_code: 'unknown' })
    assert.deepEqual(Object.keys(summary.strict_gates.gates), [
      'run_scope', 'dsh_session', 'dsh_process', 'trace_integrity', 'original_image', 'main_image_wire', 'cordis_cad_tools',
      'acp_cad_tool_results', 'current_geometry', 'artifacts', 'holes', 'visual', 'finish',
    ])
    const knownValues = buildSafeAcceptanceSummary({
      strict: { status: 'passed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
        gates: { run_scope: { status: 'passed', reasons: [] } } },
      transportStatus: 'completed', modelStatus: 'accepted_by_strict_cli_gate',
      runtime: { node: 'v22.23.3', dsh: '0.2.0-rc.2' },
      source: { bytes: 72309, sha256: 'b'.repeat(64), unchanged: true }, scratchCleanupStatus: 'removed',
      scratchRecovery: { status: 'secret-status', scanned_count: 2, removed_count: 1, path: 'private-path-sentinel' },
    })
    assert.equal(knownValues.status_scope, 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance')
    assert.deepEqual(knownValues.runtime, { node: 'v22.23.3', dsh: '0.2.0-rc.2' })
    assert.equal(knownValues.model_status, 'accepted_by_strict_cli_gate')
    assert.deepEqual(knownValues.dsh_diagnostic, { schema: 'dsh_rpc_failure_diagnostic_v1', stage: 'unknown', method: 'unknown', error_class: 'none', request_sequence: null, rpc_error_code: 'unknown' })
    assert.equal(knownValues.scratch_cleanup_status, 'removed')
    assert.deepEqual(knownValues.scratch_recovery, { status: 'complete', scanned_count: 2, removed_count: 1,
      preserved_count: 0, rejected_count: 0, unknown_owner_count: 0, failed_count: 0 })
    assert.equal(JSON.stringify(knownValues.scratch_recovery).includes('private-path'), false)
    assert.equal(buildSafeAcceptanceSummary({ scratchCleanupStatus: 'failed' }).scratch_cleanup_status, 'failed')
    const strictPassCleanupFailure = buildSafeAcceptanceSummary({
      strict: { status: 'passed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
        gates: { run_scope: { status: 'passed', reasons: [] } } },
      transportStatus: 'completed', modelStatus: 'accepted_by_strict_cli_gate',
      runtime: { node: 'v22.23.3', dsh: '0.2.0-rc.2' },
      source: { bytes: 72309, sha256: 'b'.repeat(64), unchanged: true }, scratchCleanupStatus: 'failed',
    })
    assert.equal(strictPassCleanupFailure.status, 'failed')
    assert.equal(strictPassCleanupFailure.strict_gates.status, 'passed')
    assert.equal(strictPassCleanupFailure.scratch_cleanup_status, 'failed')
    const projections = readFileSync(join(output, 'tool-result-diagnostics.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
    assert.equal(projections[0].schema, 'acp_terminal_update_projection_v4')
    assert.deepEqual(projections[0].call_group_status_sequences,
      [{ tool_name: 'cad_finish_model', sequence: 'progress_then_completed', count: 1 }])
    assert.deepEqual(projections[0].repeated_argument_fingerprint_counts, [
      { tool_name: 'cad_finish_model', repeated_call_count: 4 },
      { tool_name: 'unknown', repeated_call_count: 99 },
      { tool_name: 'cad_get_reference_image', repeated_call_count: 0 },
    ])
    assert.deepEqual(projections[0].call_group_terminal_distribution,
      [{ tool_name: 'cad_finish_model', terminal_category: 'completed_once', count: 1 }])
    assert.equal(projections[1].schema, 'cordis_binding_projection_v1')
    assert.deepEqual(projections[2], { stage: 'vision_review_projection', schema: 'vision_review_diagnostics_v1', review_count: 1,
      reviews: [{ required_evidence_count: 2, mapped_evidence_count: 2, unmapped_evidence_count: 0,
        missing_evidence_count: 0, unsupported_evidence_count: 0, out_of_scope_evidence_count: 0,
        mismatch_evidence_count: 0, unknown_evidence_count: 0,
        view_comparable: true, coverage_class: 'verified', stage_eligible: true }] })
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('published Cordis diagnostics preserve fixed native audit error classes only', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-safe-native-error-class-'))
  const output = join(root, 'run')
  const input = fixture()
  const errorTypes = ['argument_validation', 'state_conflict', 'execution', 'raw-native-error-class-sentinel']
  const cases = [
    { bridgeSuccess: true, auditSuccess: true },
    ...errorTypes.map((errorClass, index) => ({
      bridgeSuccess: false, auditSuccess: false, errorClass,
    })),
  ]
  const secrets = []
  input.toolEvents = []
  input.toolBridgeCalls = []
  input.nativeAudit = []

  for (const [index, item] of cases.entries()) {
    const acpCallId = `raw-acp-call-id-sentinel-${index}`
    const nativeCallId = `raw-native-call-id-sentinel-${index}`
    const argumentValue = `raw-argument-value-sentinel-${index}`
    const bridgeError = `raw-bridge-error-sentinel-${index}`
    const nativeError = `raw-native-error-sentinel-${index}`
    const argumentFingerprint = toolArgumentsFingerprint({ value: argumentValue })
    const startedAt = 2200 + index * 100
    secrets.push(acpCallId, nativeCallId, argumentValue, bridgeError, nativeError, argumentFingerprint)
    input.toolEvents.push(
      { run_id: input.run.id, session_id: input.run.acp_session_id, turn_id: input.run.turn_id,
        at_ms: startedAt, tool_call_id: acpCallId, title: 'cad_finish_model', status: 'in_progress',
        kind: 'tool_call', arguments_sha256_24: argumentFingerprint },
      { run_id: input.run.id, session_id: input.run.acp_session_id, turn_id: input.run.turn_id,
        at_ms: startedAt + 40, tool_call_id: acpCallId, title: 'cad_finish_model',
        status: item.bridgeSuccess ? 'completed' : 'failed', kind: 'tool_call_update' },
    )
    input.toolBridgeCalls.push({ run_id: input.run.id, session_id: input.run.tool_session_id,
      turn_id: input.run.turn_id, call_id: nativeCallId, acp_call_id: acpCallId, tool: 'cad_finish_model',
      authenticated_scope: true, started_at_ms: startedAt + 10, completed_at_ms: startedAt + 30,
      success: item.bridgeSuccess, arguments_sha256_24: argumentFingerprint, arguments: { value: argumentValue },
      error: bridgeError })
    input.nativeAudit.push({ call_id: nativeCallId, tool: 'cad_finish_model', at: 3 + index / 10,
      success: item.auditSuccess, ...(item.errorClass ? { error: { type: item.errorClass, message: nativeError } } : {}) })
  }

  try {
    const bindingProjection = summarizeCordisBindingDiagnostics(input)
    const summarizedUnknown = bindingProjection.counts.find(row => row.stage === 'bridge_to_audit'
      && row.outcome === 'native_audit_rejected' && row.error_class === 'unknown')
    assert.ok(summarizedUnknown, 'invalid native audit error class must first normalize to unknown')
    // Exercise the publication boundary as well: if an upstream projection is
    // malformed, only the fixed enum may cross into the published JSONL.
    summarizedUnknown.error_class = 'raw-native-error-class-sentinel'
    summarizedUnknown.call_id = secrets[1]
    summarizedUnknown.error_message = secrets[4]
    bindingProjection.raw_error = secrets.join('|')
    writeSafeAcceptanceArtifacts(output, {
      summary: buildSafeAcceptanceSummary({}), acpProjection: {}, bindingProjection, visionProjection: {},
      buildStatus: { status: 'passed', exit_code: 0 },
    })

    const publishedText = readFileSync(join(output, 'tool-result-diagnostics.jsonl'), 'utf8')
    const cordis = publishedText.trim().split('\n').map(JSON.parse)
      .find(row => row.stage === 'cordis_binding_projection')
    assert.equal(cordis.schema, 'cordis_binding_projection_v1')
    const rejected = cordis.counts.filter(row => row.stage === 'bridge_to_audit' && row.outcome === 'native_audit_rejected')
    assert.deepEqual(rejected.map(row => row.error_class).sort(),
      ['argument_validation', 'execution', 'state_conflict', 'unknown'])
    const success = cordis.counts.find(row => row.stage === 'bridge_to_audit' && row.outcome === 'matched')
    assert.equal(success.count, 1)
    assert.equal(Object.hasOwn(success, 'error_class'), false)
    const dispatchProjection = summarizeCordisBindingDiagnostics({ ...fixture(), toolBridgeCalls: [{
      tool: 'cad_finish_model', success: false, dispatch_stage: 'executor_return', dispatch_failure_code: 'executor_failed',
    }] })
    const dispatchRow = dispatchProjection.counts.find(row => row.stage === 'bridge_dispatch')
    dispatchRow.dispatch_stage = 'raw-stage-sentinel'
    dispatchRow.failure_code = 'raw-failure-sentinel'
    writeSafeAcceptanceArtifacts(join(root, 'dispatch-run'), {
      summary: buildSafeAcceptanceSummary({}), acpProjection: {}, bindingProjection: dispatchProjection, visionProjection: {},
      buildStatus: { status: 'passed', exit_code: 0 },
    })
    const dispatchText = readFileSync(join(root, 'dispatch-run', 'tool-result-diagnostics.jsonl'), 'utf8')
    const dispatchRecord = dispatchText.trim().split('\n').map(JSON.parse)
      .find(row => row.stage === 'cordis_binding_projection')
    const publishedDispatch = dispatchRecord.counts.find(row => row.stage === 'bridge_dispatch')
    assert.equal(publishedDispatch.dispatch_stage, 'unknown')
    assert.equal(publishedDispatch.failure_code, 'unknown')
    assert.equal(dispatchText.includes('raw-stage-sentinel'), false)
    assert.equal(dispatchText.includes('raw-failure-sentinel'), false)
    for (const secret of [...secrets, 'raw-native-error-class-sentinel']) {
      assert.equal(publishedText.includes(secret), false, `published JSONL leaked ${secret}`)
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('visual review diagnostics retain only bounded counts and fixed status enums', () => {
  const projection = summarizeVisualReviewDiagnostics({ reference_similarity_history: [
    { review_diagnostics: { required_evidence_count: 3, mapped_evidence_count: 3, unmapped_evidence_count: 0,
      missing_evidence_count: 0, unsupported_evidence_count: 0, out_of_scope_evidence_count: 0,
      mismatch_evidence_count: 0, unknown_evidence_count: 0,
      view_comparable: true, coverage_class: 'verified', stage_eligible: true } },
    { review_diagnostics: { required_evidence_count: 3, mapped_evidence_count: 2, unmapped_evidence_count: 1,
      missing_evidence_count: 1, unsupported_evidence_count: 1, out_of_scope_evidence_count: 0,
      mismatch_evidence_count: 1, unknown_evidence_count: 0,
      view_comparable: true, coverage_class: 'partial', stage_eligible: false, summary: 'free-text-sentinel',
      reference_id: 'id-sentinel', source_sha256: 'hash-sentinel', response_id: 'call-sentinel' } },
    { review_diagnostics: { required_evidence_count: 'count-sentinel', mapped_evidence_count: -1,
      unmapped_evidence_count: 1_000_001, missing_evidence_count: null, unsupported_evidence_count: null,
      out_of_scope_evidence_count: null, mismatch_evidence_count: null, unknown_evidence_count: null,
      view_comparable: 'boolean-sentinel', coverage_class: 'unknown-sentinel',
      stage_eligible: 'boolean-sentinel', evidence_id: 'evidence-sentinel' } },
    { summary: 'missing-diagnostics-sentinel', payload: 'payload-sentinel' },
  ] })
  assert.equal(projection.schema, 'vision_review_diagnostics_v1')
  assert.equal(projection.review_count, 4)
  assert.deepEqual(projection.reviews[0], { required_evidence_count: 3, mapped_evidence_count: 3,
    unmapped_evidence_count: 0, missing_evidence_count: 0, unsupported_evidence_count: 0,
    out_of_scope_evidence_count: 0, mismatch_evidence_count: 0, unknown_evidence_count: 0,
    view_comparable: true, coverage_class: 'verified', stage_eligible: true })
  assert.deepEqual(projection.reviews[1], { required_evidence_count: 3, mapped_evidence_count: 2,
    unmapped_evidence_count: 1, missing_evidence_count: 1, unsupported_evidence_count: 1,
    out_of_scope_evidence_count: 0, mismatch_evidence_count: 1, unknown_evidence_count: 0,
    view_comparable: true, coverage_class: 'partial', stage_eligible: false })
  assert.deepEqual(projection.reviews[2], { required_evidence_count: null, mapped_evidence_count: null,
    unmapped_evidence_count: null, missing_evidence_count: null, unsupported_evidence_count: null,
    out_of_scope_evidence_count: null, mismatch_evidence_count: null, unknown_evidence_count: null,
    view_comparable: null, coverage_class: 'unknown', stage_eligible: null })
  assert.deepEqual(projection.reviews[3], { required_evidence_count: null, mapped_evidence_count: null,
    unmapped_evidence_count: null, missing_evidence_count: null, unsupported_evidence_count: null,
    out_of_scope_evidence_count: null, mismatch_evidence_count: null, unknown_evidence_count: null,
    view_comparable: null, coverage_class: 'unknown', stage_eligible: null })
  const serialized = JSON.stringify(projection)
  for (const secret of ['free-text-sentinel', 'id-sentinel', 'hash-sentinel', 'call-sentinel', 'count-sentinel',
    'boolean-sentinel', 'unknown-sentinel', 'evidence-sentinel', 'missing-diagnostics-sentinel', 'payload-sentinel']) {
    assert.equal(serialized.includes(secret), false, `must omit ${secret}`)
  }
})

test('fatal failure publication uses only fixed stage and code enums', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-safe-failure-'))
  const output = join(root, 'run')
  const secrets = ['provider-error-sentinel', 'secret-key-sentinel', 'model-answer-sentinel',
    'prompt-sentinel', 'tool-arguments-sentinel', 'payload-sentinel', 'exception-stack-sentinel']
  try {
    const summary = writeSafeFailureSummary(output, {
      stage: 'dsh_session', code: 'unhandled_rejection', scratchCleanupStatus: 'removed',
      scratchRecovery: { status: 'complete', scanned_count: 1, path: secrets[0], error: secrets[1] },
      // Exception details are deliberately not accepted by the publisher.
      exception: new Error(secrets.join('|')),
    })
    assert.deepEqual(readdirSync(output).sort(), ['summary.json'])
    assert.deepEqual(summary.failure_diagnostic, { stage: 'dsh_session', phase: 'unknown', code: 'unhandled_rejection' })
    const serialized = readFileSync(join(output, 'summary.json'), 'utf8')
    for (const secret of secrets) assert.equal(serialized.includes(secret), false)
    assert.equal(serialized.includes('exception'), false)
    assert.equal(summary.strict_gates.status, 'failed')
    assert.equal(summary.status, 'failed')
    assert.throws(() => writeSafeFailureSummary(output, { stage: 'publication', code: 'uncaught_exception' }), /EEXIST/)
    assert.equal(readFileSync(join(output, 'summary.json'), 'utf8'), serialized)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('publication fatal preserves evaluated gate allowlist and marks unavailable gates unknown', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-publication-gates-'))
  const output = join(root, 'run')
  const sentinel = 'provider-tool-geometry-secret-sentinel'
  const phases = []
  try {
    mkdirSync(output, { recursive: true })
    // Force the regular artifact publisher to fail after its first allowlisted
    // file but before summary publication, as in the coldstart fatal fallback.
    mkdirSync(join(output, 'build-status.json'))
    const strict = {
      status: 'failed', failure_reasons: ['private-summary-reason'], evidence_errors: ['private-evidence-error'],
      gates: {
        run_scope: { status: 'passed', reasons: [] },
        dsh_session: { status: 'failed', reasons: ['dsh_session_not_completed', sentinel] },
      },
      provider: { response: sentinel }, geometry: { payload: sentinel },
    }
    const evaluatedSummary = buildSafeAcceptanceSummary({ strict })
    assert.throws(() => writeSafeAcceptanceArtifacts(output, {
      summary: evaluatedSummary, acpProjection: {}, bindingProjection: {}, visionProjection: {},
      buildStatus: { status: 'passed', exit_code: 0 },
      onPhase: phase => phases.push(phase),
    }), error => ['EISDIR', 'EPERM', 'EEXIST'].includes(error.code))
    assert.deepEqual(phases, [SAFE_PUBLICATION_PHASE.WRITING_DIAGNOSTICS,
      SAFE_PUBLICATION_PHASE.WRITING_BUILD_STATUS])
    const evaluated = evaluatedSummary.strict_gates
    const failed = writeSafeFailureSummary(output, {
      stage: 'publication', phase: phases.at(-1), code: 'uncaught_exception', strictGates: evaluated,
      exception: new Error(sentinel),
    })
    assert.deepEqual(failed.failure_diagnostic, {
      stage: 'publication', phase: 'writing_build_status', code: 'uncaught_exception',
    })
    assert.deepEqual(Object.keys(failed.strict_gates.gates), [
      'run_scope', 'dsh_session', 'dsh_process', 'trace_integrity', 'original_image', 'main_image_wire',
      'cordis_cad_tools', 'acp_cad_tool_results', 'current_geometry', 'artifacts', 'holes', 'visual', 'finish',
    ])
    assert.deepEqual(failed.strict_gates.gates.run_scope,
      { status: 'passed', reason_count: 0, reason_codes: [] })
    assert.deepEqual(failed.strict_gates.gates.dsh_session,
      { status: 'failed', reason_count: 2, reason_codes: ['dsh_session_not_completed'] })
    assert.deepEqual(failed.strict_gates.gates.visual,
      { status: 'unknown', reason_count: null, reason_codes: [] })
    assert.equal(failed.strict_gates.failure_reason_count, 1)
    assert.equal(failed.strict_gates.evidence_error_count, 1)
    const serialized = readFileSync(join(output, 'summary.json'), 'utf8')
    assert.equal(serialized.includes(sentinel), false)
    assert.equal(serialized.includes('private-summary-reason'), false)

    const noEvaluation = writeSafeFailureSummary(join(root, 'no-evaluation'), {
      stage: 'publication', code: 'uncaught_exception',
    })
    assert.equal(noEvaluation.failure_diagnostic.phase, 'unknown')
    assert.ok(Object.values(noEvaluation.strict_gates.gates).every(gate => gate.status === 'unknown'))
    assert.ok(Object.values(noEvaluation.strict_gates.gates).every(gate => gate.reason_count === null))
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('publication phases are fixed, ordered, and distinguish runner completion', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-publication-phases-'))
  const phases = []
  try {
    const summary = buildSafeAcceptanceSummary({})
    writeSafeAcceptanceArtifacts(join(root, 'run'), {
      summary, acpProjection: {}, bindingProjection: {}, visionProjection: {},
      buildStatus: { status: 'passed', exit_code: 0 },
      onPhase: phase => phases.push(phase),
    })
    phases.push(SAFE_PUBLICATION_PHASE.RUNNER_COMPLETION)
    assert.deepEqual(phases, [
      'writing_diagnostics', 'writing_build_status', 'writing_summary',
      'publication_complete', 'runner_completion',
    ])
    assert.deepEqual(buildSafeFailureDiagnostic({ stage: 'publication', phase: 'writing_summary',
      code: 'uncaught_exception' }), {
      stage: 'publication', phase: 'writing_summary', code: 'uncaught_exception',
    })
    const unknown = buildSafeFailureDiagnostic({
      stage: 'publication', phase: 'provider-answer-and-stack-sentinel', code: 'raw-exception-sentinel',
    })
    assert.deepEqual(unknown, { stage: 'publication', phase: 'unknown', code: 'uncaught_exception' })
    assert.equal(JSON.stringify(unknown).includes('sentinel'), false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('fatal failure publication rejects unknown stage and code values', () => {
  // The synthetic error-like strings stand in for any provider/exception text;
  // callers can publish only these fixed classifications.
  const root = mkdtempSync(join(tmpdir(), 'coldstart-safe-failure-enum-'))
  try {
    const failed = writeSafeFailureSummary(join(root, 'run'), {
      stage: 'private-provider-response-sentinel', code: 'raw-error-message-sentinel',
    })
    assert.deepEqual(failed.failure_diagnostic, { stage: 'unknown', phase: 'unknown', code: 'uncaught_exception' })
    assert.equal(JSON.stringify(failed).includes('sentinel'), false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('Windows late fatal atomically replaces only a published passed summary with a safe failed summary', {
  skip: process.platform !== 'win32',
}, () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-late-fatal-'))
  const output = join(root, 'run')
  const sentinel = 'late-fatal-private-error-sentinel'
  try {
    const gateNames = ['run_scope', 'dsh_session', 'dsh_process', 'trace_integrity', 'original_image',
      'main_image_wire', 'cordis_cad_tools', 'acp_cad_tool_results', 'current_geometry', 'artifacts', 'holes',
      'visual', 'finish']
    const strict = {
      status: 'passed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
      failure_reasons: [], evidence_errors: [],
      gates: Object.fromEntries(gateNames.map(name => [name, { status: 'passed', reasons: [] }])),
    }
    const passed = buildSafeAcceptanceSummary({ strict, scratchCleanupStatus: 'removed' })
    writeSafeAcceptanceArtifacts(output, { summary: passed, acpProjection: {}, bindingProjection: {},
      buildStatus: { status: 'passed', exit_code: 0 } })
    assert.equal(passed.status, 'passed')

    const failed = replacePublishedSafeFailureSummary(output, {
      stage: 'publication', phase: SAFE_PUBLICATION_PHASE.RUNNER_COMPLETION,
      code: 'unhandled_rejection', scratchCleanupStatus: 'failed', exception: sentinel,
    })
    const serialized = readFileSync(join(output, 'summary.json'), 'utf8')
    assert.equal(failed.status, 'failed')
    assert.deepEqual(failed.failure_diagnostic, {
      stage: 'publication', phase: 'runner_completion', code: 'unhandled_rejection',
    })
    assert.equal(JSON.parse(serialized).status, 'failed')
    assert.equal(serialized.includes(sentinel), false)
    assert.equal(readdirSync(output).some(name => name.startsWith('.summary-')), false)
    assert.throws(() => writeSafeFailureSummary(output, { stage: 'publication', code: 'uncaught_exception' }), /EEXIST/)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('DSH diagnostic publication keeps only allowlisted RPC method and error class', () => {
  const classified = buildSafeAcceptanceSummary({
    dshDiagnostic: { stage: 'session_rpc', method: 'session/prompt', error_class: 'rpc_error', message: 'provider secret sentinel' },
  })
  assert.deepEqual(classified.dsh_diagnostic, { schema: 'dsh_rpc_failure_diagnostic_v1', stage: 'session_rpc', method: 'session/prompt', error_class: 'rpc_error', request_sequence: null, rpc_error_code: 'unknown' })
  const unknown = buildSafeAcceptanceSummary({
    dshDiagnostic: { stage: 'private-stage-sentinel', method: 'private-method-sentinel', error_class: 'raw-provider-error-sentinel' },
  })
  assert.deepEqual(unknown.dsh_diagnostic, { schema: 'dsh_rpc_failure_diagnostic_v1', stage: 'unknown', method: 'unknown', error_class: 'unknown', request_sequence: null, rpc_error_code: 'unknown' })
  assert.equal(JSON.stringify(classified).includes('provider secret sentinel'), false)
  assert.equal(JSON.stringify(unknown).includes('sentinel'), false)
})

test('visual review artifact publication status is a path-free projection outside acceptance and cleanup gates', () => {
  const strict = { status: 'passed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
    gates: { run_scope: { status: 'passed', reasons: [] } } }
  const baseInput = { strict, transportStatus: 'completed', modelStatus: 'accepted_by_strict_cli_gate',
    runtime: { node: 'v22.23.3', dsh: '0.2.0-rc.2' }, scratchCleanupStatus: 'removed' }
  const baseline = buildSafeAcceptanceSummary(baseInput)
  assert.deepEqual(baseline.visual_review_artifact_status, { status: 'not_attempted', code: 'not_attempted' })

  const published = buildSafeAcceptanceSummary({ ...baseInput,
    visualReviewArtifactStatus: { status: 'published', code: 'ok', path: 'private-path-sentinel',
      sha256: 'private-hash-sentinel', fingerprint: 'private-fingerprint-sentinel' } })
  assert.deepEqual(published.visual_review_artifact_status, { status: 'published', code: 'published' })
  assert.equal(published.status, baseline.status)
  assert.deepEqual(published.strict_gates, baseline.strict_gates)
  assert.equal(published.scratch_cleanup_status, baseline.scratch_cleanup_status)

  const rejected = buildSafeAcceptanceSummary({ ...baseInput,
    visualReviewArtifactStatus: { status: 'rejected', code: 'invalid_output_root', path: 'private-path-sentinel' } })
  assert.deepEqual(rejected.visual_review_artifact_status, { status: 'rejected', code: 'artifact_invalid' })
  assert.equal(rejected.status, baseline.status)
  assert.deepEqual(rejected.strict_gates, baseline.strict_gates)
  assert.equal(rejected.scratch_cleanup_status, baseline.scratch_cleanup_status)

  const unavailable = buildSafeAcceptanceSummary({ ...baseInput,
    visualReviewArtifactStatus: { status: 'unavailable', code: 'preview_missing', detail: 'private-detail-sentinel' } })
  assert.deepEqual(unavailable.visual_review_artifact_status, { status: 'unavailable', code: 'preview_missing' })
  const invalid = buildSafeAcceptanceSummary({ ...baseInput,
    visualReviewArtifactStatus: { status: 'private-status-sentinel', code: 'private-code-sentinel',
      detail: 'private-detail-sentinel' } })
  assert.deepEqual(invalid.visual_review_artifact_status, { status: 'unavailable', code: 'not_attempted' })
  for (const summary of [published, rejected, unavailable, invalid]) {
    assert.equal(summary.status, baseline.status)
    assert.deepEqual(summary.strict_gates, baseline.strict_gates)
    assert.equal(summary.scratch_cleanup_status, baseline.scratch_cleanup_status)
  }
  const serialized = JSON.stringify([published, rejected, unavailable, invalid])
  for (const sentinel of ['private-path-sentinel', 'private-hash-sentinel', 'private-fingerprint-sentinel',
    'private-status-sentinel', 'private-code-sentinel', 'private-detail-sentinel']) {
    assert.equal(serialized.includes(sentinel), false, `must omit ${sentinel}`)
  }

  const failureRoot = mkdtempSync(join(tmpdir(), 'coldstart-visual-status-'))
  try {
    const failureInput = { stage: 'publication', code: 'uncaught_exception', scratchCleanupStatus: 'removed',
      strictGates: baseline.strict_gates }
    const failureBaseline = writeSafeFailureSummary(join(failureRoot, 'baseline'), failureInput)
    const failure = writeSafeFailureSummary(join(failureRoot, 'with-visual-status'), {
      ...failureInput, visualReviewArtifactStatus: { status: 'published', code: 'ok', path: 'private-path-sentinel' },
    })
    assert.deepEqual(failure.visual_review_artifact_status, { status: 'published', code: 'published' })
    assert.equal(failure.status, 'failed')
    assert.deepEqual(failure.strict_gates, failureBaseline.strict_gates)
    assert.equal(failure.scratch_cleanup_status, 'removed')
    assert.equal(failure.scratch_cleanup_status, failureBaseline.scratch_cleanup_status)
    assert.equal(JSON.stringify(failure).includes('private-path-sentinel'), false)
  } finally {
    rmSync(failureRoot, { recursive: true, force: true })
  }
})

test('visual and finish subcondition projection exposes fixed booleans and strips private fields', () => {
  const strict = {
    status: 'failed',
    gates: {
      visual: { status: 'failed', reasons: ['visual_all_references_not_covered', 'visual_reference_mismatch_detected',
        'private-reason-sentinel'], raw: 'provider-text-sentinel', reference_id: 'private-id-sentinel' },
      finish: { status: 'failed', reasons: ['finish_geometry_review_decision_not_accept', 'finish_open_findings_nonzero'],
        unresolved_findings: [{ text: 'private-finding-sentinel' }], geometry: { secret: 'payload-sentinel' } },
    },
    answer: 'answer-sentinel', thought: 'thought-sentinel', image: 'image-sentinel',
  }
  const summary = buildSafeAcceptanceSummary({ strict })
  const diagnostic = summary.visual_finish_diagnostics
  assert.deepEqual(Object.keys(diagnostic), ['schema', 'visual', 'finish'])
  assert.equal(diagnostic.schema, 'visual_finish_subconditions_v1')
  assert.equal(diagnostic.visual.status, 'failed')
  assert.equal(diagnostic.visual.failed_check_count, 3)
  assert.equal(diagnostic.visual.unclassified_failure, true)
  assert.equal(diagnostic.visual.checks.all_references_covered, false)
  assert.equal(diagnostic.visual.checks.mismatch_free, false)
  assert.equal(diagnostic.visual.checks.finish_coverage, true)
  assert.equal(diagnostic.finish.status, 'failed')
  assert.equal(diagnostic.finish.failed_check_count, 2)
  assert.equal(diagnostic.finish.checks.review_decision_accept, false)
  assert.equal(diagnostic.finish.checks.no_open_findings, false)
  assert.equal(diagnostic.finish.checks.accepted_flag, true)
  assert.deepEqual(Object.keys(diagnostic.visual.checks), [
    'finish_coverage', 'all_references_covered', 'independent_review_present', 'host_coverage_complete', 'manifest_valid',
    'mismatch_free', 'one_reference_in_inventory', 'reference_id_bound', 'reference_sha_bound', 'original_reference_covered',
    'original_inventory_valid', 'visual_summary_current',
  ])
  assert.deepEqual(Object.keys(diagnostic.finish.checks), [
    'host_finished', 'domain_accepted', 'summary_accepted', 'accepted_flag', 'geometry_review_accepted',
    'review_issuer_valid', 'review_decision_accept', 'construction_complete', 'no_open_findings',
    'no_unresolved_findings', 'external_fact_count_valid', 'external_facts_verified', 'no_failed_external_facts',
    'no_unknown_external_facts',
  ])
  for (const gate of [diagnostic.visual, diagnostic.finish]) {
    assert.ok(['passed', 'failed'].includes(gate.status))
    assert.equal(Number.isSafeInteger(gate.failed_check_count), true)
    assert.ok(Object.values(gate.checks).every(value => typeof value === 'boolean'))
  }
  const serialized = JSON.stringify(summary)
  for (const secret of ['private-reason-sentinel', 'provider-text-sentinel', 'private-id-sentinel', 'private-finding-sentinel',
    'payload-sentinel', 'answer-sentinel', 'thought-sentinel', 'image-sentinel']) {
    assert.equal(serialized.includes(secret), false)
  }
  const missing = buildSafeAcceptanceSummary({}).visual_finish_diagnostics
  assert.equal(missing.visual.status, 'failed')
  assert.equal(missing.visual.checks.mismatch_free, false)
  assert.equal(missing.finish.status, 'failed')
})

test('offline Cargo failure publishes only safe status fields before fatal publication', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-offline-build-safe-'))
  const output = join(root, 'run')
  const secret = 'provider-error-secret-key-stack-and-path-sentinel'
  let time = 100
  try {
    const failed = runOfflineCargoBuild({
      spawnSyncImpl: () => ({ status: 101, stdout: `stdout ${secret}`, stderr: `stderr ${secret}` }),
      args: ['build'], cwd: root, output, now: () => (time += 12),
    })
    assert.deepEqual(failed, {
      status: 'failed', exit_code: 101, elapsed_ms: 12,
      stdout_bytes: Buffer.byteLength(`stdout ${secret}`), stderr_bytes: Buffer.byteLength(`stderr ${secret}`),
    })
    const buildStatusText = readFileSync(join(output, 'build-status.json'), 'utf8')
    const buildStatus = JSON.parse(buildStatusText)
    assert.deepEqual(Object.keys(buildStatus).sort(), ['elapsed_ms', 'exit_code', 'status', 'stderr_bytes', 'stdout_bytes'])
    assert.deepEqual(buildStatus, failed)
    assert.equal(buildStatusText.includes(secret), false)
    writeSafeFailureSummary(output, { stage: 'offline_build', code: 'uncaught_exception' })
    assert.deepEqual(readdirSync(output).sort(), ['build-status.json', 'summary.json'])
    const fatalPublication = readdirSync(output).map(name => readFileSync(join(output, name), 'utf8')).join('\n')
    assert.equal(fatalPublication.includes(secret), false)

    const threwOutput = join(root, 'threw')
    const thrown = runOfflineCargoBuild({
      spawnSyncImpl: () => { throw new Error(secret) },
      args: ['build'], cwd: root, output: threwOutput, now: () => (time += 7),
    })
    assert.deepEqual(thrown, {
      status: 'failed', exit_code: null, elapsed_ms: 7, stdout_bytes: 0, stderr_bytes: 0,
    })
    const thrownText = readFileSync(join(threwOutput, 'build-status.json'), 'utf8')
    assert.deepEqual(JSON.parse(thrownText), thrown)
    assert.equal(thrownText.includes(secret), false)

    const erroredOutput = join(root, 'errored')
    const errored = runOfflineCargoBuild({
      spawnSyncImpl: () => ({ status: 0, stdout: `partial ${secret}`, stderr: '', error: new Error(secret) }),
      args: ['build'], cwd: root, output: erroredOutput, now: () => (time += 4),
    })
    assert.deepEqual(errored, {
      status: 'failed', exit_code: null, elapsed_ms: 4,
      stdout_bytes: Buffer.byteLength(`partial ${secret}`), stderr_bytes: 0,
    })
    const erroredText = readFileSync(join(erroredOutput, 'build-status.json'), 'utf8')
    assert.deepEqual(JSON.parse(erroredText), errored)
    assert.equal(erroredText.includes(secret), false)

    const passedOutput = join(root, 'passed')
    const passed = runOfflineCargoBuild({
      spawnSyncImpl: () => ({ status: 0, stdout: 'build output', stderr: '' }),
      args: ['build'], cwd: root, output: passedOutput, now: () => (time += 2),
    })
    assert.equal(passed.status, 'passed')
    assert.equal(existsSync(join(passedOutput, 'build-status.json')), false)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('offline Cargo example build clears missing bundle resources and preserves caller config', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-offline-build-tauri-config-'))
  const sourceEnvironment = {
    TAURI_CONFIG: JSON.stringify({
      productName: 'caller config sentinel',
      build: { devUrl: 'http://127.0.0.1:1420' },
      bundle: { active: true, resources: ['target/package-resources/app'] },
    }),
    COLDSTART_ENV_SENTINEL: 'preserved',
  }
  let childOptions
  try {
    const status = runOfflineCargoBuild({
      spawnSyncImpl: (_command, _args, options) => {
        childOptions = options
        return { status: 0, stdout: '', stderr: '' }
      },
      args: ['build', '--offline', '--example', 'native_read_probe'],
      cwd: root,
      output: join(root, 'run'),
      environment: sourceEnvironment,
    })

    assert.equal(status.status, 'passed')
    assert.deepEqual(JSON.parse(childOptions.env.TAURI_CONFIG), {
      productName: 'caller config sentinel',
      build: { devUrl: 'http://127.0.0.1:1420' },
      bundle: { active: true, resources: [] },
    })
    assert.equal(childOptions.env.COLDSTART_ENV_SENTINEL, 'preserved')
    assert.deepEqual(JSON.parse(sourceEnvironment.TAURI_CONFIG).bundle.resources, [
      'target/package-resources/app',
    ])
    assert.equal(sourceEnvironment.COLDSTART_ENV_SENTINEL, 'preserved')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('offline Cargo build rejects malformed TAURI_CONFIG without spawning Cargo or leaking it', () => {
  const root = mkdtempSync(join(tmpdir(), 'coldstart-offline-build-invalid-config-'))
  const output = join(root, 'run')
  const invalid = '{invalid-tauri-config-sentinel'
  let spawnCount = 0
  try {
    const status = runOfflineCargoBuild({
      spawnSyncImpl: () => {
        spawnCount += 1
        return { status: 0, stdout: '', stderr: '' }
      },
      args: ['build', '--offline'],
      cwd: root,
      output,
      environment: { TAURI_CONFIG: invalid },
      now: () => 123,
    })

    assert.deepEqual(status, {
      status: 'failed', exit_code: null, elapsed_ms: 0, stdout_bytes: 0, stderr_bytes: 0,
    })
    assert.equal(spawnCount, 0)
    const published = readFileSync(join(output, 'build-status.json'), 'utf8')
    assert.deepEqual(JSON.parse(published), status)
    assert.equal(published.includes(invalid), false)
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
  }, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
  ['ACP tool name mismatch cannot prove bridge call', f => { f.toolEvents.forEach(event => { event.title = 'cad_preview_model' }) }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['ACP failed update remains a hard failure after Rust success', f => { f.toolEvents[1].status = 'failed' }, 'tool_call_rejected_or_failed'],
  ['missing ACP argument fingerprint fails closed', f => { delete f.toolEvents[0].arguments_sha256_24 }, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
  ['foreign bridge session', f => { f.toolBridgeCalls[0].session_id = 'old' }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['audit from unrelated UUID', f => { f.nativeAudit[0].call_id = 'other' }, 'cad_tool_rejected_unbound_or_unaudited'],
  ['direct CLI postcheck is not DSH execution', f => { f.nativeAudit[0].call_id = 'probe-0'; f.toolBridgeCalls = [] }, 'successful_cordis_cad_call_and_native_audit_missing'],
  ['DSH child nonzero exit', f => { f.dsh.exit_code = 1 }, 'dsh_child_exit_nonzero_or_missing'],
  ['DSH session did not complete', f => { f.dsh.status = 'failed' }, 'dsh_session_not_completed'],
  ['DSH RPC failure is a distinct session reason', f => { f.dsh.failure.error_class = 'rpc_error' }, 'dsh_session_rpc_failed'],
  ['DSH child exit not observed', f => { f.dsh.child_exited = false }, 'dsh_child_exit_not_observed'],
  ['host accepted false', f => { f.domain.finish_summary.accepted = false }, 'finish_acceptance_flag_false'],
  ['preview geometry stale', f => { f.domain.last_preview.geometry_fingerprint = 'd'.repeat(64) }, 'preview_geometry_fingerprint_stale'],
  ['current plan fingerprint differs from preview', f => { f.computedState.geometry_signature = 'd'.repeat(64) }, 'preview_geometry_fingerprint_stale'],
  ['geometry report stale', f => { f.artifacts.geometry_report.revision = 1 }, 'geometry_report_file_stale'],
  ['holes incomplete', f => { f.domain.finish_summary.hole_measurement.complete = false }, 'hole_finish_measurement_incomplete'],
  ['host says holes complete but backend does not', f => { f.domain.last_preview.local_geometry_checks.hole_measurement_complete = false }, 'hole_native_measurement_incomplete'],
  ['visual coverage incomplete', f => { f.domain.finish_summary.reference_coverage = false }, 'visual_finish_coverage_incomplete'],
  ['stale visual summary', f => { f.computedState.visual_evidence_summary = { ...f.computedState.visual_evidence_summary, all_references_covered: false } }, 'visual_summary_stale_or_mismatched'],
  ['attestation invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.geometry.host_attestation_valid = false }, 'geometry_host_attestation_invalid'],
  ['missing preview artifact', f => { f.artifacts.failures = ['artifact_preview_missing'] }, 'current_run_artifacts_missing_or_invalid'],
  ['missing mandatory fields', f => { delete f.domain.finish_summary; delete f.computedState }, 'finish_summary_state_not_accepted'],
]
const criticalGatePredicateFlips = [
  ['geometry postcheck revision stale', f => { f.computedState.revision = 1 }, 'rust_postcheck_revision_stale'],
  ['geometry signature absent', f => { f.computedState.geometry_signature = null }, 'rust_postcheck_geometry_signature_missing'],
  ['preview revision stale', f => { f.domain.preview_revision = 1 }, 'preview_revision_stale'],
  ['native check fingerprint stale', f => { f.domain.last_preview.local_geometry_checks.geometry_fingerprint = 'd'.repeat(64) }, 'geometry_checks_fingerprint_stale'],
  ['finish fingerprint stale', f => { f.domain.finish_summary.geometry_fingerprint = 'd'.repeat(64) }, 'finish_geometry_fingerprint_stale'],
  ['finish review fingerprint stale', f => { f.domain.finish_summary.overall_geometry_review.geometry_fingerprint = 'd'.repeat(64) }, 'finish_review_fingerprint_stale'],
  ['latest review fingerprint stale', f => { f.domain.latest_geometry_review.geometry_fingerprint = 'd'.repeat(64) }, 'latest_review_fingerprint_stale'],
  ['native geometry engine absent', f => { f.domain.last_preview.execution_engine = 'other' }, 'native_geometry_engine_missing'],
  ['native geometry state not passed', f => { f.domain.last_preview.local_geometry_checks.state = 'failed' }, 'native_geometry_check_state_not_passed'],
  ['native geometry validity false', f => { f.domain.last_preview.local_geometry_checks.valid = false }, 'native_geometry_validity_false'],
  ['native closed solid count invalid', f => { f.domain.last_preview.local_geometry_checks.closed_solid_count = 2 }, 'native_geometry_solid_count_invalid'],
  ['native validation invalid', f => { f.domain.last_preview.local_geometry_checks.validation.state = 'invalid' }, 'native_geometry_validation_invalid'],
  ['geometry binding invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.geometry.binding_valid = false }, 'geometry_binding_invalid'],
  ['geometry attestation failure', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.geometry.passed = false }, 'geometry_attestation_failed'],
  ['attested geometry checks differ', f => { const geometry = f.domain.finish_summary.overall_geometry_review.result.evidence.geometry; geometry.checks = { ...geometry.checks, valid: false } }, 'geometry_attested_checks_mismatch'],
  ['finish review differs from latest review', f => { f.domain.latest_geometry_review = structuredClone(f.domain.latest_geometry_review); f.domain.latest_geometry_review.result.decision = 'reject' }, 'finish_geometry_review_differs_from_current_gate'],
  ['finish hole evidence incomplete', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.holes.measurement_complete = false }, 'hole_evidence_measurement_incomplete'],
  ['hole measurement unverified', f => { f.domain.last_preview.local_geometry_checks.hole_measurements_verified = false }, 'hole_native_measurement_unverified'],
  ['visual coverage: finish reference coverage missing', f => { f.domain.finish_summary.reference_coverage = false }, 'visual_finish_coverage_incomplete'],
  ['visual coverage: all references covered missing', f => { f.domain.finish_summary.visual_evidence.all_references_covered = false; f.computedState.visual_evidence_summary.all_references_covered = false }, 'visual_all_references_not_covered'],
  ['visual coverage: independent review pair count zero', f => { f.domain.finish_summary.independent_review_pair_count = 0 }, 'visual_independent_review_missing'],
  ['visual coverage: Rust complete flag missing', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.visual_coverage_complete = false }, 'visual_gate_coverage_incomplete'],
  ['visual coverage: reference manifest invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.manifest_valid = false }, 'visual_reference_manifest_invalid'],
  ['visual coverage: mismatch count nonzero', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.mismatch_count = 1 }, 'visual_reference_mismatch_detected'],
  ['original binding: inventory row missing', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources = [] }, 'original_reference_inventory_count_invalid'],
  ['original binding: duplicate inventory rows', f => { const refs = f.domain.finish_summary.overall_geometry_review.result.evidence.references; refs.original_reference_sources.push({ ...refs.original_reference_sources[0] }) }, 'original_reference_inventory_count_invalid'],
  ['original binding: reference id wrong', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].id = 'sha256:' + 'd'.repeat(64) }, 'original_reference_id_mismatch'],
  ['original binding: source hash wrong', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].sha256 = 'd'.repeat(64) }, 'original_reference_sha256_mismatch'],
  ['original binding: coverage false', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].covered = false }, 'original_reference_not_covered'],
  ['original binding: inventory invalid', f => { f.domain.finish_summary.overall_geometry_review.result.evidence.references.original_reference_sources[0].inventory_valid = false }, 'original_reference_inventory_invalid'],
  ['host finish: domain finished false', f => { f.domain.finished = false }, 'finish_host_not_finished'],
  ['host finish: domain state not accepted', f => { f.domain.finish_state = 'candidate' }, 'finish_domain_state_not_accepted'],
  ['host finish: summary state not accepted', f => { f.domain.finish_summary.finish_state = 'candidate' }, 'finish_summary_state_not_accepted'],
  ['host finish: summary acceptance false', f => { f.domain.finish_summary.accepted = false }, 'finish_acceptance_flag_false'],
  ['host finish: geometry review acceptance false', f => { f.domain.finish_summary.overall_geometry_review_accepted = false }, 'finish_geometry_review_acceptance_false'],
  ['host finish: review issuer wrong', f => { f.domain.finish_summary.overall_geometry_review.issuer = 'other' }, 'finish_geometry_review_issuer_invalid'],
  ['host finish: review decision not accept', f => { f.domain.finish_summary.overall_geometry_review.result.decision = 'reject' }, 'finish_geometry_review_decision_not_accept'],
  ['finish construction incomplete', f => { f.domain.finish_summary.construction_complete = false }, 'finish_construction_incomplete'],
  ['finish open finding count nonzero', f => { f.domain.finish_summary.open_finding_count = 1 }, 'finish_open_findings_nonzero'],
  ['finish unresolved finding listed', f => { f.domain.finish_summary.unresolved_findings = [{ id: 'synthetic' }] }, 'finish_unresolved_findings_present'],
  ['finish external fact failed', f => { f.domain.finish_summary.required_external_facts.failed_count = 1 }, 'finish_external_facts_failed'],
  ['finish external fact unknown', f => { f.domain.finish_summary.required_external_facts.unknown_count = 1 }, 'finish_external_facts_unknown'],
  ['finish external fact unverified', f => { f.domain.finish_summary.required_external_facts.required_count = 1 }, 'finish_external_facts_unverified'],
]
const safeVisualFinishCheckByReason = new Map([
  ['visual_finish_coverage_incomplete', ['visual', 'finish_coverage']],
  ['visual_all_references_not_covered', ['visual', 'all_references_covered']],
  ['visual_independent_review_missing', ['visual', 'independent_review_present']],
  ['visual_gate_coverage_incomplete', ['visual', 'host_coverage_complete']],
  ['visual_reference_manifest_invalid', ['visual', 'manifest_valid']],
  ['visual_reference_mismatch_detected', ['visual', 'mismatch_free']],
  ['original_reference_inventory_count_invalid', ['visual', 'one_reference_in_inventory']],
  ['original_reference_id_mismatch', ['visual', 'reference_id_bound']],
  ['original_reference_sha256_mismatch', ['visual', 'reference_sha_bound']],
  ['original_reference_not_covered', ['visual', 'original_reference_covered']],
  ['original_reference_inventory_invalid', ['visual', 'original_inventory_valid']],
  ['visual_summary_stale_or_mismatched', ['visual', 'visual_summary_current']],
  ['finish_host_not_finished', ['finish', 'host_finished']],
  ['finish_domain_state_not_accepted', ['finish', 'domain_accepted']],
  ['finish_summary_state_not_accepted', ['finish', 'summary_accepted']],
  ['finish_acceptance_flag_false', ['finish', 'accepted_flag']],
  ['finish_geometry_review_acceptance_false', ['finish', 'geometry_review_accepted']],
  ['finish_geometry_review_issuer_invalid', ['finish', 'review_issuer_valid']],
  ['finish_geometry_review_decision_not_accept', ['finish', 'review_decision_accept']],
  ['finish_construction_incomplete', ['finish', 'construction_complete']],
  ['finish_open_findings_nonzero', ['finish', 'no_open_findings']],
  ['finish_unresolved_findings_present', ['finish', 'no_unresolved_findings']],
  ['finish_external_fact_required_count_invalid', ['finish', 'external_fact_count_valid']],
  ['finish_external_facts_unverified', ['finish', 'external_facts_verified']],
  ['finish_external_facts_failed', ['finish', 'no_failed_external_facts']],
  ['finish_external_facts_unknown', ['finish', 'no_unknown_external_facts']],
])
for (const [label, modify, reason] of criticalGatePredicateFlips) test(`critical gate predicate flip: ${label}`, () => {
  const accepted = evaluateColdstartGates(fixture())
  assert.equal(accepted.status, 'passed', 'all predicates satisfied must pass')
  assert.equal(accepted.gates.visual.status, 'passed')
  assert.equal(accepted.gates.finish.status, 'passed')

  const input = fixture()
  modify(input)
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  const [gateName, gateResult] = Object.entries(result.gates).find(([, gate]) => gate.status === 'failed' && gate.reasons.includes(reason)) || []
  assert.ok(gateResult, JSON.stringify(result.failure_reasons))
  assert.ok(result.failure_reasons.includes(reason), JSON.stringify(result.failure_reasons))
  const safe = buildSafeAcceptanceSummary({ strict: result })
  assert.ok(safe.strict_gates.gates[gateName].reason_codes.includes(reason), `${reason} must be allowlisted for publication`)
  const mappedCheck = safeVisualFinishCheckByReason.get(reason)
  if (mappedCheck) {
    const [diagnosticGate, check] = mappedCheck
    assert.equal(safe.visual_finish_diagnostics[diagnosticGate].checks[check], false,
      `${reason} must mark its fixed diagnostic check failed`)
  }
})
for (const [label, modify, reason] of negativeFixtures) test(label, () => {
  const input = fixture(); modify(input)
  const result = evaluateColdstartGates(input)
  assert.equal(result.status, 'failed')
  assert.ok(result.failure_reasons.includes(reason), JSON.stringify(result.failure_reasons))
  const gateName = Object.entries(result.gates).find(([, gate]) => gate.reasons.includes(reason))?.[0]
  assert.ok(buildSafeAcceptanceSummary({ strict: result }).strict_gates.gates[gateName].reason_codes.includes(reason),
    `${reason} must be allowlisted for publication`)
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

test('loopback bridge proxy reduces native error data to fixed dispatch enums', async () => {
  const native = createServer(socket => {
    let text = ''
    socket.on('data', chunk => {
      text += chunk
      if (!text.includes('\n')) return
      const request = JSON.parse(text.slice(0, text.indexOf('\n')))
      socket.end(JSON.stringify({ jsonrpc: '2.0', id: request.id, error: { code: -32020,
        message: 'private-error-body-sentinel', data: { code: 'DEADLINE_EXCEEDED', dispatch_stage: 'pre_executor_deadline',
          detail: 'private-arguments-sentinel' } } }) + '\n')
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
        token: 'fixture-token-marker', sessionId: 'native-session', turnId: 'turn', toolCallId: 'private-call-id',
        name: 'cad_finish_model', arguments: { secret: 'private-arguments-sentinel' } } }) + '\n'))
    })
    assert.equal(proxy.records.length, 1)
    assert.equal(proxy.records[0].dispatch_stage, 'pre_executor_deadline')
    assert.equal(proxy.records[0].dispatch_failure_code, 'deadline_exceeded')
    const projected = fixture()
    projected.toolBridgeCalls[0].success = false
    projected.toolBridgeCalls[0].dispatch_stage = proxy.records[0].dispatch_stage
    projected.toolBridgeCalls[0].dispatch_failure_code = proxy.records[0].dispatch_failure_code
    const serialized = JSON.stringify(summarizeCordisBindingDiagnostics(projected))
    for (const secret of ['private-error-body-sentinel', 'private-arguments-sentinel', 'private-call-id', 'fixture-token-marker']) {
      assert.equal(serialized.includes(secret), false)
    }
  } finally { await proxy.close(); await new Promise(done => native.close(done)) }
})
