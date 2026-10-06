import { createInterface } from 'node:readline'
import { readFileSync, writeFileSync } from 'node:fs'

const mode = process.env.FAKE_ACP_MODE || 'idle_noise'
const sessionId = 'fixture-session'
const timers = new Set()
let interval = null
let cancelReceived = false
let sequence = 0

function send(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

function schedule(callback, delayMs) {
  const timer = setTimeout(() => {
    timers.delete(timer)
    callback()
  }, delayMs)
  timers.add(timer)
}

function stopActivity() {
  if (interval) clearInterval(interval)
  interval = null
  for (const timer of timers) clearTimeout(timer)
  timers.clear()
}

function update(targetSessionId, value) {
  send({ jsonrpc: '2.0', method: 'session/update', params: { sessionId: targetSessionId, update: value } })
}

createInterface({ input: process.stdin }).on('line', line => {
  let request
  try { request = JSON.parse(line) } catch { return }
  if (request.method === 'initialize') {
    send({ jsonrpc: '2.0', id: request.id, result: { agentCapabilities: { promptCapabilities: { image: true } } } })
  } else if (request.method === 'session/new') {
    send({ jsonrpc: '2.0', id: request.id, result: { sessionId } })
  } else if (request.method === 'session/cancel') {
    cancelReceived = true
    stopActivity()
  } else if (request.method === 'session/prompt') {
    if (mode === 'rpc_error') {
      send({ jsonrpc: '2.0', id: request.id, error: { code: -32602,
        message: 'rpc message sentinel', data: {
          prompt: 'private prompt sentinel', provider: 'provider payload sentinel', tool: 'tool payload sentinel',
        } } })
      return
    }
    if (mode === 'total') {
      interval = setInterval(() => update(sessionId, {
        sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: `thought-${++sequence}` },
      }), 12)
    } else if (mode === 'idle_noise') {
      interval = setInterval(() => {
        update('another-session', { sessionUpdate: 'agent_thought_chunk', content: { text: `wrong-${++sequence}` } })
        update(sessionId, { sessionUpdate: 'usage_update', usage: { totalTokens: ++sequence } })
        update(sessionId, { sessionUpdate: 'unknown_update', value: ++sequence })
        update(sessionId, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: '' } })
        update(sessionId, { sessionUpdate: 'tool_call_update', status: 'completed' })
      }, 12)
    } else if (mode === 'duplicate_tool') {
      schedule(() => update(sessionId, {
        sessionUpdate: 'tool_call_update', toolCallId: 'tool-fixture', status: 'in_progress', title: 'Build', kind: 'other',
      }), 20)
      interval = setInterval(() => update(sessionId, {
        sessionUpdate: 'tool_call_update', toolCallId: 'tool-fixture', status: 'in_progress', title: 'Build', kind: 'other',
      }), 12)
    } else if (mode === 'terminal') {
      schedule(() => update(sessionId, {
        sessionUpdate: 'tool_call_update', toolCallId: 'tool-fixture', status: 'completed',
      }), 120)
      schedule(() => send({ jsonrpc: '2.0', id: request.id, result: { accepted: true } }), 300)
    } else if (mode === 'geometry') {
      schedule(() => {
        const path = process.env.FAKE_ACP_STATE_PATH
        const current = JSON.parse(readFileSync(path, 'utf8'))
        writeFileSync(path, JSON.stringify({ revision: current.revision + 1 }))
      }, 100)
      schedule(() => send({ jsonrpc: '2.0', id: request.id, result: { accepted: true } }), 300)
    }
  } else if (request.method === 'session/close') {
    stopActivity()
    send({ jsonrpc: '2.0', id: request.id, result: { cancelReceived } })
    schedule(() => process.exit(cancelReceived ? 0 : 7), 5)
  }
})
