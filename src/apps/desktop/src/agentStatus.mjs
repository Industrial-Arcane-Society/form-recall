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
  const accepted = model.status === 'accepted'
    && model.finish_status === 'accepted'
    && finishSummary?.accepted === true
  const geometry = label(model.geometric?.status)
  const visual = label(model.visual?.status)
  return {
    outcome: accepted ? 'accepted' : 'candidate',
    outcomeLabel: accepted ? '已验收' : '候选模型',
    finish: label(model.finish_status),
    geometry: labels.geometric[geometry] || geometry,
    visual: labels.visual[visual] || visual,
  }
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
