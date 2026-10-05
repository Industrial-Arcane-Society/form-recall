import { createInterface } from 'node:readline'
import { performance } from 'node:perf_hooks'
import { existsSync, readFileSync } from 'node:fs'

const DEFAULT_TOTAL_TIMEOUT_SECONDS = 60 * 60
const DEFAULT_IDLE_TIMEOUT_SECONDS = 10 * 60
const MAX_TIMEOUT_SECONDS = 24 * 60 * 60
const PROMPT_DEADLINE_KINDS = new Set(['none', 'idle', 'total'])
const SAFE_RPC_METHODS = new Set(['initialize', 'session/new', 'session/set_config_option', 'session/prompt',
  'session/close', 'session/cancel'])

function timeoutSeconds(value, fallback, label) {
  const seconds = value === undefined ? fallback : Number(value)
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > MAX_TIMEOUT_SECONDS) {
    throw new Error(`${label} 须为 1..86400 秒。`)
  }
  return seconds
}

// The old single deadline env remains an alias for the total deadline. The new
// name wins when both are present, while the idle deadline has its own setting.
export function resolvePromptTimeoutPolicy(env = process.env) {
  const totalRaw = env.AIMODELER_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS
    ?? env.AIMODELER_ACCEPTANCE_DEADLINE_SECONDS
  return {
    totalTimeoutSeconds: timeoutSeconds(totalRaw, DEFAULT_TOTAL_TIMEOUT_SECONDS, '总时限'),
    idleTimeoutSeconds: timeoutSeconds(env.AIMODELER_ACCEPTANCE_IDLE_TIMEOUT_SECONDS,
      DEFAULT_IDLE_TIMEOUT_SECONDS, '无进展时限'),
  }
}

export function safePromptProgressSummary(value = {}) {
  const count = key => Number.isSafeInteger(value.counts?.[key]) && value.counts[key] >= 0
    ? Math.min(value.counts[key], 1_000_000_000) : 0
  const safeMs = key => Number.isSafeInteger(value[key]) && value[key] >= 0
    ? Math.min(value[key], 86_400_000) : null
  return {
    schema: 'prompt_progress_v1',
    deadline_kind: PROMPT_DEADLINE_KINDS.has(value.deadline_kind) ? value.deadline_kind : 'none',
    elapsed_ms: safeMs('elapsed_ms') ?? 0,
    last_meaningful_update_age_ms: safeMs('last_meaningful_update_age_ms'),
    counts: {
      valid_session_updates: count('valid_session_updates'),
      message_chunks: count('message_chunks'),
      thought_chunks: count('thought_chunks'),
      tool_calls: count('tool_calls'),
      tool_updates: count('tool_updates'),
      tool_terminal_completed: count('tool_terminal_completed'),
      tool_terminal_failed: count('tool_terminal_failed'),
      geometry_revision_advances: count('geometry_revision_advances'),
    },
  }
}

export class PromptProgressTracker {
  constructor({ sessionId, initialRevision = null, now = () => performance.now() } = {}) {
    if (typeof sessionId !== 'string' || sessionId.length === 0) throw new Error('sessionId is required')
    this.sessionId = sessionId
    this.now = now
    this.startedAt = now()
    this.lastMeaningfulAt = null
    this.lastTextChunk = null
    this.revision = Number.isSafeInteger(initialRevision) && initialRevision >= 0 ? initialRevision : null
    this.toolStatuses = new Map()
    this.listeners = new Set()
    this.counts = {
      valid_session_updates: 0,
      message_chunks: 0,
      thought_chunks: 0,
      tool_calls: 0,
      tool_updates: 0,
      tool_terminal_completed: 0,
      tool_terminal_failed: 0,
      geometry_revision_advances: 0,
    }
  }

  subscribe(listener) {
    if (typeof listener !== 'function') throw new Error('progress listener must be a function')
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  #progress() {
    this.lastMeaningfulAt = this.now()
    for (const listener of this.listeners) listener()
  }

  observeAcpUpdate(message) {
    if (message?.method !== 'session/update' || message.params?.sessionId !== this.sessionId) return false
    const update = message.params?.update
    if (!update || typeof update !== 'object' || Array.isArray(update)) return false
    const kind = update.sessionUpdate
    if (kind === 'agent_message_chunk' || kind === 'agent_thought_chunk') {
      const text = update.content?.text
      if (typeof text !== 'string' || text.length === 0 || text === this.lastTextChunk) return false
      this.lastTextChunk = text
      this.counts.valid_session_updates += 1
      this.counts[kind === 'agent_message_chunk' ? 'message_chunks' : 'thought_chunks'] += 1
      this.#progress()
      return true
    }
    if (kind === 'tool_call' || kind === 'tool_call_update') {
      const callId = update.toolCallId ?? update.id
      if (typeof callId !== 'string' || callId.length === 0) return false
      const state = `${typeof update.status === 'string' ? update.status : ''}|${typeof update.title === 'string' ? update.title : ''}|${typeof update.kind === 'string' ? update.kind : ''}`
      if (this.toolStatuses.get(callId) === state) return false
      this.toolStatuses.set(callId, state)
      if (kind === 'tool_call') {
        this.counts.tool_calls += 1
      } else {
        this.counts.tool_updates += 1
        if (update.status === 'completed') this.counts.tool_terminal_completed += 1
        if (update.status === 'failed') this.counts.tool_terminal_failed += 1
      }
      this.counts.valid_session_updates += 1
      this.#progress()
      return true
    }
    // Match the production Agent watchdog: usage, mode/config and unknown
    // notification kinds are not activity evidence and cannot keep a turn alive.
    return false
  }

  observeGeometryRevision(revision) {
    if (!Number.isSafeInteger(revision) || revision < 0) return false
    if (this.revision === null) {
      this.revision = revision
      return false
    }
    if (revision <= this.revision) return false
    this.revision = revision
    this.counts.geometry_revision_advances += 1
    this.#progress()
    return true
  }

  snapshot(deadlineKind = 'none') {
    const now = this.now()
    return safePromptProgressSummary({
      deadline_kind: deadlineKind,
      elapsed_ms: Math.max(0, Math.floor(now - this.startedAt)),
      last_meaningful_update_age_ms: this.lastMeaningfulAt === null
        ? Math.max(0, Math.floor(now - this.startedAt))
        : Math.max(0, Math.floor(now - this.lastMeaningfulAt)),
      counts: this.counts,
    })
  }
}

function safeRpcError(method, errorClass, deadlineKind = 'none', progress = null) {
  const error = new Error('DSH RPC failed')
  error.safeStage = 'session_rpc'
  error.safeMethod = SAFE_RPC_METHODS.has(method) ? method : 'unknown'
  error.safeClass = ['rpc_error', 'timeout', 'child_exit', 'write_error', 'invalid_response'].includes(errorClass)
    ? errorClass : 'unknown'
  error.promptProgress = progress
    ? safePromptProgressSummary({ ...progress, deadline_kind: deadlineKind })
    : null
  return error
}

export function createAcpRpcClient(child, { onNotification = () => {}, onInvalidMessage = () => {} } = {}) {
  const pending = new Map()
  let sequence = 0
  let exited = false
  const messages = createInterface({ input: child.stdout })
  const clearTimers = task => {
    if (task.totalTimer) clearTimeout(task.totalTimer)
    if (task.idleTimer) clearTimeout(task.idleTimer)
    task.unsubscribe?.()
  }
  const rejectPending = (errorClass) => {
    for (const [id, task] of pending) {
      pending.delete(id)
      clearTimers(task)
      task.reject(safeRpcError(task.method, errorClass, 'none', task.progressTracker?.snapshot()))
    }
  }

  messages.on('line', line => {
    let row
    try { row = JSON.parse(line) } catch {
      try { onInvalidMessage() } catch { /* diagnostics must not stall RPC dispatch */ }
      return
    }
    if (row?.method === 'session/update') {
      for (const task of pending.values()) {
        if (task.method !== 'session/prompt' || !task.progressTracker) continue
        task.progressTracker.observeAcpUpdate(row)
      }
      try { onNotification(row) } catch { /* diagnostics must not stall RPC dispatch */ }
      return
    }
    if (row && row.id !== undefined && !row.method) {
      const task = pending.get(row.id)
      if (!task) return
      pending.delete(row.id)
      clearTimers(task)
      if (row.error) task.reject(safeRpcError(task.method, 'rpc_error', 'none', task.progressTracker?.snapshot()))
      else if (Object.prototype.hasOwnProperty.call(row, 'result')) task.resolve(row.result || {})
      else task.reject(safeRpcError(task.method, 'invalid_response', 'none', task.progressTracker?.snapshot()))
      return
    }
    if (row?.method && row.id !== undefined) {
      try {
        child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: row.id,
          error: { code: -32601, message: 'Unsupported acceptance client method' } })}\n`)
      } catch { /* a closing DSH process will fail its pending request */ }
    }
  })

  child.once('exit', () => { exited = true; rejectPending('child_exit') })
  child.once('error', () => { exited = true; rejectPending('child_exit') })

  function request(method, params, timeoutMs = 45_000, options = {}) {
    if (exited) return Promise.reject(safeRpcError(method, 'child_exit'))
    const id = ++sequence
    return new Promise((resolve, reject) => {
      const task = { method, resolve, reject, progressTracker: options.progressTracker || null,
        totalTimer: null, idleTimer: null, unsubscribe: null, resetIdle: null }
      pending.set(id, task)
      const timeoutTask = kind => {
        if (!pending.has(id)) return
        pending.delete(id)
        clearTimers(task)
        const progress = task.progressTracker?.snapshot(kind) || null
        reject(safeRpcError(method, 'timeout', kind, progress))
      }
      if (task.progressTracker && method === 'session/prompt') {
        const idleTimeoutMs = options.idleTimeoutMs
        if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || !Number.isInteger(idleTimeoutMs) || idleTimeoutMs < 1) {
          pending.delete(id)
          reject(safeRpcError(method, 'invalid_response'))
          return
        }
        task.totalTimer = setTimeout(() => timeoutTask('total'), timeoutMs)
        task.resetIdle = () => {
          if (!pending.has(id)) return
          if (task.idleTimer) clearTimeout(task.idleTimer)
          task.idleTimer = setTimeout(() => timeoutTask('idle'), idleTimeoutMs)
        }
        task.unsubscribe = task.progressTracker.subscribe(task.resetIdle)
        task.resetIdle()
      } else {
        if (!Number.isInteger(timeoutMs) || timeoutMs < 1) {
          pending.delete(id)
          reject(safeRpcError(method, 'invalid_response'))
          return
        }
        task.totalTimer = setTimeout(() => timeoutTask('none'), timeoutMs)
      }
      try {
        child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`)
      } catch {
        pending.delete(id)
        clearTimers(task)
        reject(safeRpcError(method, 'write_error', 'none', task.progressTracker?.snapshot()))
      }
    })
  }

  function notify(method, params) {
    if (exited) throw safeRpcError(method, 'child_exit')
    try {
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`)
    } catch {
      throw safeRpcError(method, 'write_error')
    }
  }

  return {
    request,
    notify,
    close() { messages.close() },
  }
}

export function readGeometryRevision(path) {
  try {
    if (!existsSync(path)) return null
    const state = JSON.parse(readFileSync(path, 'utf8'))
    return Number.isSafeInteger(state?.revision) && state.revision >= 0 ? state.revision : null
  } catch { return null }
}

export function watchGeometryRevision(path, tracker, intervalMs = 100, initialRevision = readGeometryRevision(path)) {
  if (!Number.isInteger(intervalMs) || intervalMs < 1) throw new Error('intervalMs must be positive')
  let lastRevision = Number.isSafeInteger(initialRevision) && initialRevision >= 0 ? initialRevision : null
  const poll = () => {
    const revision = readGeometryRevision(path)
    if (revision === null) return
    if (lastRevision === null) { lastRevision = revision; return }
    if (revision > lastRevision) {
      lastRevision = revision
      tracker.observeGeometryRevision(revision)
    } else if (revision < lastRevision) {
      // A replacement/reset is a new baseline, never progress.
      lastRevision = revision
    }
  }
  const timer = setInterval(poll, intervalMs)
  return { poll, close() { clearInterval(timer) } }
}

export async function cancelAndCloseSession(client, sessionId, closeTimeoutMs = 15_000) {
  let cancelSent = false
  let closeCompleted = false
  try {
    client.notify('session/cancel', { sessionId })
    cancelSent = true
  } catch { /* bounded close and process termination still follow */ }
  try {
    await client.request('session/close', { sessionId }, closeTimeoutMs)
    closeCompleted = true
  } catch { /* the caller terminates the managed child with a bounded wait */ }
  return { cancel_sent: cancelSent, close_completed: closeCompleted }
}
