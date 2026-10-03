const labels = {
  geometric: {
    passed: '通过', complete: '通过', stale: '已过期', not_built: '未生成',
    incomplete: '未完成', failed: '失败', unknown: '未知',
  },
  visual: {
    passed: '通过', covered: '已覆盖', partial: '部分复核', incomplete: '未完成',
    not_reviewed: '未复核', stale: '已过期', failed: '失败', unknown: '未知',
  },
}

/** @param {unknown} value */
function label(value) {
  return typeof value === 'string' && value ? value : 'unknown'
}

/** @param {any} model */
export function modelAcceptanceView(model) {
  if (!model || model.exists !== true) {
    return {
      outcome: model?.status === 'unavailable' ? 'unavailable' : 'none',
      outcomeLabel: model?.status === 'unavailable' ? '状态不可读取' : '尚无 CADPlan 模型',
      finish: '未知', geometry: '未知', visual: '未知',
    }
  }

  const finishSummary = model.finish_summary
  const finishCurrent = Number.isInteger(model.revision)
    && finishSummary?.revision === model.revision
    && typeof model.geometry_fingerprint === 'string'
    && model.geometry_fingerprint.length > 0
    && finishSummary?.geometry_fingerprint === model.geometry_fingerprint
  const accepted = model.acceptance_confirmed === true
    && finishCurrent
    && model.status === 'accepted'
    && model.finish_status === 'accepted'
    && finishSummary?.accepted === true
  const geometry = label(model.geometric?.status)
  const visual = label(model.visual?.status)
  const finish = finishCurrent
    ? (!model.acceptance_confirmed && finishSummary?.accepted === true ? '未由主机确认' : label(model.finish_status))
    : finishSummary ? '已过期' : label(model.finish_status)
  return {
    outcome: accepted ? 'accepted' : 'candidate',
    outcomeLabel: accepted ? '已验收' : '候选模型',
    finish,
    geometry: labels.geometric[geometry] || geometry,
    visual: labels.visual[visual] || visual,
  }
}

/** @param {any} document */
export function documentAcceptanceView(document) {
  const finishSummary = document?.finish_summary
  const finishCurrent = Number.isInteger(document?.cad_revision)
    && finishSummary?.revision === document.cad_revision
    && typeof document?.geometry_fingerprint === 'string'
    && document.geometry_fingerprint.length > 0
    && finishSummary?.geometry_fingerprint === document.geometry_fingerprint
  return modelAcceptanceView({
    exists: Array.isArray(document?.features) && document.features.length > 0,
    status: document?.acceptance_confirmed === true && finishCurrent && finishSummary?.accepted === true ? 'accepted' : 'candidate',
    acceptance_confirmed: document?.acceptance_confirmed === true,
    revision: document?.cad_revision,
    geometry_fingerprint: document?.geometry_fingerprint,
    finish_status: finishCurrent ? finishSummary?.finish_state || 'unknown' : finishSummary ? 'stale' : 'not_finished',
    finish_summary: finishSummary,
    geometric: { status: document?.geometric_status || 'unknown' },
    visual: { status: document?.visual_status || 'unknown' },
  })
}

/** @param {unknown} status @param {unknown} stopReason */
export function turnStatusView(status, stopReason) {
  if (status === 'running') return { key: 'running', text: '本轮处理中' }
  if (status === 'completed') return { key: 'completed', text: '本轮回复完成' }
  if (status === 'cancelled') return { key: 'cancelled', text: '本轮已取消' }
  if (status === 'stopped') {
    return { key: 'stopped', text: `本轮已停止${typeof stopReason === 'string' && stopReason ? `（${stopReason}）` : ''}` }
  }
  if (status === 'ended') return { key: 'ended', text: '本轮已结束（未提供终止类型）' }
  if (status === 'error') return { key: 'error', text: '本轮处理失败' }
  return { key: 'idle', text: '尚未开始本轮' }
}
