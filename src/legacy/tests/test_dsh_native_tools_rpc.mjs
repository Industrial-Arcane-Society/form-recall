import assert from 'node:assert/strict'
import { once } from 'node:events'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

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

const testDirectory = path.dirname(fileURLToPath(import.meta.url))
const repositoryRoot = path.resolve(testDirectory, '../../..')
const pinnedDshNodeModules = path.join(repositoryRoot, 'src', 'legacy', 'packaging', 'dsh-runtime', 'node_modules')

function rustNativeToolManifest() {
  const sourcePath = path.join(repositoryRoot, 'src', 'apps', 'desktop', 'src-tauri', 'src', 'native_tool_schemas.rs')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const match = /const SCHEMAS_JSON: &str = r###"([\s\S]*?)"###;/.exec(source)
  assert.ok(match, 'Rust native schema manifest raw string exists')
  return { schemaVersion: 1, tools: JSON.parse(match[1]) }
}

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
}, { dshNodeModules } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-native-tools-rpc-'))
  const nodeModules = path.join(root, 'node_modules')
  if (!dshNodeModules) {
    fs.mkdirSync(path.join(nodeModules, '@deepseek-ai', 'dsh'), { recursive: true })
    fs.mkdirSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools'), { recursive: true })
    fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh', version: 'test' }))
    fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools', 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh-tools', version: 'test', main: 'index.cjs' }))
    fs.writeFileSync(path.join(nodeModules, '@deepseek-ai', 'dsh-tools', 'index.cjs'), 'exports.defineTool = value => value\n')
  }

  const schemasPath = path.join(root, 'native-tool-schemas.json')
  const activeTurnPath = path.join(root, 'active-turn.json')
  const registrationPath = path.join(root, 'registration.json')
  fs.writeFileSync(schemasPath, JSON.stringify(manifest))
  fs.writeFileSync(activeTurnPath, JSON.stringify({ turn_id: 'turn-fixture-42' }))

  const previous = Object.fromEntries(environmentKeys.map(key => [key, process.env[key]]))
  process.env.AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE = schemasPath
  process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
  process.env.AI_MODELER_DSH_NODE_MODULES = dshNodeModules || nodeModules
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

test('Cordis preserves the ordered original-reference and current-preview image pair', async () => {
  const manifest = rustNativeToolManifest()
  manifest.tools = manifest.tools.filter(tool => tool.name === 'cad_preview_model')
  const fixture = fixtureEnvironment(manifest)
  const originalBytes = Buffer.from('original reference bytes')
  const previewBytes = Buffer.from('current front preview bytes')
  const comparisonAttachments = {
    reference_id: 'sha256:reference-fixture',
    reference_sha256: 'reference-fixture',
    order: ['original_reference', 'current_model_single_view'],
    view: 'front',
    revision: 9,
    preview_generation: 4,
    preview_sha256: 'preview-fixture',
  }
  const bridge = await createBridgeServer((request, socket) => {
    respond(socket, request, { result: {
      text: 'front preview ready',
      payload: { comparison_attachments: comparisonAttachments },
      images: [originalBytes, previewBytes].map(data => ({
        mediaType: 'image/png',
        data: data.toString('base64'),
      })),
    } })
  })
  process.env.AI_MODELER_TOOL_BRIDGE_ADDR = bridge.address
  try {
    const registered = []
    const saved = []
    apply({
      tools: { register(tool) { registered.push(tool) } },
      attachments: { async saveImages(images) {
        saved.push(...images)
        return images.map((_image, index) => `attachment-image-${index + 1}`)
      } },
    })

    assert.equal(registered.length, 1)
    assert.equal(registered[0].name, 'cad_preview_model')
    assert.deepEqual(registered[0].parameters.view.enum, ['iso', 'front', 'right', 'top', 'back', 'left', 'bottom'])
    assert.equal(registered[0].parameters.reference_id.type, 'string')
    assert.notEqual(registered[0].parameters.reference_id.required, true)

    const toolArguments = { view: 'front', reference_id: comparisonAttachments.reference_id }
    const output = await registered[0].execute(toolArguments, {
      signal: new AbortController().signal, callId: 'acp-call-fixture-42',
    })
    assert.equal(output.text, 'front preview ready')
    assert.deepEqual(output.payload, { comparison_attachments: comparisonAttachments })
    assert.deepEqual(output.imageAttachments, ['attachment-image-1', 'attachment-image-2'])
    assert.deepEqual(registered[0].output.render(toolArguments, output), [
      { type: 'text', text: 'front preview ready' },
      { type: 'image', attachment: 'attachment-image-1' },
      { type: 'image', attachment: 'attachment-image-2' },
    ])
    assert.equal(saved.length, 2)
    assert.deepEqual(saved.map(image => image.mediaType), ['image/png', 'image/png'])
    assert.deepEqual(saved.map(image => image.data), [originalBytes, previewBytes])

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
    assert.deepEqual(request.params.arguments, toolArguments)
    assert.ok(request.params.deadlineMs > Date.now())
    assert.deepEqual(JSON.parse(fs.readFileSync(fixture.registrationPath, 'utf8')).registeredToolNames, ['cad_preview_model'])
  } finally {
    await bridge.close()
    fixture.restore()
  }
})

test('pinned DSH defineTool accepts native dynamic maps and rejects malformed map contracts', {
  skip: fs.existsSync(path.join(pinnedDshNodeModules, '@deepseek-ai', 'dsh-tools', 'package.json'))
    ? false
    : 'run npm ci in src/legacy/packaging/dsh-runtime to enable the pinned DSH schema test',
}, async () => {
  const dshPackage = JSON.parse(fs.readFileSync(path.join(pinnedDshNodeModules, '@deepseek-ai', 'dsh', 'package.json'), 'utf8'))
  const toolsPackage = JSON.parse(fs.readFileSync(path.join(pinnedDshNodeModules, '@deepseek-ai', 'dsh-tools', 'package.json'), 'utf8'))
  assert.equal(dshPackage.version, '0.2.0-rc.2')
  assert.equal(toolsPackage.version, '0.2.0-rc.2')

  const fixture = fixtureEnvironment(rustNativeToolManifest(), { dshNodeModules: pinnedDshNodeModules })
  const bridge = await createBridgeServer((request, socket) => respond(socket, request))
  process.env.AI_MODELER_TOOL_BRIDGE_ADDR = bridge.address
  try {
    const registered = []
    apply({ tools: { register(tool) { registered.push(tool) } } })
    const create = registered.find(tool => tool.name === 'cad_create_model')
    const update = registered.find(tool => tool.name === 'cad_update_feature')
    assert.ok(create)
    assert.ok(update)
    assert.equal(registered.some(tool => tool.name === 'cad_restore_partial_candidate'), false)

    const createProperties = create.parameters.properties.first_feature.properties
    assert.equal(create.parameters.properties.units.additionalProperties, false)
    assert.deepEqual(create.parameters.properties.units.required, ['length', 'angle'])
    assert.equal(createProperties.sources.type, 'object')
    assert.equal(typeof createProperties.sources.description, 'string')
    assert.equal(createProperties.sources.additionalProperties, true)
    assert.deepEqual(createProperties.relations.items.required, ['type', 'feature_id'])
    assert.equal(createProperties.relations.items.additionalProperties, false)

    const updateProperties = update.parameters.properties
    for (const key of ['parameter_updates', 'source_updates']) {
      assert.equal(updateProperties[key].type, 'object')
      assert.equal(updateProperties[key].additionalProperties, true)
    }
    assert.deepEqual(Object.keys(updateProperties.fields.properties).sort(), ['name', 'note'])
    assert.equal(updateProperties.fields.additionalProperties, false)

    const validCreate = {
      name: 'Fixture block',
      units: { length: 'mm', angle: 'deg' },
      first_feature: {
        id: 'body', kind: 'box', operation: 'add',
        parameters: { length: 10, width: 8, height: 5 },
        sources: {
          length: { type: 'image_estimate', evidence: 'front view ratio' },
          width: { type: 'image_estimate', evidence: 'side view ratio' },
          height: { type: 'image_estimate', evidence: 'front view ratio' },
        },
        relations: [],
      },
    }
    await create.execute(validCreate, { signal: new AbortController().signal, callId: 'create-fixture' })
    assert.equal(bridge.requests[0].params.name, 'cad_create_model')
    assert.deepEqual(bridge.requests[0].params.arguments.first_feature.sources, validCreate.first_feature.sources)

    const validUpdate = {
      feature_id: 'body',
      parameter_updates: { length: 12, center: [1, 0, 0] },
      source_updates: {
        length: { type: 'image_estimate', evidence: 'revised front ratio' },
        center: { type: 'derived', evidence: 'center from bounding edges' },
      },
      fields: { name: 'Revised block' },
    }
    await update.execute(validUpdate, { signal: new AbortController().signal, callId: 'update-fixture' })
    assert.equal(bridge.requests[1].params.name, 'cad_update_feature')
    assert.deepEqual(bridge.requests[1].params.arguments.parameter_updates, validUpdate.parameter_updates)
    assert.deepEqual(bridge.requests[1].params.arguments.source_updates, validUpdate.source_updates)

    await assert.rejects(
      create.execute({ ...validCreate, units: { length: 4, angle: 'deg' } }, { signal: new AbortController().signal }),
      /invalid arguments/i,
    )
    await assert.rejects(
      create.execute({ ...validCreate, first_feature: { ...validCreate.first_feature, relations: [{ type: 'depends_on', feature_id: 'prior', extra: true }] } }, { signal: new AbortController().signal }),
      /invalid arguments/i,
    )
    await assert.rejects(
      update.execute({ ...validUpdate, parameter_updates: ['length'] }, { signal: new AbortController().signal }),
      /invalid arguments/i,
    )
    await assert.rejects(
      update.execute({ ...validUpdate, source_updates: { length: { type: 'image_estimate', evidence: 4 } } }, { signal: new AbortController().signal }),
      /source_updates\.length\.evidence must be a string/i,
    )
    await assert.rejects(
      update.execute({ ...validUpdate, source_updates: { length: { type: 'image_estimate', evidence: 'quote', fabricated: true } } }, { signal: new AbortController().signal }),
      /undeclared source field/i,
    )
    await assert.rejects(
      update.execute({ ...validUpdate, fields: { field_updates: { length: 12 } } }, { signal: new AbortController().signal }),
      /invalid arguments/i,
    )
    assert.equal(bridge.requests.length, 2, 'invalid contracts must be rejected before the native bridge')
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
