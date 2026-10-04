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
  environment = process.env,
  now = Date.now,
  timeout = 20 * 60 * 1000,
}) {
  const startedAt = now()
  let result
  try {
    const env = buildCargoEnvironment(environment)
    result = spawnSyncImpl(command, args, {
      cwd,
      encoding: 'utf8',
      env,
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

function buildCargoEnvironment(source) {
  const env = { ...source }
  let config = {}
  if (env.TAURI_CONFIG !== undefined) {
    config = JSON.parse(env.TAURI_CONFIG)
    if (!isJsonObject(config)) throw new TypeError('TAURI_CONFIG must be a JSON object')
  }

  const bundle = config.bundle === undefined ? {} : config.bundle
  if (!isJsonObject(bundle)) throw new TypeError('TAURI_CONFIG.bundle must be a JSON object')
  config.bundle = { ...bundle, resources: [] }
  env.TAURI_CONFIG = JSON.stringify(config)
  return env
}

function isJsonObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
