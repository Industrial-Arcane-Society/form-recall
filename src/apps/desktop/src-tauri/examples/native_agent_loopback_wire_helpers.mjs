import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'

const digest = bytes => createHash('sha256').update(bytes).digest('hex')

export function imageRowsFromOpenAIChat(payload) {
  const images = []
  for (const message of payload?.messages || []) {
    for (const block of Array.isArray(message.content) ? message.content : []) {
      const value = block?.image_url?.url
      const match = typeof value === 'string' && /^data:(image\/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)$/.exec(value)
      if (!match) continue
      const bytes = Buffer.from(match[2], 'base64')
      images.push({ sha256: digest(bytes), bytes: bytes.length, media_type: match[1] })
    }
  }
  return images
}

export function assertImageTransportProof({ sourceSha, sourceBytes, model, boundaryImages, wireRows }) {
  assert.equal(boundaryImages.length, 1, 'single-image acceptance must contain exactly one DSH stream-boundary image')
  assert.ok(boundaryImages.some(image => image.transport_sha256 === sourceSha
    && image.source_sha256 === sourceSha && image.byte_length === sourceBytes),
  'DSH stream-boundary image SHA and size must equal the input')
  assert.equal(wireRows.length, 1, 'loopback must receive exactly one main DSH chat-completions request')
  assert.ok(wireRows.every(row => row.path === '/v1/chat/completions' && row.model === model),
    'wire evidence must belong only to the configured main DSH model')
  assert.ok(wireRows.some(row => row.image_count === 1 && row.images.some(image => image.sha256 === sourceSha
    && image.bytes === sourceBytes)), 'loopback wire must contain the exact input image SHA and byte length')
  assert.ok(wireRows.some(row => row.images.some(image => image.sha256 === sourceSha)
    && boundaryImages.some(boundary => boundary.transport_sha256 === sourceSha)),
  'wire image SHA must match the DSH stream-boundary SHA')
}
