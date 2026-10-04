import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { COLDSTART_TOOL_NAME_ALLOWLIST } from './native_agent_coldstart_gates.mjs'
import { sanitizeScratchRecoverySummary } from './native_agent_coldstart_scratch.mjs'

const toolNames = new Set([...COLDSTART_TOOL_NAME_ALLOWLIST, 'unknown'])
const safeCount = value => Number.isSafeInteger(value) && value >= 0 ? value : 0
const GATE_NAMES = Object.freeze([
  'run_scope', 'dsh', 'trace_integrity', 'original_image', 'main_image_wire',
  'cordis_cad_tools', 'acp_cad_tool_results', 'current_geometry', 'artifacts', 'holes', 'visual', 'finish',
])
const SAFE_SCOPE = 'single_image_single_turn_cli_host_gates_not_independent_visual_or_CATIA_acceptance'
const SAFE_NODE_VERSIONS = new Set(['v22.23.3'])
const SAFE_DSH_VERSIONS = new Set(['0.2.0-rc.2'])
const MODEL_STATUSES = new Set(['accepted_by_strict_cli_gate', 'candidate', 'none'])
const SCRATCH_CLEANUP_STATUSES = new Set(['removed', 'failed', 'not_created'])
const FAILURE_STAGES = new Set(['startup', 'config_validation', 'input_validation', 'runtime_validation',
  'offline_build', 'native_probe', 'dsh_launch', 'dsh_session', 'strict_gate_evaluation', 'publication', 'unknown'])
const FAILURE_CODES = new Set(['uncaught_exception', 'unhandled_rejection'])
const GATE_REASON_CODES = Object.freeze({
  run_scope: new Set(['run_identity_missing', 'manifest_session_or_turn_mismatch',
    'original_reference_manifest_mismatch', 'user_turn_reference_binding_missing']),
  dsh: new Set(['dsh_not_completed_or_nonzero_exit']),
  trace_integrity: new Set(['evidence_trace_missing_or_malformed']),
  original_image: new Set(['original_image_changed_or_invalid']),
  main_image_wire: new Set(['main_model_original_image_wire_missing', 'same_image_request_successful_response_missing']),
  cordis_cad_tools: new Set(['cordis_registration_or_schema_missing', 'successful_cordis_cad_call_and_native_audit_missing',
    'cad_tool_rejected_unbound_or_unaudited', 'cordis_tool_arguments_or_identity_missing_or_unmatched',
    'acp_cad_tool_call_id_missing', 'native_calls_not_individually_observed_or_duplicate',
    'tool_call_rejected_or_failed', 'native_cad_audit_without_cordis_call', 'acp_cad_tool_failed']),
  acp_cad_tool_results: new Set(['acp_cad_tool_result_completion_missing', 'acp_cad_tool_call_id_missing',
    'acp_cad_tool_result_failed',
    'acp_cad_tool_result_terminal_status_invalid']),
  current_geometry: new Set(['rust_postcheck_state_missing_or_stale', 'preview_geometry_finish_revision_or_fingerprint_stale',
    'complete_valid_native_geometry_missing', 'partial_preview_not_accepted', 'geometry_attestation_or_gate_binding_invalid',
    'finish_geometry_review_differs_from_current_gate']),
  artifacts: new Set(['current_run_artifacts_missing_or_invalid', 'geometry_report_file_stale',
    'published_geometry_report_differs_from_preview_checks', 'published_cadplan_differs_from_current_state',
    'preview_image_hash_mismatch']),
  holes: new Set(['holes_incomplete_or_unverified']),
  visual: new Set(['visual_coverage_incomplete_or_conflicting', 'visual_gate_original_reference_binding_missing',
    'visual_evidence_not_current_rust_summary']),
  finish: new Set(['host_finish_not_accepted', 'finish_findings_construction_or_external_facts_incomplete']),
})
const pickCounts = (rows, keys, enums) => Array.isArray(rows) ? rows.map(row => {
  const result = {}
  for (const key of keys) {
    if (key === 'tool_name') result[key] = toolNames.has(row?.[key]) ? row[key] : 'unknown'
    else if (key === 'count') result[key] = safeCount(row?.[key])
    else result[key] = enums[key].has(row?.[key]) ? row[key] : 'other'
  }
  return result
}) : []

function safeAcpProjection(value) {
  return {
    schema: 'acp_terminal_update_projection_v2',
    tool_status_counts: pickCounts(value?.tool_status_counts, ['tool_name', 'kind', 'status', 'count'], {
      kind: new Set(['start', 'update']), status: new Set(['pending', 'in_progress', 'completed', 'failed', 'other']),
    }),
    call_group_terminal_status_counts: pickCounts(value?.call_group_terminal_status_counts,
      ['tool_name', 'terminal_status', 'count'], { terminal_status: new Set(['none', 'mixed', 'completed', 'failed']) }),
    call_group_terminal_distribution: pickCounts(value?.call_group_terminal_distribution,
      ['tool_name', 'terminal_category', 'count'], { terminal_category: new Set([
        'no_terminal', 'completed_once', 'failed_once', 'repeated_mixed_terminal_statuses',
        'repeated_same_status_digest_unavailable', 'repeated_same_status_identical_update',
        'repeated_same_status_distinct_updates',
      ]) }),
    same_terminal_event_pair_count: safeCount(value?.same_terminal_event_pair_count),
    different_terminal_event_pair_count: safeCount(value?.different_terminal_event_pair_count),
    terminal_update_hmac_unavailable_event_count: safeCount(value?.terminal_update_hmac_unavailable_event_count),
    missing_tool_call_id_event_count: safeCount(value?.missing_tool_call_id_event_count),
  }
}

function safeBindingProjection(value) {
  const outcomes = new Set(['matched', 'bridge_call_missing', 'acp_call_missing', 'tool_name_not_allowlisted',
    'correlation_fingerprint_missing', 'invocation_count_mismatch', 'identity_pairing_ambiguous', 'acp_identity_missing',
    'run_scope_mismatch', 'bridge_call_id_missing', 'bridge_scope_rejected', 'bridge_time_invalid',
    'bridge_execution_rejected', 'schema_missing', 'registration_missing', 'native_audit_missing',
    'native_audit_tool_mismatch', 'native_audit_out_of_run', 'native_audit_rejected'])
  return {
    schema: 'cordis_binding_projection_v1',
    counts: Array.isArray(value?.counts) ? value.counts.map(row => ({
      tool_name: toolNames.has(row?.tool_name) ? row.tool_name : 'unknown',
      stage: ['acp_to_bridge', 'bridge_to_audit'].includes(row?.stage) ? row.stage : 'unknown',
      outcome: outcomes.has(row?.outcome) ? row.outcome : 'tool_name_not_allowlisted',
      count: safeCount(row?.count),
    })) : [],
  }
}

const safeGate = (name, gate) => ({
  status: gate?.status === 'passed' ? 'passed' : 'failed',
  reason_count: Array.isArray(gate?.reasons) ? safeCount(gate.reasons.length) : 0,
  reason_codes: Array.isArray(gate?.reasons) && GATE_REASON_CODES[name]
    ? [...new Set(gate.reasons.filter(reason => GATE_REASON_CODES[name].has(reason)))].sort()
    : [],
})

export function buildSafeFailureDiagnostic({ stage, code } = {}) {
  return {
    stage: FAILURE_STAGES.has(stage) ? stage : 'unknown',
    code: FAILURE_CODES.has(code) ? code : 'uncaught_exception',
  }
}

export function writeSafeFailureSummary(output, { stage, code, scratchCleanupStatus, scratchRecovery } = {}) {
  mkdirSync(output, { recursive: true })
  const summary = {
    schema_version: 1,
    status: 'failed',
    transport_status: 'failed',
    model_status: 'none',
    status_scope: 'scope_unverified',
    runtime: { node: 'unverified', dsh: 'unverified' },
    scratch_cleanup_status: SCRATCH_CLEANUP_STATUSES.has(scratchCleanupStatus) ? scratchCleanupStatus : 'not_created',
    scratch_recovery: sanitizeScratchRecoverySummary(scratchRecovery),
    failure_diagnostic: buildSafeFailureDiagnostic({ stage, code }),
    strict_gates: { status: 'failed', failure_reason_count: 1, evidence_error_count: 0, gates: {} },
  }
  writeFileSync(join(output, 'summary.json'), JSON.stringify(summary, null, 2), { flag: 'wx', mode: 0o600 })
  return summary
}

export function buildSafeAcceptanceSummary({ strict, transportStatus, modelStatus, runtime, source, scratchCleanupStatus, scratchRecovery }) {
  const gates = Object.fromEntries(GATE_NAMES.map(name => [name, safeGate(name, strict?.gates?.[name])]))
  const overallPassed = strict?.status === 'passed' && scratchCleanupStatus === 'removed'
  return {
    schema_version: 1,
    status: overallPassed ? 'passed' : 'failed',
    status_scope: strict?.scope === SAFE_SCOPE ? SAFE_SCOPE : 'scope_unverified',
    transport_status: transportStatus === 'completed' ? 'completed' : 'failed',
    model_status: MODEL_STATUSES.has(modelStatus) ? modelStatus : 'none',
    scratch_cleanup_status: SCRATCH_CLEANUP_STATUSES.has(scratchCleanupStatus) ? scratchCleanupStatus : 'not_created',
    scratch_recovery: sanitizeScratchRecoverySummary(scratchRecovery),
    strict_gates: {
      status: strict?.status === 'passed' ? 'passed' : 'failed',
      failure_reason_count: Array.isArray(strict?.failure_reasons) ? safeCount(strict.failure_reasons.length) : 0,
      evidence_error_count: Array.isArray(strict?.evidence_errors) ? safeCount(strict.evidence_errors.length) : 0,
      gates,
    },
    runtime: {
      node: SAFE_NODE_VERSIONS.has(runtime?.node) ? runtime.node : 'unverified',
      dsh: SAFE_DSH_VERSIONS.has(runtime?.dsh) ? runtime.dsh : 'unverified',
    },
    original_image: {
      bytes: Number.isSafeInteger(source?.bytes) ? source.bytes : null,
      sha256: typeof source?.sha256 === 'string' && /^[a-f0-9]{64}$/.test(source.sha256) ? source.sha256 : null,
      unchanged: source?.unchanged === true,
    },
  }
}

export function sanitizeSafeBuildStatus(buildStatus = {}) {
  const passed = buildStatus?.status === 'passed' && buildStatus?.exit_code === 0
  return {
    status: passed ? 'passed' : 'failed',
    exit_code: Number.isInteger(buildStatus?.exit_code) ? buildStatus.exit_code : null,
    elapsed_ms: Number.isFinite(buildStatus?.elapsed_ms) && buildStatus.elapsed_ms >= 0
      ? buildStatus.elapsed_ms : null,
    stdout_bytes: Number.isSafeInteger(buildStatus?.stdout_bytes) && buildStatus.stdout_bytes >= 0
      ? buildStatus.stdout_bytes : 0,
    stderr_bytes: Number.isSafeInteger(buildStatus?.stderr_bytes) && buildStatus.stderr_bytes >= 0
      ? buildStatus.stderr_bytes : 0,
  }
}

export function writeSafeBuildStatus(output, buildStatus) {
  mkdirSync(output, { recursive: true })
  const safeStatus = sanitizeSafeBuildStatus(buildStatus)
  writeFileSync(join(output, 'build-status.json'), JSON.stringify(safeStatus, null, 2),
    { flag: 'wx', mode: 0o600 })
  return safeStatus
}

export function writeSafeAcceptanceArtifacts(output, { summary, acpProjection, bindingProjection, buildStatus }) {
  mkdirSync(output, { recursive: true })
  const artifacts = new Map([
    ['summary.json', summary],
    ['tool-result-diagnostics.jsonl', [
      { stage: 'acp_terminal_update_projection', ...safeAcpProjection(acpProjection) },
      { stage: 'cordis_binding_projection', ...safeBindingProjection(bindingProjection) },
    ].map(row => JSON.stringify(row)).join('\n') + '\n'],
    ['build-status.json', sanitizeSafeBuildStatus(buildStatus)],
  ])
  for (const [name, value] of artifacts) {
    const body = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    writeFileSync(join(output, name), body, { flag: 'wx', mode: 0o600 })
  }
}
