import { writeSafeBuildStatus } from './native_agent_coldstart_publication.mjs'

// Run an offline Cargo build without allowing process output or error details
// to escape this module. A failed attempt publishes only the fixed status
// projection before the caller enters its fatal-failure path.
export function runOfflineCargoBuild({
  spawnSyncImpl,
  command = 'cargo',
  args,
  cwd,
  output,
  now = Date.now,
  timeout = 20 * 60 * 1000,
}) {
  const startedAt = now()
  let result
  try {
    result = spawnSyncImpl(command, args, {
      cwd,
      encoding: 'utf8',
      windowsHide: true,
      timeout,
      maxBuffer: 8 * 1024 * 1024,
    })
  } catch {
    const status = {
      status: 'failed', exit_code: null, elapsed_ms: Math.max(0, now() - startedAt),
      stdout_bytes: 0, stderr_bytes: 0,
    }
    writeSafeBuildStatus(output, status)
    return status
  }

  const status = {
    status: result?.status === 0 && !result?.error ? 'passed' : 'failed',
    exit_code: !result?.error && Number.isInteger(result?.status) ? result.status : null,
    elapsed_ms: Math.max(0, now() - startedAt),
    stdout_bytes: byteCount(result?.stdout),
    stderr_bytes: byteCount(result?.stderr),
  }
  if (status.status !== 'passed') writeSafeBuildStatus(output, status)
  return status
}

function byteCount(value) {
  if (typeof value === 'string' || Buffer.isBuffer(value)) return Buffer.byteLength(value)
  return 0
}
