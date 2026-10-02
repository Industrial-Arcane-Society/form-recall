import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { execFileSync, spawn } from 'node:child_process'
import { assertImageTransportProof, imageRowsFromOpenAIChat } from './native_agent_loopback_wire_helpers.mjs'

const [repoArg, imageArg, runtimeArg, outputArg] = process.argv.slice(2)
if (!repoArg || !imageArg || !runtimeArg || !outputArg) {
  console.error('Usage: node native_agent_loopback_wire_acceptance.mjs <repo-root> <image> <fixed-runtime-root> <fresh-output-dir>')
  process.exit(2)
}

const repo = resolve(repoArg)
const imagePath = resolve(imageArg)
const runtime = resolve(runtimeArg)
const out = resolve(outputArg)
const legacy = join(repo, 'src', 'legacy')
const home = join(out, 'dsh-home')
const workspace = join(out, 'workspace')
const state = join(workspace, 'state')
const model = 'f7-loopback-vision'
const turnId = `f7-loopback-${randomUUID()}`
const promptText = 'Reply exactly READY. Do not call tools or create a model.'
const sourceBytes = readFileSync(imagePath)
const sha256 = value => createHash('sha256').update(value).digest('hex')
const sourceSha = sha256(sourceBytes)
const filename = imagePath.split(/[\\/]/).pop()
const mediaType = /\.jpe?g$/i.test(filename) ? 'image/jpeg' : 'image/png'
assert.ok(sourceBytes.length > 0, 'input image must not be empty')
assert.equal(mediaType === 'image/png' ? sourceBytes.subarray(0, 8).toString('hex') : sourceBytes.subarray(0, 3).toString('hex'),
  mediaType === 'image/png' ? '89504e470d0a1a0a' : 'ffd8ff', 'input extension and image signature must agree')
assert.ok(readFileSync(join(runtime, 'node.exe')).length > 0, 'fixed Node runtime is missing')
const readPackageVersion = name => JSON.parse(readFileSync(join(runtime, 'node_modules', name, 'package.json'), 'utf8')).version
const runtimeVersions = {
  node: execFileSync(join(runtime, 'node.exe'), ['-p', 'process.version'], { encoding: 'utf8' }).trim(),
  dsh: readPackageVersion('@deepseek-ai/dsh'),
  acp: readPackageVersion('@deepseek-ai/dsh-acp'),
  acp_sdk: readPackageVersion('@agentclientprotocol/sdk'),
}
assert.deepEqual(runtimeVersions, { node: 'v22.23.3', dsh: '0.2.0-rc.2', acp: '0.2.0-rc.2', acp_sdk: '1.4.0' },
  'loopback acceptance requires the fixed Node, DSH, ACP and SDK versions')
mkdirSync(out, { recursive: false })
mkdirSync(home, { recursive: true })
mkdirSync(join(home, 'Roaming'), { recursive: true })
mkdirSync(join(home, 'Local'), { recursive: true })
mkdirSync(state, { recursive: true })

const referenceManifest = join(workspace, 'reference-images.json')
const userTurns = join(workspace, 'user-turns.json')
const activeTurn = join(workspace, 'active-turn.json')
const streamEvents = join(workspace, 'stream-events.jsonl')
const transportMap = join(workspace, 'transport-image-map.json')
writeFileSync(referenceManifest, JSON.stringify({ schema_version: 1, session_id: turnId, images: [{
  id: `sha256:${sourceSha}`, sha256: sourceSha, filename, width: null, height: null, media_type: mediaType, path: imagePath,
}] }))
writeFileSync(userTurns, JSON.stringify({ schema_version: 1, session_id: turnId, turns: [{
  turn_id: turnId, session_id: turnId, raw_user_prompt: promptText, reference_ids: [`sha256:${sourceSha}`],
  at_epoch_ms: Date.now(), intent_source: 'unclassified',
}] }))
writeFileSync(activeTurn, JSON.stringify({ schema_version: 1, session_id: turnId, turn_id: turnId, reference_images: [{
  id: `sha256:${sourceSha}`, sha256: sourceSha, filename, media_type: mediaType, bytes: sourceBytes.length, source_kind: 'user_reference',
}] }))
writeFileSync(transportMap, '{}')

const wireRows = []
let mockError = null
const server = createServer(async (req, res) => {
  const chunks = []
  try {
    for await (const chunk of req) chunks.push(chunk)
    const raw = Buffer.concat(chunks)
    const pathname = new URL(req.url || '/', 'http://127.0.0.1').pathname
    if (pathname === '/v1/models' && req.method === 'GET') {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ object: 'list', data: [{ id: model, object: 'model', owned_by: 'local-loopback' }] }))
      return
    }
    if (pathname !== '/v1/chat/completions' || req.method !== 'POST') {
      res.writeHead(404, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: { message: 'loopback mock only serves OpenAI chat completions' } }))
      return
    }
    const payload = JSON.parse(raw.toString('utf8'))
    const images = imageRowsFromOpenAIChat(payload)
    wireRows.push({ method: req.method, path: pathname, model: payload.model || null, stream: payload.stream === true, image_count: images.length, images })
    if (payload.model !== model) throw new Error('unexpected model in loopback wire request')
    const body = { id: `chatcmpl-${randomUUID()}`, object: 'chat.completion', created: Math.floor(Date.now() / 1000), model,
      choices: [{ index: 0, message: { role: 'assistant', content: 'READY' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }
    if (payload.stream === true) {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' })
      const event = { id: body.id, object: 'chat.completion.chunk', created: body.created, model,
        choices: [{ index: 0, delta: { role: 'assistant', content: 'READY' }, finish_reason: null }] }
      const finish = { id: body.id, object: 'chat.completion.chunk', created: body.created, model,
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }
      res.write(`data: ${JSON.stringify(event)}\n\n`)
      res.write(`data: ${JSON.stringify(finish)}\n\n`)
      res.end('data: [DONE]\n\n')
    } else {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(body))
    }
  } catch (error) {
    mockError = String(error?.message || error)
    res.writeHead(400, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: { message: 'invalid loopback mock request' } }))
  }
})
server.listen(0, '127.0.0.1')
await new Promise((resolveListen, rejectListen) => {
  server.once('listening', resolveListen)
  server.once('error', rejectListen)
})
const address = server.address()
assert.ok(address && typeof address === 'object')
const baseURL = `http://127.0.0.1:${address.port}/v1`

const pluginPaths = [join(legacy, 'ai_modeler/agent/dsh_attachment_store.mjs'), join(legacy, 'ai_modeler/agent/dsh_stream_bridge.mjs')]
const quote = value => JSON.stringify(value)
const disabled = ['session-log-deepseek', 'session-telemetry-otel', 'attachment-local', 'tool-bash', 'tool-pwsh', 'tool-fs', 'tool-fs-search',
  'tool-skill', 'tool-jobs', 'tool-subagent-control', 'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork',
  'tool-workflow', 'tool-todo', 'tool-goal', 'tool-ralph', 'tool-web']
const patch = [
  '# F7 loopback-only profile. The dummy credential is supplied only through the child environment.',
  '- id: llm-pi-ai', '  config:', '    providers:', '      ai-modeler:',
  '        displayName: AI Modeler loopback acceptance', '        apiKeyEnv: AIMODELER_LLM_API_KEY',
  '        api: openai-completions', `        baseURL: ${quote(baseURL)}`, '        models:', `          - id: ${quote(model)}`,
  `            name: ${quote(model)}`, '            contextWindow: 32768', '            maxTokens: 32', '            input: [text, image]',
  '        defaultInput: [text, image]', '        maxRequestImageBytes: 20971520', '        requestImagePixelBudget: 64000000',
  '        requestImageMaxBytes: 20971520', '- id: acp', '  config:', '    provider: ai-modeler', `    model: ${quote(model)}`,
  '- id: system-prompt', '  config:', '    includeHarnessIdentity: false', '    includeRuntimeContext: false',
  "    personaPrefix: !!js process.env.DSH_SYSTEM_PROMPT ?? ''", "    personaSuffix: ''",
  ...disabled.flatMap(id => [`- id: ${id}`, '  disabled: true']),
  '- insert:', '    - id: ai-modeler-attachment-store', `      name: ${quote(pathToFileURL(pluginPaths[0]).href)}`,
  '- insert:', '    - id: ai-modeler-live-stream', `      name: ${quote(pathToFileURL(pluginPaths[1]).href)}`,
]
const patchPath = join(out, 'profile-patch.yml')
writeFileSync(patchPath, `${patch.join('\n')}\n`)

const cli = join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js')
const child = spawn(join(runtime, 'node.exe'), [cli, '--profile', 'acp', '--patch', patchPath], {
  cwd: workspace,
  env: {
    PATH: process.env.PATH || '',
    SystemRoot: process.env.SystemRoot || '',
    WINDIR: process.env.WINDIR || '',
    TEMP: process.env.TEMP || out,
    TMP: process.env.TMP || out,
    USERPROFILE: home,
    APPDATA: join(home, 'Roaming'),
    LOCALAPPDATA: join(home, 'Local'),
    DSH_HOME: home,
    DSH_TELEMETRY_DISABLED: '1',
    DSH_SYSTEM_PROMPT: 'Reply exactly READY. Do not use tools or call external services.',
    AIMODELER_LLM_API_KEY: 'loopback-only-dummy-key',
    AI_MODELER_PROJECT_ROOT: legacy,
    AI_MODELER_STATE_DIR: state,
    AI_MODELER_REFERENCE_MANIFEST: referenceManifest,
    AI_MODELER_USER_TURNS_MANIFEST: userTurns,
    AI_MODELER_ACTIVE_TURN_FILE: activeTurn,
    AI_MODELER_STREAM_FILE: streamEvents,
    AI_MODELER_TRANSPORT_IMAGE_MAP: transportMap,
    AI_MODELER_DSH_NODE_MODULES: join(runtime, 'node_modules'),
    AI_MODELER_ONLINE_ENABLED: 'true',
    AI_MODELER_HAS_REFERENCES: 'true',
    AI_MODELER_REFERENCE_IMAGES: JSON.stringify([{ id: `sha256:${sourceSha}`, filename, media_type: mediaType }]),
    AI_MODELER_PROVIDER_BASE_URL: baseURL,
    AI_MODELER_PROVIDER_MODEL: model,
    AI_MODELER_REVIEW_BASE_URL: baseURL,
    AI_MODELER_REVIEW_MODEL: model,
    NODE_OPTIONS: '',
  },
  windowsHide: true,
  stdio: ['pipe', 'pipe', 'pipe'],
})
let stdoutBuffer = ''
let stderrBytes = 0
let exitCode = null
let sequence = 0
let assistantText = ''
const pending = new Map()
child.stdout.setEncoding('utf8')
child.stdout.on('data', chunk => {
  stdoutBuffer += chunk
  let newline
  while ((newline = stdoutBuffer.indexOf('\n')) >= 0) {
    const line = stdoutBuffer.slice(0, newline).trim()
    stdoutBuffer = stdoutBuffer.slice(newline + 1)
    if (!line) continue
    let msg
    try { msg = JSON.parse(line) } catch { continue }
    if (msg.method === 'session/update') {
      const update = msg.params?.update || {}
      if (update.sessionUpdate === 'agent_message_chunk') assistantText += update.content?.text || ''
    } else if (msg.method && msg.id !== undefined) {
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Unsupported loopback client method.' } })}\n`)
    } else if (msg.id !== undefined && pending.has(msg.id)) {
      const waiting = pending.get(msg.id)
      pending.delete(msg.id)
      clearTimeout(waiting.timer)
      msg.error ? waiting.reject(new Error(`ACP ${waiting.method} failed (${msg.error.code})`)) : waiting.resolve(msg.result || {})
    }
  }
})
child.stderr.on('data', chunk => { stderrBytes += Buffer.byteLength(chunk) })
child.on('close', code => {
  exitCode = code
  for (const waiting of pending.values()) waiting.reject(new Error('DSH exited before answering ACP request.'))
  pending.clear()
})
function rpc(method, params, timeoutMs = 60_000) {
  const id = ++sequence
  return new Promise((resolveRpc, rejectRpc) => {
    const timer = setTimeout(() => { pending.delete(id); rejectRpc(new Error(`ACP ${method} timed out.`)) }, timeoutMs)
    pending.set(id, { method, resolve: resolveRpc, reject: rejectRpc, timer })
    child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`)
  })
}
function waitExit(ms) {
  if (exitCode !== null) return Promise.resolve()
  return new Promise(resolveExit => {
    const timer = setTimeout(resolveExit, ms)
    child.once('close', () => { clearTimeout(timer); resolveExit() })
  })
}

let failure = null
let sessionId = null
try {
  const init = await rpc('initialize', { protocolVersion: 1, clientInfo: { name: 'f7-loopback-wire-acceptance', version: '1' }, clientCapabilities: {} }, 30_000)
  assert.equal(init.agentCapabilities?.promptCapabilities?.image, true, 'fixed DSH ACP runtime must advertise image prompts')
  const created = await rpc('session/new', { cwd: workspace, mcpServers: [] })
  sessionId = created.sessionId
  assert.ok(sessionId, 'ACP session/new must return a session id')
  const result = await rpc('session/prompt', { sessionId, prompt: [
    { type: 'text', text: promptText },
    { type: 'image', mimeType: mediaType, data: sourceBytes.toString('base64') },
  ] }, 120_000)
  assert.equal(result.stopReason, 'end_turn')
  await rpc('session/close', { sessionId }, 20_000)
} catch (error) {
  failure = String(error?.message || error)
} finally {
  child.stdin.end()
  await waitExit(5_000)
  if (exitCode === null) child.kill()
  await waitExit(1_000)
  await new Promise(resolveClose => server.close(resolveClose))
}

const streamRows = readFileSync(streamEvents, 'utf8').split(/\r?\n/).filter(Boolean).flatMap(line => {
  try { return [JSON.parse(line)] } catch { return [] }
})
const mainRows = streamRows.filter(row => row.kind === 'dsh_request_start' && row.model === model && row.provider === 'ai-modeler')
const boundaryImages = mainRows.flatMap(row => row.request_images || []).filter(row => row.source_kind === 'user_reference')
const mockSummary = { scope: 'local_loopback_only', requests: wireRows, external_api_key_used: false, raw_image_bodies_persisted: false }
writeFileSync(join(out, 'mock-wire-summary.json'), JSON.stringify(mockSummary, null, 2))
const summary = {
  status: 'failed', model, provider: 'ai-modeler', endpoint: baseURL,
  input: { filename, media_type: mediaType, bytes: sourceBytes.length, sha256: sourceSha },
  stream_boundary: boundaryImages.map(row => ({ sha256: row.transport_sha256, bytes: row.byte_length, source_sha256: row.source_sha256 })),
  mock_wire: wireRows,
  assistant_text_sha256: sha256(Buffer.from(assistantText)),
  runtime: runtimeVersions,
  process: { exit_code: exitCode, stderr_bytes: stderrBytes },
  failure: failure || mockError,
}
try {
  assert.equal(failure, null, failure || 'ACP acceptance failed')
  assert.equal(mockError, null, mockError || 'loopback mock rejected a request')
  assert.equal(exitCode, 0, 'fixed DSH child must exit normally')
  assert.equal(stderrBytes, 0, 'fixed DSH child stderr must be empty')
  assert.equal(mainRows.length, 1, 'single-turn acceptance must produce exactly one main DSH llm/stream request')
  assertImageTransportProof({ sourceSha, sourceBytes: sourceBytes.length, model, boundaryImages, wireRows })
  summary.status = 'passed'
} catch (error) {
  summary.failure ||= String(error?.message || error)
} finally {
  writeFileSync(join(out, 'summary.json'), JSON.stringify(summary, null, 2))
}
console.log(JSON.stringify(summary, null, 2))
if (summary.status !== 'passed') process.exitCode = 1
