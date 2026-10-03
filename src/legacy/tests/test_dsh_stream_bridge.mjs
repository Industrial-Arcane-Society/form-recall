import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { apply, pruneStalePreviewImages, safeUsage } from '../ai_modeler/agent/dsh_stream_bridge.mjs'

const sha256 = value => createHash('sha256').update(value).digest('hex')
const imagePart = value => ({
  type: 'image_url',
  image_url: { url: `data:image/png;base64,${Buffer.from(value).toString('base64')}` },
})

test('preview pruning removes only mapped stale tool previews', () => {
  const oldPreview = Buffer.from('old-preview')
  const latestPreview = Buffer.from('latest-preview')
  const original = Buffer.from('original-reference')
  const unknown = Buffer.from('unknown-image')
  const payload = {
    messages: [{ role: 'user', content: [
      imagePart(oldPreview), imagePart(latestPreview), imagePart(original), imagePart(unknown),
      { type: 'image_url', image_url: { url: 'https://images.example.test/reference.png' } },
    ] }],
    input: [{ role: 'user', content: [imagePart(oldPreview)] }],
  }
  const transportMap = {
    [sha256(oldPreview)]: { sources: [{
      reference_id: 'preview-old', source_kind: 'tool_preview', preview_generation: 1,
    }] },
    [sha256(latestPreview)]: { sources: [{
      reference_id: 'preview-current', source_kind: 'tool_preview', preview_generation: 2,
    }] },
    [sha256(original)]: { sources: [{
      reference_id: 'original-id', source_kind: 'user_reference', source_sha256: sha256(original),
    }] },
  }

  const stats = pruneStalePreviewImages(payload, transportMap)

  assert.equal(stats.latest_generation, 2)
  assert.equal(stats.removed_stale_tool_previews, 2)
  assert.equal(stats.kept_tool_previews, 1)
  assert.equal(stats.kept_user_references, 1)
  assert.equal(stats.kept_unknown_images, 1)
  assert.deepEqual(payload.messages[0].content.map(part => part.image_url?.url).filter(Boolean), [
    imagePart(latestPreview).image_url.url,
    imagePart(original).image_url.url,
    imagePart(unknown).image_url.url,
    'https://images.example.test/reference.png',
  ])
  assert.deepEqual(payload.input[0].content, [])
})

test('safeUsage omits missing and boolean counts instead of turning them into zero', () => {
  assert.deepEqual(safeUsage({
    prompt_tokens: null,
    completion_tokens: false,
    total_tokens: 12,
    reasoning_tokens: '',
    prompt_tokens_details: { cached_tokens: null, cache_hit_tokens: '4' },
    output_tokens_details: { reasoning_tokens: true },
  }), {
    total_tokens: 12,
    cache_token_details: { 'prompt_tokens_details.cache_hit_tokens': 4 },
  })
  assert.deepEqual(safeUsage({ input_tokens: 0, output_tokens: '2' }), {
    input_tokens: 0, output_tokens: 2,
  })
  assert.deepEqual(safeUsage({ input_tokens: null, prompt_tokens: '8' }), {
    input_tokens: 8,
  })
})

test('DSH stream boundary maps unchanged user image bytes to active-turn references', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-image-source-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const activeTurnPath = path.join(temp, 'active-turn.json')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    activeTurn: process.env.AI_MODELER_ACTIVE_TURN_FILE,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  const imageBytes = Buffer.from('exact-user-image-bytes')
  const imageHash = sha256(imageBytes)
  const patchSymbol = Symbol.for('ai-modeler.provider-fetch-observer.v1')
  const previousPatch = globalThis[patchSymbol]
  const previousFetch = globalThis.fetch
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = ''
    fs.writeFileSync(activeTurnPath, JSON.stringify({
      turn_id: 'image-turn',
      reference_images: [{ id: `sha256:${imageHash}`, sha256: imageHash }],
    }))
    globalThis[patchSymbol] = undefined
    globalThis.fetch = previousFetch
    const handlers = new Map()
    apply({ on(event, handler) { handlers.set(event, handler) } })
    const stream = handlers.get('llm/stream')({ messages: [{ role: 'user', content: [
      imagePart(imageBytes),
    ] }] }, async function * () {
      yield { type: 'text-delta', text: 'response not persisted' }
    })
    for await (const _chunk of stream) {}

    const rows = fs.readFileSync(telemetryPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse)
    const request = rows.find(row => row.kind === 'dsh_request_start')
    assert.equal(request.turn_id, 'image-turn')
    assert.equal(request.request_image_count, 1)
    assert.deepEqual(request.request_images, [{
      transport_sha256: imageHash,
      byte_length: imageBytes.length,
      source_sha256: imageHash,
      source_reference_ids: [`sha256:${imageHash}`],
      source_kind: 'user_reference',
      source_mapping: 'exact-source-hash-match',
      identity_source: 'embedded_image_bytes',
    }])
    assert.equal(JSON.stringify(rows).includes('exact-user-image-bytes'), false)
  } finally {
    globalThis.fetch = previousFetch
    if (previousPatch === undefined) delete globalThis[patchSymbol]
    else globalThis[patchSymbol] = previousPatch
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_ACTIVE_TURN_FILE', previousEnvironment.activeTurn)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})

test('DSH content-addressed ACP attachments map to the exact active-turn source hash', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-attachment-source-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const activeTurnPath = path.join(temp, 'active-turn.json')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    activeTurn: process.env.AI_MODELER_ACTIVE_TURN_FILE,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  const imageBytes = Buffer.from('exact-acp-attachment-image-bytes')
  const imageHash = sha256(imageBytes)
  const patchSymbol = Symbol.for('ai-modeler.provider-fetch-observer.v1')
  const previousPatch = globalThis[patchSymbol]
  const previousFetch = globalThis.fetch
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = ''
    fs.writeFileSync(activeTurnPath, JSON.stringify({
      turn_id: 'attachment-turn',
      // The same image may be attached twice in one turn while retaining a
      // content-addressed identity. Hash provenance must remain exact.
      reference_images: [
        { id: `sha256:${imageHash}`, sha256: imageHash },
        { id: `sha256:${imageHash}`, sha256: imageHash },
      ],
    }))
    globalThis[patchSymbol] = undefined
    globalThis.fetch = previousFetch
    const handlers = new Map()
    apply({ on(event, handler) { handlers.set(event, handler) } })
    const stream = handlers.get('llm/stream')({ messages: [{ role: 'user', content: [{
      type: 'image', attachment: {
        attachmentId: `sha256:${imageHash}`, mediaType: 'image/png', bytes: imageBytes.length,
      },
    }] }] }, async function * () {
      yield { type: 'text-delta', text: 'response not persisted' }
    })
    for await (const _chunk of stream) {}

    const rows = fs.readFileSync(telemetryPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse)
    const request = rows.find(row => row.kind === 'dsh_request_start')
    assert.equal(request.turn_id, 'attachment-turn')
    assert.equal(request.request_image_count, 1)
    assert.equal(request.request_image_hash_count, 1)
    assert.deepEqual(request.request_images, [{
      transport_sha256: imageHash,
      byte_length: imageBytes.length,
      source_sha256: imageHash,
      source_reference_ids: [`sha256:${imageHash}`],
      source_kind: 'user_reference',
      source_mapping: 'exact-dsh-attachment-id-match',
      identity_source: 'dsh_content_addressed_attachment',
    }])
    assert.equal(JSON.stringify(rows).includes('exact-acp-attachment-image-bytes'), false)
  } finally {
    globalThis.fetch = previousFetch
    if (previousPatch === undefined) delete globalThis[patchSymbol]
    else globalThis[patchSymbol] = previousPatch
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_ACTIVE_TURN_FILE', previousEnvironment.activeTurn)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})

test('provider wire preserves prior original and current preview images across text continuation, pruning only provably stale previews', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-bridge-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const activeTurnPath = path.join(temp, 'active-turn.json')
  const imageMapPath = path.join(temp, 'transport-map.json')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    activeTurn: process.env.AI_MODELER_ACTIVE_TURN_FILE,
    imageMap: process.env.AI_MODELER_TRANSPORT_IMAGE_MAP,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  const patchSymbol = Symbol.for('ai-modeler.provider-fetch-observer.v1')
  const previousPatch = globalThis[patchSymbol]
  const previousFetch = globalThis.fetch
  const oldPreview = Buffer.from('old-preview-request')
  const latestPreview = Buffer.from('latest-preview-request')
  const original = Buffer.from('source-image-request')
  const requestPayload = {
    model: 'provider-model',
    messages: [
      { role: 'user', content: [
        { type: 'text', text: 'prior image turn' }, imagePart(original), imagePart(oldPreview),
      ] },
      { role: 'assistant', content: [{ type: 'text', text: 'model response' }] },
      { role: 'user', content: [
        { type: 'text', text: 'continue using the current preview' }, imagePart(latestPreview),
        { type: 'image_url', image_url: { url: 'https://images.example.test/remote.png' } },
      ] },
    ],
    max_tokens: 80,
    thinking_budget: null,
  }
  fs.writeFileSync(activeTurnPath, JSON.stringify({
    turn_id: 'text-continuation-turn',
    // No image was newly attached to this text turn. The prior conversation
    // still contains the original and the exact transport map preserves it.
    reference_images: [],
  }))
  fs.writeFileSync(imageMapPath, JSON.stringify({
    [sha256(oldPreview)]: { sources: [{
      reference_id: 'preview-old', source_kind: 'tool_preview', preview_generation: 1,
    }] },
    [sha256(latestPreview)]: { sources: [{
      reference_id: 'preview-current', source_kind: 'tool_preview', preview_generation: 2,
    }] },
    [sha256(original)]: { sources: [{
      reference_id: 'original-id', source_kind: 'user_reference', source_sha256: sha256(original),
    }, {
      reference_id: 'preview-old-same-bytes', source_kind: 'tool_preview', preview_generation: 1,
    }] },
  }))

  let receivedRequest
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
    process.env.AI_MODELER_TRANSPORT_IMAGE_MAP = imageMapPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = 'https://provider.example.test/v1'
    globalThis[patchSymbol] = undefined
    globalThis.fetch = async input => {
      receivedRequest = input
      const encoder = new TextEncoder()
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(
            'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}\n\n'))
          controller.enqueue(encoder.encode('data: [DONE]\n\n'))
          // Keep the stream open to verify that the observer honors [DONE].
        },
      }), { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }
    apply({ on() {} })

    const request = new Request('https://provider.example.test/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-original': 'from-request',
        authorization: 'Bearer wire-secret-must-not-leak' },
      body: JSON.stringify(requestPayload),
    })
    const controller = new AbortController()
    await globalThis.fetch(request, {
      headers: { 'content-type': 'application/json', 'x-trace': 'from-init' },
      signal: controller.signal,
    })

    assert.equal(receivedRequest.headers.get('x-trace'), 'from-init')
    assert.equal(receivedRequest.signal.aborted, false)
    const sent = JSON.parse(await receivedRequest.text())
    const sentParts = sent.messages.flatMap(message => message.content || [])
    const sentImageBytes = sentParts.filter(part => part.type === 'image_url'
      && part.image_url?.url?.startsWith('data:image/'))
      .map(part => Buffer.from(part.image_url.url.split(',')[1], 'base64'))
    assert.deepEqual(sentImageBytes.map(sha256), [sha256(original), sha256(latestPreview)])
    assert.equal(sent.messages[0].content.some(part => part.type === 'image_url'
      && sha256(Buffer.from(part.image_url.url.split(',')[1], 'base64')) === sha256(original)), true)
    assert.equal(sent.messages[2].content.some(part => part.type === 'image_url'
      && sha256(Buffer.from(part.image_url.url.split(',')[1], 'base64')) === sha256(latestPreview)), true)
    assert.equal(sentParts.some(part => part.image_url?.url === 'https://images.example.test/remote.png'), true)
    let rows = []
    for (let attempt = 0; attempt < 100; attempt += 1) {
      rows = fs.readFileSync(telemetryPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse)
      if (rows.some(row => row.kind === 'provider_wire_response_complete')) break
      await new Promise(resolve => setTimeout(resolve, 10))
    }
    const wireRequest = rows.find(row => row.kind === 'provider_wire_request')
    const completion = rows.find(row => row.kind === 'provider_wire_response_complete')
    assert.equal(wireRequest.image_count_before_cleanup, 4)
    assert.equal(wireRequest.image_hash_count_before_cleanup, 3)
    assert.equal(wireRequest.images_before_cleanup[0].identity_source, 'embedded_image_bytes')
    assert.equal(wireRequest.images_before_cleanup[0].source_kind, 'user_reference')
    assert.equal(wireRequest.images_before_cleanup[0].source_mapping, 'exact-transport-hash-match')
    assert.equal(wireRequest.image_count, 3)
    assert.equal(wireRequest.image_hash_count, 2)
    assert.equal(wireRequest.images[0].byte_length, original.length)
    assert.deepEqual(wireRequest.images.map(row => row.source_kind), [
      'user_reference', 'tool_preview', 'unknown',
    ])
    assert.deepEqual(wireRequest.images.slice(0, 2).map(row => row.transport_sha256), [
      sha256(original), sha256(latestPreview),
    ])
    assert.equal(wireRequest.images[0].source_sha256, sha256(original))
    const telemetryText = fs.readFileSync(telemetryPath, 'utf8')
    for (const sensitive of ['from-init', 'wire-secret-must-not-leak', 'prior image turn',
      'model response', 'data:image/png;base64,']) {
      assert.equal(telemetryText.includes(sensitive), false, `telemetry must omit ${sensitive}`)
    }
    assert.equal(wireRequest.request_fields.thinking_budget, null)
    assert.equal(wireRequest.image_context_cleanup.removed_stale_tool_previews, 1)
    assert.equal(wireRequest.image_context_cleanup.kept_user_references, 1)
    assert.equal(completion.response_terminator, 'sse_done')
    assert.equal(rows.find(row => row.kind === 'provider_wire_response_headers').status_code, 200)
    assert.deepEqual(completion.usage, {
      input_tokens: 10, output_tokens: 3, total_tokens: 13,
    })
  } finally {
    globalThis.fetch = previousFetch
    if (previousPatch === undefined) delete globalThis[patchSymbol]
    else globalThis[patchSymbol] = previousPatch
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_ACTIVE_TURN_FILE', previousEnvironment.activeTurn)
    restore('AI_MODELER_TRANSPORT_IMAGE_MAP', previousEnvironment.imageMap)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})

test('provider wire observation bypasses text-only turns', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-text-turn-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const activeTurnPath = path.join(temp, 'active-turn.json')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    activeTurn: process.env.AI_MODELER_ACTIVE_TURN_FILE,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  const patchSymbol = Symbol.for('ai-modeler.provider-fetch-observer.v1')
  const previousPatch = globalThis[patchSymbol]
  const previousFetch = globalThis.fetch
  let actualRequest = null
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = 'https://provider.example.test/v1'
    fs.writeFileSync(activeTurnPath, JSON.stringify({ turn_id: 'text-only-turn', reference_images: [] }))
    globalThis[patchSymbol] = undefined
    globalThis.fetch = async input => {
      actualRequest = input
      return new Response('{}', { status: 200 })
    }
    apply({ on() {} })
    const request = new Request('https://provider.example.test/v1/responses', {
      method: 'POST', body: JSON.stringify({ model: 'fixture-model', input: 'private prompt' }),
    })
    const response = await globalThis.fetch(request)
    assert.equal(response.status, 200)
    assert.equal(actualRequest, request)
    assert.equal(fs.existsSync(telemetryPath), false)
  } finally {
    globalThis.fetch = previousFetch
    if (previousPatch === undefined) delete globalThis[patchSymbol]
    else globalThis[patchSymbol] = previousPatch
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_ACTIVE_TURN_FILE', previousEnvironment.activeTurn)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})

test('provider image-capability rejection is surfaced with the original image still present on the wire', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-no-vision-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  const patchSymbol = Symbol.for('ai-modeler.provider-fetch-observer.v1')
  const previousPatch = globalThis[patchSymbol]
  const previousFetch = globalThis.fetch
  const original = Buffer.from('original-image-for-non-vision-route')
  const originalHash = sha256(original)
  let providerPayload
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = 'https://provider.example.test/v1'
    globalThis[patchSymbol] = undefined
    globalThis.fetch = async input => {
      providerPayload = JSON.parse(await input.clone().text())
      return new Response(JSON.stringify({
        error: { message: 'This model does not support image input.' },
      }), { status: 400, headers: { 'content-type': 'application/json' } })
    }
    apply({ on() {} })
    const request = new Request('https://provider.example.test/v1/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: 'text-only-model', messages: [{ role: 'user', content: [
        { type: 'text', text: 'Please inspect this image.' }, imagePart(original),
      ] }] }),
    })
    const response = await globalThis.fetch(request)
    assert.equal(response.status, 400)
    assert.match(await response.text(), /does not support image input/)
    const sentImage = providerPayload.messages[0].content.find(part => part.type === 'image_url')
    assert.ok(sentImage, 'the original must not be silently scrubbed before the provider response')
    assert.equal(sha256(Buffer.from(sentImage.image_url.url.split(',')[1], 'base64')), originalHash)
  } finally {
    globalThis.fetch = previousFetch
    if (previousPatch === undefined) delete globalThis[patchSymbol]
    else globalThis[patchSymbol] = previousPatch
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})

test('stream activity is sampled and summarized without saving streamed content', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ai-modeler-stream-activity-'))
  const telemetryPath = path.join(temp, 'observability.jsonl')
  const activeTurnPath = path.join(temp, 'active-turn.json')
  const previousEnvironment = {
    telemetry: process.env.AI_MODELER_STREAM_FILE,
    activeTurn: process.env.AI_MODELER_ACTIVE_TURN_FILE,
    providerBase: process.env.AI_MODELER_PROVIDER_BASE_URL,
  }
  fs.writeFileSync(activeTurnPath, JSON.stringify({ turn_id: 'activity-turn' }))
  const handlers = new Map()
  try {
    process.env.AI_MODELER_STREAM_FILE = telemetryPath
    process.env.AI_MODELER_ACTIVE_TURN_FILE = activeTurnPath
    process.env.AI_MODELER_PROVIDER_BASE_URL = ''
    apply({ on(event, handler) { handlers.set(event, handler) } })

    const stream = handlers.get('llm/stream')({ model: 'test-model' }, async function * () {
      for (let index = 0; index < 2000; index += 1) {
        yield { type: 'reasoning-delta', text: 'PRIVATE THOUGHT' }
      }
      yield { type: 'text-delta', text: 'PRIVATE ANSWER' }
    })
    let consumed = 0
    for await (const _chunk of stream) consumed += 1

    const rows = fs.readFileSync(telemetryPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse)
    const activityRows = rows.filter(row => row.kind === 'stream_activity')
    const summaries = rows.filter(row => row.kind === 'stream_activity_summary')
    assert.equal(consumed, 2001)
    assert.equal(activityRows.length, 2)
    assert.deepEqual(summaries.map(row => [row.activity, row.observed_chunk_events]), [
      ['reasoning', 2000], ['response', 1],
    ])
    assert.ok(summaries.every(row => row.capture_layer === 'llm_stream_middleware'))
    assert.equal(JSON.stringify(rows).includes('PRIVATE THOUGHT'), false)
    assert.equal(JSON.stringify(rows).includes('PRIVATE ANSWER'), false)
  } finally {
    const restore = (name, value) => {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
    restore('AI_MODELER_STREAM_FILE', previousEnvironment.telemetry)
    restore('AI_MODELER_ACTIVE_TURN_FILE', previousEnvironment.activeTurn)
    restore('AI_MODELER_PROVIDER_BASE_URL', previousEnvironment.providerBase)
    fs.rmSync(temp, { recursive: true, force: true })
  }
})
