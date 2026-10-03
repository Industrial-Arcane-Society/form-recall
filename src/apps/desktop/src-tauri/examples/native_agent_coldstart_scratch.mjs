import { randomBytes } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'

export const COLDSTART_SCRATCH_PREFIX = 'aimodeler-coldstart-private-'
export const COLDSTART_SCRATCH_SCHEMA = 'aimodeler_coldstart_scratch_owner_v1'
export const COLDSTART_SCRATCH_MAX_AGE_MS = 24 * 60 * 60 * 1000
const OWNER_FILE = '.aimodeler-scratch-owner.json'
const OWNER_MAX_BYTES = 1024
const NONCE_PATTERN = /^[a-f0-9]{64}$/

const safeCount = value => Number.isSafeInteger(value) && value >= 0 ? value : 0
const safeRecoverySummary = counts => ({
  status: counts.failed_count || counts.unknown_owner_count || counts.rejected_count ? 'partial' : 'complete',
  scanned_count: safeCount(counts.scanned_count),
  removed_count: safeCount(counts.removed_count),
  preserved_count: safeCount(counts.preserved_count),
  rejected_count: safeCount(counts.rejected_count),
  unknown_owner_count: safeCount(counts.unknown_owner_count),
  failed_count: safeCount(counts.failed_count),
})

const samePath = (left, right) => process.platform === 'win32'
  ? left.toLowerCase() === right.toLowerCase()
  : left === right

function isPlainDirectory(path, expectedParent) {
  try {
    const absolute = resolve(path)
    const parent = resolve(expectedParent)
    if (!samePath(dirname(absolute), parent)) return false
    const stat = lstatSync(absolute)
    if (!stat.isDirectory() || stat.isSymbolicLink()) return false
    return samePath(realpathSync(absolute), absolute)
  } catch {
    return false
  }
}

function safeRoot(root) {
  try {
    const absolute = resolve(root)
    const stat = lstatSync(absolute)
    return stat.isDirectory() && !stat.isSymbolicLink() && samePath(realpathSync(absolute), absolute)
      ? absolute : null
  } catch {
    return null
  }
}

function parseOwnerMarker(directory, expectedNonce) {
  try {
    const markerPath = join(directory, OWNER_FILE)
    const stat = lstatSync(markerPath)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > OWNER_MAX_BYTES ||
        !samePath(realpathSync(markerPath), markerPath)) return null
    const owner = JSON.parse(readFileSync(markerPath, 'utf8'))
    if (!owner || owner.schema !== COLDSTART_SCRATCH_SCHEMA || !Number.isSafeInteger(owner.pid) || owner.pid < 1 ||
        typeof owner.created_at !== 'string' || !NONCE_PATTERN.test(owner.nonce) || owner.nonce !== expectedNonce) return null
    const createdAt = Date.parse(owner.created_at)
    if (!Number.isFinite(createdAt) || new Date(createdAt).toISOString() !== owner.created_at) return null
    return { pid: owner.pid, createdAt }
  } catch {
    return null
  }
}

function ownerProcessState(pid, processIsAlive) {
  try {
    const state = processIsAlive(pid)
    return state === 'alive' || state === 'dead' ? state : 'unknown'
  } catch (error) {
    return error?.code === 'ESRCH' ? 'dead' : 'unknown'
  }
}

export function createColdstartScratchDirectory({ root = tmpdir(), pid = process.pid, now = Date.now(), random = randomBytes } = {}) {
  const safeTempRoot = safeRoot(root)
  if (!safeTempRoot) throw new Error('Private scratch root is unavailable.')
  if (!Number.isSafeInteger(pid) || pid < 1 || !Number.isFinite(now)) throw new Error('Private scratch owner is invalid.')
  const nonce = random(32).toString('hex')
  if (!NONCE_PATTERN.test(nonce)) throw new Error('Private scratch nonce is invalid.')
  const directory = join(safeTempRoot, COLDSTART_SCRATCH_PREFIX + nonce)
  mkdirSync(directory, { mode: 0o700 })
  const markerPath = join(directory, OWNER_FILE)
  const tempMarkerPath = join(directory, `${OWNER_FILE}.${nonce}.tmp`)
  const marker = JSON.stringify({
    schema: COLDSTART_SCRATCH_SCHEMA,
    pid,
    created_at: new Date(now).toISOString(),
    nonce,
  })
  try {
    writeFileSync(tempMarkerPath, marker, { flag: 'wx', mode: 0o600 })
    renameSync(tempMarkerPath, markerPath)
  } catch {
    try { rmSync(directory, { recursive: true, force: true }) } catch { /* do not expose private paths */ }
    throw new Error('Private scratch owner marker could not be created.')
  }
  return directory
}

export function recoverStaleColdstartScratch({
  root = tmpdir(), now = Date.now(), maxAgeMs = COLDSTART_SCRATCH_MAX_AGE_MS,
  processIsAlive = pid => {
    try { process.kill(pid, 0); return 'alive' } catch (error) { return error?.code === 'ESRCH' ? 'dead' : 'unknown' }
  }, remove = rmSync,
} = {}) {
  const counts = { scanned_count: 0, removed_count: 0, preserved_count: 0,
    rejected_count: 0, unknown_owner_count: 0, failed_count: 0 }
  const safeTempRoot = safeRoot(root)
  if (!safeTempRoot || !Number.isFinite(now) || !Number.isFinite(maxAgeMs) || maxAgeMs < 0) {
    counts.failed_count = 1
    return safeRecoverySummary(counts)
  }

  let entries
  try { entries = readdirSync(safeTempRoot, { withFileTypes: true }) } catch {
    counts.failed_count = 1
    return safeRecoverySummary(counts)
  }
  for (const entry of entries) {
    if (!entry.name.startsWith(COLDSTART_SCRATCH_PREFIX)) continue
    counts.scanned_count += 1
    const candidate = join(safeTempRoot, entry.name)
    const nonce = entry.name.slice(COLDSTART_SCRATCH_PREFIX.length)
    if (!NONCE_PATTERN.test(nonce) || !isPlainDirectory(candidate, safeTempRoot)) {
      counts.rejected_count += 1
      continue
    }
    const owner = parseOwnerMarker(candidate, nonce)
    if (!owner) {
      counts.rejected_count += 1
      continue
    }
    if (now - owner.createdAt < maxAgeMs) {
      counts.preserved_count += 1
      continue
    }
    const processState = ownerProcessState(owner.pid, processIsAlive)
    if (processState === 'alive') {
      counts.preserved_count += 1
      continue
    }
    if (processState === 'unknown') {
      counts.unknown_owner_count += 1
      continue
    }
    // Revalidate immediately before deletion to reject path replacement races.
    if (!isPlainDirectory(candidate, safeTempRoot) || !parseOwnerMarker(candidate, nonce)) {
      counts.rejected_count += 1
      continue
    }
    try {
      remove(candidate, { recursive: true, force: true })
      if (existsSync(candidate)) counts.failed_count += 1
      else counts.removed_count += 1
    } catch {
      counts.failed_count += 1
    }
  }
  return safeRecoverySummary(counts)
}

export function sanitizeScratchRecoverySummary(value) {
  const counts = {
    scanned_count: safeCount(value?.scanned_count),
    removed_count: safeCount(value?.removed_count),
    preserved_count: safeCount(value?.preserved_count),
    rejected_count: safeCount(value?.rejected_count),
    unknown_owner_count: safeCount(value?.unknown_owner_count),
    failed_count: safeCount(value?.failed_count),
  }
  return safeRecoverySummary(counts)
}
