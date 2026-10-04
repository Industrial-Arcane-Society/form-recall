import { createServer, createConnection } from 'node:net'
import { createHash, createHmac } from 'node:crypto'
import { readFileSync, realpathSync, statSync } from 'node:fs'
import { resolve, relative, isAbsolute } from 'node:path'
import { isDeepStrictEqual } from 'node:util'

const sha = value => createHash('sha256').update(value).digest('hex')
const rows = value => Array.isArray(value) ? value : []
const nonempty = value => typeof value === 'string' && value.length > 0
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const hasSingleTerminalStatus = event => Array.isArray(event?.terminal_statuses)
  && event.terminal_statuses.length === 1
  && ['completed', 'failed'].includes(event.terminal_statuses[0])

const acpStatusCategory = value => ['pending', 'in_progress', 'completed', 'failed'].includes(value) ? value : 'other'
export const COLDSTART_TOOL_NAME_ALLOWLIST = Object.freeze([
  'cad_add_face_recess', 'cad_add_feature', 'cad_add_hole_array', 'cad_apply_recipe',
  'cad_attach_external_fact_evidence', 'cad_create_model', 'cad_finish_model', 'cad_get_feature_spec',
  'cad_get_reference_image', 'cad_get_state', 'cad_inspect_references', 'cad_preview_model',
  'cad_record_visual_review', 'cad_remove_feature', 'cad_require_external_fact', 'cad_restore_best_candidate',
  'cad_restore_partial_candidate', 'cad_review_reference_similarity', 'cad_search_knowledge',
  'cad_search_recipes', 'cad_set_construction_milestone', 'cad_update_feature', 'cad_validate_model',
  'web_open_result', 'web_search',
])
const coldstartToolNames = new Set(COLDSTART_TOOL_NAME_ALLOWLIST)
const safeToolName = value => coldstartToolNames.has(value) ? value : 'unknown'
const bindingOutcomes = Object.freeze({
  acp_to_bridge: Object.freeze(['matched', 'bridge_call_missing', 'acp_call_missing', 'tool_name_not_allowlisted',
    'correlation_fingerprint_missing', 'invocation_count_mismatch', 'identity_pairing_ambiguous',
    'acp_identity_missing', 'run_scope_mismatch']),
  bridge_to_audit: Object.freeze(['matched', 'tool_name_not_allowlisted', 'bridge_call_id_missing',
    'bridge_scope_rejected', 'bridge_time_invalid', 'bridge_execution_rejected', 'schema_missing',
    'registration_missing', 'native_audit_missing', 'native_audit_tool_mismatch', 'native_audit_out_of_run',
    'native_audit_rejected']),
})

const stableJson = value => {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(item => stableJson(item)).join(',')}]`
  const entries = Object.keys(value).sort().filter(key => value[key] !== undefined)
  return `{${entries.map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
}

// HMACs exist only in memory for one run. Callers must never persist the
// returned value; it is used only to compare terminal ACP update equality.
export function createTerminalUpdateHmac(update, key) {
  return createHmac('sha256', key).update(stableJson(update)).digest('hex')
}

// Return an allowlisted count-only projection. Call IDs are used transiently
// for grouping and are never included in the returned diagnostic.
export function summarizeAcpTerminalUpdateDiagnostics(toolEvents = [], { terminalUpdateHmacByEvent = new WeakMap() } = {}) {
  const groups = new Map()
  const argumentFingerprints = new Map()
  const argumentInvocationKeys = new Set()
  let missingIdentityEventCount = 0
  const statusCounts = new Map()
  for (const event of rows(toolEvents)) {
    const isStart = event?.kind === 'tool_call'
    const isUpdate = event?.kind === 'tool_call_update'
    if (!isStart && !isUpdate) continue
    const callId = nonempty(event.tool_call_id) ? event.tool_call_id : null
    const group = callId ? groups.get(callId) : null
    const status = acpStatusCategory(event.status)
    const eventToolName = event.title ? safeToolName(event.title) : 'unknown'
    const toolName = eventToolName !== 'unknown' ? eventToolName : group?.tool_name || 'unknown'
    const countKey = `${toolName}\u0000${isStart ? 'start' : 'update'}\u0000${status}`
    statusCounts.set(countKey, (statusCounts.get(countKey) || 0) + 1)
    const terminal = isUpdate && ['completed', 'failed'].includes(status)
    if (!callId) {
      missingIdentityEventCount += 1
      continue
    }
    if (isStart && eventToolName.startsWith('cad_') && /^[a-f0-9]{24}$/.test(event.arguments_sha256_24 || '')) {
      const invocationKey = `${eventToolName}\u0000${callId}\u0000${event.arguments_sha256_24}`
      if (!argumentInvocationKeys.has(invocationKey)) {
        argumentInvocationKeys.add(invocationKey)
        const key = `${eventToolName}\u0000${event.arguments_sha256_24}`
        argumentFingerprints.set(key, (argumentFingerprints.get(key) || 0) + 1)
      }
    }
    const currentGroup = group || { tool_name: 'unknown', terminal_summaries: [] }
    if (eventToolName !== 'unknown') currentGroup.tool_name = eventToolName
    if (terminal) currentGroup.terminal_summaries.push({ status, hmac: terminalUpdateHmacByEvent.get(event) || null })
    groups.set(callId, currentGroup)
  }

  const toolStatusCounts = [...statusCounts.entries()].map(([key, count]) => {
    const [tool_name, kind, status] = key.split('\u0000')
    return { tool_name, kind, status, count }
  }).sort((a, b) => a.tool_name.localeCompare(b.tool_name) || a.kind.localeCompare(b.kind) || a.status.localeCompare(b.status))
  const repeatedArgumentsByTool = new Map()
  for (const [key, count] of argumentFingerprints) {
    if (count < 2) continue
    const toolName = key.slice(0, key.indexOf('\u0000'))
    repeatedArgumentsByTool.set(toolName, (repeatedArgumentsByTool.get(toolName) || 0) + count - 1)
  }
  const groupCounts = new Map(), terminalStatusCounts = new Map()
  let sameTerminalEventPairCount = 0, differentTerminalEventPairCount = 0, unavailableHmacEventCount = 0
  for (const group of groups.values()) {
    const terminal = group.terminal_summaries
    const statuses = terminal.map(item => item.status)
    const terminal_status = terminal.length === 0 ? 'none'
      : new Set(statuses).size > 1 ? 'mixed' : statuses[0]
    const statusKey = `${group.tool_name}\u0000${terminal_status}`
    terminalStatusCounts.set(statusKey, (terminalStatusCounts.get(statusKey) || 0) + 1)
    let terminal_category = 'no_terminal'
    if (terminal.length === 1) terminal_category = statuses[0] === 'completed' ? 'completed_once' : 'failed_once'
    else if (terminal.length > 1 && new Set(statuses).size > 1) terminal_category = 'repeated_mixed_terminal_statuses'
    else if (terminal.length > 1) {
      const hmacs = terminal.map(item => item.hmac)
      if (hmacs.some(value => !value)) terminal_category = 'repeated_same_status_digest_unavailable'
      else if (new Set(hmacs).size === 1) terminal_category = 'repeated_same_status_identical_update'
      else terminal_category = 'repeated_same_status_distinct_updates'
    }
    const key = `${group.tool_name}\u0000${terminal_category}`
    groupCounts.set(key, (groupCounts.get(key) || 0) + 1)
    const hmacs = terminal.map(item => item.hmac)
    unavailableHmacEventCount += hmacs.filter(value => !value).length
    for (let left = 0; left < hmacs.length; left += 1) {
      for (let right = left + 1; right < hmacs.length; right += 1) {
        if (!hmacs[left] || !hmacs[right]) continue
        if (hmacs[left] === hmacs[right]) sameTerminalEventPairCount += 1
        else differentTerminalEventPairCount += 1
      }
    }
  }
  const callGroupTerminalDistribution = [...groupCounts.entries()].map(([key, count]) => {
    const [tool_name, terminal_category] = key.split('\u0000')
    return { tool_name, terminal_category, count }
  }).sort((a, b) => a.tool_name.localeCompare(b.tool_name) || a.terminal_category.localeCompare(b.terminal_category))
  const callGroupTerminalStatusCounts = [...terminalStatusCounts.entries()].map(([key, count]) => {
    const [tool_name, terminal_status] = key.split('\u0000')
    return { tool_name, terminal_status, count }
  }).sort((a, b) => a.tool_name.localeCompare(b.tool_name) || a.terminal_status.localeCompare(b.terminal_status))
  return {
    schema: 'acp_terminal_update_projection_v3',
    tool_status_counts: toolStatusCounts,
    repeated_argument_fingerprint_counts: [...repeatedArgumentsByTool.entries()]
      .map(([tool_name, repeated_call_count]) => ({ tool_name, repeated_call_count }))
      .sort((a, b) => a.tool_name.localeCompare(b.tool_name)),
    call_group_terminal_status_counts: callGroupTerminalStatusCounts,
    call_group_terminal_distribution: callGroupTerminalDistribution,
    same_terminal_event_pair_count: sameTerminalEventPairCount,
    different_terminal_event_pair_count: differentTerminalEventPairCount,
    terminal_update_hmac_unavailable_event_count: unavailableHmacEventCount,
    missing_tool_call_id_event_count: missingIdentityEventCount,
  }
}

// Aggregate the ACP -> bridge -> Rust audit relationship without disclosing
// IDs, argument fingerprints, timestamps or free-form error fields.
export function summarizeCordisBindingDiagnostics(input = {}) {
  const { run = {}, registration, schema, toolEvents = [], toolBridgeCalls = [], nativeAudit = [] } = input
  const counts = new Map()
  const bump = (toolName, stage, outcome, count = 1) => {
    const name = safeToolName(toolName)
    const code = bindingOutcomes[stage].includes(outcome) ? outcome : 'tool_name_not_allowlisted'
    const key = `${name}\u0000${stage}\u0000${code}`
    counts.set(key, (counts.get(key) || 0) + count)
  }
  const inRun = at => Number.isFinite(at) && at >= run.started_at_ms && at <= run.ended_at_ms
  const groups = new Map(), missingAcpIdentity = []
  for (const event of rows(toolEvents)) {
    if (event.run_id !== run.id || event.session_id !== run.acp_session_id || event.turn_id !== run.turn_id || !inRun(event.at_ms)) continue
    const start = event.kind === 'tool_call'
    const terminal = event.kind === 'tool_call_update' && ['completed', 'failed'].includes(event.status)
    if (!nonempty(event.tool_call_id)) {
      if ((start && safeToolName(event.title) !== 'unknown') || terminal) missingAcpIdentity.push(event)
      continue
    }
    const previous = groups.get(event.tool_call_id) || { tool: '', starts: 0 }
    const terminalStatuses = terminal ? [...new Set([...(previous.terminal_statuses || []), event.status])] : previous.terminal_statuses || []
    groups.set(event.tool_call_id, {
      ...previous, tool: event.title || previous.tool,
      tool_call_id: event.tool_call_id,
      starts: previous.starts + (start ? 1 : 0),
      started_at_ms: start ? event.at_ms : previous.started_at_ms,
      terminal_updates: (previous.terminal_updates || 0) + (terminal ? 1 : 0),
      terminal_statuses: terminalStatuses,
      // ACP updates are upserts, so repeated snapshots with the same terminal
      // state do not extend this call's execution interval.
      terminal_at_ms: terminal && !Number.isFinite(previous.terminal_at_ms) ? event.at_ms : previous.terminal_at_ms,
      arguments_sha256_24: event.arguments_sha256_24 || previous.arguments_sha256_24,
    })
  }
  const acpCalls = [...groups.values()].filter(event => safeToolName(event.tool) !== 'unknown')
  const keyFor = row => coldstartToolNames.has(row.tool) && /^[a-f0-9]{24}$/.test(row.arguments_sha256_24 || '')
    ? `${row.tool}\u0000${row.arguments_sha256_24}` : null
  const acpById = new Map()
  for (const event of acpCalls) if (nonempty(event.tool_call_id)) {
    acpById.set(event.tool_call_id, [...(acpById.get(event.tool_call_id) || []), event])
  }
  const bridgeIds = new Set()
  const validIdentity = (call, event) => event?.starts === 1 && nonempty(event.tool_call_id)
    && call.acp_call_id === event.tool_call_id && keyFor(call) !== null && keyFor(call) === keyFor(event)
    && call.run_id === run.id && call.session_id === run.tool_session_id && call.turn_id === run.turn_id
    && nonempty(call.call_id) && event.terminal_updates > 0 && hasSingleTerminalStatus(event)

  for (const call of rows(toolBridgeCalls)) {
    const name = safeToolName(call.tool), key = keyFor(call)
    let outcome
    if (name === 'unknown') outcome = 'tool_name_not_allowlisted'
    else if (!key) outcome = 'correlation_fingerprint_missing'
    else if (!nonempty(call.acp_call_id)) outcome = 'acp_identity_missing'
    else if (!acpById.has(call.acp_call_id)) outcome = 'acp_call_missing'
    else if (call.run_id !== run.id || call.session_id !== run.tool_session_id || call.turn_id !== run.turn_id) outcome = 'run_scope_mismatch'
    else if ((acpById.get(call.acp_call_id) || []).length !== 1 || !validIdentity(call, acpById.get(call.acp_call_id)?.[0])) outcome = 'identity_pairing_ambiguous'
    else if (bridgeIds.has(call.acp_call_id)) outcome = 'identity_pairing_ambiguous'
    else outcome = 'matched'
    bridgeIds.add(call.acp_call_id)
    bump(name, 'acp_to_bridge', outcome)
  }
  for (const event of acpCalls) if (!rows(toolBridgeCalls).some(call => call.acp_call_id === event.tool_call_id)) bump(event.tool, 'acp_to_bridge', 'bridge_call_missing')
  for (const event of missingAcpIdentity) bump(event.title, 'acp_to_bridge', 'acp_identity_missing')
  for (const event of acpCalls) if (!keyFor(event)) bump(event.tool, 'acp_to_bridge', 'correlation_fingerprint_missing')

  const schemaNames = new Set(rows(schema?.tools).map(row => row.name))
  const registeredNames = new Set(rows(registration?.registeredToolNames))
  for (const call of rows(toolBridgeCalls)) {
    const name = safeToolName(call.tool)
    let outcome = 'matched'
    const idRows = nonempty(call.call_id) ? rows(nativeAudit).filter(row => row.call_id === call.call_id) : []
    const toolRows = idRows.filter(row => row.tool === call.tool)
    const timedRows = toolRows.filter(row => Number.isFinite(row.at)
      && row.at >= Math.floor(run.started_at_ms / 1000) && row.at <= Math.ceil(run.ended_at_ms / 1000))
    if (name === 'unknown') outcome = 'tool_name_not_allowlisted'
    else if (!nonempty(call.call_id)) outcome = 'bridge_call_id_missing'
    else if (call.authenticated_scope !== true || call.run_id !== run.id || call.session_id !== run.tool_session_id || call.turn_id !== run.turn_id) outcome = 'bridge_scope_rejected'
    else if (!Number.isFinite(call.started_at_ms) || !Number.isFinite(call.completed_at_ms)
      || call.completed_at_ms < call.started_at_ms || !inRun(call.started_at_ms) || !inRun(call.completed_at_ms)) outcome = 'bridge_time_invalid'
    else if (call.success !== true) outcome = 'bridge_execution_rejected'
    else if (!schemaNames.has(call.tool)) outcome = 'schema_missing'
    else if (!registeredNames.has(call.tool)) outcome = 'registration_missing'
    else if (!idRows.length) outcome = 'native_audit_missing'
    else if (!toolRows.length) outcome = 'native_audit_tool_mismatch'
    else if (!timedRows.length) outcome = 'native_audit_out_of_run'
    else if (!timedRows.some(row => row.success === true)) outcome = 'native_audit_rejected'
    bump(name, 'bridge_to_audit', outcome)
  }
  const countRows = [...counts.entries()].map(([key, count]) => {
    const [tool_name, stage, outcome] = key.split('\u0000')
    return { tool_name, stage, outcome, count }
  }).sort((left, right) => left.tool_name.localeCompare(right.tool_name)
    || left.stage.localeCompare(right.stage) || left.outcome.localeCompare(right.outcome))
  return { schema: 'cordis_binding_projection_v1', counts: countRows }
}

// Persist only a short digest of tool arguments so ACP invocation events can
// be joined to the native bridge without retaining user/model parameter data.
export function toolArgumentsFingerprint(value) {
  if (value === undefined) return null
  const normalize = item => {
    if (Array.isArray(item)) return item.map(normalize)
    if (item && typeof item === 'object') return Object.fromEntries(Object.keys(item).sort().map(key => [key, normalize(item[key])]))
    return item
  }
  try { return sha(JSON.stringify(normalize(value))).slice(0, 24) } catch { return null }
}

// Observe the authenticated Cordis bridge without persisting tokens, raw arguments,
// image bodies, response bodies or provider credentials. The native audit's UUID
// is distinct from ACP's tool ID; this gives an exact native call-ID join.
export async function createAuditedToolProxy({ address, token, sessionId, turnId, runId }) {
  const match = /^127\.0\.0\.1:(\d+)$/.exec(address)
  if (!match || Number(match[1]) < 1 || Number(match[1]) > 65535) throw new Error('Expected a loopback native bridge')
  const records = [], sockets = new Set()
  const server = createServer(client => {
    const upstream = createConnection({ host: '127.0.0.1', port: Number(match[1]) })
    sockets.add(client); sockets.add(upstream)
    let requestBuffer = '', responseBuffer = '', rpcId, record, settled = false
    const finish = () => {
      if (record && !settled) {
        settled = true
        records.push({ ...record, completed_at_ms: Date.now(), success: record.success === true })
      }
    }
    client.on('data', chunk => {
      if (record) return
      requestBuffer += chunk.toString('utf8')
      if (requestBuffer.length > 64 * 1024 * 1024) { client.destroy(); upstream.destroy(); return }
      const end = requestBuffer.indexOf('\n')
      if (end < 0) return
      try {
        const request = JSON.parse(requestBuffer.slice(0, end)), p = request.params || {}
        rpcId = request.id
        if (request.method === 'tools/call') record = {
          run_id: runId, session_id: p.sessionId, turn_id: p.turnId,
          call_id: p.toolCallId, acp_call_id: p.acpCallId, tool: p.name, started_at_ms: Date.now(),
          arguments_sha256_24: toolArgumentsFingerprint(p.arguments),
          authenticated_scope: p.token === token && p.sessionId === sessionId && p.turnId === turnId,
        }
      } catch { /* malformed native requests cannot become success evidence */ }
      requestBuffer = ''
    })
    upstream.on('data', chunk => {
      if (!record || settled) return
      responseBuffer += chunk.toString('utf8')
      if (responseBuffer.length > 64 * 1024 * 1024) { client.destroy(); upstream.destroy(); return }
      const end = responseBuffer.indexOf('\n')
      if (end < 0) return
      try {
        const response = JSON.parse(responseBuffer.slice(0, end)), payload = response.result?.payload
        record.success = response.id === rpcId && !response.error && payload?.isError === false
          && payload.execution_engine === 'rust' && payload.tool === record.tool
      } catch { record.success = false }
      responseBuffer = ''; finish()
    })
    const failed = () => { finish(); client.destroy(); upstream.destroy() }
    client.on('error', failed); upstream.on('error', failed)
    client.on('close', () => { finish(); sockets.delete(client); upstream.destroy() })
    upstream.on('close', () => { finish(); sockets.delete(upstream); client.destroy() })
    client.pipe(upstream); upstream.pipe(client)
  })
  await new Promise((yes, no) => { server.once('error', no); server.listen(0, '127.0.0.1', yes) })
  return {
    address: `127.0.0.1:${server.address().port}`, records,
    async close() { for (const socket of sockets) socket.destroy(); await new Promise(done => server.close(done)) },
  }
}

export function parsePostcheckState(response) {
  const result = response?.result
  if (response?.ok !== true || result?.payload?.tool !== 'cad_get_state' || result.payload.isError !== false) return null
  const text = result.text
  if (typeof text !== 'string' || !text.startsWith('AI_MODELER_PROGRESS:') || !text.includes('\n')) return null
  try { return JSON.parse(text.slice(text.indexOf('\n') + 1)) } catch { return null }
}

export function parseEvidenceJsonl(text, name) {
  const records = [], errors = []
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    if (!line.trim()) continue
    try {
      const row = JSON.parse(line)
      if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('not a record')
      records.push(row)
    } catch { errors.push(`${name}_invalid_record_line_${index + 1}`) }
  }
  return { records, errors }
}

// Every published file must resolve inside this fresh run, including symlinks.
export function readRunArtifacts(output, domain) {
  const failures = [], files = {}, paths = domain?.last_preview?.output_paths || {}
  let geometryReport = null, cadplan = null, previewSha = null
  for (const kind of ['cadplan', 'geometry_report', 'preview', 'stl', 'glb', 'aimodel']) {
    try {
      if (!nonempty(paths[kind])) throw new Error('missing path')
      const full = realpathSync(resolve(paths[kind])), root = realpathSync(output)
      const rel = relative(root, full)
      if (!rel || rel === '..' || rel.startsWith('..\\') || rel.startsWith('../') || isAbsolute(rel)) throw new Error('outside fresh run')
      if (!statSync(full).isFile()) throw new Error('not a file')
      const bytes = readFileSync(full)
      if (!bytes.length) throw new Error('empty file')
      files[kind] = { path: full, bytes: bytes.length, sha256: sha(bytes) }
      if (kind === 'geometry_report') geometryReport = JSON.parse(bytes.toString('utf8'))
      if (kind === 'cadplan') cadplan = JSON.parse(bytes.toString('utf8'))
      if (kind === 'preview') previewSha = sha(bytes)
    } catch { failures.push(`artifact_${kind}_missing_invalid_or_outside_run`) }
  }
  return { files, failures, geometry_report: geometryReport, cadplan, preview_sha256: previewSha }
}

export function evaluateColdstartGates(input) {
  const { run = {}, source = {}, domain = {}, computedState, artifacts = {}, registration, schema,
    references, userTurns, activeTurn, provider = {}, dsh = {}, toolEvents = [], toolBridgeCalls = [], nativeAudit = [], streamEvents = [], evidenceErrors = [] } = input
  const gates = {}, failed = []
  function gate(name, conditions) {
    const reasons = conditions.filter(([ok]) => !ok).map(([, reason]) => reason)
    gates[name] = { status: reasons.length ? 'failed' : 'passed', reasons }
    failed.push(...reasons)
  }
  const inRun = at => Number.isFinite(at) && at >= run.started_at_ms && at <= run.ended_at_ms
  const bound = row => Number.isSafeInteger(domain.revision) && domain.revision > 0 && digest(computedState?.geometry_signature)
    && row?.revision === domain.revision && row?.geometry_fingerprint === computedState.geometry_signature
  gate('run_scope', [
    [nonempty(run.id) && nonempty(run.turn_id) && nonempty(run.acp_session_id) && nonempty(run.tool_session_id)
      && Number.isFinite(run.started_at_ms) && run.ended_at_ms >= run.started_at_ms, 'run_identity_missing'],
    [references?.session_id === run.tool_session_id && userTurns?.session_id === run.tool_session_id
      && activeTurn?.session_id === run.tool_session_id && activeTurn?.turn_id === run.turn_id, 'manifest_session_or_turn_mismatch'],
    [rows(references?.images).length === 1 && references.images[0].id === source.reference_id
      && references.images[0].sha256 === source.sha256, 'original_reference_manifest_mismatch'],
    [rows(userTurns?.turns).length === 1 && userTurns.turns[0].turn_id === run.turn_id
      && rows(userTurns.turns[0].reference_ids).includes(source.reference_id), 'user_turn_reference_binding_missing'],
  ])
  gate('dsh_session', [[dsh.status === 'completed', 'dsh_session_not_completed'],
    [dsh.failure?.error_class === undefined || dsh.failure.error_class === 'none', 'dsh_session_rpc_failed']])
  gate('dsh_process', [[dsh.exit_code === 0, 'dsh_child_exit_nonzero_or_missing'],
    [dsh.child_exited === true, 'dsh_child_exit_not_observed']])
  gate('trace_integrity', [[Array.isArray(evidenceErrors) && evidenceErrors.length === 0, 'evidence_trace_missing_or_malformed']])
  gate('original_image', [[digest(source.sha256) && source.sha256 === source.sha256_after && source.bytes === 72309, 'original_image_changed_or_invalid']])
  const imageRequests = rows(streamEvents).filter(row => row.kind === 'provider_wire_request'
    && row.capture_layer === 'node_global_fetch' && row.wire_observed === true && row.turn_id === run.turn_id
    && inRun(row.at) && nonempty(row.request_id) && row.model === provider.model && row.provider_host === provider.host
    && nonempty(provider.model) && nonempty(provider.host) && rows(row.images).some(image => image.source_sha256 === source.sha256
      && image.transport_sha256 === source.sha256 && image.byte_length === source.bytes
      && rows(image.source_reference_ids).includes(source.reference_id)))
  const successfulImageRequests = imageRequests.filter(request =>
    rows(streamEvents).some(row => row.kind === 'provider_wire_response_headers' && row.request_id === request.request_id
      && row.turn_id === run.turn_id && inRun(row.at) && row.at >= request.at && row.status_code === 200)
    && rows(streamEvents).some(row => row.kind === 'provider_wire_response_complete' && row.request_id === request.request_id
      && row.turn_id === run.turn_id && inRun(row.at) && row.at >= request.at
      && ['sse_done', 'response.completed', 'body_end'].includes(row.response_terminator)
      && !['length', 'content_filter', 'error'].includes(row.finish_reason)
      && (request.request_fields?.stream !== true || row.response_terminator !== 'body_end'
        || ['stop', 'tool_calls', 'function_call'].includes(row.finish_reason)))
    && !rows(streamEvents).some(row => ['provider_wire_error', 'provider_wire_response_read_error'].includes(row.kind)
      && row.request_id === request.request_id && row.turn_id === run.turn_id))
  gate('main_image_wire', [[imageRequests.length > 0, 'main_model_original_image_wire_missing'],
    [successfulImageRequests.length > 0, 'same_image_request_successful_response_missing']])
  const calls = rows(toolBridgeCalls).filter(row => typeof row.tool === 'string' && row.tool.startsWith('cad_'))
  const acpTools = new Map(), acpEventsMissingIdentity = []
  for (const event of rows(toolEvents)) {
    if (event.session_id !== run.acp_session_id || event.run_id !== run.id || event.turn_id !== run.turn_id || !inRun(event.at_ms)) continue
    if (!nonempty(event.tool_call_id)) {
      // Never coalesce unidentified events under the same `undefined` Map key.
      // CAD starts identify their tool by title; terminal updates have no
      // required title, so an unidentified terminal is also unbindable.
      if ((event.kind === 'tool_call' && event.title?.startsWith('cad_'))
        || (event.kind === 'tool_call_update' && ['completed', 'failed'].includes(event.status))) {
        acpEventsMissingIdentity.push(event)
      }
      continue
    }
    const old = acpTools.get(event.tool_call_id) || {}
    const terminal = event.kind === 'tool_call_update' && ['completed', 'failed'].includes(event.status)
    const terminalStatuses = terminal ? [...new Set([...(old.terminal_statuses || []), event.status])] : old.terminal_statuses || []
    acpTools.set(event.tool_call_id, { tool: event.title || old.tool, status: event.status || old.status,
      tool_call_id: event.tool_call_id, starts: (old.starts || 0) + (event.kind === 'tool_call' ? 1 : 0),
      started_at_ms: event.kind === 'tool_call' ? event.at_ms : old.started_at_ms,
      terminal_updates: (old.terminal_updates || 0) + (terminal ? 1 : 0),
      terminal_statuses: terminalStatuses,
      terminal_at_ms: terminal && !Number.isFinite(old.terminal_at_ms) ? event.at_ms : old.terminal_at_ms,
      arguments_sha256_24: event.arguments_sha256_24 || old.arguments_sha256_24 })
  }
  const observedCadTools = [...acpTools.values()].filter(row => row.tool?.startsWith('cad_'))
  const acpCadIdentityMissing = acpEventsMissingIdentity.length > 0
  const correlationKey = row => nonempty(row.tool) && /^[a-f0-9]{24}$/.test(row.arguments_sha256_24 || '')
    ? `${row.tool}\u0000${row.arguments_sha256_24}` : null
  // Carry the ACP ID separately from the native UUID. Missing or duplicate IDs
  // fail closed; timestamps and argument-only matching are never a fallback.
  const pairedCallIds = new Map()
  const argumentsAndIdentityPresent = calls.every(call => correlationKey(call) !== null && nonempty(call.call_id)
    && nonempty(call.acp_call_id)
    && call.run_id === run.id && call.session_id === run.tool_session_id && call.turn_id === run.turn_id)
    && observedCadTools.every(event => correlationKey(event) !== null && nonempty(event.tool_call_id) && event.starts === 1)
  const byAcpId = new Map()
  for (const event of observedCadTools) byAcpId.set(event.tool_call_id, [...(byAcpId.get(event.tool_call_id) || []), event])
  const allInvocationsPaired = calls.every(call => {
    const matches = byAcpId.get(call.acp_call_id) || []
    const event = matches.length === 1 ? matches[0] : null
    if (!event || correlationKey(call) !== correlationKey(event) || event.starts !== 1 || event.terminal_updates === 0
      || !hasSingleTerminalStatus(event) || call.run_id !== run.id
      || call.session_id !== run.tool_session_id || call.turn_id !== run.turn_id || !nonempty(call.call_id)) return false
    pairedCallIds.set(call.call_id, event)
    return true
  }) && argumentsAndIdentityPresent && observedCadTools.length === calls.length
    && new Set(calls.map(call => call.acp_call_id)).size === calls.length
    && observedCadTools.every(event => (byAcpId.get(event.tool_call_id) || []).length === 1
      && calls.some(call => call.acp_call_id === event.tool_call_id))
  const pairedOnce = pairedCallIds.size === calls.length && new Set(calls.map(call => call.call_id)).size === calls.length
  const schemaNames = new Set(rows(schema?.tools).map(row => row.name))
  const registrationNames = new Set(rows(registration?.registeredToolNames))
  const matched = calls.filter(call => call.authenticated_scope === true && call.run_id === run.id
    && call.session_id === run.tool_session_id && call.turn_id === run.turn_id && call.success === true
    && inRun(call.started_at_ms) && inRun(call.completed_at_ms) && nonempty(call.call_id)
    && schemaNames.has(call.tool) && registrationNames.has(call.tool)
    && rows(nativeAudit).some(audit => audit.call_id === call.call_id && audit.tool === call.tool && audit.success === true
      && Number.isFinite(audit.at) && audit.at >= Math.floor(run.started_at_ms / 1000) && audit.at <= Math.ceil(run.ended_at_ms / 1000))
    && pairedCallIds.has(call.call_id))
  const individualAcpCallsValid = allInvocationsPaired && pairedOnce
  gate('cordis_cad_tools', [
    [registration?.phase === 'registered' && schema?.schemaVersion === 1, 'cordis_registration_or_schema_missing'],
    [matched.length > 0, 'successful_cordis_cad_call_and_native_audit_missing'],
    [calls.length > 0 && matched.length === calls.length, 'cad_tool_rejected_unbound_or_unaudited'],
    [allInvocationsPaired && pairedOnce, 'cordis_tool_arguments_or_identity_missing_or_unmatched'],
    [!acpCadIdentityMissing, 'acp_cad_tool_call_id_missing'],
    [individualAcpCallsValid && new Set(calls.map(call => call.call_id)).size === calls.length, 'native_calls_not_individually_observed_or_duplicate'],
    [!rows(toolBridgeCalls).some(call => call.success !== true)
      && ![...acpTools.values()].some(event => event.tool?.startsWith('cad_') && event.status === 'failed'), 'tool_call_rejected_or_failed'],
    [!rows(nativeAudit).some(audit => typeof audit.tool === 'string' && audit.tool.startsWith('cad_')
      && !String(audit.call_id).startsWith('probe-') && !calls.some(call => call.call_id === audit.call_id)), 'native_cad_audit_without_cordis_call'],
    [![...acpTools.values()].some(event => event.tool?.startsWith('cad_') && event.status === 'failed'), 'acp_cad_tool_failed'],
  ])
  const acpResultReasons = []
  if ((observedCadTools.length === 0 && !acpCadIdentityMissing)
    || observedCadTools.some(event => event.terminal_updates === 0)) {
    acpResultReasons.push('acp_cad_tool_result_completion_missing')
  }
  if (acpCadIdentityMissing) acpResultReasons.push('acp_cad_tool_call_id_missing')
  if (observedCadTools.some(event => event.terminal_statuses?.includes('failed'))) {
    acpResultReasons.push('acp_cad_tool_result_failed')
  }
  if (observedCadTools.some(event => event.terminal_updates > 0 && !hasSingleTerminalStatus(event))) {
    acpResultReasons.push('acp_cad_tool_result_terminal_status_invalid')
  }
  gates.acp_cad_tool_results = { status: acpResultReasons.length ? 'failed' : 'passed', reasons: acpResultReasons }
  failed.push(...acpResultReasons)
  const preview = domain.last_preview, checks = preview?.local_geometry_checks, finish = domain.finish_summary
  const review = finish?.overall_geometry_review, evidence = review?.result?.evidence
  const { host_attestation: ignoredAttestation, ...unsignedChecks } = checks || {}
  gate('current_geometry', [
    [computedState?.revision === domain.revision, 'rust_postcheck_revision_stale'],
    [digest(computedState?.geometry_signature), 'rust_postcheck_geometry_signature_missing'],
    [domain.preview_revision === domain.revision, 'preview_revision_stale'],
    [bound(preview), 'preview_geometry_fingerprint_stale'],
    [bound(checks), 'geometry_checks_fingerprint_stale'],
    [bound(finish), 'finish_geometry_fingerprint_stale'],
    [bound(review), 'finish_review_fingerprint_stale'],
    [bound(domain.latest_geometry_review), 'latest_review_fingerprint_stale'],
    [preview?.execution_engine === 'rust_c_abi_occt' && checks?.execution_engine === 'rust_c_abi_occt', 'native_geometry_engine_missing'],
    [checks?.state === 'passed', 'native_geometry_check_state_not_passed'],
    [checks?.valid === true, 'native_geometry_validity_false'],
    [checks?.closed_solid_count === 1, 'native_geometry_solid_count_invalid'],
    [checks?.validation?.state === 'valid', 'native_geometry_validation_invalid'],
    [domain.partial_preview?.state !== 'candidate_partial_not_accepted', 'partial_preview_not_accepted'],
    [evidence?.geometry?.binding_valid === true, 'geometry_binding_invalid'],
    [evidence?.geometry?.passed === true, 'geometry_attestation_failed'],
    [evidence?.geometry?.host_attestation_valid === true, 'geometry_host_attestation_invalid'],
    [isDeepStrictEqual(evidence?.geometry?.checks, checks), 'geometry_attested_checks_mismatch'],
    [isDeepStrictEqual(review, domain.latest_geometry_review), 'finish_geometry_review_differs_from_current_gate'],
  ])
  gate('artifacts', [
    [Array.isArray(artifacts.failures) && artifacts.failures.length === 0, 'current_run_artifacts_missing_or_invalid'],
    [bound(artifacts.geometry_report), 'geometry_report_file_stale'],
    [isDeepStrictEqual(artifacts.geometry_report, unsignedChecks), 'published_geometry_report_differs_from_preview_checks'],
    [isDeepStrictEqual(artifacts.cadplan, domain.plan) && Array.isArray(domain.plan?.features) && domain.plan.features.length > 0, 'published_cadplan_differs_from_current_state'],
    [digest(preview?.preview_sha256) && preview.preview_sha256 === artifacts.preview_sha256, 'preview_image_hash_mismatch'],
  ])
  const hasHoles = rows(domain.plan?.features).some(feature => feature.kind === 'hole' && feature.enabled !== false)
  gate('holes', [
    [finish?.hole_measurement?.complete === true, 'hole_finish_measurement_incomplete'],
    [evidence?.holes?.measurement_complete === true, 'hole_evidence_measurement_incomplete'],
    [!hasHoles || checks?.hole_measurement_complete === true, 'hole_native_measurement_incomplete'],
    [!hasHoles || checks?.hole_measurements_verified === true, 'hole_native_measurement_unverified'],
  ])
  const referenceRows = rows(evidence?.references?.original_reference_sources)
  gate('visual', [
    [finish?.reference_coverage === true, 'visual_finish_coverage_incomplete'],
    [finish?.visual_evidence?.all_references_covered === true, 'visual_all_references_not_covered'],
    [Number.isFinite(finish?.independent_review_pair_count) && finish.independent_review_pair_count > 0, 'visual_independent_review_missing'],
    [evidence?.references?.visual_coverage_complete === true, 'visual_gate_coverage_incomplete'],
    [evidence?.references?.manifest_valid === true, 'visual_reference_manifest_invalid'],
    [evidence?.references?.mismatch_count === 0, 'visual_reference_mismatch_detected'],
    [referenceRows.length === 1, 'original_reference_inventory_count_invalid'],
    [referenceRows.length !== 1 || referenceRows[0].id === source.reference_id, 'original_reference_id_mismatch'],
    [referenceRows.length !== 1 || referenceRows[0].sha256 === source.sha256, 'original_reference_sha256_mismatch'],
    [referenceRows.length !== 1 || referenceRows[0].covered === true, 'original_reference_not_covered'],
    [referenceRows.length !== 1 || referenceRows[0].inventory_valid === true, 'original_reference_inventory_invalid'],
    [isDeepStrictEqual(finish?.visual_evidence, computedState?.visual_evidence_summary), 'visual_summary_stale_or_mismatched'],
  ])
  gate('finish', [
    [domain.finished === true, 'finish_host_not_finished'],
    [domain.finish_state === 'accepted', 'finish_domain_state_not_accepted'],
    [finish?.finish_state === 'accepted', 'finish_summary_state_not_accepted'],
    [finish?.accepted === true, 'finish_acceptance_flag_false'],
    [finish?.overall_geometry_review_accepted === true, 'finish_geometry_review_acceptance_false'],
    [review?.issuer === 'native_review_gate', 'finish_geometry_review_issuer_invalid'],
    [review?.result?.decision === 'accept', 'finish_geometry_review_decision_not_accept'],
    [finish?.construction_complete === true, 'finish_construction_incomplete'],
    [Number.isSafeInteger(finish?.open_finding_count) && finish.open_finding_count === 0, 'finish_open_findings_nonzero'],
    [Array.isArray(finish?.unresolved_findings) && finish.unresolved_findings.length === 0, 'finish_unresolved_findings_present'],
    [Number.isSafeInteger(finish?.required_external_facts?.required_count) && finish.required_external_facts.required_count >= 0,
      'finish_external_fact_required_count_invalid'],
    [finish?.required_external_facts?.required_count === finish?.required_external_facts?.verified_count,
      'finish_external_facts_unverified'],
    [finish?.required_external_facts?.failed_count === 0, 'finish_external_facts_failed'],
    [finish?.required_external_facts?.unknown_count === 0, 'finish_external_facts_unknown'],
  ])
  return { schema_version: 1, status: failed.length ? 'failed' : 'passed', gates,
    failure_reasons: [...new Set(failed)], evidence_errors: evidenceErrors, successful_image_request_ids: successfulImageRequests.map(row => row.request_id),
    matched_native_call_ids: matched.map(row => row.call_id),
    scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance' }
}
