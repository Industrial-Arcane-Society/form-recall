import { mkdirSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { COLDSTART_TOOL_NAME_ALLOWLIST } from './native_agent_coldstart_gates.mjs'
import { sanitizeScratchRecoverySummary } from './native_agent_coldstart_scratch.mjs'
import { safePromptProgressSummary } from './native_agent_coldstart_prompt.mjs'
import { NATIVE_PROBE_DIAGNOSTICS } from './native_agent_coldstart_probe.mjs'

const toolNames = new Set([...COLDSTART_TOOL_NAME_ALLOWLIST, 'unknown'])
const safeCount = value => Number.isSafeInteger(value) && value >= 0 ? value : 0
const ACP_STATUS_SEQUENCES = new Set([
  'no_terminal_status', 'progress_then_completed', 'progress_then_failed',
  'completed_without_progress', 'failed_without_progress', 'update_after_terminal',
  'repeated_same_terminal_status', 'conflicting_terminal_statuses',
])
const GATE_NAMES = Object.freeze([
  'run_scope', 'dsh_session', 'dsh_process', 'trace_integrity', 'original_image', 'main_image_wire',
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
const SAFE_NATIVE_PROBE_DIAGNOSTICS = new Set(NATIVE_PROBE_DIAGNOSTICS)
export const SAFE_PUBLICATION_PHASE = Object.freeze({
  NOT_STARTED: 'not_started',
  PROJECTION: 'projection',
  WRITING_DIAGNOSTICS: 'writing_diagnostics',
  WRITING_BUILD_STATUS: 'writing_build_status',
  WRITING_SUMMARY: 'writing_summary',
  PUBLICATION_COMPLETE: 'publication_complete',
  RUNNER_COMPLETION: 'runner_completion',
})
const FAILURE_PHASES = new Set(Object.values(SAFE_PUBLICATION_PHASE))
const GATE_REASON_CODES = Object.freeze({
  run_scope: new Set(['run_identity_missing', 'manifest_session_or_turn_mismatch',
    'original_reference_manifest_mismatch', 'user_turn_reference_binding_missing']),
  dsh_session: new Set(['dsh_session_not_completed', 'dsh_session_rpc_failed']),
  dsh_process: new Set(['dsh_child_exit_nonzero_or_missing', 'dsh_child_exit_not_observed']),
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
  current_geometry: new Set(['rust_postcheck_revision_stale', 'rust_postcheck_geometry_signature_missing', 'preview_revision_stale',
    'preview_geometry_fingerprint_stale', 'geometry_checks_fingerprint_stale', 'finish_geometry_fingerprint_stale',
    'finish_review_fingerprint_stale', 'latest_review_fingerprint_stale', 'native_geometry_engine_missing',
    'native_geometry_check_state_not_passed', 'native_geometry_validity_false', 'native_geometry_solid_count_invalid', 'native_geometry_validation_invalid',
    'partial_preview_not_accepted', 'geometry_binding_invalid', 'geometry_attestation_failed',
    'geometry_host_attestation_invalid', 'geometry_attested_checks_mismatch', 'finish_geometry_review_differs_from_current_gate']),
  artifacts: new Set(['current_run_artifacts_missing_or_invalid', 'geometry_report_file_stale',
    'published_geometry_report_differs_from_preview_checks', 'published_cadplan_differs_from_current_state',
    'preview_image_hash_mismatch']),
  holes: new Set(['hole_finish_measurement_incomplete', 'hole_evidence_measurement_incomplete',
    'hole_native_measurement_incomplete', 'hole_native_measurement_unverified']),
  visual: new Set(['visual_finish_coverage_incomplete', 'visual_all_references_not_covered',
    'visual_independent_review_missing', 'visual_gate_coverage_incomplete', 'visual_reference_manifest_invalid',
    'visual_reference_mismatch_detected', 'original_reference_inventory_count_invalid', 'original_reference_id_mismatch',
    'original_reference_sha256_mismatch', 'original_reference_not_covered', 'original_reference_inventory_invalid',
    'visual_summary_stale_or_mismatched']),
  finish: new Set(['finish_host_not_finished', 'finish_domain_state_not_accepted', 'finish_summary_state_not_accepted', 'finish_acceptance_flag_false',
    'finish_geometry_review_acceptance_false', 'finish_geometry_review_issuer_invalid', 'finish_geometry_review_decision_not_accept',
    'finish_construction_incomplete', 'finish_open_findings_nonzero', 'finish_unresolved_findings_present',
    'finish_external_fact_required_count_invalid', 'finish_external_facts_unverified', 'finish_external_facts_failed',
    'finish_external_facts_unknown']),
})
const VISUAL_DIAGNOSTIC_CHECKS = Object.freeze({
  finish_coverage: 'visual_finish_coverage_incomplete',
  all_references_covered: 'visual_all_references_not_covered',
  independent_review_present: 'visual_independent_review_missing',
  host_coverage_complete: 'visual_gate_coverage_incomplete',
  manifest_valid: 'visual_reference_manifest_invalid',
  mismatch_free: 'visual_reference_mismatch_detected',
  one_reference_in_inventory: 'original_reference_inventory_count_invalid',
  reference_id_bound: 'original_reference_id_mismatch',
  reference_sha_bound: 'original_reference_sha256_mismatch',
  original_reference_covered: 'original_reference_not_covered',
  original_inventory_valid: 'original_reference_inventory_invalid',
  visual_summary_current: 'visual_summary_stale_or_mismatched',
})
const FINISH_DIAGNOSTIC_CHECKS = Object.freeze({
  host_finished: 'finish_host_not_finished',
  domain_accepted: 'finish_domain_state_not_accepted',
  summary_accepted: 'finish_summary_state_not_accepted',
  accepted_flag: 'finish_acceptance_flag_false',
  geometry_review_accepted: 'finish_geometry_review_acceptance_false',
  review_issuer_valid: 'finish_geometry_review_issuer_invalid',
  review_decision_accept: 'finish_geometry_review_decision_not_accept',
  construction_complete: 'finish_construction_incomplete',
  no_open_findings: 'finish_open_findings_nonzero',
  no_unresolved_findings: 'finish_unresolved_findings_present',
  external_fact_count_valid: 'finish_external_fact_required_count_invalid',
  external_facts_verified: 'finish_external_facts_unverified',
  no_failed_external_facts: 'finish_external_facts_failed',
  no_unknown_external_facts: 'finish_external_facts_unknown',
})
const RPC_METHODS = new Set(['initialize', 'session/new', 'session/set_config_option', 'session/prompt', 'session/close', 'session/cancel'])
const DSH_FAILURE_STAGES = new Set(['session_rpc', 'capability_check', 'reasoning_selection', 'unknown'])
const RPC_ERROR_CLASSES = new Set(['none', 'rpc_error', 'timeout', 'child_exit', 'write_error', 'capability_missing',
  'invalid_response', 'config_selection_failed', 'unknown'])
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
    schema: 'acp_terminal_update_projection_v4',
    tool_status_counts: pickCounts(value?.tool_status_counts, ['tool_name', 'kind', 'status', 'count'], {
      kind: new Set(['start', 'update']), status: new Set(['pending', 'in_progress', 'completed', 'failed', 'other']),
    }),
    repeated_argument_fingerprint_counts: Array.isArray(value?.repeated_argument_fingerprint_counts)
      ? value.repeated_argument_fingerprint_counts.map(row => ({
        tool_name: toolNames.has(row?.tool_name) ? row.tool_name : 'unknown',
        repeated_call_count: safeCount(row?.repeated_call_count),
      })) : [],
    call_group_terminal_status_counts: pickCounts(value?.call_group_terminal_status_counts,
      ['tool_name', 'terminal_status', 'count'], { terminal_status: new Set(['none', 'mixed', 'completed', 'failed']) }),
    call_group_terminal_distribution: pickCounts(value?.call_group_terminal_distribution,
      ['tool_name', 'terminal_category', 'count'], { terminal_category: new Set([
        'no_terminal', 'completed_once', 'failed_once', 'repeated_mixed_terminal_statuses',
        'repeated_same_status_digest_unavailable', 'repeated_same_status_identical_update',
        'repeated_same_status_distinct_updates',
      ]) }),
    call_group_status_sequences: Array.isArray(value?.call_group_status_sequences)
      ? value.call_group_status_sequences.flatMap(row => {
        if (!toolNames.has(row?.tool_name) || !ACP_STATUS_SEQUENCES.has(row?.sequence)
          || !Number.isSafeInteger(row?.count) || row.count < 0) return []
        return [{ tool_name: row.tool_name, sequence: row.sequence, count: row.count }]
      }) : [],
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
    'native_audit_tool_mismatch', 'native_audit_out_of_run', 'native_audit_rejected', 'bridge_dispatch_failed'])
  const dispatchStages = new Set(['pre_executor_validation', 'pre_executor_deadline', 'executor_return', 'response_writeback', 'unknown'])
  const dispatchFailureCodes = new Set(['invalid_params', 'unknown_tool', 'call_conflict', 'deadline_exceeded',
    'executor_failed', 'response_missing', 'response_invalid', 'unknown'])
  const nativeAuditErrorClasses = new Set(['argument_validation', 'state_conflict', 'execution', 'unknown'])
  return {
    schema: 'cordis_binding_projection_v1',
    counts: Array.isArray(value?.counts) ? value.counts.map(row => {
      const stage = ['acp_to_bridge', 'bridge_to_audit', 'bridge_dispatch'].includes(row?.stage) ? row.stage : 'unknown'
      const outcome = outcomes.has(row?.outcome) ? row.outcome : 'tool_name_not_allowlisted'
      return {
        tool_name: toolNames.has(row?.tool_name) ? row.tool_name : 'unknown',
        stage,
        outcome,
        ...(stage === 'bridge_to_audit' && outcome === 'native_audit_rejected'
          ? { error_class: nativeAuditErrorClasses.has(row?.error_class) ? row.error_class : 'unknown' }
          : {}),
        ...(stage === 'bridge_dispatch' && outcome === 'bridge_dispatch_failed' ? {
          dispatch_stage: dispatchStages.has(row?.dispatch_stage) ? row.dispatch_stage : 'unknown',
          failure_code: dispatchFailureCodes.has(row?.failure_code) ? row.failure_code : 'unknown',
        } : {}),
        count: safeCount(row?.count),
      }
    }) : [],
  }
}

function safeVisionReviewProjection(value) {
  const coverageClasses = new Set(['verified', 'estimated', 'partial', 'unobservable', 'unverified', 'unknown'])
  const reviewCount = value => Number.isSafeInteger(value) && value >= 0 && value <= 1_000_000 ? value : null
  return {
    schema: 'vision_review_diagnostics_v1',
    review_count: reviewCount(value?.review_count),
    reviews: Array.isArray(value?.reviews) ? value.reviews.slice(-64).map(row => ({
      required_evidence_count: reviewCount(row?.required_evidence_count),
      mapped_evidence_count: reviewCount(row?.mapped_evidence_count),
      unmapped_evidence_count: reviewCount(row?.unmapped_evidence_count),
      missing_evidence_count: reviewCount(row?.missing_evidence_count),
      unsupported_evidence_count: reviewCount(row?.unsupported_evidence_count),
      out_of_scope_evidence_count: reviewCount(row?.out_of_scope_evidence_count),
      mismatch_evidence_count: reviewCount(row?.mismatch_evidence_count),
      unknown_evidence_count: reviewCount(row?.unknown_evidence_count),
      view_comparable: typeof row?.view_comparable === 'boolean' ? row.view_comparable : null,
      coverage_class: coverageClasses.has(row?.coverage_class) ? row.coverage_class : 'unknown',
      stage_eligible: typeof row?.stage_eligible === 'boolean' ? row.stage_eligible : null,
    })) : [],
  }
}

const safeGate = (name, gate) => ({
  status: gate?.status === 'passed' ? 'passed' : gate?.status === 'failed' ? 'failed' : 'unknown',
  reason_count: Number.isSafeInteger(gate?.reason_count) && gate.reason_count >= 0
    ? gate.reason_count : Array.isArray(gate?.reasons) ? safeCount(gate.reasons.length) : null,
  reason_codes: GATE_REASON_CODES[name]
    ? [...new Set([...(Array.isArray(gate?.reasons) ? gate.reasons : []),
      ...(Array.isArray(gate?.reason_codes) ? gate.reason_codes : [])]
      .filter(reason => GATE_REASON_CODES[name].has(reason)))].sort()
    : [],
})

export function buildSafeStrictGateProjection(strict) {
  const gates = Object.fromEntries(GATE_NAMES.map(name => [name, safeGate(name, strict?.gates?.[name])]))
  return {
    status: strict?.status === 'passed' ? 'passed' : strict?.status === 'failed' ? 'failed' : 'unknown',
    failure_reason_count: Array.isArray(strict?.failure_reasons) ? safeCount(strict.failure_reasons.length) : null,
    evidence_error_count: Array.isArray(strict?.evidence_errors) ? safeCount(strict.evidence_errors.length) : null,
    gates,
  }
}

function sanitizeSafeStrictGateProjection(value) {
  const gates = Object.fromEntries(GATE_NAMES.map(name => [name, safeGate(name, value?.gates?.[name])]))
  return {
    // The overall run is fatal even when the in-memory gate evaluation passed.
    status: 'failed',
    failure_reason_count: Number.isSafeInteger(value?.failure_reason_count) && value.failure_reason_count >= 0
      ? value.failure_reason_count : 1,
    evidence_error_count: Number.isSafeInteger(value?.evidence_error_count) && value.evidence_error_count >= 0
      ? value.evidence_error_count : 0,
    gates,
  }
}

const safePredicateProjection = (gate, predicates, allowedReasons) => {
  const reasons = Array.isArray(gate?.reasons) ? gate.reasons : null
  const checks = Object.fromEntries(Object.entries(predicates).map(([name, reason]) => [
    name, reasons !== null && !reasons.includes(reason),
  ]))
  const unclassifiedFailure = reasons !== null && reasons.some(reason => !allowedReasons.has(reason))
  return {
    status: gate?.status === 'passed' && reasons?.length === 0 ? 'passed' : 'failed',
    failed_check_count: reasons === null ? 0 : safeCount(reasons.length),
    unclassified_failure: unclassifiedFailure,
    checks,
  }
}

export function buildSafeVisualFinishDiagnostics(strict = {}) {
  return {
    schema: 'visual_finish_subconditions_v1',
    visual: safePredicateProjection(strict?.gates?.visual, VISUAL_DIAGNOSTIC_CHECKS,
      new Set(Object.values(VISUAL_DIAGNOSTIC_CHECKS))),
    finish: safePredicateProjection(strict?.gates?.finish, FINISH_DIAGNOSTIC_CHECKS,
      new Set(Object.values(FINISH_DIAGNOSTIC_CHECKS))),
  }
}

export function buildSafeFailureDiagnostic({ stage, phase, code } = {}) {
  return {
    stage: FAILURE_STAGES.has(stage) ? stage : 'unknown',
    phase: FAILURE_PHASES.has(phase) ? phase : 'unknown',
    code: FAILURE_CODES.has(code) ? code : 'uncaught_exception',
  }
}

export function buildSafeDshDiagnostic(value = {}) {
  return {
    stage: DSH_FAILURE_STAGES.has(value.stage) ? value.stage : 'unknown',
    method: RPC_METHODS.has(value.method) ? value.method : 'unknown',
    error_class: value.error_class === undefined ? 'none'
      : RPC_ERROR_CLASSES.has(value.error_class) ? value.error_class : 'unknown',
  }
}

function safeNativeProbeDiagnostic(value) {
  return SAFE_NATIVE_PROBE_DIAGNOSTICS.has(value) ? value : null
}

function createSafeFailureSummary({ stage, phase, code, scratchCleanupStatus, scratchRecovery, strictGates,
  promptProgress, nativeProbeDiagnostic } = {}) {
  return {
    schema_version: 1,
    status: 'failed',
    transport_status: 'failed',
    model_status: 'none',
    status_scope: 'scope_unverified',
    runtime: { node: 'unverified', dsh: 'unverified' },
    scratch_cleanup_status: SCRATCH_CLEANUP_STATUSES.has(scratchCleanupStatus) ? scratchCleanupStatus : 'not_created',
    scratch_recovery: sanitizeScratchRecoverySummary(scratchRecovery),
    ...(promptProgress ? { prompt_progress: safePromptProgressSummary(promptProgress) } : {}),
    native_probe_diagnostic: safeNativeProbeDiagnostic(nativeProbeDiagnostic),
    failure_diagnostic: buildSafeFailureDiagnostic({ stage, phase, code }),
    strict_gates: strictGates ? sanitizeSafeStrictGateProjection(strictGates) : sanitizeSafeStrictGateProjection({}),
  }
}

export function writeSafeFailureSummary(output, failure = {}) {
  mkdirSync(output, { recursive: true })
  const summary = createSafeFailureSummary(failure)
  writeFileSync(join(output, 'summary.json'), JSON.stringify(summary, null, 2), { flag: 'wx', mode: 0o600 })
  return summary
}

// Late fatal errors may supersede only this process's already-published summary.
// A same-directory rename keeps replacement atomic on Windows and POSIX.
export function replacePublishedSafeFailureSummary(output, failure = {}) {
  mkdirSync(output, { recursive: true })
  const target = join(output, 'summary.json')
  const temporary = join(output, `.summary-${randomUUID()}.tmp`)
  const summary = createSafeFailureSummary(failure)
  try {
    writeFileSync(temporary, JSON.stringify(summary, null, 2), { flag: 'wx', mode: 0o600 })
    renameSync(temporary, target)
  } catch (error) {
    try { unlinkSync(temporary) } catch { /* preserve the original failure */ }
    throw error
  }
  return summary
}

export function buildSafeAcceptanceSummary({ strict, transportStatus, modelStatus, runtime, source, scratchCleanupStatus,
  scratchRecovery, dshDiagnostic, promptProgress, nativeProbeDiagnostic }) {
  const strictGateProjection = buildSafeStrictGateProjection(strict)
  const overallPassed = strict?.status === 'passed' && scratchCleanupStatus === 'removed'
  return {
    schema_version: 1,
    status: overallPassed ? 'passed' : 'failed',
    status_scope: strict?.scope === SAFE_SCOPE ? SAFE_SCOPE : 'scope_unverified',
    transport_status: transportStatus === 'completed' ? 'completed' : 'failed',
    model_status: MODEL_STATUSES.has(modelStatus) ? modelStatus : 'none',
    scratch_cleanup_status: SCRATCH_CLEANUP_STATUSES.has(scratchCleanupStatus) ? scratchCleanupStatus : 'not_created',
    scratch_recovery: sanitizeScratchRecoverySummary(scratchRecovery),
    ...(promptProgress ? { prompt_progress: safePromptProgressSummary(promptProgress) } : {}),
    native_probe_diagnostic: safeNativeProbeDiagnostic(nativeProbeDiagnostic),
    dsh_diagnostic: buildSafeDshDiagnostic(dshDiagnostic),
    strict_gates: strictGateProjection,
    visual_finish_diagnostics: buildSafeVisualFinishDiagnostics(strict),
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

export function writeSafeAcceptanceArtifacts(output, {
  summary, acpProjection, bindingProjection, visionProjection, buildStatus, onPhase,
}) {
  mkdirSync(output, { recursive: true })
  const artifacts = [
    ['tool-result-diagnostics.jsonl', SAFE_PUBLICATION_PHASE.WRITING_DIAGNOSTICS, [
      { stage: 'acp_terminal_update_projection', ...safeAcpProjection(acpProjection) },
      { stage: 'cordis_binding_projection', ...safeBindingProjection(bindingProjection) },
      { stage: 'vision_review_projection', ...safeVisionReviewProjection(visionProjection) },
    ].map(row => JSON.stringify(row)).join('\n') + '\n'],
    ['build-status.json', SAFE_PUBLICATION_PHASE.WRITING_BUILD_STATUS, sanitizeSafeBuildStatus(buildStatus)],
    // Publish the summary last so it also serves as the complete-set marker.
    ['summary.json', SAFE_PUBLICATION_PHASE.WRITING_SUMMARY, summary],
  ]
  for (const [name, phase, value] of artifacts) {
    onPhase?.(phase)
    const body = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    writeFileSync(join(output, name), body, { flag: 'wx', mode: 0o600 })
  }
  onPhase?.(SAFE_PUBLICATION_PHASE.PUBLICATION_COMPLETE)
}
