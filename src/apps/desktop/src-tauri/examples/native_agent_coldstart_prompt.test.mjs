import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { buildSafeAcceptanceSummary, writeSafeFailureSummary } from './native_agent_coldstart_publication.mjs'
import { cancelAndCloseSession, createAcpRpcClient, PromptProgressTracker, resolvePromptTimeoutPolicy,
  safePromptProgressSummary, safeRpcErrorCodeBucket, watchGeometryRevision } from './native_agent_coldstart_prompt.mjs'

const fixture = fileURLToPath(new URL('./fixtures/fake_acp_prompt.mjs', import.meta.url))

function startFixture(mode, extraEnv = {}) {
  const child = spawn(process.execPath, [fixture], {
    stdio: ['pipe', 'pipe', 'ignore'],
    windowsHide: true,
    env: { ...process.env, FAKE_ACP_MODE: mode, ...extraEnv },
  })
  const client = createAcpRpcClient(child)
  return { child, client }
}

async function initialize(client) {
  await client.request('initialize', {}, 1000)
  return (await client.request('session/new', {}, 1000)).sessionId
}

async function finishFixture({ child, client }, sessionId) {
  const result = await cancelAndCloseSession(client, sessionId, 500)
  assert.equal(result.cancel_sent, true)
  assert.equal(result.close_completed, true)
  client.close()
  child.stdin.end()
  if (child.exitCode === null) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('fake ACP did not exit')), 1000)
      child.once('exit', code => { clearTimeout(timer); resolve(code) })
    })
  }
  assert.equal(child.exitCode, 0)
}

function prompt(client, sessionId, tracker, { totalMs = 300, idleMs = 70 } = {}) {
  return client.request('session/prompt', { sessionId, prompt: [{ type: 'text', text: 'fixture' }] }, totalMs, {
    idleTimeoutMs: idleMs,
    progressTracker: tracker,
  })
}


test('JSON-RPC errors retain only standard code buckets and request-local sequence', async () => {
  for (const [code, bucket] of [[-32700, 'parse_error'], [-32600, 'invalid_request'],
    [-32601, 'method_not_found'], [-32602, 'invalid_params'], [-32603, 'internal_error'],
    [-32099, 'server_error'], [-32000, 'server_error'], [-31999, 'unknown'], [42, 'unknown'],
    [Number.MAX_SAFE_INTEGER + 1, 'unknown'], ['secret', 'unknown']]) {
    assert.equal(safeRpcErrorCodeBucket(code), bucket, `JSON-RPC code ${String(code)} maps to ${bucket}`)
  }
  const run = startFixture('rpc_error')
  const sessionId = await initialize(run.client)
  try {
    await assert.rejects(prompt(run.client, sessionId, new PromptProgressTracker({ sessionId })), error => {
      assert.equal(error.safeClass, 'rpc_error')
      assert.equal(error.safeRequestSequence, 3)
      assert.equal(error.safeRpcErrorCode, 'invalid_params')
      assert.equal(error.message, 'DSH RPC failed')
      assert.deepEqual(Object.keys(error).sort(), ['promptProgress', 'safeClass', 'safeMethod', 'safeRequestSequence',
        'safeRpcErrorCode', 'safeStage'].sort())
      const summary = buildSafeAcceptanceSummary({ dshDiagnostic: { stage: error.safeStage, method: error.safeMethod,
        error_class: error.safeClass, request_sequence: error.safeRequestSequence, rpc_error_code: error.safeRpcErrorCode,
        message: 'rpc message sentinel', data: { prompt: 'private prompt sentinel', provider: 'provider payload sentinel',
          tool: 'tool payload sentinel' } } })
      assert.deepEqual(summary.dsh_diagnostic, { schema: 'dsh_rpc_failure_diagnostic_v1', stage: 'session_rpc', method: 'session/prompt', error_class: 'rpc_error',
        request_sequence: 3, rpc_error_code: 'invalid_params' })
      assert.doesNotMatch(JSON.stringify(summary), /rpc message sentinel|private prompt sentinel|provider payload sentinel|tool payload sentinel/)
      assert.doesNotMatch(JSON.stringify(error), /rpc message sentinel|private prompt sentinel|provider payload sentinel|tool payload sentinel/)
      return true
    })
  } finally {
    run.client.close()
    run.child.kill()
  }
})

test('coldstart prompt timeout policy defaults to production watchdog and supports the legacy alias', () => {
  assert.deepEqual(resolvePromptTimeoutPolicy({}), { totalTimeoutSeconds: 3600, idleTimeoutSeconds: 600 })
  assert.deepEqual(resolvePromptTimeoutPolicy({ AIMODELER_ACCEPTANCE_DEADLINE_SECONDS: '1800' }),
    { totalTimeoutSeconds: 1800, idleTimeoutSeconds: 600 })
  assert.deepEqual(resolvePromptTimeoutPolicy({ AIMODELER_ACCEPTANCE_DEADLINE_SECONDS: '90',
    AIMODELER_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS: '120', AIMODELER_ACCEPTANCE_IDLE_TIMEOUT_SECONDS: '30' }),
  { totalTimeoutSeconds: 120, idleTimeoutSeconds: 30 })
  assert.throws(() => resolvePromptTimeoutPolicy({ AIMODELER_ACCEPTANCE_IDLE_TIMEOUT_SECONDS: '0' }), /无进展时限/)
})

test('hard total deadline is not reset by valid ACP thought progress', async () => {
  const run = startFixture('total')
  const sessionId = await initialize(run.client)
  const tracker = new PromptProgressTracker({ sessionId })
  try {
    await assert.rejects(prompt(run.client, sessionId, tracker, { totalMs: 115, idleMs: 38 }), error => {
      assert.equal(error.safeClass, 'timeout')
      assert.equal(error.promptProgress.deadline_kind, 'total')
      assert.ok(error.promptProgress.counts.thought_chunks >= 2)
      return true
    })
  } finally {
    await finishFixture(run, sessionId)
  }
})

test('wrong-session, empty, unknown and usage updates do not reset idle deadline', async () => {
  const run = startFixture('idle_noise')
  const sessionId = await initialize(run.client)
  const tracker = new PromptProgressTracker({ sessionId })
  try {
    await assert.rejects(prompt(run.client, sessionId, tracker, { totalMs: 300, idleMs: 75 }), error => {
      assert.equal(error.safeClass, 'timeout')
      assert.equal(error.promptProgress.deadline_kind, 'idle')
      assert.equal(error.promptProgress.counts.valid_session_updates, 0)
      assert.equal(error.promptProgress.counts.message_chunks, 0)
      assert.equal(error.promptProgress.counts.thought_chunks, 0)
      return true
    })
  } finally {
    await finishFixture(run, sessionId)
  }
})

test('repeated identical tool status is not progress after its first update', async () => {
  const run = startFixture('duplicate_tool')
  const sessionId = await initialize(run.client)
  const tracker = new PromptProgressTracker({ sessionId })
  try {
    await assert.rejects(prompt(run.client, sessionId, tracker, { totalMs: 300, idleMs: 65 }), error => {
      assert.equal(error.promptProgress.deadline_kind, 'idle')
      assert.equal(error.promptProgress.counts.tool_updates, 1)
      return true
    })
  } finally {
    await finishFixture(run, sessionId)
  }
})

test('matching terminal tool update resets idle deadline and is counted', async () => {
  const run = startFixture('terminal')
  const sessionId = await initialize(run.client)
  const tracker = new PromptProgressTracker({ sessionId })
  try {
    const result = await prompt(run.client, sessionId, tracker, { totalMs: 800, idleMs: 250 })
    assert.equal(result.accepted, true)
    const summary = tracker.snapshot()
    assert.equal(summary.counts.tool_terminal_completed, 1)
    assert.equal(summary.counts.tool_updates, 1)
  } finally {
    await finishFixture(run, sessionId)
  }
})

test('increasing geometry revision resets idle deadline and is counted', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'coldstart-progress-'))
  const statePath = join(directory, 'cad-state.json')
  writeFileSync(statePath, JSON.stringify({ revision: 4 }))
  const run = startFixture('geometry', { FAKE_ACP_STATE_PATH: statePath })
  const sessionId = await initialize(run.client)
  const tracker = new PromptProgressTracker({ sessionId, initialRevision: 4 })
  const watcher = watchGeometryRevision(statePath, tracker, 10)
  try {
    const result = await prompt(run.client, sessionId, tracker, { totalMs: 800, idleMs: 250 })
    assert.equal(result.accepted, true)
    assert.equal(JSON.parse(readFileSync(statePath, 'utf8')).revision, 5)
    assert.equal(tracker.snapshot().counts.geometry_revision_advances, 1)
  } finally {
    watcher.close()
    await finishFixture(run, sessionId)
    rmSync(directory, { recursive: true, force: true })
  }
})

test('safe progress projection retains only fixed enums, timings and counts', () => {
  const safe = safePromptProgressSummary({
    deadline_kind: 'total', elapsed_ms: 1234, last_meaningful_update_age_ms: 234,
    session_id: 'secret-session-id', path: 'C:\\private\\scratch', thought: 'private model content',
    counts: { valid_session_updates: 2, thought_chunks: 1, tool_calls: 3, tool_terminal_failed: 1,
      secret_counter: 999 },
  })
  assert.deepEqual(safe, {
    schema: 'prompt_progress_v1', deadline_kind: 'total', elapsed_ms: 1234,
    last_meaningful_update_age_ms: 234,
    counts: { valid_session_updates: 2, message_chunks: 0, thought_chunks: 1, tool_calls: 3,
      tool_updates: 0, tool_terminal_completed: 0, tool_terminal_failed: 1, geometry_revision_advances: 0 },
  })
  assert.doesNotMatch(JSON.stringify(safe), /secret-session-id|private|scratch|thought-/)
})

test('safe acceptance and fatal summaries embed only the allowlisted progress projection', () => {
  const progress = { deadline_kind: 'idle', elapsed_ms: 4200, last_meaningful_update_age_ms: 60000,
    session_id: 'secret-session-id', path: 'C:\\private\\scratch', text: 'private provider text',
    counts: { valid_session_updates: 4, thought_chunks: 3, tool_terminal_failed: 1 } }
  const expected = safePromptProgressSummary(progress)
  const acceptedProjection = buildSafeAcceptanceSummary({ promptProgress: progress })
  assert.deepEqual(acceptedProjection.prompt_progress, expected)

  const directory = mkdtempSync(join(tmpdir(), 'coldstart-safe-summary-'))
  try {
    const failure = writeSafeFailureSummary(directory, { stage: 'dsh_session', code: 'uncaught_exception',
      promptProgress: progress })
    assert.deepEqual(failure.prompt_progress, expected)
    const serialized = readFileSync(join(directory, 'summary.json'), 'utf8')
    assert.doesNotMatch(serialized, /secret-session-id|private provider text|C:\\\\private\\\\scratch/)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
