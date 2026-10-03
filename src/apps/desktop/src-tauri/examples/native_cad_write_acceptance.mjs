// Real pinned DSH acceptance for Rust-native CAD writes. Python fallback is forbidden.
// API credentials are inherited by child processes and are never written to the report.
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createInterface } from 'node:readline'

const [repoArg, runtimeArg, outputArg, manifestArg, sizeArg = 'M5x8'] = process.argv.slice(2)
if (!repoArg || !runtimeArg || !outputArg || !manifestArg) {
  throw new Error('usage: node native_cad_write_acceptance.mjs REPO PINNED_RUNTIME OUTPUT OCCT_MANIFEST [BOLT_SIZE]')
}
const apiKey = process.env.DEEPSEEK_API_KEY || process.env.AIMODELER_LLM_API_KEY
if (!apiKey) throw new Error('设置 DEEPSEEK_API_KEY 或 AIMODELER_LLM_API_KEY 环境变量后再运行。')

const repo = resolve(repoArg), runtime = resolve(runtimeArg), output = resolve(outputArg)
const manifest = resolve(manifestArg), legacy = join(repo, 'src/legacy')
const state = join(output, 'state'), home = join(output, 'dsh-home')
const probePath = join(repo, 'src/apps/desktop/src-tauri/target/debug/examples/native_read_probe.exe')
const dshPath = join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js')
const turnId = `native-cad-write-${Date.now()}`
const prompt = `请为我建立一颗 ${sizeArg} 内六角圆柱头螺钉。只调用 cad_apply_recipe，recipe_id 使用 fastener.socket_head_cap_screw，size 原样使用 ${sizeArg}。工具成功后简短汇报，不要再调用其他建模工具。`

for (const directory of [output, state, home]) mkdirSync(directory, { recursive: true })
if (existsSync(join(state, 'cad-state.json'))) throw new Error('验收 state 目录已有 CAD 状态；请换一个新的 OUTPUT 路径。')
const nodeVersion = spawnSync(join(runtime, 'node.exe'), ['--version'], { encoding: 'utf8', windowsHide: true })
if (nodeVersion.status !== 0 || nodeVersion.stdout.trim() !== 'v22.23.3') {
  throw new Error(`Pinned Node 22.23.3 check failed: ${nodeVersion.stdout.trim()}`)
}
const dshPackage = JSON.parse(readFileSync(join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), 'utf8'))
if (dshPackage.version !== '0.2.0-rc.2') throw new Error(`Pinned DSH version mismatch: ${dshPackage.version}`)
if (!existsSync(probePath) || !existsSync(manifest)) throw new Error('native_read_probe 或 OCCT runtime manifest 不存在。')

const probe = spawn(probePath, [legacy, state, '--serve', '--occt', manifest], {
  windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
})
let probeStderrBytes = 0
probe.stderr.on('data', chunk => { probeStderrBytes += chunk.length })
const probeLines = createInterface({ input: probe.stdout })
const bridge = await new Promise((yes, no) => {
  const timer = setTimeout(() => no(new Error('Rust native probe startup timed out')), 15_000)
  probeLines.once('line', line => {
    clearTimeout(timer)
    try { yes(JSON.parse(line)) } catch (error) { no(error) }
  })
  probe.once('error', error => { clearTimeout(timer); no(error) })
  probe.once('exit', () => { clearTimeout(timer); no(new Error('Rust native probe exited at startup')) })
}).catch(error => { probe.kill(); probeLines.close(); throw error })

const utf8 = JSON.stringify({ schema_version: 1, session_id: bridge.session_id, turns: [
  { turn_id: turnId, session_id: bridge.session_id, raw_user_prompt: prompt, reference_ids: [], intent_source: 'acceptance' },
] })
writeFileSync(join(state, 'active-turn.json'), JSON.stringify({ schema_version: 1, session_id: bridge.session_id, turn_id: turnId }))
writeFileSync(join(state, 'user-turns.json'), utf8)
writeFileSync(join(state, 'reference-images.json'), JSON.stringify({ schema_version: 1, session_id: bridge.session_id, images: [] }))

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
    'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork', 'tool-workflow', 'tool-todo', 'tool-goal',
    'tool-ralph', 'tool-web'].flatMap(id => [`- id: ${id}`, '  disabled: true']),
  ...['dsh_native_tools.mjs', 'dsh_attachment_store.mjs', 'dsh_stream_bridge.mjs'].flatMap((file, index) => [
    '- insert:', `    - id: ${['ai-modeler-native-tools', 'ai-modeler-attachment-store', 'ai-modeler-live-stream'][index]}`,
    `      name: ${JSON.stringify(pathToFileURL(join(legacy, 'ai_modeler/agent', file)).href)}`,
  ]),
]
writeFileSync(join(output, 'profile.yml'), patch.join('\n') + '\n')

const child = spawn(join(runtime, 'node.exe'), [dshPath, '--profile', 'acp', '--patch', join(output, 'profile.yml')], {
  cwd: output, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: {
    ...process.env, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1',
    DSH_SYSTEM_PROMPT: '这是原生 CAD 工具链验收。必须按用户要求实际调用 cad_apply_recipe 一次；不得调用 Python、MCP 或其他建模工具。工具失败时如实结束。',
    AIMODELER_LLM_API_KEY: apiKey,
    AI_MODELER_TOOL_BRIDGE_ADDR: bridge.address, AI_MODELER_TOOL_BRIDGE_TOKEN: bridge.token,
    AI_MODELER_TOOL_SESSION_ID: bridge.session_id, AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE: bridge.schemas_path,
    AI_MODELER_PROJECT_ROOT: legacy, AI_MODELER_STATE_DIR: state,
    AI_MODELER_REFERENCE_MANIFEST: join(state, 'reference-images.json'),
    AI_MODELER_USER_TURNS_MANIFEST: join(state, 'user-turns.json'),
    AI_MODELER_ACTIVE_TURN_FILE: join(state, 'active-turn.json'),
    AI_MODELER_STREAM_FILE: join(state, 'stream-events.jsonl'),
    AI_MODELER_TRANSPORT_IMAGE_MAP: join(state, 'transport-image-map.json'),
    AI_MODELER_NATIVE_TOOLS_FILE: join(state, 'native-tool-registration.json'),
    AI_MODELER_DSH_NODE_MODULES: join(runtime, 'node_modules'),
    AI_MODELER_ONLINE_ENABLED: 'false', AI_MODELER_HAS_REFERENCES: 'false',
  },
})

let seq = 0, stderrBytes = 0, exitCode = null, answer = ''
const pending = new Map(), tools = []
child.stderr.on('data', chunk => { stderrBytes += chunk.length })
const messages = createInterface({ input: child.stdout })
messages.on('line', line => {
  let row; try { row = JSON.parse(line) } catch { return }
  if (row.method === 'session/update') {
    const update = row.params?.update || {}
    if (update.sessionUpdate === 'agent_message_chunk') answer += update.content?.text || ''
    if (update.sessionUpdate === 'tool_call' || update.sessionUpdate === 'tool_call_update') {
      tools.push({ kind: update.sessionUpdate, title: update.title, status: update.status })
    }
  } else if (row.id !== undefined && !row.method && pending.has(row.id)) {
    const task = pending.get(row.id); pending.delete(row.id); clearTimeout(task.timer)
    row.error ? task.no(new Error(`ACP ${task.method}: ${row.error.code}`)) : task.yes(row.result || {})
  } else if (row.method && row.id !== undefined) {
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: row.id, error: { code: -32601, message: 'Unsupported acceptance client method' } }) + '\n')
  }
})
child.once('exit', code => {
  exitCode = code
  for (const task of pending.values()) { clearTimeout(task.timer); task.no(new Error('DSH exited')) }
  pending.clear()
})
function rpc(method, params, ms = 45_000) {
  const id = ++seq
  return new Promise((yes, no) => {
    const timer = setTimeout(() => { pending.delete(id); no(new Error(`${method} timed out`)) }, ms)
    pending.set(id, { yes, no, timer, method })
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
  })
}
async function boundedExit(process, ms) {
  if (process.exitCode !== null) return
  await new Promise(done => { const timer = setTimeout(done, ms); process.once('exit', () => { clearTimeout(timer); done() }) })
  if (process.exitCode === null) process.kill()
}

let result
try {
  await rpc('initialize', { protocolVersion: 1, clientInfo: { name: 'native-cad-write-acceptance', version: '1' }, clientCapabilities: {} })
  const session = await rpc('session/new', { cwd: output, mcpServers: [] })
  await rpc('session/set_config_option', { sessionId: session.sessionId, configId: 'reasoning_effort', value: 'off' })
  const response = await rpc('session/prompt', {
    sessionId: session.sessionId, prompt: [{ type: 'text', text: prompt }],
  }, 240_000)
  await rpc('session/close', { sessionId: session.sessionId })
  result = { status: 'completed', session_id: session.sessionId, stop_reason: response.stopReason }
} catch (error) {
  result = { status: 'failed', error: String(error.message).slice(0, 300) }
} finally {
  child.stdin.end(); await boundedExit(child, 5_500)
  probe.stdin.write('quit\n'); probe.stdin.end(); await boundedExit(probe, 1_500)
}

const domainPath = join(state, 'cad-state.json')
const domain = existsSync(domainPath) ? JSON.parse(readFileSync(domainPath, 'utf8')) : {}
const actions = (domain.actions || []).filter(row => row.execution_engine === 'rust').map(row => ({ tool: row.tool, revision: row.revision }))
const feature = domain.plan?.features?.find(row => row.id === 'socket-head-cap-screw')
const preview = domain.last_preview
const report = preview?.local_geometry_checks || {}
const outputPaths = preview?.output_paths || {}
const artifactFiles = Object.fromEntries(Object.entries(outputPaths).map(([key, value]) => [key, existsSync(value)]))
const sourceProvenance = (domain.actions || []).flatMap(row => row.user_source_provenance || [])
  .filter(row => row.feature_id === feature?.id)
const viewDirectory = outputPaths.aimodel ? join(dirname(outputPaths.aimodel), 'views') : null
const previewViews = viewDirectory && existsSync(viewDirectory)
  ? readdirSync(viewDirectory).filter(name => name.toLowerCase().endsWith('.png')).map(name => name.slice(0, -4))
  : []
result = {
  ...result,
  runtime: { node: nodeVersion.stdout.trim(), dsh: dshPackage.version },
  python_fallback: 'forbidden', tool_actions: actions, dsh_tool_events: tools,
  feature: feature ? { id: feature.id, kind: feature.kind, parameters: feature.parameters, sources: feature.sources } : null,
  source_provenance_count: sourceProvenance.length,
  geometry: { state: report.state, closed_solid_count: report.closed_solid_count,
    dimensions_mm: report.dimensions_mm, thread: report.feature_results?.[0]?.support_details?.thread,
    catia_executed: report.catia_executed },
  artifact_files: artifactFiles, preview_views: previewViews,
  answer, stderr_bytes: stderrBytes, probe_stderr_bytes: probeStderrBytes, dsh_exit_code: exitCode,
}
writeFileSync(join(output, 'summary.json'), JSON.stringify(result, null, 2))
console.log(JSON.stringify(result, null, 2))
if (result.status !== 'completed' || exitCode !== 0
  || !actions.some(row => row.tool === 'cad_apply_recipe')
  || !feature || feature.kind !== 'socket_head_bolt'
  || report.closed_solid_count !== 1 || !report.feature_results?.[0]?.support_details?.thread?.visible_geometry
  || result.source_provenance_count !== 2 || previewViews.length !== 7
  || Object.values(artifactFiles).some(exists => !exists)) process.exitCode = 1
