// DSH stream and provider-wire observability for the desktop host.
// The DSH waterfall reports what the adapter receives; the fetch wrapper, when
// the adapter uses Node's global fetch, separately records what actually went
// over the configured provider route. No prompts, image bytes, response text,
// headers, or credentials are persisted.
import fs from 'node:fs'
import { createHash } from 'node:crypto'

export const name = 'ai-modeler-live-stream-bridge'
export const inject = ['agents']

const SAFE_CHUNK_TYPES = new Set(['reasoning-delta', 'thinking-delta', 'text-delta'])
const STREAM_ACTIVITY_SAMPLE_MS = 5000
const FETCH_PATCH = Symbol.for('ai-modeler.provider-fetch-observer.v1')

function append(path, row) {
  if (!path) return
  try {
    fs.appendFileSync(path, JSON.stringify({ at: Date.now(), ...row }) + '\n', 'utf8')
  } catch (error) {
    console.error('[ai-modeler-live-stream-bridge] telemetry write failed:', error?.message || error)
  }
}

function readJson(path, fallback = {}) {
  if (!path) return fallback
  try { return JSON.parse(fs.readFileSync(path, 'utf8')) } catch { return fallback }
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function imageBytesFromUrl(value) {
  if (typeof value !== 'string') return null
  const match = /^data:(image\/(?:png|jpe?g|webp|gif));base64,([a-z0-9+/=\s]+)$/i.exec(value)
  if (!match) return null
  try { return Buffer.from(match[2], 'base64') } catch { return null }
}

function collectImages(value, output = [], key = '') {
  if (!value) return output
  if (typeof value === 'string') {
    if (/^(?:image_url|image|url|data)$/i.test(key)) {
      const bytes = imageBytesFromUrl(value)
      if (bytes?.length) output.push(bytes)
    }
    return output
  }
  if (value instanceof Uint8Array && /^(?:image|data)$/i.test(key)) {
    if (value.byteLength) output.push(Buffer.from(value))
    return output
  }
  if (Array.isArray(value)) {
    for (const child of value) collectImages(child, output, key)
    return output
  }
  if (typeof value === 'object') {
    for (const [childKey, child] of Object.entries(value)) collectImages(child, output, childKey)
  }
  return output
}

function imagePartBytes(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const kind = String(value.type || '').toLowerCase()
  if (!['image', 'image_url', 'input_image'].includes(kind)) return []
  return collectImages(value)
}

function imagePartIdentity(value) {
  const byteVariants = imagePartBytes(value)
  if (byteVariants.length === 1) {
    const bytes = byteVariants[0]
    return { sha256: sha256(bytes), byte_length: bytes.byteLength, identity_source: 'embedded_image_bytes' }
  }
  // ACP persists accepted image bytes before they enter the DSH LLM stream.
  // The attachment ID is itself the SHA-256 of the admitted bytes.
  const attachmentId = value?.attachment?.attachmentId ?? value?.attachmentId
  const match = typeof attachmentId === 'string' ? /^sha256:([a-f0-9]{64})$/i.exec(attachmentId) : null
  if (match) {
    return {
      sha256: match[1].toLowerCase(),
      byte_length: knownNonNegativeCount(value?.attachment?.bytes),
      identity_source: 'dsh_content_addressed_attachment',
    }
  }
  return { sha256: null, byte_length: null, identity_source: 'unavailable' }
}

function imageEvidenceRows(parts, referenceImages = [], transportMap = {}) {
  return parts.map(part => {
    const identity = imagePartIdentity(part)
    const digest = identity.sha256
    const references = digest ? referenceImages.filter(row =>
      row && typeof row === 'object' && (row.sha256 === digest || row.id === `sha256:${digest}`)) : []
    const mapValue = digest ? transportMap[digest] : null
    const mapRows = Array.isArray(mapValue) ? mapValue.map(reference_id => ({ reference_id }))
      : Array.isArray(mapValue?.sources) ? mapValue.sources : []
    const userSources = mapRows.filter(row => row && row.source_kind === 'user_reference')
    const previewSources = mapRows.filter(row => row && row.source_kind === 'tool_preview')
    const ids = [...new Set([
      ...references.map(row => row.id),
      ...mapRows.map(row => row.reference_id),
    ].filter(Boolean))]
    const sourceHashes = [...new Set(userSources.map(row => row.source_sha256)
      .filter(value => typeof value === 'string'))]
    let sourceMapping = 'unknown'
    if (references.length > 0) {
      sourceMapping = identity.identity_source === 'dsh_content_addressed_attachment'
        ? 'exact-dsh-attachment-id-match' : 'exact-source-hash-match'
    } else if (mapRows.length) {
      sourceMapping = 'exact-transport-hash-match'
    }
    const unique = field => [...new Set(mapRows.map(row => row[field])
      .filter(value => value !== undefined && value !== null))]
    return {
      transport_sha256: digest,
      byte_length: identity.byte_length,
      source_sha256: references.length > 0 ? digest : sourceHashes.length === 1 ? sourceHashes[0] : null,
      source_reference_ids: ids,
      source_kind: references.length || userSources.length ? 'user_reference'
        : previewSources.length ? 'tool_preview'
        : mapRows.length ? 'mapped_other' : 'unknown',
      source_mapping: sourceMapping,
      identity_source: identity.identity_source,
      ...(mapRows.length ? {
        roi_norm: mapRows.length === 1 ? mapRows[0].roi_norm ?? null : null,
        preview_revisions: unique('revision'),
        preview_generations: unique('preview_generation'),
        preview_views: unique('view'),
        preview_geometry_signatures: unique('geometry_signature'),
        partial_preview: previewSources.some(row => row.partial === true),
      } : {}),
    }
  })
}

function collectImageParts(value, output = []) {
  if (Array.isArray(value)) {
    for (const child of value) collectImageParts(child, output)
    return output
  }
  if (!value || typeof value !== 'object') return output
  const kind = String(value.type || '').toLowerCase()
  if (['image', 'image_url', 'input_image'].includes(kind)) {
    output.push(value)
    return output
  }
  for (const child of Object.values(value)) collectImageParts(child, output)
  return output
}

function hasProtectedReference(rows) {
  return rows.some(row => {
    // Legacy maps stored arrays of user reference IDs without source metadata.
    if (typeof row === 'string') return true
    if (!row || typeof row !== 'object') return false
    return row.source_kind === 'user_reference' ||
      (Boolean(row.reference_id) && row.source_kind !== 'tool_preview')
  })
}

function knownNonNegativeCount(value) {
  if (typeof value === 'number') {
    return Number.isFinite(value) && value >= 0 ? value : null
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
  }
  return null
}

function previewRowsForHash(transportMap, bytes) {
  const value = transportMap?.[sha256(bytes)]
  const rows = Array.isArray(value) ? value.map(reference_id => ({ reference_id }))
    : Array.isArray(value?.sources) ? value.sources : []
  return rows.filter(row => row && typeof row === 'object' && row.source_kind === 'tool_preview')
}

export function pruneStalePreviewImages(payload, transportMap) {
  const previewGenerations = Object.values(transportMap || {}).flatMap(value => {
    const rows = Array.isArray(value) ? [] : Array.isArray(value?.sources) ? value.sources : []
    return rows.filter(row => row?.source_kind === 'tool_preview')
      .map(row => Number(row.preview_generation ?? row.generation))
      .filter(value => Number.isSafeInteger(value) && value > 0)
  })
  const latestGeneration = previewGenerations.length ? Math.max(...previewGenerations) : null
  const stats = { latest_generation: latestGeneration, removed_stale_tool_previews: 0,
    kept_tool_previews: 0, kept_user_references: 0, kept_unknown_images: 0 }
  if (latestGeneration === null) return stats

  const shouldRemove = bytes => {
    const rows = previewRowsForHash(transportMap, bytes)
    if (!rows.length) {
      const mapValue = transportMap?.[sha256(bytes)]
      const refs = Array.isArray(mapValue) ? mapValue : mapValue?.sources || []
      if (hasProtectedReference(refs)) {
        stats.kept_user_references += 1
      } else {
        stats.kept_unknown_images += 1
      }
      return false
    }
    const mapValue = transportMap?.[sha256(bytes)]
    const allRows = Array.isArray(mapValue) ? mapValue : mapValue?.sources || []
    if (hasProtectedReference(allRows)) {
      stats.kept_user_references += 1
      return false
    }
    const generations = rows.map(row => Number(row.preview_generation ?? row.generation))
      .filter(value => Number.isSafeInteger(value) && value > 0)
    // Unknown provenance is retained. Only remove an exact-hash tool preview
    // whose complete mapping proves that it predates the newest generation.
    if (!generations.length) {
      stats.kept_unknown_images += 1
      return false
    }
    if (generations.some(value => value === latestGeneration)) {
      stats.kept_tool_previews += 1
      return false
    }
    stats.removed_stale_tool_previews += 1
    return true
  }

  const visit = value => {
    if (Array.isArray(value)) {
      for (let index = value.length - 1; index >= 0; index -= 1) {
        const child = value[index]
        const bytes = imagePartBytes(child)
        if (bytes.length === 1 && shouldRemove(bytes[0])) {
          value.splice(index, 1)
          continue
        }
        visit(child)
      }
    } else if (value && typeof value === 'object') {
      for (const child of Object.values(value)) visit(child)
    }
  }
  visit(payload?.messages)
  visit(payload?.input)
  return stats
}

function requestPayload(request, init) {
  if (typeof init?.body === 'string') {
    try { return JSON.parse(init.body) } catch { return null }
  }
  if (init?.body instanceof Uint8Array) {
    try { return JSON.parse(Buffer.from(init.body).toString('utf8')) } catch { return null }
  }
  if (request instanceof Request && request.body) {
    // Body inspection is asynchronous and must not delay or consume fetch.
    return request.clone().text().then(text => {
      try { return JSON.parse(text) } catch { return null }
    }).catch(() => null)
  }
  return null
}

export function safeUsage(usage) {
  if (!usage || typeof usage !== 'object') return null
  const pairs = [
    ['input_tokens', [usage.input_tokens, usage.prompt_tokens, usage.inputTokens]],
    ['output_tokens', [usage.output_tokens, usage.completion_tokens, usage.outputTokens]],
    ['total_tokens', [usage.total_tokens, usage.totalTokens]],
    ['reasoning_tokens', [usage.reasoning_tokens,
      usage.completion_tokens_details?.reasoning_tokens, usage.reasoningTokens]],
  ]
  const result = {}
  for (const [name, values] of pairs) {
    for (const value of values) {
      const count = knownNonNegativeCount(value)
      if (count !== null) {
        result[name] = count
        break
      }
    }
  }
  // Preserve only recognized numeric cache counters. Providers use several
  // OpenAI-compatible names; keep their exact field keys for auditability.
  const cacheFields = new Set([
    'cached_tokens', 'cache_read_tokens', 'cache_write_tokens',
    'cache_creation_tokens', 'cache_read_input_tokens',
    'cache_write_input_tokens', 'cache_creation_input_tokens',
    'cached_input_tokens', 'cache_hit_tokens', 'cache_miss_tokens',
  ])
  const cacheDetails = {}
  for (const section of ['prompt_tokens_details', 'input_tokens_details',
    'completion_tokens_details', 'output_tokens_details', 'cache_details']) {
    const value = usage[section]
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    for (const [field, count] of Object.entries(value)) {
      const numericCount = knownNonNegativeCount(count)
      if (cacheFields.has(field) && numericCount !== null) {
        cacheDetails[`${section}.${field}`] = numericCount
      }
    }
  }
  for (const field of cacheFields) {
    const count = knownNonNegativeCount(usage[field])
    if (count !== null) cacheDetails[`usage.${field}`] = count
  }
  if (Object.keys(cacheDetails).length) result.cache_token_details = cacheDetails
  return Object.keys(result).length ? result : null
}

export function safeTransportError(error) {
  // Keep diagnostics useful without serializing arbitrary error text (which can
  // contain a URL, query parameters, request headers, or provider data).
  const codes = []
  const names = []
  const categories = []
  let current = error
  for (let depth = 0; current && depth < 4; depth += 1, current = current.cause) {
    const name = String(current.name || '').slice(0, 80)
    if (name && !names.includes(name)) names.push(name)
    const code = String(current.code || current.errno || '')
    if (/^[A-Z0-9_:-]{1,64}$/i.test(code) && !codes.includes(code)) codes.push(code)
    const message = String(current.message || '').toLowerCase()
    const category = /certificate|self.signed|tls|ssl/.test(message) ? 'tls'
      : /timeout|timed out|time out/.test(message) ? 'timeout'
      : /abort/.test(message) ? 'aborted'
      : /dns|enotfound|name resolution/.test(message) ? 'dns'
      : /socket|connect|network|fetch failed/.test(message) ? 'connection'
      : ''
    if (category && !categories.includes(category)) categories.push(category)
  }
  return {
    error_name: names[0] || 'Error',
    cause_names: names.slice(1),
    error_codes: codes,
    error_categories: categories,
  }
}

function providerTarget() {
  try { return new URL(process.env.AI_MODELER_PROVIDER_BASE_URL || '') } catch { return null }
}

function installFetchObserver() {
  if (globalThis[FETCH_PATCH] || typeof globalThis.fetch !== 'function') return
  const originalFetch = globalThis.fetch.bind(globalThis)
  const target = providerTarget()
  const telemetryPath = process.env.AI_MODELER_STREAM_FILE
  const activeTurnPath = process.env.AI_MODELER_ACTIVE_TURN_FILE
  const imageMapPath = process.env.AI_MODELER_TRANSPORT_IMAGE_MAP
  const expectedModel = process.env.AI_MODELER_PROVIDER_MODEL || null
  if (!target) return

  globalThis.fetch = async function observedFetch(input, init) {
    let observedInput = input
    let observedInit = init
    let url
    try { url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url) } catch {}
    const matches = Boolean(url && url.host === target.host && /\/(?:chat\/completions|responses)(?:$|\/|\?)/i.test(url.pathname + url.search))
    if (!matches) return originalFetch(input, init)

    const activeTurn = readJson(activeTurnPath, {})
    const payloadValue = requestPayload(input, init)
    const payload = payloadValue && typeof payloadValue.then === 'function' ? await payloadValue : payloadValue
    const inboundImageParts = payload === null ? [] : collectImageParts(payload?.messages || payload?.input || [])
    // Text continuations can resend images from earlier turns, so inspect the
    // serialized payload and observe only requests that actually carry images.
    // Text-only turns continue untouched and generate no provider-wire record.
    if (payload !== null && inboundImageParts.length === 0) return originalFetch(input, init)
    if (payload === null) {
      append(telemetryPath, {
        kind: 'provider_wire_request', wire_observed: false,
        provider_host: url.host, protocol_path: url.pathname,
        image_count: null, image_inventory_source: 'unavailable',
        status: 'blocked-uninspectable-body',
      })
      throw new Error('拒绝发送无法解析的DSH provider请求体；无法证明其中不含图片。')
    }
    const transportMap = readJson(imageMapPath, {})
    const inboundImageRows = imageEvidenceRows(inboundImageParts, activeTurn.reference_images, transportMap)
    const imageContextCleanup = pruneStalePreviewImages(payload, transportMap)
    if (imageContextCleanup.removed_stale_tool_previews > 0) {
      const body = JSON.stringify(payload)
      if (typeof init?.body === 'string') {
        observedInit = { ...init, body }
      } else if (init?.body instanceof Uint8Array) {
        observedInit = { ...init, body: Buffer.from(body, 'utf8') }
      } else if (input instanceof Request) {
        const headers = new Headers(init?.headers || input.headers)
        headers.delete('content-length')
        observedInput = new Request(input, { ...(init || {}), body, headers })
        observedInit = undefined
      } else {
        // Do not alter opaque/streaming bodies when the SDK did not expose a
        // replayable request. Keep the evidence and mark that pruning was not
        // applied at this transport layer.
        imageContextCleanup.transport_body_rewrite = 'unavailable'
        throw new Error('拒绝发送含图片但无法安全重写的DSH provider请求。')
      }
    }
    const requestImages = collectImageParts(payload?.messages || payload?.input || [])
    const imageRows = imageEvidenceRows(requestImages, activeTurn.reference_images, transportMap)
    const requestId = `${activeTurn.turn_id || 'turn-unknown'}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`
    const startedAt = Date.now()
    append(telemetryPath, {
      kind: 'provider_wire_request', request_id: requestId,
      turn_id: activeTurn.turn_id || null,
      wire_observed: true,
      provider_host: url.host,
      protocol_path: url.pathname,
      model: payload?.model || expectedModel,
      request_fields: {
        reasoning_effort: payload?.reasoning_effort ?? payload?.reasoning?.effort ?? null,
        enable_thinking: typeof payload?.enable_thinking === 'boolean' ? payload.enable_thinking : null,
        thinking_budget: knownNonNegativeCount(payload?.thinking_budget),
        output_token_fields: Object.fromEntries(
          ['max_tokens', 'max_completion_tokens', 'max_output_tokens']
            .flatMap(field => {
              const count = knownNonNegativeCount(payload?.[field])
              return count === null ? [] : [[field, count]]
            })),
        stream: payload?.stream === true,
      },
      image_count_before_cleanup: inboundImageParts.length,
      image_hash_count_before_cleanup: inboundImageRows.filter(row => row.transport_sha256).length,
      images_before_cleanup: inboundImageRows,
      image_count: payload === null ? null : requestImages.length,
      image_hash_count: payload === null ? null : imageRows.filter(row => row.transport_sha256).length,
      image_inventory_source: payload === null ? 'unavailable' : 'parsed_provider_payload',
      images: imageRows,
      image_context_cleanup: imageContextCleanup,
      capture_layer: 'node_global_fetch',
      status: 'request-sent',
    })

    let response
    try {
      response = await originalFetch(observedInput, observedInit)
    } catch (error) {
      append(telemetryPath, {
        kind: 'provider_wire_error', request_id: requestId,
        turn_id: activeTurn.turn_id || null,
        elapsed_ms: Date.now() - startedAt,
        ...safeTransportError(error),
      })
      throw error
    }

    append(telemetryPath, {
      kind: 'provider_wire_response_headers', request_id: requestId,
      turn_id: activeTurn.turn_id || null,
      status_code: response.status,
      headers_elapsed_ms: Date.now() - startedAt,
    })
    if (response.body) observeResponseBody(response.clone(), {
      telemetryPath, requestId, turnId: activeTurn.turn_id || null, startedAt,
    })
    return response
  }
  globalThis[FETCH_PATCH] = true
}

function observeResponseBody(response, meta) {
  void (async () => {
    const reader = response.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let pending = ''
    let firstDataAt = null
    let latestUsage = null
    let finishReason = null
    let responseTerminator = null
    const consumeLine = line => {
      if (!line.startsWith('data:')) return false
      const data = line.slice(5).trim()
      if (!data) return false
      if (data === '[DONE]') {
        responseTerminator = 'sse_done'
        return true
      }
      try {
        const event = JSON.parse(data)
        const usage = safeUsage(event.usage || event.response?.usage || event.data?.usage)
        if (usage) latestUsage = usage
        const finish = event.choices?.[0]?.finish_reason ??
          event.response?.choices?.[0]?.finish_reason ??
          event.data?.choices?.[0]?.finish_reason
        if (finish !== undefined && finish !== null) finishReason = String(finish).slice(0, 80)
        const kind = String(event.type || event.event || event.event_type || '')
        if (['response.completed', 'response.failed', 'response.incomplete'].includes(kind)) {
          responseTerminator = kind
          return true
        }
      } catch {}
      return false
    }
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        if (firstDataAt === null) firstDataAt = Date.now()
        pending += decoder.decode(value, { stream: true })
        const lines = pending.split(/\r?\n/)
        pending = lines.pop() || ''
        let terminated = false
        for (const line of lines) {
          if (consumeLine(line)) {
            terminated = true
            break
          }
        }
        if (terminated) {
          // Stop the observer branch promptly after the protocol terminal
          // marker; the DSH branch independently consumes the response.
          void reader.cancel().catch(() => {})
          break
        }
      }
      if (!responseTerminator && pending) consumeLine(pending)
      append(meta.telemetryPath, {
        kind: 'provider_wire_response_complete', request_id: meta.requestId,
        turn_id: meta.turnId,
        first_body_event_ms: firstDataAt === null ? null : firstDataAt - meta.startedAt,
        total_response_ms: Date.now() - meta.startedAt,
        usage: latestUsage,
        usage_source: latestUsage ? 'provider_response_body' : 'unknown',
        finish_reason: finishReason,
        response_terminator: responseTerminator || 'body_end',
      })
    } catch (error) {
      append(meta.telemetryPath, {
        kind: 'provider_wire_response_read_error', request_id: meta.requestId,
        turn_id: meta.turnId, error_name: String(error?.name || 'Error').slice(0, 80),
      })
    } finally {
      try { reader.releaseLock() } catch {}
    }
  })()
}

export function apply(ctx) {
  const target = process.env.AI_MODELER_STREAM_FILE
  if (!target) return
  installFetchObserver()

  // DSH may emit a stream event for every delta (often several thousand per
  // request). Persist activity transitions and sparse heartbeats instead of
  // synchronously appending one JSONL row per delta. Counts are explicitly
  // bridge-observed chunk events, not provider token usage.
  const activityStates = new Map()
  const activityKey = (turnId, captureLayer) =>
    `${turnId || 'turn-unknown'}:${captureLayer}`
  const flushActivity = (turnId, captureLayer, summaryReason) => {
    const key = activityKey(turnId, captureLayer)
    const state = activityStates.get(key)
    if (!state) return
    append(target, {
      kind: 'stream_activity_summary', turn_id: turnId || null,
      activity: state.activity, capture_layer: captureLayer,
      observed_chunk_events: state.observedChunkEvents,
      elapsed_ms: Date.now() - state.startedAt,
      summary_reason: summaryReason,
    })
    activityStates.delete(key)
  }
  const recordActivity = (turnId, captureLayer, chunkType) => {
    const key = activityKey(turnId, captureLayer)
    const now = Date.now()
    const activity = chunkType === 'reasoning-delta' || chunkType === 'thinking-delta'
      ? 'reasoning' : 'response'
    let state = activityStates.get(key)
    if (state && state.activity !== activity) {
      flushActivity(turnId, captureLayer, 'activity_changed')
      state = null
    }
    if (!state) {
      state = { activity, startedAt: now, lastSampleAt: null, observedChunkEvents: 0 }
      activityStates.set(key, state)
    }
    state.observedChunkEvents += 1
    if (state.lastSampleAt === null || now - state.lastSampleAt >= STREAM_ACTIVITY_SAMPLE_MS) {
      append(target, {
        kind: 'stream_activity', turn_id: turnId || null,
        activity, capture_layer: captureLayer,
        observed_chunk_events: state.observedChunkEvents,
        phase_elapsed_ms: now - state.startedAt,
      })
      state.lastSampleAt = now
    }
  }

  ctx.on('llm/stream', (options, next) => {
    const turn = readJson(process.env.AI_MODELER_ACTIVE_TURN_FILE, {})
    const start = Date.now()
    const imageParts = collectImageParts(options?.messages || [])
    const referenceImages = Array.isArray(turn.reference_images) ? turn.reference_images : []
    const imageRows = imageEvidenceRows(imageParts, referenceImages)
    const hashedImageRows = imageRows.filter(row => row.transport_sha256)
    append(target, {
      kind: 'dsh_request_start', turn_id: turn.turn_id || null,
      provider: options?.provider || null,
      model: options?.model || null,
      reasoning_effort: options?.reasoningEffort || null,
      purpose: options?.purpose || null,
      request_image_count: imageParts.length,
      request_image_hash_count: hashedImageRows.length,
      request_image_sha256: hashedImageRows.map(row => row.transport_sha256),
      request_images: imageRows,
      request_capture_layer: 'dsh_llm_stream_boundary',
      provider_wire_observed: false,
      at: Date.now(),
    })
    return (async function *observedStream() {
      let firstChunkAt = null
      let usage = null
      let finish = null
      try {
        for await (const chunk of next()) {
          if (firstChunkAt === null) {
            firstChunkAt = Date.now()
            append(target, {
              kind: 'dsh_first_stream_chunk', turn_id: turn.turn_id || null,
              elapsed_ms: firstChunkAt - start,
              chunk_kind: String(chunk?.type || 'unknown'),
            })
          }
          if (chunk?.type === 'usage') {
            usage = safeUsage(chunk.usage)
            append(target, {
              kind: 'dsh_usage', turn_id: turn.turn_id || null,
              usage, usage_source: 'dsh_adapter_stream_event',
            })
          } else if (chunk?.type === 'finish') {
            finish = String(chunk.reason || 'unknown')
          }
          if (chunk && SAFE_CHUNK_TYPES.has(chunk.type)) {
            recordActivity(turn.turn_id, 'llm_stream_middleware', chunk.type)
          }
          yield chunk
        }
        append(target, {
          kind: 'dsh_request_complete', turn_id: turn.turn_id || null,
          elapsed_ms: Date.now() - start,
          first_chunk_ms: firstChunkAt === null ? null : firstChunkAt - start,
          usage, usage_source: usage ? 'dsh_adapter_stream_event' : 'unknown',
          finish_reason: finish,
        })
      } catch (error) {
        append(target, {
          kind: 'dsh_request_error', turn_id: turn.turn_id || null,
          elapsed_ms: Date.now() - start,
          error_name: String(error?.name || 'Error').slice(0, 80),
        })
        throw error
      } finally {
        flushActivity(turn.turn_id, 'llm_stream_middleware', 'llm_stream_complete')
        flushActivity(turn.turn_id, 'agent_assistant_stream', 'llm_stream_complete')
      }
    })()
  }, { global: true })

  ctx.on('agent/assistant-stream', ({ frame }) => {
    if (!frame || frame.type !== 'chunk' || !frame.chunk) return
    const kind = String(frame.chunk.type || '')
    if (!SAFE_CHUNK_TYPES.has(kind)) return
    const turn = readJson(process.env.AI_MODELER_ACTIVE_TURN_FILE, {})
    // Only reveal sampled activity; never persist chain text or character counts.
    recordActivity(turn.turn_id, 'agent_assistant_stream', kind)
  })
}
