import { rmSync } from 'node:fs'

const hasExited = child => child.exitCode != null || child.signalCode != null

export function waitForChildExit(child, timeoutMs) {
  if (hasExited(child)) return Promise.resolve(true)
  return new Promise(resolve => {
    let settled = false
    const finish = exited => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      child.off('exit', onExit)
      child.off('error', onError)
      resolve(exited)
    }
    const onExit = () => finish(true)
    const onError = () => { if (child.pid === undefined) finish(true) }
    const timer = setTimeout(() => finish(hasExited(child)), Math.max(0, timeoutMs))
    child.once('exit', onExit)
    child.once('error', onError)
    if (hasExited(child)) finish(true)
  })
}

export async function waitThenTerminateChild(child, gracefulWaitMs, terminationWaitMs) {
  if (await waitForChildExit(child, gracefulWaitMs)) return true
  try { child.kill() } catch { /* report only the bounded exit result */ }
  return waitForChildExit(child, terminationWaitMs)
}

export async function terminateChildrenAndWait(children, terminationWaitMs) {
  const results = await Promise.all(children.map(async child => {
    if (await waitForChildExit(child, 0)) return true
    try { child.kill() } catch { /* report only the bounded exit result */ }
    return waitForChildExit(child, terminationWaitMs)
  }))
  return results.every(Boolean)
}

export function removeScratchDirectory(path, remove = rmSync) {
  if (!path) return 'not_created'
  try {
    remove(path, { recursive: true, force: true })
    return 'removed'
  } catch {
    return 'failed'
  }
}

export function safeScratchCleanupStatus(childrenExited, cleanupStatus) {
  if (cleanupStatus === 'not_created') return 'not_created'
  return childrenExited === true && cleanupStatus === 'removed' ? 'removed' : 'failed'
}
