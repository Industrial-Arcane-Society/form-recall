import { createInterface } from 'node:readline'

export const NATIVE_PROBE_DIAGNOSTICS = Object.freeze([
  'spawn_error',
  'exit_before_ready',
  'startup_timeout',
  'invalid_or_non_ready_json',
  'ready_success',
])

const isReadyPayload = value => value !== null && typeof value === 'object' && !Array.isArray(value)
  && ['address', 'token', 'session_id', 'schemas_path'].every(key =>
    typeof value[key] === 'string' && value[key].length > 0)

const stopChild = child => {
  try { child?.kill() } catch { /* discard process errors from the safe diagnostic */ }
}

export async function startNativeProbe({ spawnImpl, executable, args, options, timeoutMs = 15_000,
  createInterfaceImpl = createInterface }) {
  let child
  try {
    child = spawnImpl(executable, args, options)
  } catch {
    return { diagnostic: 'spawn_error', child: null, bridge: null }
  }

  try { child.stderr?.resume?.() } catch { /* stderr is intentionally discarded */ }

  let lines
  try {
    lines = createInterfaceImpl({ input: child.stdout })
  } catch {
    stopChild(child)
    return { diagnostic: 'invalid_or_non_ready_json', child, bridge: null, lines: null }
  }

  return new Promise(resolve => {
    let settled = false
    let timer = null
    const finish = (diagnostic, bridge = null) => {
      if (settled) return
      settled = true
      if (timer !== null) clearTimeout(timer)
      if (diagnostic !== 'ready_success') {
        try { lines.close() } catch { /* discard line-reader errors */ }
        stopChild(child)
      }
      resolve({ diagnostic, child, bridge, lines })
    }

    lines.once('line', line => {
      let value
      try { value = JSON.parse(line) } catch {
        finish('invalid_or_non_ready_json')
        return
      }
      if (!isReadyPayload(value)) {
        finish('invalid_or_non_ready_json')
        return
      }
      finish('ready_success', value)
    })
    child.once('error', () => finish('spawn_error'))
    child.once('exit', () => finish('exit_before_ready'))
    timer = setTimeout(() => finish('startup_timeout'), timeoutMs)
  })
}
