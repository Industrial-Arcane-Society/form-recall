import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { apply, pruneStalePreviewImages, safeUsage, stripConversationImages } from '../ai_modeler/agent/dsh_stream_bridge.mjs'

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
      reference_id: 'original-id', source_kind: 'user_reference', source_sha256: 'source-hash',
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

test('conversation image scrub removes references and previews from the actual payload', () => {
  const payload = { messages: [{ role: 'user', content: [
    { type: 'text', text: 'stable reference sha256:abc' }, imagePart('original'),
    { type: 'input_image', image_url: 'data:image/png;base64,AA==' },
  ] }], input: [{ type: 'image', data: 'AA==' }] }
  assert.deepEqual(stripConversationImages(payload), { removed_conversation_images: 3 })
  assert.deepEqual(payload, { messages: [{ role: 'user', content: [
    { type: 'text', text: 'stable reference sha256:abc' },
  ] }], input: [] })
})

test('rewriting a Request preserves the caller init headers and reports image inventory', async () => {
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
    messages: [{ role: 'user', content: [
      imagePart(oldPreview), imagePart(latestPreview), imagePart(original),
      { type: 'image_url', image_url: { url: 'https://images.example.test/remote.png' } },
    ] }],
    max_tokens: 80,
    thinking_budget: null,
  }
  fs.writeFileSync(activeTurnPath, JSON.stringify({
    turn_id: 'test-turn',
    reference_images: [{ id: 'original-id', sha256: 'source-hash' }],
  }))
  fs.writeFileSync(imageMapPath, JSON.stringify({
    [sha256(oldPreview)]: { sources: [{
      reference_id: 'preview-old', source_kind: 'tool_preview', preview_generation: 1,
    }] },
    [sha256(latestPreview)]: { sources: [{
      reference_id: 'preview-current', source_kind: 'tool_preview', preview_generation: 2,
    }] },
    [sha256(original)]: { sources: [{
      reference_id: 'original-id', source_kind: 'user_reference', source_sha256: 'source-hash',
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
      headers: { 'content-type': 'application/json', 'x-original': 'from-request' },
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
    assert.equal(sent.messages[0].content.length, 0)
    let rows = []
    for (let attempt = 0; attempt < 100; attempt += 1) {
      rows = fs.readFileSync(telemetryPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse)
      if (rows.some(row => row.kind === 'provider_wire_response_complete')) break
      await new Promise(resolve => setTimeout(resolve, 10))
    }
    const wireRequest = rows.find(row => row.kind === 'provider_wire_request')
    const completion = rows.find(row => row.kind === 'provider_wire_response_complete')
    assert.equal(wireRequest.image_count, 0)
    assert.equal(wireRequest.image_hash_count, 0)
    assert.deepEqual(wireRequest.images, [])
    assert.equal(wireRequest.request_fields.thinking_budget, null)
    assert.equal(wireRequest.image_context_cleanup.removed_stale_tool_previews, 1)
    assert.equal(wireRequest.image_context_cleanup.removed_conversation_images, 3)
    assert.equal(completion.response_terminator, 'sse_done')
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
