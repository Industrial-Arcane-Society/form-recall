import { createHash } from 'node:crypto'
import { closeSync, existsSync, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync,
  readdirSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'

export const VISUAL_REVIEW_SCHEMA = 'aimodeler.native-agent-visual-review.v1'
export const VISUAL_REVIEW_FILES = Object.freeze(['manifest.json', 'model-iso.png', 'reference.png'])
export const VISUAL_REVIEW_CANDIDATE_STATUSES = Object.freeze(['candidate'])

/** Require the ISO artifact to be the complete preview for the current geometry revision. */
export function isCurrentVisualPreview({ lastPreview, revision, generation, fingerprint, isoView, previewArtifact }) {
  const pathMatches = typeof isoView?.preview_image_path === 'string' && typeof previewArtifact?.path === 'string'
    && samePath(isoView.preview_image_path, previewArtifact.path)
  return lastPreview?.state === 'candidate'
    && lastPreview?.partial_candidate_diagnostic_only !== true
    && Number.isSafeInteger(revision) && revision > 0
    && Number.isSafeInteger(generation) && generation > 0
    && lastPreview.revision === revision && lastPreview.preview_generation === generation
    && isoView?.revision === revision && isoView?.preview_generation === generation
    && typeof fingerprint === 'string' && fingerprint.length > 0
    && lastPreview.geometry_fingerprint === fingerprint
    && isoView.geometry_fingerprint === fingerprint
    && typeof previewArtifact?.sha256 === 'string'
    && lastPreview.preview_sha256 === previewArtifact.sha256
    && isoView.preview_sha256 === previewArtifact.sha256
    && pathMatches
    && isoView.partial !== true && isoView.partial_candidate_diagnostic_only !== true
}

const PNG_MAGIC = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const MAX_PNG_BYTES = 20 * 1024 * 1024
const MAX_PNG_SIDE = 16_384
const MAX_PNG_PIXELS = 100_000_000

export class VisualReviewError extends Error {
  constructor(code) {
    super(code)
    this.name = 'VisualReviewError'
    this.code = code
  }
}

function fail(code) {
  throw new VisualReviewError(code)
}

function canonicalDirectory(path, code) {
  if (typeof path !== 'string' || !path.trim()) fail(code)
  try {
    const absolute = resolve(path)
    const linkInfo = lstatSync(absolute)
    if (!linkInfo.isDirectory() || linkInfo.isSymbolicLink()) fail(code)
    const canonical = realpathSync(absolute)
    if (!samePath(canonical, absolute)) fail(code)
    return canonical
  } catch {
    fail(code)
  }
}

function samePath(a, b) {
  const left = resolve(a).replace(/[\\/]+$/, '')
  const right = resolve(b).replace(/[\\/]+$/, '')
  return process.platform === 'win32' ? left.toLowerCase() === right.toLowerCase() : left === right
}

function isInside(root, candidate) {
  const rel = relative(root, candidate)
  return rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`))
}

function sourceFile(path, root, code) {
  if (typeof path !== 'string' || !path.trim()) fail(code)
  try {
    const absolute = resolve(path)
    const info = lstatSync(absolute)
    if (!info.isFile() || info.isSymbolicLink()) fail(code)
    const canonical = realpathSync(absolute)
    if (!isInside(root, canonical)) fail(code)
    const canonicalInfo = lstatSync(canonical)
    if (!canonicalInfo.isFile() || canonicalInfo.isSymbolicLink()) fail(code)
    return readFileSync(canonical)
  } catch {
    fail(code)
  }
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function validateExpectedDigest(value, code) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/i.test(value)) fail(code)
  return value.toLowerCase()
}

function pngDimensions(bytes, code) {
  if (bytes.length < 33 || bytes.length > MAX_PNG_BYTES || !bytes.subarray(0, 8).equals(PNG_MAGIC)) fail(code)
  if (bytes.readUInt32BE(8) !== 13 || bytes.toString('ascii', 12, 16) !== 'IHDR') fail(code)
  const width = bytes.readUInt32BE(16)
  const height = bytes.readUInt32BE(20)
  if (width < 1 || height < 1 || width > MAX_PNG_SIDE || height > MAX_PNG_SIDE || width * height > MAX_PNG_PIXELS) fail(code)
  return { width, height }
}

function checkedPng(path, root, expectedSha, expectedBytes, pathCode, digestCode, pngCode, sizeCode) {
  const expected = validateExpectedDigest(expectedSha, digestCode)
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes < 33 || expectedBytes > MAX_PNG_BYTES) fail(sizeCode)
  const bytes = sourceFile(path, root, pathCode)
  if (bytes.length !== expectedBytes) fail(sizeCode)
  if (sha256(bytes) !== expected) fail(digestCode)
  return { bytes, sha256: expected, bytes_count: bytes.length, dimensions: pngDimensions(bytes, pngCode) }
}

function requiredText(value, code) {
  if (typeof value !== 'string' || !value.trim() || value.length > 512) fail(code)
  return value.trim()
}

function writeOwnedFile(directory, name, contents, ownedFiles) {
  const path = resolve(directory, name)
  let descriptor
  try {
    descriptor = openSync(path, 'wx')
    const identity = fstatSync(descriptor)
    ownedFiles.set(name, { dev: identity.dev, ino: identity.ino })
    writeFileSync(descriptor, contents)
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function cleanupOwnedFiles(directory, ownedFiles, directoryIdentity) {
  let clean = true
  try {
    const info = lstatSync(directory)
    if (!info.isDirectory() || info.isSymbolicLink() || info.dev !== directoryIdentity?.dev ||
      info.ino !== directoryIdentity?.ino || !samePath(realpathSync(directory), directory)) return false
  } catch (error) {
    return error?.code === 'ENOENT'
  }
  for (const [name, identity] of ownedFiles) {
    const path = resolve(directory, name)
    try {
      const current = lstatSync(path)
      if (!current.isFile() || current.isSymbolicLink() || current.dev !== identity.dev || current.ino !== identity.ino) {
        clean = false
        continue
      }
      unlinkSync(path)
    } catch (error) {
      if (error?.code !== 'ENOENT') clean = false
    }
  }
  try {
    rmdirSync(directory)
  } catch (error) {
    if (error?.code !== 'ENOENT') clean = false
  }
  return clean
}

/**
 * Publish only a bounded, path-free visual review packet for one candidate.
 * All source evidence is hashed again and copied only after canonical root checks.
 */
function publishVisualReviewInternal({
  outputRoot,
  referenceRoot,
  scratchRoot,
  referencePath,
  referenceSha256,
  referenceBytes,
  modelPath,
  modelSha256,
  revision,
  previewGeneration,
  geometryFingerprint,
  candidateStatus,
}) {
  const output = canonicalDirectory(outputRoot, 'invalid_output_root')
  const referenceBase = canonicalDirectory(referenceRoot, 'invalid_reference_root')
  const scratchBase = canonicalDirectory(scratchRoot, 'invalid_scratch_root')
  if (!VISUAL_REVIEW_CANDIDATE_STATUSES.includes(candidateStatus)) fail('not_a_candidate')

  const ref = checkedPng(referencePath, referenceBase, referenceSha256, referenceBytes,
    'invalid_reference_path', 'reference_sha256_mismatch', 'invalid_reference_png', 'reference_size_mismatch')
  const modelExpectedSha = validateExpectedDigest(modelSha256, 'model_sha256_mismatch')
  const modelBytes = sourceFile(modelPath, scratchBase, 'invalid_model_path')
  if (modelBytes.length > MAX_PNG_BYTES) fail('invalid_model_png')
  if (sha256(modelBytes) !== modelExpectedSha) fail('model_sha256_mismatch')
  const model = { bytes: modelBytes, sha256: modelExpectedSha, bytes_count: modelBytes.length,
    dimensions: pngDimensions(modelBytes, 'invalid_model_png') }
  const safeRevision = requiredText(revision, 'missing_revision')
  const safeGeneration = requiredText(previewGeneration, 'missing_preview_generation')
  const safeFingerprint = requiredText(geometryFingerprint, 'missing_geometry_fingerprint')

  const reviewDir = resolve(output, 'visual-review')
  if (!isInside(output, reviewDir) || existsSync(reviewDir)) fail('review_output_exists')
  const ownedFiles = new Map()
  let created = false
  let directoryIdentity = null
  try {
    mkdirSync(reviewDir)
    created = true
    const reviewInfo = lstatSync(reviewDir)
    if (!reviewInfo.isDirectory() || reviewInfo.isSymbolicLink() || !samePath(realpathSync(reviewDir), reviewDir)) {
      fail('review_output_invalid')
    }
    directoryIdentity = { dev: reviewInfo.dev, ino: reviewInfo.ino }
    writeOwnedFile(reviewDir, 'reference.png', ref.bytes, ownedFiles)
    writeOwnedFile(reviewDir, 'model-iso.png', model.bytes, ownedFiles)
    const manifest = {
      schema: VISUAL_REVIEW_SCHEMA,
      reference: { sha256: ref.sha256, bytes: ref.bytes_count, width: ref.dimensions.width, height: ref.dimensions.height },
      model: { view: 'iso', sha256: model.sha256, bytes: model.bytes_count, width: model.dimensions.width, height: model.dimensions.height },
      revision: safeRevision,
      preview_generation: safeGeneration,
      geometry_fingerprint: safeFingerprint,
      candidate_status: candidateStatus,
    }
    writeOwnedFile(reviewDir, 'manifest.json', `${JSON.stringify(manifest, null, 2)}\n`, ownedFiles)
    const actual = lstatSync(reviewDir)
    if (!actual.isDirectory() || actual.isSymbolicLink() || !samePath(realpathSync(reviewDir), reviewDir)) fail('review_output_invalid')
    const entries = readdirSync(reviewDir).sort()
    if (JSON.stringify(entries) !== JSON.stringify([...VISUAL_REVIEW_FILES].sort())) fail('review_output_invalid')
    return { status: 'published', code: 'ok', manifest }
  } catch (error) {
    const cleanupSucceeded = !created || cleanupOwnedFiles(reviewDir, ownedFiles, directoryIdentity)
    if (!cleanupSucceeded) fail('publication_cleanup_failed')
    if (error instanceof VisualReviewError) throw error
    fail('publication_failed')
  }
}

/** Public boundary: failures contain only a stable, path-free error code. */
export function publishVisualReviewArtifacts(input) {
  try {
    return publishVisualReviewInternal(input)
  } catch (error) {
    const code = error instanceof VisualReviewError ? error.code : 'publication_failed'
    return { status: 'rejected', code, manifest: null }
  }
}
