// Real pinned DSH acceptance. No global DSH, Python executor, or MCP server.
// Build native_read_probe first; API credentials are supplied only through env.
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createInterface } from 'node:readline'

const [repoArg, runtimeArg, outputArg] = process.argv.slice(2)
if (!repoArg || !runtimeArg || !outputArg) throw new Error('usage: node native_read_acceptance.mjs REPO PINNED_RUNTIME OUTPUT')
if (!process.env.DEEPSEEK_API_KEY) throw new Error('DEEPSEEK_API_KEY is not set')
const repo = resolve(repoArg), runtime = resolve(runtimeArg), output = resolve(outputArg)
const legacy = join(repo, 'src/legacy'), state = join(output, 'state'), home = join(output, 'dsh-home')
for (const dir of [output, state, home]) mkdirSync(dir, { recursive: true })
const probe = spawn(join(repo, 'src/apps/desktop/src-tauri/target/debug/examples/native_read_probe.exe'),
  [legacy, state, '--online', '--serve'], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
let probeErrorBytes = 0
probe.stderr.on('data', chunk => { probeErrorBytes += chunk.length })
const lines = createInterface({ input: probe.stdout })
const bridge = await new Promise((yes, no) => {
  const timer = setTimeout(() => no(new Error('Rust native probe startup timed out')), 15_000)
  lines.once('line', line => { clearTimeout(timer); try { yes(JSON.parse(line)) } catch (error) { no(error) } })
  probe.once('error', error => { clearTimeout(timer); no(error) })
  probe.once('exit', () => { clearTimeout(timer); no(new Error('Rust native probe exited at startup')) })
}).catch(error => { probe.kill(); lines.close(); throw error })
const prompt = '只做接口验收，不建模。按顺序调用 cad_search_knowledge 查询 M5x8；cad_search_recipes 查询螺纹；cad_get_feature_spec 查询 box；cad_get_state 读取当前模型；web_search 查询 ISO 4762 socket head screw。搜索成功有 result_id 则调用 web_open_result 打开一个结果；失败则报告真实错误。最后简短汇总，不推测尺寸。'
const refs = join(state, 'reference-images.json'), turns = join(state, 'user-turns.json')
writeFileSync(refs, JSON.stringify({ schema_version: 1, session_id: bridge.session_id, images: [] }))
writeFileSync(turns, JSON.stringify({ schema_version: 1, session_id: bridge.session_id, turns: [
  { turn_id: 'native-read-acceptance', raw_user_prompt: prompt, reference_ids: [], at_epoch_ms: Date.now() },
] }))
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
    'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork', 'tool-workflow', 'tool-todo', 'tool-goal', 'tool-ralph', 'tool-web']
    .flatMap(id => [`- id: ${id}`, '  disabled: true']),
  ...plugins.flatMap((file, index) => ['- insert:',
    `    - id: ${['ai-modeler-native-tools', 'ai-modeler-attachment-store', 'ai-modeler-live-stream'][index]}`,
    `      name: ${JSON.stringify(pathToFileURL(join(legacy, 'ai_modeler/agent', file)).href)}`]),
]
const patchPath = join(output, 'profile.yml'), stream = join(state, 'stream-events.jsonl')
writeFileSync(patchPath, patch.join('\n') + '\n')
const child = spawn(join(runtime, 'node.exe'), [join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js'),
  '--profile', 'acp', '--patch', patchPath], { cwd: output, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: {
    ...process.env, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1',
    DSH_SYSTEM_PROMPT: '完成用户要求的只读工具验收。必须实际调用工具，简短汇总真实结果，不调用 CAD 写工具。',
    AIMODELER_LLM_API_KEY: process.env.DEEPSEEK_API_KEY,
    AI_MODELER_TOOL_BRIDGE_ADDR: bridge.address, AI_MODELER_TOOL_BRIDGE_TOKEN: bridge.token,
    AI_MODELER_TOOL_SESSION_ID: bridge.session_id, AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE: bridge.schemas_path,
    AI_MODELER_PROJECT_ROOT: legacy, AI_MODELER_STATE_DIR: state,
    AI_MODELER_REFERENCE_MANIFEST: refs, AI_MODELER_USER_TURNS_MANIFEST: turns,
    AI_MODELER_STREAM_FILE: stream, AI_MODELER_TRANSPORT_IMAGE_MAP: join(state, 'transport-image-map.json'),
    AI_MODELER_NATIVE_TOOLS_FILE: join(state, 'native-tool-registration.json'),
    AI_MODELER_DSH_NODE_MODULES: join(runtime, 'node_modules'),
    AI_MODELER_ONLINE_ENABLED: 'true', AI_MODELER_HAS_REFERENCES: 'false',
  } })
let seq = 0, stderrBytes = 0, exitCode = null, answer = '', thoughts = 0
const pending = new Map(), tools = [], kinds = new Set()
child.stderr.on('data', chunk => { stderrBytes += chunk.length })
const messages = createInterface({ input: child.stdout })
messages.on('line', line => {
  let row; try { row = JSON.parse(line) } catch { return }
  if (row.method === 'session/update') {
    const update = row.params?.update || {}, kind = update.sessionUpdate
    kinds.add(kind)
    if (kind === 'agent_message_chunk') answer += update.content?.text || ''
    if (kind === 'agent_thought_chunk') thoughts += (update.content?.text || '').length
    if (kind === 'tool_call' || kind === 'tool_call_update') tools.push({ kind, title: update.title, status: update.status })
  } else if (row.id !== undefined && !row.method && pending.has(row.id)) {
    const task = pending.get(row.id); pending.delete(row.id); clearTimeout(task.timer)
    row.error ? task.no(new Error(`ACP ${task.method}: ${row.error.code}`)) : task.yes(row.result || {})
  } else if (row.method && row.id !== undefined) {
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: row.id, error: { code: -32601, message: 'Unsupported acceptance client method' } }) + '\n')
  }
})
child.once('exit', code => { exitCode = code; for (const task of pending.values()) { clearTimeout(task.timer); task.no(new Error('DSH exited')) } pending.clear() })
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
  await rpc('initialize', { protocolVersion: 1, clientInfo: { name: 'native-read-acceptance', version: '1' }, clientCapabilities: {} })
  const session = await rpc('session/new', { cwd: output, mcpServers: [] })
  await rpc('session/set_config_option', { sessionId: session.sessionId, configId: 'reasoning_effort', value: 'off' })
  const response = await rpc('session/prompt', { sessionId: session.sessionId, prompt: [{ type: 'text', text: prompt }] }, 240_000)
  await rpc('session/close', { sessionId: session.sessionId })
  result = { status: 'completed', session_id: session.sessionId, stop_reason: response.stopReason }
} catch (error) { result = { status: 'failed', error: String(error.message).slice(0, 300) } }
finally {
  child.stdin.end(); await boundedExit(child, 5500)
  probe.stdin.write('quit\n'); probe.stdin.end(); await boundedExit(probe, 1500)
}
const domain = existsSync(join(state, 'cad-state.json')) ? JSON.parse(readFileSync(join(state, 'cad-state.json'))) : {}
const actionTools = (domain.actions || []).filter(row => row.execution_engine === 'rust').map(row => row.tool)
const required = ['cad_search_knowledge', 'cad_search_recipes', 'cad_get_feature_spec', 'cad_get_state', 'web_search']
result = { ...result, runtime: { node: process.version,
  dsh: JSON.parse(readFileSync(join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'))).version }, python_fallback: 'forbidden',
  actions: actionTools, required_tools_present: required.every(name => actionTools.includes(name)),
  events: tools, update_kinds: [...kinds], thought_characters: thoughts, answer,
  answer_sha256: createHash('sha256').update(answer).digest('hex'), stderr_bytes: stderrBytes,
  dsh_exit_code: exitCode, probe_stderr_bytes: probeErrorBytes,
}
writeFileSync(join(output, 'summary.json'), JSON.stringify(result, null, 2))
console.log(JSON.stringify(result, null, 2))
if (result.status !== 'completed' || !result.required_tools_present || exitCode !== 0) process.exitCode = 1
