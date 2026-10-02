// Real pinned DSH + native Cordis visual-tool acceptance. The original image
// remains immutable; transport wiring and observation quality are separate.
// API credentials are passed only in child-process environments.
import { spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createInterface } from 'node:readline'

const [repoArg, runtimeArg, outputArg, imageArg] = process.argv.slice(2)
if (!repoArg || !runtimeArg || !outputArg) {
  throw new Error('usage: node native_vision_acceptance.mjs REPO PINNED_RUNTIME OUTPUT [ORIGINAL_PNG]')
}
const apiKey = process.env.DEEPSEEK_API_KEY || process.env.AIMODELER_LLM_API_KEY
if (!apiKey) throw new Error('设置 DEEPSEEK_API_KEY 或 AIMODELER_LLM_API_KEY 环境变量后再运行。')

const repo = resolve(repoArg)
const runtime = resolve(runtimeArg)
const output = resolve(outputArg)
const legacy = join(repo, 'src/legacy')
const state = join(output, 'state')
const home = join(output, 'dsh-home')
const defaultImage = 'C:\\Users\\lycohana\\AppData\\Local\\Temp\\codex-clipboard-c8107ca1-5b7e-4378-9db4-efd0bbe9b9a2.png'
const imagePath = resolve(imageArg || defaultImage)
const imageBytes = readFileSync(imagePath)
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const sourceSha = hash(imageBytes)
const expectedSha = '53ed87fadf8cca869da921627e6a13c845424c6906b3de517d5cca972db94553'
if (sourceSha !== expectedSha || imageBytes.length !== 72309) {
  throw new Error('原图不可变校验失败：sha256=' + sourceSha + ', bytes=' + imageBytes.length)
}
if (!imageBytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
  throw new Error('指定原图不是 PNG。')
}
const width = imageBytes.readUInt32BE(16)
const height = imageBytes.readUInt32BE(20)
const referenceId = 'sha256:' + sourceSha
const turnId = 'native-vision-dsh-acceptance-turn'
const prompt = '请对我附带的参考图片做只读观察。请实际调用 cad_inspect_references 检查当前会话的第 1 张原图；只复述工具返回的观察和不确定项，不要建立或修改模型，不估算毫米尺寸，也不要补充图片外的结构信息。'

for (const directory of [output, state, home]) mkdirSync(directory, { recursive: true })
const nodeVersion = spawnSync(join(runtime, 'node.exe'), ['--version'], { encoding: 'utf8', windowsHide: true })
if (nodeVersion.status !== 0 || nodeVersion.stdout.trim() !== 'v22.23.3') {
  throw new Error('Pinned Node 22.23.3 check failed: ' + nodeVersion.stdout.trim())
}
const dshPackage = JSON.parse(readFileSync(join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), 'utf8'))
if (dshPackage.version !== '0.2.0-rc.2') throw new Error('Pinned DSH version mismatch: ' + dshPackage.version)
const probePath = join(repo, 'src/apps/desktop/src-tauri/target/debug/examples/native_read_probe.exe')
if (!existsSync(probePath)) throw new Error('native_read_probe.exe is missing; build --example native_read_probe first.')

const probe = spawn(probePath, [legacy, state, '--online', '--serve', '--vision'], {
  windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  env: {
    ...process.env,
    AIMODELER_LLM_API_KEY: apiKey,
    AI_MODELER_REVIEW_BASE_URL: process.env.AI_MODELER_REVIEW_BASE_URL || 'https://api.deepseek.com/responses',
    AI_MODELER_REVIEW_MODEL: process.env.AI_MODELER_REVIEW_MODEL || 'deepseek-flash',
    AI_MODELER_REVIEW_REASONING_EFFORT: process.env.AI_MODELER_REVIEW_REASONING_EFFORT || 'none',
    AI_MODELER_REVIEW_OUTPUT_CAP: process.env.AI_MODELER_REVIEW_OUTPUT_CAP || '2048',
  },
})
let probeStderrBytes = 0
probe.stderr.on('data', chunk => { probeStderrBytes += chunk.length })
const probeLines = createInterface({ input: probe.stdout })
const bridge = await new Promise((yes, no) => {
  const timer = setTimeout(() => no(new Error('Rust native vision probe startup timed out')), 15000)
  probeLines.once('line', line => {
    clearTimeout(timer)
    try { yes(JSON.parse(line)) } catch (error) { no(error) }
  })
  probe.once('error', error => { clearTimeout(timer); no(error) })
  probe.once('exit', () => { clearTimeout(timer); no(new Error('Rust native vision probe exited at startup')) })
}).catch(error => { probe.kill(); probeLines.close(); throw error })

const refsPath = join(state, 'reference-images.json')
const turnsPath = join(state, 'user-turns.json')
const activeTurnPath = join(state, 'active-turn.json')
writeFileSync(refsPath, JSON.stringify({
  schema_version: 1, session_id: bridge.session_id,
  images: [{ id: referenceId, sha256: sourceSha, path: imagePath, filename: basename(imagePath),
    width, height, media_type: 'image/png' }],
}))
writeFileSync(turnsPath, JSON.stringify({
  schema_version: 1, session_id: bridge.session_id,
  turns: [{ turn_id: turnId, raw_user_prompt: prompt, reference_ids: [referenceId],
    intent: 'model_target', intent_source: 'acceptance_fixture', affects_model_target: true,
    at_epoch_ms: Date.now() }],
}))
writeFileSync(activeTurnPath, JSON.stringify({
  schema_version: 1, session_id: bridge.session_id, turn_id: turnId,
  reference_images: [{ id: referenceId, sha256: sourceSha, filename: basename(imagePath),
    media_type: 'image/png', bytes: imageBytes.length, source_kind: 'user_reference' }],
}))

const plugins = ['dsh_native_tools.mjs', 'dsh_attachment_store.mjs', 'dsh_stream_bridge.mjs']
const patch = [
  '- id: llm-pi-ai', '  config:', '    providers:', '      ai-modeler:',
  '        apiKeyEnv: AIMODELER_LLM_API_KEY', '        api: openai-responses',
  '        baseURL: "https://api.deepseek.com"', '        models:',
  '          - id: deepseek-flash', '            name: deepseek-flash',
  '            contextWindow: 262144', '            maxTokens: 2048', '            input: [text, image]',
  '            reasoningEfforts:', '              off: null', '              low: low',
  '        compat:', '          supportsMaxOutputTokens: false',
  '- id: acp', '  config:', '    provider: ai-modeler', '    model: deepseek-flash',
  '- id: system-prompt', '  config:', '    includeHarnessIdentity: false', '    includeRuntimeContext: false',
  "    personaPrefix: !!js process.env.DSH_SYSTEM_PROMPT ?? ''", "    personaSuffix: ''",
  ...['session-log-deepseek', 'session-telemetry-otel', 'attachment-local', 'agent-instructions',
    'tool-bash', 'tool-pwsh', 'tool-fs', 'tool-fs-search', 'tool-skill', 'tool-jobs', 'tool-subagent-control',
    'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork', 'tool-workflow', 'tool-todo',
    'tool-goal', 'tool-ralph', 'tool-web'].flatMap(id => ['- id: ' + id, '  disabled: true']),
  ...plugins.flatMap((file, index) => ['- insert:',
    '    - id: ' + ['ai-modeler-native-tools', 'ai-modeler-attachment-store', 'ai-modeler-live-stream'][index],
    '      name: ' + JSON.stringify(pathToFileURL(join(legacy, 'ai_modeler/agent', file)).href)]),
]
const patchPath = join(output, 'profile.yml')
const streamPath = join(state, 'stream-events.jsonl')
writeFileSync(patchPath, patch.join('\n') + '\n')

const child = spawn(join(runtime, 'node.exe'), [join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js'),
  '--profile', 'acp', '--patch', patchPath], {
  cwd: output, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: {
    ...process.env,
    DSH_HOME: home,
    DSH_TELEMETRY_DISABLED: '1',
    DSH_SYSTEM_PROMPT: '你正在执行只读图像观察验收。根据用户请求调用可用的原生视觉工具，并简短总结工具实际返回；不建模、不估算尺寸、不猜测。',
    AIMODELER_LLM_API_KEY: apiKey,
    AI_MODELER_TOOL_BRIDGE_ADDR: bridge.address,
    AI_MODELER_TOOL_BRIDGE_TOKEN: bridge.token,
    AI_MODELER_TOOL_SESSION_ID: bridge.session_id,
    AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE: bridge.schemas_path,
    AI_MODELER_STATE_DIR: state,
    AI_MODELER_REFERENCE_MANIFEST: refsPath,
    AI_MODELER_USER_TURNS_MANIFEST: turnsPath,
    AI_MODELER_ACTIVE_TURN_FILE: activeTurnPath,
    AI_MODELER_STREAM_FILE: streamPath,
    AI_MODELER_TRANSPORT_IMAGE_MAP: join(state, 'transport-image-map.json'),
    AI_MODELER_NATIVE_TOOLS_FILE: join(state, 'native-tool-registration.json'),
    AI_MODELER_DSH_NODE_MODULES: join(runtime, 'node_modules'),
    AI_MODELER_ONLINE_ENABLED: 'true',
    AI_MODELER_HAS_REFERENCES: 'true',
  },
})
let seq = 0, dshStderrBytes = 0, dshExitCode = null, answer = '', thoughts = 0
const pending = new Map(), toolEvents = [], updateKinds = new Set()
child.stderr.on('data', chunk => { dshStderrBytes += chunk.length })
const messages = createInterface({ input: child.stdout })
messages.on('line', line => {
  let row
  try { row = JSON.parse(line) } catch { return }
  if (row.method === 'session/update') {
    const update = row.params?.update || {}, kind = update.sessionUpdate
    updateKinds.add(kind)
    if (kind === 'agent_message_chunk') answer += update.content?.text || ''
    if (kind === 'agent_thought_chunk') thoughts += (update.content?.text || '').length
    if (kind === 'tool_call' || kind === 'tool_call_update') {
      toolEvents.push({ kind, title: update.title, status: update.status, tool_call_id: update.toolCallId })
    }
  } else if (row.id !== undefined && !row.method && pending.has(row.id)) {
    const task = pending.get(row.id); pending.delete(row.id); clearTimeout(task.timer)
    row.error ? task.no(new Error('ACP ' + task.method + ': ' + row.error.code)) : task.yes(row.result || {})
  } else if (row.method && row.id !== undefined) {
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: row.id,
      error: { code: -32601, message: 'Unsupported acceptance client method' } }) + '\n')
  }
})
child.once('exit', code => {
  dshExitCode = code
  for (const task of pending.values()) { clearTimeout(task.timer); task.no(new Error('DSH exited')) }
  pending.clear()
})
function rpc(method, params, ms = 45000) {
  const id = ++seq
  return new Promise((yes, no) => {
    const timer = setTimeout(() => { pending.delete(id); no(new Error(method + ' timed out')) }, ms)
    pending.set(id, { yes, no, timer, method })
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
  })
}
async function boundedExit(process, ms) {
  if (process.exitCode !== null) return
  await new Promise(done => {
    const timer = setTimeout(done, ms)
    process.once('exit', () => { clearTimeout(timer); done() })
  })
  if (process.exitCode === null) process.kill()
}
let sessionId = null, dshStatus = 'failed', dshError = null, stopReason = null
try {
  await rpc('initialize', { protocolVersion: 1,
    clientInfo: { name: 'native-vision-acceptance', version: '1' }, clientCapabilities: {} })
  const session = await rpc('session/new', { cwd: output, mcpServers: [] })
  sessionId = session.sessionId
  await rpc('session/set_config_option', { sessionId, configId: 'reasoning_effort', value: 'off' })
  const response = await rpc('session/prompt', { sessionId, prompt: [
    { type: 'text', text: prompt },
    { type: 'image', mimeType: 'image/png', data: imageBytes.toString('base64') },
  ] }, 240000)
  await rpc('session/close', { sessionId })
  dshStatus = 'completed'
  stopReason = response.stopReason || null
} catch (error) {
  dshError = String(error.message).slice(0, 300)
} finally {
  child.stdin.end()
  await boundedExit(child, 5500)
  probe.stdin.write('quit\n')
  probe.stdin.end()
  await boundedExit(probe, 1500)
}

const regPath = join(state, 'native-tool-registration.json')
const schemaPath = join(state, 'schemas.json')
const registration = existsSync(regPath) ? JSON.parse(readFileSync(regPath, 'utf8')) : null
const schemaManifest = existsSync(schemaPath) ? JSON.parse(readFileSync(schemaPath, 'utf8')) : null
const registered = registration?.phase === 'registered'
  && registration.registeredToolNames?.includes('cad_inspect_references') === true
const schemaPresent = schemaManifest?.schemaVersion === 1
  && schemaManifest.tools?.some(tool => tool.name === 'cad_inspect_references') === true
const cadStatePath = join(state, 'cad-state.json')
const domain = existsSync(cadStatePath) ? JSON.parse(readFileSync(cadStatePath, 'utf8')) : {}
const actions = (domain.actions || []).filter(action => action.tool === 'cad_inspect_references')
const observation = domain.reference_inventory?.[referenceId] || null
const streamEvents = existsSync(streamPath) ? readFileSync(streamPath, 'utf8').split(/\r?\n/).filter(Boolean).flatMap(line => {
  try { return [JSON.parse(line)] } catch { return [] }
}) : []
const wireRequests = streamEvents.filter(row => row.kind === 'provider_wire_request')
const wireResponseHeaders = streamEvents.filter(row => row.kind === 'provider_wire_response_headers')
const wireResponses = streamEvents.filter(row => row.kind === 'provider_wire_response_complete')
const successfulWireResponse = wireRequests.some(request =>
  wireResponseHeaders.some(row => row.request_id === request.request_id && row.status_code === 200)
  && wireResponses.some(row => row.request_id === request.request_id))
const wireIdentityValid = wireRequests.length > 0 && wireRequests.every(row =>
  row.wire_observed === true && row.image_count === 1 && row.images?.length === 1
  && row.images[0].source_sha256 === sourceSha
  && row.images[0].transport_sha256 === sourceSha
  && row.images[0].byte_length === imageBytes.length
  && row.images[0].source_reference_ids?.includes(referenceId))
const toolEventObserved = toolEvents.some(event => String(event.title || '').includes('cad_inspect_references'))
  || actions.length > 0
const toolCalled = registered && schemaPresent && toolEventObserved && actions.length > 0
const sourceShaAfter = hash(readFileSync(imagePath))
const acceptance = {
  status: dshStatus === 'completed' && toolCalled && wireIdentityValid && successfulWireResponse ? 'completed' : 'failed',
  dsh_status: dshStatus,
  error: dshError,
  session_id: sessionId,
  stop_reason: stopReason,
  runtime: { node: nodeVersion.stdout.trim(), dsh: dshPackage.version, directory: runtime },
  original_image: {
    path: imagePath, immutable: true, bytes: imageBytes.length, width, height,
    source_sha256_before: sourceSha, source_sha256_after: sourceShaAfter,
    unchanged: sourceShaAfter === sourceSha, reference_id: referenceId,
  },
  native_cordis: {
    registered, schema_present: schemaPresent, dsh_tool_events: toolEvents,
    native_actions: actions, called_by_dsh: toolCalled,
    python_fallback: 'removed',
  },
  provider_transport: {
    wire_requests: wireRequests, wire_response_headers: wireResponseHeaders,
    wire_responses: wireResponses, image_hashes_match_original: wireIdentityValid,
    successful_wire_response: successfulWireResponse,
    protocol: observation?.diagnostics?.protocol || wireRequests.map(row => row.protocol),
    usage: observation?.diagnostics?.usage || null,
    request_profile: observation?.diagnostics?.request_profile || null,
    observation_status: observation?.observation_status || null,
    observation_valid: observation?.observation_valid ?? null,
    observation: observation ? {
      summary: observation.summary,
      visible_evidence: observation.visible_evidence,
      uncertain: observation.uncertain,
      inferred: observation.inferred,
      source_sha256: observation.source_sha256,
      diagnostics: observation.diagnostics,
    } : null,
  },
  observation_quality: 'not_scored_by_this_transport_and_tool-wiring_acceptance',
  answer,
  answer_sha256: hash(Buffer.from(answer)),
  thought_characters_observed: thoughts,
  update_kinds: [...updateKinds],
  stderr_bytes: { dsh: dshStderrBytes, rust_probe: probeStderrBytes },
  dsh_exit_code: dshExitCode,
  direct_cli_reproduction: toolCalled ? null : {
    executable: probePath,
    arguments: [legacy, state, '--online', '--vision'],
    stdin_json: JSON.stringify({ name: 'cad_inspect_references', arguments: { reference_image_index: 1 } }),
    note: '直接 CLI 复现用于定位 Rust 工具；不计作 DSH 工具调用通过。',
  },
}
writeFileSync(join(output, 'summary.json'), JSON.stringify(acceptance, null, 2))
console.log(JSON.stringify(acceptance, null, 2))
if (acceptance.status !== 'completed' || !acceptance.original_image.unchanged || dshExitCode !== 0) {
  process.exitCode = 1
}
