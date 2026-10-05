import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { startNativeProbe } from './native_agent_coldstart_probe.mjs'
import { buildSafeAcceptanceSummary, writeSafeFailureSummary } from './native_agent_coldstart_publication.mjs'

class FakeLines extends EventEmitter {
  close() { this.closed = true }
}

class FakeChild extends EventEmitter {
  constructor() {
    super()
    this.stdout = {}
    this.stderr = { resume() { } }
    this.killed = false
  }

  kill() { this.killed = true; return true }
}

const makeProbe = (child, schedule = () => {}) => {
  const lines = new FakeLines()
  const promise = startNativeProbe({
    spawnImpl: () => child,
    executable: 'private-probe-path-marker',
    args: ['private-argument-marker'],
    options: { env: { AIMODELER_LLM_API_KEY: 'private-key-marker' } },
    timeoutMs: 15,
    createInterfaceImpl: () => lines,
  })
  schedule(child, lines)
  return { promise, child, lines }
}

test('native probe startup classifies spawn errors without exposing error text', async () => {
  const child = new FakeChild()
  const { promise } = makeProbe(child, spawned => queueMicrotask(() => {
    spawned.emit('error', new Error('private-spawn-error-marker'))
  }))
  const result = await promise
  assert.equal(result.diagnostic, 'spawn_error')
  assert.equal(child.killed, true)
  assert.equal(JSON.stringify({ native_probe_diagnostic: result.diagnostic }).includes('private-'), false)
})

test('native probe startup classifies a synchronous spawn throw without its message', async () => {
  const result = await startNativeProbe({
    spawnImpl: () => { throw new Error('private-sync-spawn-marker') },
    executable: 'private-probe-path-marker', args: ['private-argument-marker'], options: {},
  })
  assert.equal(result.diagnostic, 'spawn_error')
  assert.equal(result.child, null)
  assert.equal(JSON.stringify({ native_probe_diagnostic: result.diagnostic }).includes('private-'), false)
})

test('native probe startup classifies exit before its ready line', async () => {
  const child = new FakeChild()
  const { promise } = makeProbe(child, spawned => queueMicrotask(() => spawned.emit('exit', 1, null)))
  const result = await promise
  assert.equal(result.diagnostic, 'exit_before_ready')
  assert.equal(child.killed, true)
})

test('native probe startup classifies a first-line timeout', async () => {
  const child = new FakeChild()
  const { promise } = makeProbe(child)
  const result = await promise
  assert.equal(result.diagnostic, 'startup_timeout')
  assert.equal(child.killed, true)
})

test('native probe startup classifies invalid and non-ready first-line JSON identically', async t => {
  for (const payload of ['private-invalid-json-marker', JSON.stringify({ error: 'private-error-marker' })]) {
    await t.test(payload.startsWith('{') ? 'valid JSON without ready fields' : 'invalid JSON', async () => {
      const child = new FakeChild()
      const { promise } = makeProbe(child, (_spawned, lines) => queueMicrotask(() => lines.emit('line', payload)))
      const result = await promise
      assert.equal(result.diagnostic, 'invalid_or_non_ready_json')
      assert.equal(child.killed, true)
      assert.equal(JSON.stringify({ native_probe_diagnostic: result.diagnostic }).includes('private-'), false)
    })
  }
})

test('native probe startup accepts the ready bridge shape and retains its line reader', async () => {
  const child = new FakeChild()
  const ready = { address: '127.0.0.1:1234', token: 'private-token-marker',
    session_id: 'private-session-marker', schemas_path: 'private-schema-path-marker' }
  const { promise, lines } = makeProbe(child, (_spawned, reader) => queueMicrotask(() => reader.emit('line', JSON.stringify(ready))))
  const result = await promise
  assert.equal(result.diagnostic, 'ready_success')
  assert.equal(result.child, child)
  assert.equal(result.lines, lines)
  assert.equal(result.bridge.token, ready.token)
  assert.equal(child.killed, false)
})

test('safe failure summary only publishes the fixed native probe enum', () => {
  const root = mkdtempSync(join(tmpdir(), 'f7-native-probe-summary-'))
  try {
    writeSafeFailureSummary(root, { stage: 'native_probe', phase: 'not_started', code: 'uncaught_exception',
      scratchCleanupStatus: 'removed', nativeProbeDiagnostic: 'spawn_error' })
    const body = readFileSync(join(root, 'summary.json'), 'utf8')
    const summary = JSON.parse(body)
    assert.equal(summary.native_probe_diagnostic, 'spawn_error')
    for (const marker of ['private-probe-path-marker', 'private-argument-marker', 'private-key-marker',
      'private-spawn-error-marker', 'private-token-marker']) assert.equal(body.includes(marker), false)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('all native probe outcomes remain enum-only in published summaries', () => {
  const root = mkdtempSync(join(tmpdir(), 'f7-native-probe-enum-summary-'))
  const markers = ['private-probe-path-marker', 'private-argument-marker', 'private-key-marker',
    'private-stderr-marker', 'private-error-marker', 'private-token-marker', 'private-session-marker',
    'private-schema-path-marker']
  try {
    for (const diagnostic of ['spawn_error', 'exit_before_ready', 'startup_timeout',
      'invalid_or_non_ready_json', 'ready_success']) {
      const dir = join(root, diagnostic)
      writeSafeFailureSummary(dir, { stage: 'native_probe', phase: 'not_started', code: 'uncaught_exception',
        scratchCleanupStatus: 'removed', nativeProbeDiagnostic: diagnostic,
        error: new Error('private-error-marker'), args: markers, apiKey: 'private-key-marker' })
      const body = readFileSync(join(dir, 'summary.json'), 'utf8')
      assert.equal(JSON.parse(body).native_probe_diagnostic, diagnostic)
      for (const marker of markers) assert.equal(body.includes(marker), false)
    }
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('completed summary carries only the fixed native probe success enum', () => {
  const summary = buildSafeAcceptanceSummary({
    strict: { status: 'failed', scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance',
      gates: {}, failure_reasons: [], evidence_errors: [] },
    transportStatus: 'failed', modelStatus: 'none', runtime: { node: 'v22.23.3', dsh: '0.2.0-rc.2' },
    source: { bytes: 1, sha256: 'a'.repeat(64), unchanged: true }, scratchCleanupStatus: 'removed',
    nativeProbeDiagnostic: 'ready_success',
  })
  assert.equal(summary.native_probe_diagnostic, 'ready_success')
  assert.equal(JSON.stringify(summary).includes('private-'), false)
})

test('unrecognized native probe diagnostic is omitted as null', () => {
  const root = mkdtempSync(join(tmpdir(), 'f7-native-probe-invalid-summary-'))
  try {
    writeSafeFailureSummary(root, { stage: 'native_probe', phase: 'not_started', code: 'uncaught_exception',
      scratchCleanupStatus: 'removed', nativeProbeDiagnostic: 'private-unclassified-marker' })
    const summary = JSON.parse(readFileSync(join(root, 'summary.json'), 'utf8'))
    assert.equal(summary.native_probe_diagnostic, null)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
