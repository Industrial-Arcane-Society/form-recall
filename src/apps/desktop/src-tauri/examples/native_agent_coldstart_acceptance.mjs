// Cold-start DSH Agent image + text run; no plan or answer fixture is loaded.
// API credentials stay in process memory / child env or stdin, never argv or logs.
import { spawn, spawnSync } from 'node:child_process'
import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { createAuditedToolProxy, createTerminalUpdateHmac, evaluateColdstartGates, parseEvidenceJsonl, parsePostcheckState, readRunArtifacts, summarizeAcpTerminalUpdateDiagnostics, summarizeCordisBindingDiagnostics, summarizeVisualReviewDiagnostics, toolArgumentsFingerprint } from './native_agent_coldstart_gates.mjs'
import { buildSafeAcceptanceSummary, buildSafeStrictGateProjection, replacePublishedSafeFailureSummary,
  SAFE_PUBLICATION_PHASE, writeSafeAcceptanceArtifacts,
  writeSafeFailureSummary } from './native_agent_coldstart_publication.mjs'
import { removeScratchDirectory, safeScratchCleanupStatus, terminateChildrenAndWait, waitThenTerminateChild } from './native_agent_coldstart_lifecycle.mjs'
import { createColdstartScratchDirectory, recoverStaleColdstartScratch } from './native_agent_coldstart_scratch.mjs'
import { runOfflineCargoBuild } from './native_agent_coldstart_build.mjs'
import { cancelAndCloseSession, createAcpRpcClient, PromptProgressTracker, resolvePromptTimeoutPolicy,
  readGeometryRevision, watchGeometryRevision } from './native_agent_coldstart_prompt.mjs'

const [repoArg, runtimeArg, outputArg, configArg, imageArg, userTextArg] = process.argv.slice(2)
let archiveDir = null
let safeSummaryPublished = false
let scratchDir = null
let scratchRecovery = { status: 'complete', scanned_count: 0, removed_count: 0, preserved_count: 0,
  rejected_count: 0, unknown_owner_count: 0, failed_count: 0 }
const managedChildren = []
let fatalFailurePromise = null
// Only a fixed stage enum is ever published. It marks the runner phase, not
// the exception location or message; raw error/provider content is discarded.
let failureStage = 'startup'
let failurePhase = SAFE_PUBLICATION_PHASE.NOT_STARTED
let safeStrictGates = null
let promptProgressDiagnostics = null
let currentPromptTracker = null
const stopManagedChildren = () => terminateChildrenAndWait(managedChildren, 1500)
const archiveFailure = (scratchCleanupStatus, code) => {
  if (!archiveDir) return
  try {
    const failure = { stage: failureStage, phase: failurePhase, code, scratchCleanupStatus, scratchRecovery,
      strictGates: safeStrictGates,
      promptProgress: promptProgressDiagnostics || currentPromptTracker?.snapshot() }
    if (safeSummaryPublished) replacePublishedSafeFailureSummary(archiveDir, failure)
    else writeSafeFailureSummary(archiveDir, failure)
  } catch { /* preserve the original process failure */ }
}
const cleanupScratch = () => {
  if (!scratchDir) return 'not_created'
  const status = removeScratchDirectory(scratchDir)
  if (status === 'removed') scratchDir = null
  return status
}
process.once('exit', cleanupScratch)
const failSafely = code => {
  if (fatalFailurePromise) return fatalFailurePromise
  fatalFailurePromise = (async () => {
    const childrenStopped = await stopManagedChildren().catch(() => false)
    const cleanupStatus = cleanupScratch()
    archiveFailure(safeScratchCleanupStatus(childrenStopped, cleanupStatus), code)
    console.error('Acceptance failed; see safe summary in the evidence directory.')
    process.exit(1)
  })()
  return fatalFailurePromise
}
process.on('uncaughtException', () => { void failSafely('uncaught_exception') })
process.on('unhandledRejection', () => { void failSafely('unhandled_rejection') })
if (!repoArg || !runtimeArg || !outputArg || !configArg) {
  throw new Error('usage: node native_agent_coldstart_acceptance.mjs REPO PINNED_RUNTIME FRESH_OUTPUT PROVIDER_JSON [ORIGINAL_PNG] [USER_TEXT]')
}
failureStage = 'config_validation'
const suppliedConfig = JSON.parse(readFileSync(resolve(configArg), 'utf8').replace(/^\uFEFF/, ''))
const provider = suppliedConfig.llm
  ? suppliedConfig.llm.providers?.find(item => item.id === suppliedConfig.llm.active_provider_id) || suppliedConfig.llm
  : suppliedConfig
if (suppliedConfig.online?.enabled === false) throw new Error('生产配置已关闭联网，未覆盖该设置或发起验收请求。')
const apiKey = provider.api_key || process.env.AIMODELER_LLM_API_KEY || process.env.DEEPSEEK_API_KEY
if (!apiKey) throw new Error('设置 DEEPSEEK_API_KEY 或 AIMODELER_LLM_API_KEY 环境变量后再运行。')

const repo = resolve(repoArg)
const runtime = resolve(runtimeArg)
const output = resolve(outputArg)
// Existing evidence is never reused or overwritten by another acceptance run.
if (existsSync(output)) throw new Error('验收输出目录已经存在；请提供全新目录。')
mkdirSync(dirname(output), { recursive: true })
mkdirSync(output)
archiveDir = output
failureStage = 'input_validation'
scratchRecovery = recoverStaleColdstartScratch()
scratchDir = createColdstartScratchDirectory()
const workDir = scratchDir
const toolResultDiagnosticsPath = join(workDir, 'tool-result-diagnostics.jsonl')
const legacy = join(repo, 'src/legacy')
const state = join(workDir, 'state')
const home = join(workDir, 'dsh-home')
const defaultImage = join(repo, 'temp/evidence-archive/artifacts/agent-quality-rebuild/references/user-nine-hole-recess-iso.png')
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
const runId = 'native-agent-coldstart-' + randomUUID()
const turnId = runId
const userText = userTextArg || '请根据参考图片复刻模型，未标注尺寸按图像比例自行估算并记录来源，使用工具生成模型并复核。'
const prompt = userText

failureStage = 'runtime_validation'
for (const directory of [workDir, state, home]) mkdirSync(directory, { recursive: true })
const nodePath = existsSync(join(runtime, 'node/node.exe')) ? join(runtime, 'node/node.exe') : join(runtime, 'node.exe')
const modules = existsSync(join(runtime, 'dsh/node_modules')) ? join(runtime, 'dsh/node_modules') : join(runtime, 'node_modules')
const nodeVersion = spawnSync(nodePath, ['--version'], { encoding: 'utf8', windowsHide: true })
if (nodeVersion.status !== 0 || nodeVersion.stdout.trim() !== 'v22.23.3') {
  throw new Error('Pinned Node 22.23.3 check failed: ' + nodeVersion.stdout.trim())
}
const dshPackage = JSON.parse(readFileSync(join(modules, '@deepseek-ai/dsh/package.json'), 'utf8'))
if (dshPackage.version !== '0.2.0-rc.2') throw new Error('Pinned DSH version mismatch: ' + dshPackage.version)
// Always rebuild against this checkout before any provider request. Old binaries
// must not validate newly edited gates, schemas or the native tool audit.
failureStage = 'offline_build'
const buildStatus = runOfflineCargoBuild({
  spawnSyncImpl: spawnSync,
  args: ['build', '--manifest-path', join(repo, 'src/apps/desktop/src-tauri/Cargo.toml'),
    '--example', 'native_read_probe', '--example', 'native_agent_profile', '--offline'],
  cwd: repo,
  output,
})
if (buildStatus.status !== 'passed') throw new Error('Offline Cargo example rebuild failed; no provider request was sent.')
const probePath = join(repo, 'src/apps/desktop/src-tauri/target/debug/examples/native_read_probe.exe')
if (!existsSync(probePath)) throw new Error('native_read_probe.exe is missing; build --example native_read_probe first.')
const profilePath = join(repo, 'src/apps/desktop/src-tauri/target/debug/examples/native_agent_profile.exe')
if (!existsSync(profilePath)) throw new Error('native_agent_profile.exe is missing; build the production profile example first.')
const patchPath = join(workDir, 'profile.yml')
const prepared = spawnSync(profilePath, [legacy, patchPath], { input: JSON.stringify({ ...provider, api_key: apiKey }), encoding: 'utf8', windowsHide: true })
if (prepared.status !== 0) throw new Error('生产profile构造失败。')
const effectiveProfile = JSON.parse(prepared.stdout)
const productionPrompt = readFileSync(effectiveProfile.prompt_path, 'utf8')
const occtManifest = process.env.AIMODELER_OCCT_MANIFEST || join(repo, 'src/apps/desktop/src-tauri/target/f4-occt/runtime-manifest.json')
if (!existsSync(occtManifest)) throw new Error('OCCT manifest不存在，不能执行建模验收。')

failureStage = 'native_probe'
const probe = spawn(probePath, [legacy, state, '--online', '--serve', '--vision', '--occt', occtManifest], {
  windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  env: {
    ...process.env,
    AIMODELER_LLM_API_KEY: apiKey,
    AI_MODELER_REVIEW_BASE_URL: provider.base_url,
    AI_MODELER_REVIEW_MODEL: provider.model,
    AI_MODELER_REVIEW_REASONING_EFFORT: provider.reasoning_effort || 'none',
    AI_MODELER_REVIEW_THINKING_BUDGET: provider.thinking_budget ? String(provider.thinking_budget) : '',
    AI_MODELER_REVIEW_OUTPUT_CAP: String(effectiveProfile.vision_output_cap),
  },
})
managedChildren.push(probe)
const probeLines = createInterface({ input: probe.stdout })
probe.stderr.resume()
const bridge = await new Promise((yes, no) => {
  const timer = setTimeout(() => no(new Error('Rust native vision probe startup timed out')), 15000)
  probeLines.once('line', line => {
    clearTimeout(timer)
    try { yes(JSON.parse(line)) } catch (error) { no(error) }
  })
  probe.once('error', error => { clearTimeout(timer); no(error) })
  probe.once('exit', () => { clearTimeout(timer); no(new Error('Rust native vision probe exited at startup')) })
}).catch(error => { probe.kill(); probeLines.close(); throw error })
const toolProxy = await createAuditedToolProxy({ address: bridge.address, token: bridge.token,
  sessionId: bridge.session_id, turnId, runId }).catch(error => { probe.kill(); throw error })
const runStartedAt = Date.now()

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

const streamPath = join(state, 'stream-events.jsonl')

failureStage = 'dsh_launch'
const child = spawn(nodePath, [join(modules, '@deepseek-ai/dsh/lib/bin.js'),
  '--profile', 'acp', '--patch', patchPath], {
  cwd: workDir, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: {
    ...process.env,
    DSH_HOME: home,
    DSH_TELEMETRY_DISABLED: '1',
    DSH_SYSTEM_PROMPT: productionPrompt,
    AIMODELER_LLM_API_KEY: apiKey,
    // Required by dsh_stream_bridge to install its provider-fetch observer.
    // Telemetry retains only hashes, lengths, request correlation and status.
    AI_MODELER_PROVIDER_BASE_URL: provider.base_url,
    AI_MODELER_PROVIDER_MODEL: provider.model,
    AI_MODELER_TOOL_BRIDGE_ADDR: toolProxy.address,
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
    AI_MODELER_DSH_NODE_MODULES: modules,
    AI_MODELER_ONLINE_ENABLED: 'true',
    AI_MODELER_HAS_REFERENCES: 'true',
    AI_MODELER_TOOL_RESULT_DIAGNOSTICS_FILE: toolResultDiagnosticsPath,
  },
})
managedChildren.push(child)
let dshExitCode = null
const toolEvents = [], evidenceErrors = []
// The random key and WeakMap are run-local. Digests are never attached to
// persisted event rows and cannot be compared across process runs.
const terminalUpdateHmacKey = randomBytes(32)
const terminalUpdateHmacByEvent = new WeakMap()
child.stderr.resume()
const acp = createAcpRpcClient(child, { onInvalidMessage: () => evidenceErrors.push('acp_stdout_invalid_json_record'),
  onNotification: row => {
  const update = row.params?.update || {}, kind = update.sessionUpdate
  if (kind !== 'tool_call' && kind !== 'tool_call_update') return
  const toolEvent = { kind, title: update.title, status: update.status, tool_call_id: update.toolCallId,
    ...(kind === 'tool_call' ? { arguments_sha256_24: toolArgumentsFingerprint(update.rawInput) } : {}),
    session_id: row.params?.sessionId, run_id: runId, turn_id: turnId, at_ms: Date.now() }
  if (kind === 'tool_call_update' && ['completed', 'failed'].includes(update.status) && terminalUpdateHmacKey) {
    terminalUpdateHmacByEvent.set(toolEvent, createTerminalUpdateHmac(update, terminalUpdateHmacKey))
  }
  toolEvents.push(toolEvent)
} })
const rpc = acp.request
child.once('exit', code => { dshExitCode = code })
child.once('error', () => { evidenceErrors.push('dsh_process_start_failed') })
let sessionId = null, dshStatus = 'failed', computedState = null, managedChildrenExited = true
let dshFailure = { stage: 'unknown', method: 'unknown', error_class: 'none' }, dshChildExited = false
let scratchCleanupStatus = 'not_created'
function safeDshError(method, errorClass, stage = 'session_rpc') {
  const error = new Error('DSH RPC failed')
  error.safeStage = ['session_rpc', 'capability_check', 'reasoning_selection'].includes(stage) ? stage : 'unknown'
  error.safeMethod = ['initialize', 'session/new', 'session/set_config_option', 'session/prompt', 'session/close', 'session/cancel'].includes(method)
    ? method : 'unknown'
  error.safeClass = ['rpc_error', 'timeout', 'child_exit', 'write_error', 'capability_missing',
    'invalid_response', 'config_selection_failed', 'unknown'].includes(errorClass) ? errorClass : 'unknown'
  return error
}
try {
  failureStage = 'dsh_session'
  const initialized = await rpc('initialize', { protocolVersion: 1,
    clientInfo: { name: 'native-agent-coldstart-acceptance', version: '1' }, clientCapabilities: {} })
  if (initialized.agentCapabilities?.promptCapabilities?.image !== true) throw safeDshError('initialize', 'capability_missing', 'capability_check')
  const session = await rpc('session/new', { cwd: workDir, mcpServers: [] })
  sessionId = session.sessionId
  const selection = spawnSync(profilePath, ['--select-reasoning'], {
    input: JSON.stringify({ provider: { ...provider, api_key: apiKey }, session_response: session }),
    encoding: 'utf8', windowsHide: true,
  })
  if (selection.status !== 0) throw safeDshError('session/set_config_option', 'config_selection_failed', 'reasoning_selection')
  const reasoning = JSON.parse(selection.stdout)
  if (reasoning.configurable) await rpc('session/set_config_option', { sessionId, configId: reasoning.config_id, value: reasoning.value })
  const timeoutPolicy = resolvePromptTimeoutPolicy()
  const promptCadStatePath = join(state, 'cad-state.json')
  const initialGeometryRevision = readGeometryRevision(promptCadStatePath)
  const promptProgress = new PromptProgressTracker({
    sessionId,
    initialRevision: initialGeometryRevision,
  })
  currentPromptTracker = promptProgress
  const geometryWatcher = watchGeometryRevision(promptCadStatePath, promptProgress, 100, initialGeometryRevision)
  try {
    await rpc('session/prompt', { sessionId, prompt: [
      { type: 'text', text: prompt },
      { type: 'image', mimeType: 'image/png', data: imageBytes.toString('base64') },
    ] }, timeoutPolicy.totalTimeoutSeconds * 1000, {
      idleTimeoutMs: timeoutPolicy.idleTimeoutSeconds * 1000,
      progressTracker: promptProgress,
    })
    promptProgressDiagnostics = promptProgress.snapshot('none')
  } catch (error) {
    promptProgressDiagnostics = error?.promptProgress || promptProgress.snapshot()
    throw error
  } finally {
    geometryWatcher.close()
    currentPromptTracker = null
  }
  await rpc('session/close', { sessionId })
  dshStatus = 'completed'
} catch (error) {
  dshFailure = { stage: error?.safeStage || 'unknown', method: error?.safeMethod || 'unknown', error_class: error?.safeClass || 'unknown' }
  if (sessionId && child.exitCode === null) {
    await cancelAndCloseSession(acp, sessionId, 15_000)
  }
} finally {
  acp.close()
  child.stdin.end()
  const childExited = await waitThenTerminateChild(child, 5500, 1500)
  dshChildExited = childExited
  await toolProxy.close()
  // Recompute the plan signature in Rust, without calling a provider or
  // finishing/reviewing the model. This direct probe call is not Cordis proof.
  if (probe.exitCode === null) {
    computedState = await new Promise(resolveState => {
      const onLine = line => {
        clearTimeout(timer)
        try { resolveState(parsePostcheckState(JSON.parse(line))) } catch { resolveState(null) }
      }
      const timer = setTimeout(() => { probeLines.off('line', onLine); resolveState(null) }, 5000)
      probeLines.once('line', onLine)
      probe.stdin.write(JSON.stringify({ name: 'cad_get_state', arguments: {} }) + '\n')
    })
  }
  if (probe.exitCode === null && !probe.stdin.destroyed) {
    probe.stdin.write('quit\n')
    probe.stdin.end()
  }
  const probeExited = await waitThenTerminateChild(probe, 1500, 1500)
  managedChildrenExited = childExited && probeExited
  if (!childExited || !probeExited) evidenceErrors.push('managed_child_exit_timeout')
}

const readJsonFile = (path, fallback = {}) => {
  try {
    const value = JSON.parse(readFileSync(path, 'utf8'))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected an object')
    return value
  } catch { evidenceErrors.push(`${basename(path)}_missing_or_invalid`); return fallback }
}
const regPath = join(state, 'native-tool-registration.json')
const schemaPath = join(state, 'schemas.json')
const registration = readJsonFile(regPath)
const schemaManifest = readJsonFile(schemaPath)
const cadStatePath = join(state, 'cad-state.json')
const domain = readJsonFile(cadStatePath)
const runEndedAt = Date.now()
const readJsonl = path => {
  if (!existsSync(path)) { evidenceErrors.push(`${basename(path)}_missing`); return [] }
  const parsed = parseEvidenceJsonl(readFileSync(path, 'utf8'), basename(path))
  evidenceErrors.push(...parsed.errors)
  return parsed.records
}
const nativeAudit = readJsonl(join(state, 'native-tool-audit.jsonl'))
const artifacts = readRunArtifacts(workDir, domain)
const streamEvents = readJsonl(streamPath)
const sourceShaAfter = hash(readFileSync(imagePath))
failureStage = 'strict_gate_evaluation'
const strict = evaluateColdstartGates({
  run: { id: runId, turn_id: turnId, acp_session_id: sessionId, tool_session_id: bridge.session_id,
    started_at_ms: runStartedAt, ended_at_ms: runEndedAt },
  source: { sha256: sourceSha, sha256_after: sourceShaAfter, bytes: imageBytes.length, reference_id: referenceId },
  domain, computedState, artifacts, registration, schema: schemaManifest,
  provider: { model: effectiveProfile.model, host: new URL(effectiveProfile.base_url).host },
  references: readJsonFile(refsPath), userTurns: readJsonFile(turnsPath), activeTurn: readJsonFile(activeTurnPath),
  dsh: { status: dshStatus, exit_code: dshExitCode, child_exited: dshChildExited, failure: dshFailure }, toolEvents, toolBridgeCalls: toolProxy.records,
  nativeAudit, streamEvents, evidenceErrors,
})
failureStage = 'publication'
failurePhase = SAFE_PUBLICATION_PHASE.PROJECTION
// Cache only the fixed allowlist projection so a later artifact-publication
// exception can preserve evaluated gate outcomes without retaining raw evidence.
safeStrictGates = buildSafeStrictGateProjection(strict)
let safeSummary
{
  const inRunToolEvents = toolEvents.filter(event => event.session_id === sessionId && event.run_id === runId
    && event.turn_id === turnId && Number.isFinite(event.at_ms) && event.at_ms >= runStartedAt && event.at_ms <= runEndedAt)
  const projection = summarizeAcpTerminalUpdateDiagnostics(inRunToolEvents, { terminalUpdateHmacByEvent })
  const bindingProjection = summarizeCordisBindingDiagnostics({
    run: { id: runId, turn_id: turnId, acp_session_id: sessionId, tool_session_id: bridge.session_id,
      started_at_ms: runStartedAt, ended_at_ms: runEndedAt },
    registration, schema: schemaManifest, toolEvents, toolBridgeCalls: toolProxy.records, nativeAudit,
  })
  const wireRequests = streamEvents.filter(row => row.kind === 'provider_wire_request')
  const successfulWireResponse = strict.successful_image_request_ids.length > 0
  const wireIdentityValid = strict.gates.main_image_wire.reasons.indexOf('main_model_original_image_wire_missing') < 0
  scratchCleanupStatus = cleanupScratch()
  safeSummary = buildSafeAcceptanceSummary({
    strict,
    transportStatus: dshStatus === 'completed' && wireIdentityValid && successfulWireResponse ? 'completed' : 'failed',
    modelStatus: strict.status === 'passed' ? 'accepted_by_strict_cli_gate' : domain.plan ? 'candidate' : 'none',
    runtime: { node: nodeVersion.stdout.trim(), dsh: dshPackage.version },
    source: { bytes: imageBytes.length, sha256: sourceSha, unchanged: hash(readFileSync(imagePath)) === sourceSha },
    dshDiagnostic: dshFailure,
    promptProgress: promptProgressDiagnostics,
    scratchCleanupStatus: safeScratchCleanupStatus(managedChildrenExited, scratchCleanupStatus),
    scratchRecovery,
  })
  writeSafeAcceptanceArtifacts(output, { summary: safeSummary, acpProjection: projection,
    bindingProjection, visionProjection: summarizeVisualReviewDiagnostics(domain), buildStatus,
    onPhase: phase => {
      failurePhase = phase
      if (phase === SAFE_PUBLICATION_PHASE.PUBLICATION_COMPLETE) safeSummaryPublished = true
    } })
}
failurePhase = SAFE_PUBLICATION_PHASE.RUNNER_COMPLETION
const finalStatus = safeSummary.status
const cleanupFailed = !managedChildrenExited || scratchCleanupStatus !== 'removed'
console.log(JSON.stringify({ status: finalStatus, scratch_cleanup_status: cleanupFailed ? 'failed' : 'removed',
  scratch_recovery: scratchRecovery, evidence_archive: output }, null, 2))
if (finalStatus !== 'passed') {
  process.exitCode = 1
}
