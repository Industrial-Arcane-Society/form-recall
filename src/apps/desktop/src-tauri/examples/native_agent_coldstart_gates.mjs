import { createServer, createConnection } from 'node:net'
import { createHash } from 'node:crypto'
import { readFileSync, realpathSync, statSync } from 'node:fs'
import { resolve, relative, isAbsolute } from 'node:path'
import { isDeepStrictEqual } from 'node:util'

const sha = value => createHash('sha256').update(value).digest('hex')
const rows = value => Array.isArray(value) ? value : []
const nonempty = value => typeof value === 'string' && value.length > 0
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

const acpStatusCategory = value => ['pending', 'in_progress', 'completed', 'failed'].includes(value) ? value : 'other'
const acpToolCategory = value => typeof value === 'string' && value.startsWith('cad_') ? 'cad'
  : typeof value === 'string' && value.length ? 'non_cad' : 'unknown'

// Return an allowlisted count-only projection. Call IDs are used transiently
// for grouping and are never included in the returned diagnostic.
export function summarizeAcpTerminalUpdateDiagnostics(toolEvents = []) {
  const groups = new Map(), missingIdentityEvents = []
  const statusCounts = new Map()
  for (const event of rows(toolEvents)) {
    const isStart = event?.kind === 'tool_call'
    const isUpdate = event?.kind === 'tool_call_update'
    if (!isStart && !isUpdate) continue
    const status = acpStatusCategory(event.status)
    const titleCategory = acpToolCategory(event.title)
    const countKey = `${titleCategory}\u0000${isStart ? 'start' : 'update'}\u0000${status}`
    statusCounts.set(countKey, (statusCounts.get(countKey) || 0) + 1)
    const terminal = isUpdate && ['completed', 'failed'].includes(status)
    if (!nonempty(event.tool_call_id)) {
      if ((isStart && titleCategory === 'cad') || terminal) missingIdentityEvents.push(event)
      continue
    }
    const group = groups.get(event.tool_call_id) || { tool_type: 'unknown', statuses: [], terminal_summaries: [] }
    if (titleCategory !== 'unknown') group.tool_type = titleCategory
    group.statuses.push({ kind: isStart ? 'start' : 'update', status })
    if (terminal) group.terminal_summaries.push({ tool_type: group.tool_type, status })
    groups.set(event.tool_call_id, group)
  }

  const countRows = [...statusCounts.entries()].map(([key, count]) => {
    const [tool_type, kind, status] = key.split('\u0000')
    return { tool_type, kind, status, count }
  }).sort((a, b) => a.tool_type.localeCompare(b.tool_type) || a.kind.localeCompare(b.kind) || a.status.localeCompare(b.status))
  const sequenceCounts = new Map(), terminalCountDistribution = { '0': 0, '1': 0, '2': 0, '3_plus': 0 }
  let sameTerminalSummaryPairCount = 0, differentTerminalSummaryPairCount = 0
  for (const group of groups.values()) {
    const terminal = group.terminal_summaries
    const terminalCountBucket = terminal.length >= 3 ? '3_plus' : String(terminal.length)
    terminalCountDistribution[terminalCountBucket] += 1
    let sequence_category = 'status_only_other'
    const statuses = group.statuses.map(item => item.status)
    const terminalStatuses = terminal.map(item => item.status)
    if (!terminal.length) sequence_category = statuses.includes('in_progress') || statuses.includes('pending')
      ? 'progress_without_terminal' : 'no_terminal_status'
    else if (new Set(terminalStatuses).size > 1) sequence_category = 'conflicting_terminal_statuses'
    else if (terminal.length > 1) sequence_category = 'repeated_same_terminal_status'
    else {
      const terminalIndex = statuses.findIndex(status => ['completed', 'failed'].includes(status))
      const progressBeforeTerminal = statuses.slice(0, terminalIndex).some(status => ['pending', 'in_progress'].includes(status))
      const updateAfterTerminal = statuses.slice(terminalIndex + 1).length > 0
      if (updateAfterTerminal) sequence_category = 'update_after_terminal'
      else if (terminalStatuses[0] === 'completed') sequence_category = progressBeforeTerminal ? 'progress_then_completed' : 'completed_without_progress'
      else sequence_category = progressBeforeTerminal ? 'progress_then_failed' : 'failed_without_progress'
    }
    const key = `${group.tool_type}\u0000${sequence_category}`
    sequenceCounts.set(key, (sequenceCounts.get(key) || 0) + 1)
    for (let left = 0; left < terminal.length; left += 1) {
      for (let right = left + 1; right < terminal.length; right += 1) {
        if (terminal[left].tool_type === terminal[right].tool_type && terminal[left].status === terminal[right].status) {
          sameTerminalSummaryPairCount += 1
        } else differentTerminalSummaryPairCount += 1
      }
    }
  }
  const callGroupStatusSequences = [...sequenceCounts.entries()].map(([key, count]) => {
    const [tool_type, sequence_category] = key.split('\u0000')
    return { tool_type, sequence_category, count }
  }).sort((a, b) => a.tool_type.localeCompare(b.tool_type) || a.sequence_category.localeCompare(b.sequence_category))
  return {
    schema: 'acp_terminal_update_projection_v1',
    tool_status_update_counts: countRows,
    call_group_status_sequences: callGroupStatusSequences,
    terminal_count_distribution: terminalCountDistribution,
    same_sanitized_terminal_summary_pair_count: sameTerminalSummaryPairCount,
    different_sanitized_terminal_summary_pair_count: differentTerminalSummaryPairCount,
    missing_tool_call_id_event_count: missingIdentityEvents.length,
  }
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
          call_id: p.toolCallId, tool: p.name, started_at_ms: Date.now(),
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
  gate('dsh', [[dsh.status === 'completed' && dsh.exit_code === 0, 'dsh_not_completed_or_nonzero_exit']])
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
    acpTools.set(event.tool_call_id, { tool: event.title || old.tool, status: event.status || old.status,
      tool_call_id: event.tool_call_id, starts: (old.starts || 0) + (event.kind === 'tool_call' ? 1 : 0),
      started_at_ms: event.kind === 'tool_call' ? event.at_ms : old.started_at_ms,
      terminal_updates: (old.terminal_updates || 0) + (event.kind === 'tool_call_update'
        && ['completed', 'failed'].includes(event.status) ? 1 : 0),
      terminal_at_ms: event.kind === 'tool_call_update' && ['completed', 'failed'].includes(event.status)
        ? event.at_ms : old.terminal_at_ms,
      arguments_sha256_24: event.arguments_sha256_24 || old.arguments_sha256_24 })
  }
  const observedCadTools = [...acpTools.values()].filter(row => row.tool?.startsWith('cad_'))
  const acpCadIdentityMissing = acpEventsMissingIdentity.length > 0
  const correlationKey = row => nonempty(row.tool) && /^[a-f0-9]{24}$/.test(row.arguments_sha256_24 || '')
    ? `${row.tool}\u0000${row.arguments_sha256_24}` : null
  const bridgeCallsByKey = new Map(), acpCallsByKey = new Map()
  for (const call of calls) {
    const key = correlationKey(call)
    if (key) bridgeCallsByKey.set(key, [...(bridgeCallsByKey.get(key) || []), call])
  }
  for (const event of observedCadTools) {
    const key = correlationKey(event)
    if (key) acpCallsByKey.set(key, [...(acpCallsByKey.get(key) || []), event])
  }
  // ACP IDs and native UUIDs differ. Join invocations by exact tool name and
  // normalized argument digest in the same run/turn; then bind each bridge UUID
  // independently to its Rust audit row below.
  const pairedCallIds = new Map()
  const argumentsAndIdentityPresent = calls.every(call => correlationKey(call) !== null && nonempty(call.call_id)
    && call.run_id === run.id && call.session_id === run.tool_session_id && call.turn_id === run.turn_id)
    && observedCadTools.every(event => correlationKey(event) !== null && nonempty(event.tool_call_id) && event.starts === 1)
  const allInvocationsPaired = [...new Set([...bridgeCallsByKey.keys(), ...acpCallsByKey.keys()])].every(key => {
    const native = bridgeCallsByKey.get(key) || [], acp = acpCallsByKey.get(key) || []
    if (native.length !== acp.length) return false
    const validIdentity = (call, event) => event?.starts === 1 && nonempty(event.tool_call_id)
      && call.run_id === run.id && call.session_id === run.tool_session_id && call.turn_id === run.turn_id
      && nonempty(call.call_id)
    let pairs
    if (native.length === 1) pairs = [[native[0], acp[0]]]
    else {
      // Repeated identical calls have no identity in common across ACP and the
      // native bridge. Disambiguate only when each bridge interval is wholly
      // contained in exactly one complete ACP invocation interval, and vice
      // versa. Missing, overlapping, or merely ordered timestamps stay ambiguous.
      const candidatesByNative = native.map(call => acp.filter(event => validIdentity(call, event)
        && event.terminal_updates === 1 && Number.isFinite(event.started_at_ms)
        && Number.isFinite(event.terminal_at_ms) && event.terminal_at_ms >= event.started_at_ms
        && Number.isFinite(call.started_at_ms) && Number.isFinite(call.completed_at_ms)
        && call.completed_at_ms >= call.started_at_ms
        && call.started_at_ms >= event.started_at_ms && call.completed_at_ms <= event.terminal_at_ms))
      const candidatesByAcp = acp.map(event => candidatesByNative.flatMap((candidates, nativeIndex) =>
        candidates.includes(event) ? [nativeIndex] : []))
      if (candidatesByNative.some(candidates => candidates.length !== 1)
        || candidatesByAcp.some(candidates => candidates.length !== 1)) return false
      pairs = candidatesByNative.map((candidates, nativeIndex) => [native[nativeIndex], candidates[0]])
    }
    const groupPairs = pairs.map(([call, event]) => {
      if (!validIdentity(call, event)) return null
      return [call.call_id, event]
    })
    if (groupPairs.some(pair => !pair)) return false
    for (const [callId, event] of groupPairs) pairedCallIds.set(callId, event)
    return true
  }) && argumentsAndIdentityPresent && observedCadTools.length === calls.length
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
  if (observedCadTools.some(event => event.terminal_updates > 0 && event.status === 'failed')) {
    acpResultReasons.push('acp_cad_tool_result_failed')
  }
  if (observedCadTools.some(event => event.terminal_updates > 1)) {
    acpResultReasons.push('acp_cad_tool_result_terminal_update_duplicated')
  }
  if (observedCadTools.some(event => event.terminal_updates === 1 && !['completed', 'failed'].includes(event.status))) {
    acpResultReasons.push('acp_cad_tool_result_terminal_status_invalid')
  }
  gates.acp_cad_tool_results = { status: acpResultReasons.length ? 'failed' : 'passed', reasons: acpResultReasons }
  failed.push(...acpResultReasons)
  const preview = domain.last_preview, checks = preview?.local_geometry_checks, finish = domain.finish_summary
  const review = finish?.overall_geometry_review, evidence = review?.result?.evidence
  const { host_attestation: ignoredAttestation, ...unsignedChecks } = checks || {}
  gate('current_geometry', [
    [computedState?.revision === domain.revision && digest(computedState?.geometry_signature), 'rust_postcheck_state_missing_or_stale'],
    [domain.preview_revision === domain.revision && bound(preview) && bound(checks) && bound(finish)
      && bound(review) && bound(domain.latest_geometry_review), 'preview_geometry_finish_revision_or_fingerprint_stale'],
    [preview?.execution_engine === 'rust_c_abi_occt' && checks?.execution_engine === 'rust_c_abi_occt'
      && checks.state === 'passed' && checks.valid === true && checks.closed_solid_count === 1
      && checks.validation?.state === 'valid', 'complete_valid_native_geometry_missing'],
    [domain.partial_preview?.state !== 'candidate_partial_not_accepted', 'partial_preview_not_accepted'],
    [evidence?.geometry?.binding_valid === true && evidence.geometry.passed === true
      && evidence.geometry.host_attestation_valid === true && isDeepStrictEqual(evidence.geometry.checks, checks), 'geometry_attestation_or_gate_binding_invalid'],
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
  gate('holes', [[finish?.hole_measurement?.complete === true && evidence?.holes?.measurement_complete === true
    && (!hasHoles || (checks?.hole_measurement_complete === true && checks.hole_measurements_verified === true)), 'holes_incomplete_or_unverified']])
  const referenceRows = rows(evidence?.references?.original_reference_sources)
  gate('visual', [
    [finish?.reference_coverage === true && finish?.visual_evidence?.all_references_covered === true
      && finish.independent_review_pair_count > 0 && evidence?.references?.visual_coverage_complete === true
      && evidence.references.manifest_valid === true && evidence.references.mismatch_count === 0, 'visual_coverage_incomplete_or_conflicting'],
    [referenceRows.length === 1 && referenceRows[0].id === source.reference_id && referenceRows[0].sha256 === source.sha256
      && referenceRows[0].covered === true && referenceRows[0].inventory_valid === true, 'visual_gate_original_reference_binding_missing'],
    [isDeepStrictEqual(finish?.visual_evidence, computedState?.visual_evidence_summary), 'visual_evidence_not_current_rust_summary'],
  ])
  gate('finish', [
    [domain.finished === true && domain.finish_state === 'accepted' && finish?.finish_state === 'accepted'
      && finish.accepted === true && finish.overall_geometry_review_accepted === true
      && review?.issuer === 'native_review_gate' && review.result?.decision === 'accept', 'host_finish_not_accepted'],
    [finish?.construction_complete === true && finish.open_finding_count === 0
      && Array.isArray(finish.unresolved_findings) && finish.unresolved_findings.length === 0
      && finish.required_external_facts?.failed_count === 0 && finish.required_external_facts?.unknown_count === 0
      && Number.isSafeInteger(finish.required_external_facts?.required_count) && finish.required_external_facts.required_count >= 0
      && finish.required_external_facts?.required_count === finish.required_external_facts?.verified_count, 'finish_findings_construction_or_external_facts_incomplete'],
  ])
  return { schema_version: 1, status: failed.length ? 'failed' : 'passed', gates,
    failure_reasons: [...new Set(failed)], evidence_errors: evidenceErrors, successful_image_request_ids: successfulImageRequests.map(row => row.request_id),
    matched_native_call_ids: matched.map(row => row.call_id),
    scope: 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance' }
}
