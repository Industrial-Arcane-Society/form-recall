import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'

import { assertImageTransportProof, imageRowsFromOpenAIChat } from './native_agent_loopback_wire_helpers.mjs'

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
const dataImage = bytes => ({ type: 'image_url', image_url: { url: `data:image/png;base64,${bytes.toString('base64')}` } })

test('OpenAI-compatible mock inventory hashes decoded image bytes without preserving the data URI', () => {
  const bytes = Buffer.from('controlled-image')
  const images = imageRowsFromOpenAIChat({ messages: [{ role: 'user', content: [
    { type: 'text', text: 'prompt' }, dataImage(bytes),
  ] }] })
  assert.deepEqual(images, [{ sha256: sha256(bytes), bytes: bytes.length, media_type: 'image/png' }])
})

test('wire proof requires exact input, DSH stream-boundary and main-model request SHA matches', () => {
  const bytes = Buffer.from('controlled-image')
  const hash = sha256(bytes)
  assertImageTransportProof({
    sourceSha: hash,
    sourceBytes: bytes.length,
    model: 'f7-loopback-vision',
    boundaryImages: [{ transport_sha256: hash, source_sha256: hash, byte_length: bytes.length }],
    wireRows: [{ path: '/v1/chat/completions', model: 'f7-loopback-vision', image_count: 1,
      images: [{ sha256: hash, bytes: bytes.length }] }],
  })
})

test('wire proof rejects a missing image and rejects an image from another model', () => {
  const bytes = Buffer.from('controlled-image')
  const hash = sha256(bytes)
  const base = { sourceSha: hash, sourceBytes: bytes.length, model: 'f7-loopback-vision',
    boundaryImages: [{ transport_sha256: hash, source_sha256: hash, byte_length: bytes.length }] }
  assert.throws(() => assertImageTransportProof({ ...base, wireRows: [{ path: '/v1/chat/completions',
    model: 'f7-loopback-vision', image_count: 0, images: [] }] }), /exact input image SHA/)
  assert.throws(() => assertImageTransportProof({ ...base, wireRows: [{ path: '/v1/chat/completions',
    model: 'rust_native_vision', image_count: 1, images: [{ sha256: hash, bytes: bytes.length }] }] }), /configured main DSH model/)
})

test('wire proof rejects a mismatch between the original and DSH stream boundary', () => {
  const bytes = Buffer.from('controlled-image')
  const hash = sha256(bytes)
  assert.throws(() => assertImageTransportProof({
    sourceSha: hash,
    sourceBytes: bytes.length,
    model: 'f7-loopback-vision',
    boundaryImages: [{ transport_sha256: '0'.repeat(64), source_sha256: hash, byte_length: bytes.length }],
    wireRows: [{ path: '/v1/chat/completions', model: 'f7-loopback-vision', image_count: 1,
      images: [{ sha256: hash, bytes: bytes.length }] }],
  }), /DSH stream-boundary image SHA/)
})
