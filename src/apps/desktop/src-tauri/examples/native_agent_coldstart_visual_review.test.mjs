import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { isCurrentVisualPreview, publishVisualReviewArtifacts, VISUAL_REVIEW_FILES, VISUAL_REVIEW_SCHEMA } from './native_agent_coldstart_visual_review.mjs'

function makePng(width = 2, height = 3) {
  const bytes = Buffer.alloc(33)
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes, 0)
  bytes.writeUInt32BE(13, 8)
  bytes.write('IHDR', 12, 'ascii')
  bytes.writeUInt32BE(width, 16)
  bytes.writeUInt32BE(height, 20)
  bytes[24] = 8
  bytes[25] = 2
  return bytes
}

function digest(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'visual-review-test-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const referenceRoot = join(root, 'repo')
  const scratchRoot = join(root, 'scratch')
  const outputDir = join(root, 'output')
  mkdirSync(referenceRoot)
  mkdirSync(scratchRoot)
  mkdirSync(outputDir)
  const reference = makePng(640, 480)
  const model = makePng(320, 240)
  const referencePath = join(referenceRoot, 'source.png')
  const modelIsoPath = join(scratchRoot, 'iso.png')
  writeFileSync(referencePath, reference)
  writeFileSync(modelIsoPath, model)
  return {
    root, referenceRoot, scratchRoot, outputDir, reference, model, referencePath, modelIsoPath,
    input: {
    outputRoot: outputDir, referenceRoot, scratchRoot, referencePath,
      referenceSha256: digest(reference), referenceBytes: reference.length,
      modelPath: modelIsoPath, modelSha256: digest(model),
      revision: 'revision-12', previewGeneration: 'preview-generation-7',
      geometryFingerprint: 'sha256:geometry-test-fingerprint', candidateStatus: 'candidate',
    },
  }
}

function expectCode(code, operation) {
  const result = operation()
  assert.equal(result.status, 'rejected')
  assert.equal(result.code, code)
  assert.equal(result.manifest, null)
}

test('accepts only the complete ISO preview bound to the current candidate geometry', () => {
  const previewPath = resolve('run-scratch/preview/iso.png')
  const previewArtifact = { path: previewPath, sha256: 'a'.repeat(64) }
  const fingerprint = 'sha256:geometry-fingerprint'
  const lastPreview = { state: 'candidate', revision: 12, preview_generation: 7,
    geometry_fingerprint: fingerprint, preview_sha256: previewArtifact.sha256 }
  const isoView = { view: 'iso', revision: 12, preview_generation: 7,
    geometry_fingerprint: fingerprint, preview_sha256: previewArtifact.sha256,
    preview_image_path: previewPath }
  const valid = { lastPreview, revision: 12, generation: 7, fingerprint, isoView, previewArtifact }
  assert.equal(isCurrentVisualPreview(valid), true)
  assert.equal(isCurrentVisualPreview({ ...valid, revision: 13 }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, generation: 8 }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, fingerprint: 'sha256:stale' }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, lastPreview: { ...lastPreview, state: 'partial' } }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, isoView: { ...isoView, partial: true } }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, isoView: { ...isoView, preview_generation: 6 } }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, isoView: { ...isoView, preview_sha256: 'b'.repeat(64) } }), false)
  assert.equal(isCurrentVisualPreview({ ...valid, previewArtifact: { ...previewArtifact, path: resolve('run-scratch/other.png') } }), false)
})

test('publishes only two images and a fixed-field manifest for one ISO candidate', t => {
  const fx = fixture(t)
  const result = publishVisualReviewArtifacts(fx.input)
  const dir = join(fx.outputDir, 'visual-review')
  assert.equal(result.status, 'published')
  assert.deepEqual(readdirSync(dir).sort(), [...VISUAL_REVIEW_FILES].sort())
  const manifest = JSON.parse(readFileSync(join(dir, 'manifest.json'), 'utf8'))
  assert.deepEqual(manifest, {
    schema: VISUAL_REVIEW_SCHEMA,
    reference: { sha256: digest(fx.reference), bytes: fx.reference.length, width: 640, height: 480 },
    model: { view: 'iso', sha256: digest(fx.model), bytes: fx.model.length, width: 320, height: 240 },
    revision: 'revision-12', preview_generation: 'preview-generation-7',
    geometry_fingerprint: 'sha256:geometry-test-fingerprint', candidate_status: 'candidate',
  })
  assert.equal(readFileSync(join(dir, 'reference.png')).equals(fx.reference), true)
  assert.equal(readFileSync(join(dir, 'model-iso.png')).equals(fx.model), true)
})

test('rejects source hash and byte count mismatches without publishing', t => {
  const fx = fixture(t)
  expectCode('reference_sha256_mismatch', () => publishVisualReviewArtifacts({ ...fx.input, referenceSha256: '0'.repeat(64) }))
  expectCode('model_sha256_mismatch', () => publishVisualReviewArtifacts({ ...fx.input, modelSha256: '0'.repeat(64) }))
  assert.equal(readdirSync(fx.outputDir).length, 0)
})

test('rejects malformed PNGs and unreasonable IHDR dimensions', t => {
  const fx = fixture(t)
  const badMagic = Buffer.from(fx.reference)
  badMagic[0] = 0
  writeFileSync(fx.referencePath, badMagic)
  expectCode('invalid_reference_png', () => publishVisualReviewArtifacts({
    ...fx.input, referenceSha256: digest(badMagic),
  }))

  const malformed = makePng()
  malformed.writeUInt32BE(12, 8)
  writeFileSync(fx.referencePath, malformed)
  expectCode('invalid_reference_png', () => publishVisualReviewArtifacts({
    ...fx.input, referenceSha256: digest(malformed), referenceBytes: malformed.length,
  }))

  const huge = makePng(0xffffffff, 2)
  writeFileSync(fx.referencePath, huge)
  expectCode('invalid_reference_png', () => publishVisualReviewArtifacts({
    ...fx.input, referenceSha256: digest(huge), referenceBytes: huge.length,
  }))
})

test('rejects source paths outside their canonical roots, including junction escapes', t => {
  const fx = fixture(t)
  const outside = join(fx.root, 'outside')
  mkdirSync(outside)
  const outsideImage = join(outside, 'outside.png')
  writeFileSync(outsideImage, fx.reference)
  expectCode('invalid_reference_path', () => publishVisualReviewArtifacts({ ...fx.input, referencePath: outsideImage }))

  const junction = join(fx.referenceRoot, 'linked')
  try {
    symlinkSync(outside, junction, 'junction')
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES', 'UNKNOWN'].includes(error.code)) {
      t.diagnostic(`junction creation unavailable (${error.code}); direct containment case still covered`)
      return
    }
    throw error
  }
  expectCode('invalid_reference_path', () => publishVisualReviewArtifacts({
    ...fx.input, referencePath: join(junction, 'outside.png'),
  }))
})

test('rejects missing metadata and non-candidate statuses', t => {
  const fx = fixture(t)
  expectCode('missing_revision', () => publishVisualReviewArtifacts({ ...fx.input, revision: '' }))
  expectCode('missing_preview_generation', () => publishVisualReviewArtifacts({ ...fx.input, previewGeneration: null }))
  expectCode('missing_geometry_fingerprint', () => publishVisualReviewArtifacts({ ...fx.input, geometryFingerprint: '  ' }))
  expectCode('not_a_candidate', () => publishVisualReviewArtifacts({ ...fx.input, candidateStatus: 'failed' }))
})

test('refuses existing visual-review output and never overwrites files', t => {
  const fx = fixture(t)
  const reviewDir = join(fx.outputDir, 'visual-review')
  mkdirSync(reviewDir)
  const marker = join(reviewDir, 'keep.txt')
  writeFileSync(marker, 'preserve')
  expectCode('review_output_exists', () => publishVisualReviewArtifacts(fx.input))
  assert.deepEqual(readdirSync(reviewDir), ['keep.txt'])
})

test('requires output root to be a real non-linked directory', t => {
  const fx = fixture(t)
  const outside = join(fx.root, 'external-output')
  mkdirSync(outside)
  const link = join(fx.root, 'output-link')
  try {
    symlinkSync(outside, link, 'junction')
  } catch (error) {
    if (process.platform === 'win32' && ['EPERM', 'EACCES', 'UNKNOWN'].includes(error.code)) {
      t.diagnostic(`junction creation unavailable (${error.code})`)
      return
    }
    throw error
  }
  expectCode('invalid_output_root', () => publishVisualReviewArtifacts({ ...fx.input, outputRoot: link }))
})
