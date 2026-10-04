import assert from 'node:assert/strict'
import { once } from 'node:events'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  apply,
  createToolBridgeClient,
  readNativeToolSchemas,
  requestToolBridge,
} from '../ai_modeler/agent/dsh_native_tools.mjs'

const environmentKeys = [
  'AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE',
  'AI_MODELER_TOOL_BRIDGE_ADDR',
  'AI_MODELER_TOOL_BRIDGE_TOKEN',
  'AI_MODELER_TOOL_SESSION_ID',
  'AI_MODELER_ACTIVE_TURN_FILE',
  'AI_MODELER_DSH_NODE_MODULES',
  'AI_MODELER_NATIVE_TOOLS_FILE',
  'AI_MODELER_PYTHON',
  'AI_MODELER_PROJECT_ROOT',
]

function fixtureEnvironment(manifest = {
  schemaVersion: 1,
  tools: [{
    name: 'cad_preview_model',
    description: 'Create an on-demand preview.',
    inputSchema: {
      type: 'object',
      properties: {
        view: { type: 'string', enum: ['front', 'iso'], description: 'Requested view.' },
        generation: { type: 'integer', minimum: 1 },
        sources: {
          type: 'object',
          additionalProperties: {
            type: 'object',
            description: 'CADPlan provenance value with type and evidence fields.',
            properties: { type: { type: 'string' }, evidence: { type: 'string' } },
          },
        },
      },
      required: ['view'],
      additionalProperties: false,
    },
  }],
}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-native-tools-rpc-'))
  const nodeModules = path.join(root, 'node_modules')
  fs.mkdirSync(path.join(nodeModules, '@deepseek-ai', 'dsh'), { recursive: true })
  fs.mkdirSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools'), { recursive: true })
  fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh', version: 'test' }))
  fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: 'test', main: 'index.cjs' }))
  fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools', 'index.cjs'), 'exports.defineTool = value => value\n')

  const schemasPath = path.join(root, 'native-tool-schemas.json')
  const activeTurnPath = path.join(root, 'active-turn.json')
  const registrationPath = path.join(root, 'registration.json')
  fs.writeFileSync(schemasPath, JSON.stringify(manifest))
  fs.writeFileSync(activeTurnPath, JSON.stringify({ turn_id: 'turn-fixture-42' }))

  const previous = Object.fromEntries(environmentKeys.map(key => [key, process.env[key]]))
  process.env.AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE = schemasPath
  process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
  process.env.AI_MODELER_DSH_NODE_MODULES = nodeModules
  process.env.AI_MODELER_NATIVE_TOOLS_FILE = registrationPath
  process.env.AI_MODELER_TOOL_BRIDGE_TOKEN = 'fixture-secret-token'
  process.env.AI_MODELER_TOOL_SESSION_ID = 'session-fixture-27'
  // If a hidden Python fallback remains, this deliberately unusable command
  // should make plugin setup or the call fail.
  process.env.AI_MODELER_PYTHON = 'python-must-not-be-launched'
  delete process.env.AI_MODELER_PROJECT_ROOT

  return {
    root,
    nodeModules,
    schemasPath,
    activeTurnPath,
    registrationPath,
    restore() {
      for (const key of environmentKeys) {
        if (previous[key] === undefined) delete process.env[key]
        else process.env[key] = previous[key]
      }
      fs.rmSync(root, { recursive: true, force: true })
    },
  }
}

async function createBridgeServer(handler) {
  const requests = []
  const server = net.createServer(socket => {
    let buffer = Buffer.alloc(0)
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk])
      const newline = buffer.indexOf(0x0a)
      if (newline < 0) return
      const line = buffer.subarray(0, newline).toString('utf8').replace(/\r$/, '')
      buffer = buffer.subarray(newline + 1)
      let request
      try {
        request = JSON.parse(line)
      } catch {
        socket.end('invalid request\n')
        return
      }
      requests.push(request)
      handler(request, socket, requests)
    })
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const { port } = server.address()
  return {
    address: '127.0.0.1:' + port,
    requests,
    async close() {
      server.close()
      await once(server, 'close')
    },
  }
}

function respond(socket, request, body = { result: { text: 'ok' } }) {
  socket.end(JSON.stringify({ jsonrpc: '2.0', id: request.id, ...body }) + '\n')
}

test('Cordis tools register from Rust schema manifest and call the local bridge with turn/session identity', async () => {
  const fixture = fixtureEnvironment()
  const bridge = await createBridgeServer((request, socket) => {
    respond(socket, request, { result: {
      text: 'front preview ready',
      payload: { previewGeneration: 4 },
      images: [{ mediaType: 'image/png', data: Buffer.from('preview image bytes').toString('base64') }],
    } })
  })
  process.env.AI_MODELER_TOOL_BRIDGE_ADDR = bridge.address
  try {
    const registered = []
    const saved = []
    apply({
      tools: { register(tool) { registered.push(tool) } },
      attachments: { async saveImages(images) { saved.push(...images); return ['attachment-preview-4'] } },
    })

    assert.equal(registered.length, 1)
    assert.equal(registered[0].name, 'cad_preview_model')
    assert.equal(registered[0].parameters.view.required, true)
    assert.deepEqual(registered[0].parameters.view.enum, ['front', 'iso'])
    assert.equal(registered[0].parameters.generation.type, 'integer')
    assert.equal(registered[0].parameters.generation.description.includes('minimum=1'), true)
    assert.equal(registered[0].parameters.sources.additionalProperties, true)
    assert.match(registered[0].parameters.sources.description, /additionalProperties schema=/)
    assert.match(registered[0].parameters.sources.description, /CADPlan provenance value/)

    const output = await registered[0].execute({ view: 'front', generation: 4 }, {
      signal: new AbortController().signal, callId: 'acp-call-fixture-42',
    })
    assert.equal(output.text, 'front preview ready')
    assert.deepEqual(output.payload, { previewGeneration: 4 })
    assert.deepEqual(output.imageAttachments, ['attachment-preview-4'])
    assert.equal(saved.length, 1)
    assert.equal(saved[0].mediaType, 'image/png')
    assert.equal(saved[0].data.toString(), 'preview image bytes')

    assert.equal(bridge.requests.length, 1)
    const request = bridge.requests[0]
    assert.equal(request.jsonrpc, '2.0')
    assert.equal(request.method, 'tools/call')
    assert.match(request.id, /^[0-9a-f-]{36}$/i)
    assert.equal(request.params.token, 'fixture-secret-token')
    assert.equal(request.params.sessionId, 'session-fixture-27')
    assert.match(request.params.toolCallId, /^[0-9a-f-]{36}$/i)
    assert.equal(request.params.turnId, 'turn-fixture-42')
    assert.equal(request.params.name, 'cad_preview_model')
    assert.equal(request.params.acpCallId, 'acp-call-fixture-42')
    assert.deepEqual(request.params.arguments, { view: 'front', generation: 4 })
    assert.ok(request.params.deadlineMs > Date.now())
    assert.deepEqual(JSON.parse(fs.readFileSync(fixture.registrationPath, 'utf8')).registeredToolNames, ['cad_preview_model'])
  } finally {
    await bridge.close()
    fixture.restore()
  }
})

test('schema manifest is versioned and rejects duplicate or malformed tool definitions', () => {
  const valid = {
    schemaVersion: 1,
    tools: [{ name: 'cad_test', description: 'Test tool.', inputSchema: { type: 'object', properties: {} } }],
  }
  const fixture = fixtureEnvironment(valid)
  try {
    assert.deepEqual(readNativeToolSchemas(fixture.schemasPath), valid.tools)
    fs.writeFileSync(fixture.schemasPath, JSON.stringify({ ...valid, schemaVersion: 2 }))
    assert.throws(() => readNativeToolSchemas(fixture.schemasPath), /schemaVersion 1/)
    fs.writeFileSync(fixture.schemasPath, JSON.stringify({
      schemaVersion: 1,
      tools: [valid.tools[0], valid.tools[0]],
    }))
    assert.throws(() => readNativeToolSchemas(fixture.schemasPath), /duplicate tool name/)
    fs.writeFileSync(fixture.schemasPath, JSON.stringify({
      schemaVersion: 1,
      tools: [{ name: 'bad name', description: 'Bad.', inputSchema: { type: 'object', properties: {} } }],
    }))
    assert.throws(() => readNativeToolSchemas(fixture.schemasPath), /invalid tool name/)
    fs.writeFileSync(fixture.schemasPath, JSON.stringify({
      schemaVersion: 1,
      tools: [{
        name: 'cad_test', description: 'Test tool.',
        inputSchema: { type: 'object', properties: { value: { type: 'not-a-json-type' } } },
      }],
    }))
    assert.throws(() => readNativeToolSchemas(fixture.schemasPath), /unsupported type/)
  } finally {
    fixture.restore()
  }
})

test('tool cancellation sends tools/cancel on a separate connection with the same call and turn ids', async () => {
  const fixture = fixtureEnvironment()
  let callSocket
  const bridge = await createBridgeServer((request, socket) => {
    if (request.method === 'tools/call') callSocket = socket
    else if (request.method === 'tools/cancel') respond(socket, request, { result: { accepted: true } })
  })
  try {
    const client = createToolBridgeClient({
      address: bridge.address,
      token: 'cancel-token',
      sessionId: 'cancel-session',
      activeTurnFile: fixture.activeTurnPath,
      connectTimeoutMs: 200,
      operationTimeoutMs: 1_000,
      cancelTimeoutMs: 200,
    })
    const controller = new AbortController()
    const pending = client.callTool('cad_preview_model', { view: 'iso' }, controller.signal)
    for (let i = 0; i < 50 && !callSocket; i += 1) await new Promise(resolve => setTimeout(resolve, 5))
    assert.ok(callSocket, 'tools/call request reached the local bridge')
    controller.abort(new Error('fixture cancel requested'))
    await assert.rejects(pending, /fixture cancel requested/)
    const cancelRequest = bridge.requests.find(request => request.method === 'tools/cancel')
    const callRequest = bridge.requests.find(request => request.method === 'tools/call')
    assert.ok(cancelRequest)
    assert.notEqual(cancelRequest.id, callRequest.id)
    assert.equal(cancelRequest.params.toolCallId, callRequest.params.toolCallId)
    assert.equal(cancelRequest.params.turnId, 'turn-fixture-42')
    assert.equal(cancelRequest.params.sessionId, 'cancel-session')
  } finally {
    callSocket?.destroy()
    await bridge.close()
    fixture.restore()
  }
})

test('tool bridge returns readable JSON-RPC errors, invalid JSON, and operation timeout failures', async () => {
  const errorBridge = await createBridgeServer((request, socket) => {
    respond(socket, request, { error: { code: -32012, message: 'feature revision conflict' } })
  })
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: errorBridge.address, connectTimeoutMs: 200, timeoutMs: 200 }),
      /failed \(-32012\): feature revision conflict/,
    )
  } finally {
    await errorBridge.close()
  }

  const invalidBridge = await createBridgeServer((_request, socket) => socket.end('not json\n'))
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: invalidBridge.address, connectTimeoutMs: 200, timeoutMs: 200 }),
      /returned invalid JSON/,
    )
  } finally {
    await invalidBridge.close()
  }

  const disconnectBridge = await createBridgeServer((_request, socket) => socket.end())
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: disconnectBridge.address, connectTimeoutMs: 200, timeoutMs: 200 }),
      /closed before sending a complete JSON-RPC response line/,
    )
  } finally {
    await disconnectBridge.close()
  }

  const mismatchedBridge = await createBridgeServer((_request, socket) => {
    socket.end(JSON.stringify({ jsonrpc: '2.0', id: 'different-id', result: {} }) + '\n')
  })
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: mismatchedBridge.address, connectTimeoutMs: 200, timeoutMs: 200 }),
      /response id did not match/,
    )
  } finally {
    await mismatchedBridge.close()
  }

  const timeoutBridge = await createBridgeServer(() => {})
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: timeoutBridge.address, connectTimeoutMs: 200, timeoutMs: 30 }),
      /tools\/call timed out after 30ms/,
    )
  } finally {
    await timeoutBridge.close()
  }
})

test('request and response JSON lines are capped to a configured size', async () => {
  const tooLargeResponse = await createBridgeServer((request, socket) => {
    respond(socket, request, { result: { text: 'x'.repeat(200) } })
  })
  try {
    await assert.rejects(
      requestToolBridge('tools/call', {}, { address: tooLargeResponse.address, connectTimeoutMs: 200, timeoutMs: 200, maxLineBytes: 128 }),
      /response exceeds the 128-byte line limit/,
    )
  } finally {
    await tooLargeResponse.close()
  }

  const tinyBridge = await createBridgeServer(() => {})
  try {
    await assert.rejects(
      requestToolBridge('tools/call', { large: 'x'.repeat(128) }, { address: tinyBridge.address, maxLineBytes: 64 }),
      /request exceeds the 64-byte line limit/,
    )
  } finally {
    await tinyBridge.close()
  }
})

test('native Cordis plugin has no Python worker or Python executable fallback', () => {
  const source = fs.readFileSync(new URL('../ai_modeler/agent/dsh_native_tools.mjs', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /node:child_process|AI_MODELER_PYTHON|AI_MODELER_PROJECT_ROOT|dsh_tool_worker/)
})
