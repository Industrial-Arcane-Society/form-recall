// Native Cordis tool registration for the DSH Agent. Tool schemas are supplied
// by the Rust host and calls cross its authenticated loopback JSON-RPC bridge.
import { randomUUID } from 'node:crypto'
import { readFileSync, statSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { createConnection } from 'node:net'
import { join, resolve } from 'node:path'

export const name = 'ai-modeler-native-cordis-tools'
export const inject = ['tools', 'attachments']

const TOOL_TIMEOUT_MS = 210_000
const CONNECT_TIMEOUT_MS = 5_000
const CANCEL_TIMEOUT_MS = 5_000
const MAX_JSON_LINE_BYTES = 64 * 1024 * 1024
const MAX_SCHEMA_FILE_BYTES = 8 * 1024 * 1024
const MAX_IMAGE_BYTES = 20 * 1024 * 1024

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validateJsonSchema(schema, location, depth = 0) {
  if (!isRecord(schema) || depth > 48) {
    throw new Error(`AI Modeler native tool schema ${location} is malformed or nested too deeply.`)
  }
  const supportedTypes = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null'])
  if (schema.type !== undefined && !supportedTypes.has(schema.type)) {
    throw new Error(`AI Modeler native tool schema ${location} has an unsupported type.`)
  }
  if (schema.enum !== undefined && !Array.isArray(schema.enum)) {
    throw new Error(`AI Modeler native tool schema ${location} has a malformed enum.`)
  }
  if (schema.oneOf !== undefined && (!Array.isArray(schema.oneOf) || schema.oneOf.length === 0)) {
    throw new Error(`AI Modeler native tool schema ${location} has a malformed oneOf.`)
  }
  if (schema.required !== undefined
      && (!Array.isArray(schema.required) || schema.required.some(key => typeof key !== 'string'))) {
    throw new Error(`AI Modeler native tool schema ${location} has an invalid required list.`)
  }
  if (schema.properties !== undefined && !isRecord(schema.properties)) {
    throw new Error(`AI Modeler native tool schema ${location} has malformed properties.`)
  }
  for (const key of schema.required || []) {
    if (schema.properties && !Object.hasOwn(schema.properties, key)) {
      throw new Error(`AI Modeler native tool schema ${location} requires unknown property "${key}".`)
    }
  }
  for (const [key, child] of Object.entries(schema.properties || {})) {
    validateJsonSchema(child, `${location}.properties.${key}`, depth + 1)
  }
  if (schema.items !== undefined) validateJsonSchema(schema.items, `${location}.items`, depth + 1)
  if (Array.isArray(schema.oneOf)) {
    schema.oneOf.forEach((branch, index) => validateJsonSchema(branch, `${location}.oneOf[${index}]`, depth + 1))
  }
  if (isRecord(schema.additionalProperties)) {
    validateJsonSchema(schema.additionalProperties, `${location}.additionalProperties`, depth + 1)
  } else if (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== 'boolean') {
    throw new Error(`AI Modeler native tool schema ${location} has malformed additionalProperties.`)
  }
}

function validateToolSchemaManifest(manifest) {
  if (!isRecord(manifest) || manifest.schemaVersion !== 1 || !Array.isArray(manifest.tools)) {
    throw new Error('AI Modeler Rust host returned an invalid native tool schema manifest (expected schemaVersion 1 and tools[]).')
  }
  if (manifest.tools.length === 0) throw new Error('AI Modeler native tool schema manifest contains no tools.')

  const names = new Set()
  for (const [index, tool] of manifest.tools.entries()) {
    const location = `tools[${index}]`
    if (!isRecord(tool) || typeof tool.name !== 'string'
        || !/^[a-z][a-z0-9_]{0,63}$/.test(tool.name)) {
      throw new Error(`AI Modeler native tool schema ${location} has an invalid tool name.`)
    }
    if (names.has(tool.name)) throw new Error(`AI Modeler native tool schema contains duplicate tool name "${tool.name}".`)
    names.add(tool.name)
    if (typeof tool.description !== 'string' || !tool.description.trim()) {
      throw new Error(`AI Modeler native tool schema "${tool.name}" needs a non-empty description.`)
    }
    const schema = tool.inputSchema
    if (!isRecord(schema) || schema.type !== 'object' || !isRecord(schema.properties || {})) {
      throw new Error(`AI Modeler native tool schema "${tool.name}" needs an object inputSchema with properties.`)
    }
    validateJsonSchema(schema, `"${tool.name}".inputSchema`)
  }
  return manifest.tools
}

export function readNativeToolSchemas(filePath = process.env.AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE) {
  if (typeof filePath !== 'string' || !filePath.trim()) {
    throw new Error('AI_MODELER_NATIVE_TOOL_SCHEMAS_FILE was not configured by the Rust host.')
  }
  let fileSize
  try {
    fileSize = statSync(filePath).size
  } catch (error) {
    throw new Error(`Unable to read AI Modeler native tool schema file "${filePath}": ${error.message}`)
  }
  if (fileSize <= 0 || fileSize > MAX_SCHEMA_FILE_BYTES) {
    throw new Error(`AI Modeler native tool schema file must be between 1 byte and ${MAX_SCHEMA_FILE_BYTES} bytes.`)
  }
  let manifest
  try {
    manifest = JSON.parse(readFileSync(filePath, 'utf8'))
  } catch (error) {
    throw new Error(`AI Modeler native tool schema file is not valid JSON: ${error.message}`)
  }
  return validateToolSchemaManifest(manifest)
}

function authorSchema(schema, required = false) {
  if (!schema || typeof schema !== 'object') return { type: 'json', ...(required ? { required: true } : {}) }
  const annotations = {}
  if (typeof schema.description === 'string') annotations.description = schema.description
  if (typeof schema.title === 'string') annotations.title = schema.title
  if (Object.hasOwn(schema, 'default')) annotations.default = schema.default
  if (Object.hasOwn(schema, 'examples')) annotations.examples = schema.examples
  const constraints = ['minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
    'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern']
    .filter(key => Object.hasOwn(schema, key)).map(key => `${key}=${JSON.stringify(schema[key])}`)
  if (isRecord(schema.additionalProperties)) {
    constraints.push(`additionalProperties schema=${JSON.stringify(schema.additionalProperties)}`)
  }
  if (constraints.length) {
    annotations.description = `${annotations.description ? `${annotations.description} ` : ''}Constraints: ${constraints.join(', ')}.`
  }
  let node
  if (Array.isArray(schema.oneOf)) {
    node = { oneOf: schema.oneOf.map(branch => authorSchema(branch)) }
  } else if (schema.type === 'object') {
    const requiredKeys = new Set(Array.isArray(schema.required) ? schema.required : [])
    const properties = Object.fromEntries(Object.entries(schema.properties || {}).map(([key, value]) => [key, authorSchema(value, requiredKeys.has(key))]))
    node = {
      type: 'object',
      properties,
      // DSH's defineTool schema compiler accepts only a boolean here. Keep
      // the value shape in the description so dynamic provenance maps remain
      // usable without emitting an unsupported JSON Schema object.
      additionalProperties: schema.additionalProperties === true || isRecord(schema.additionalProperties),
    }
  } else if (schema.type === 'array') {
    node = { type: 'array', ...(schema.items ? { items: authorSchema(schema.items) } : {}) }
  } else if (['string', 'number', 'integer', 'boolean', 'null'].includes(schema.type)) {
    node = { type: schema.type }
    if (Array.isArray(schema.enum)) node.enum = schema.enum
    if (Object.hasOwn(schema, 'const')) node.const = schema.const
  } else {
    node = { type: 'json' }
  }
  Object.assign(node, annotations)
  if (required) node.required = true
  return node
}

function parseLoopbackAddress(address) {
  if (typeof address !== 'string') throw new Error('AI_MODELER_TOOL_BRIDGE_ADDR was not configured by the Rust host.')
  const match = /^127\.0\.0\.1:(\d{1,5})$/.exec(address.trim())
  const port = match ? Number(match[1]) : NaN
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('AI_MODELER_TOOL_BRIDGE_ADDR must be a loopback address in the form 127.0.0.1:PORT.')
  }
  return { host: '127.0.0.1', port }
}

function responseError(response, requestId, method) {
  if (!isRecord(response)) return new Error(`AI Modeler tool bridge ${method} returned a non-object JSON response.`)
  if (response.jsonrpc !== '2.0') return new Error(`AI Modeler tool bridge ${method} returned a response without JSON-RPC 2.0.`)
  if (response.id !== requestId) return new Error(`AI Modeler tool bridge ${method} response id did not match the request.`)
  if (Object.hasOwn(response, 'error')) {
    const error = response.error
    if (!isRecord(error) || (typeof error.code !== 'string' && typeof error.code !== 'number')
        || typeof error.message !== 'string') {
      return new Error(`AI Modeler tool bridge ${method} returned a malformed JSON-RPC error.`)
    }
    return new Error(`AI Modeler tool bridge ${method} failed (${error.code}): ${error.message.slice(0, 1200)}`)
  }
  if (!Object.hasOwn(response, 'result')) return new Error(`AI Modeler tool bridge ${method} response has neither result nor error.`)
  return null
}

export function requestToolBridge(method, params, {
  address,
  connectTimeoutMs = CONNECT_TIMEOUT_MS,
  timeoutMs = TOOL_TIMEOUT_MS,
  maxLineBytes = MAX_JSON_LINE_BYTES,
  signal,
} = {}) {
  if (!['tools/call', 'tools/cancel'].includes(method)) {
    return Promise.reject(new Error(`Unsupported AI Modeler tool bridge method "${method}".`))
  }
  const endpoint = parseLoopbackAddress(address)
  const request = { jsonrpc: '2.0', id: randomUUID(), method, params }
  let wire
  try {
    wire = `${JSON.stringify(request)}\n`
  } catch (error) {
    return Promise.reject(new Error(`Unable to encode AI Modeler tool bridge ${method} request: ${error.message}`))
  }
  if (Buffer.byteLength(wire) > maxLineBytes) {
    return Promise.reject(new Error(`AI Modeler tool bridge ${method} request exceeds the ${maxLineBytes}-byte line limit.`))
  }
  if (signal?.aborted) return Promise.reject(abortError(signal))

  return new Promise((resolvePromise, rejectPromise) => {
    const socket = createConnection(endpoint)
    let buffer = Buffer.alloc(0)
    let settled = false
    let connectTimer
    let operationTimer
    const onAbort = () => fail(abortError(signal))
    const cleanup = () => {
      clearTimeout(connectTimer)
      clearTimeout(operationTimer)
      signal?.removeEventListener('abort', onAbort)
      socket.removeAllListeners()
      if (!socket.destroyed) socket.destroy()
    }
    const fail = error => {
      if (settled) return
      settled = true
      cleanup()
      rejectPromise(error instanceof Error ? error : new Error(String(error)))
    }
    const finish = value => {
      if (settled) return
      settled = true
      cleanup()
      resolvePromise(value)
    }
    connectTimer = setTimeout(() => {
      fail(new Error(`AI Modeler tool bridge connection timed out after ${connectTimeoutMs}ms.`))
    }, connectTimeoutMs)
    signal?.addEventListener('abort', onAbort, { once: true })
    socket.once('connect', () => {
      clearTimeout(connectTimer)
      operationTimer = setTimeout(() => {
        fail(new Error(`AI Modeler tool bridge ${method} timed out after ${timeoutMs}ms.`))
      }, timeoutMs)
      socket.write(wire, error => {
        if (error) fail(new Error(`Unable to send AI Modeler tool bridge ${method} request: ${error.message}`))
      })
    })
    socket.on('data', chunk => {
      if (settled) return
      buffer = Buffer.concat([buffer, chunk])
      if (buffer.byteLength > maxLineBytes) {
        return fail(new Error(`AI Modeler tool bridge ${method} response exceeds the ${maxLineBytes}-byte line limit.`))
      }
      const newline = buffer.indexOf(0x0a)
      if (newline < 0) return
      const line = buffer.subarray(0, newline).toString('utf8').replace(/\r$/, '')
      const trailing = buffer.subarray(newline + 1).toString('utf8').trim()
      if (trailing) return fail(new Error(`AI Modeler tool bridge ${method} returned extra data after its response line.`))
      let response
      try {
        response = JSON.parse(line)
      } catch (error) {
        return fail(new Error(`AI Modeler tool bridge ${method} returned invalid JSON: ${error.message}`))
      }
      const error = responseError(response, request.id, method)
      if (error) return fail(error)
      finish(response.result)
    })
    socket.once('error', error => fail(new Error(`AI Modeler tool bridge ${method} connection failed: ${error.message}`)))
    socket.once('end', () => {
      if (!settled) fail(new Error(`AI Modeler tool bridge ${method} closed before sending a complete JSON-RPC response line.`))
    })
    socket.once('close', () => {
      if (!settled) fail(new Error(`AI Modeler tool bridge ${method} connection closed without a response.`))
    })
  })
}

function activeTurnId(filePath) {
  if (typeof filePath !== 'string' || !filePath.trim()) return undefined
  let record
  try {
    record = JSON.parse(readFileSync(filePath, 'utf8'))
  } catch (error) {
    if (error?.code === 'ENOENT') return undefined
    throw new Error(`Unable to read AI Modeler active turn id: ${error.message}`)
  }
  return typeof record?.turn_id === 'string' && record.turn_id.trim() ? record.turn_id.trim() : undefined
}

function abortError(signal) {
  if (signal?.reason instanceof Error) return signal.reason
  return new Error('AI Modeler tool call cancelled.')
}

export function createToolBridgeClient({
  address,
  token,
  sessionId,
  activeTurnFile,
  connectTimeoutMs = CONNECT_TIMEOUT_MS,
  operationTimeoutMs = TOOL_TIMEOUT_MS,
  cancelTimeoutMs = CANCEL_TIMEOUT_MS,
  maxLineBytes = MAX_JSON_LINE_BYTES,
} = {}) {
  parseLoopbackAddress(address)
  if (typeof token !== 'string' || !token.trim()) throw new Error('AI_MODELER_TOOL_BRIDGE_TOKEN was not configured by the Rust host.')
  if (typeof sessionId !== 'string' || !sessionId.trim()) throw new Error('AI_MODELER_TOOL_SESSION_ID was not configured by the Rust host.')

  const request = (method, params, timeoutMs, signal) => requestToolBridge(method, params, {
    address,
    connectTimeoutMs,
    timeoutMs,
    maxLineBytes,
    signal,
  })

  return {
    async callTool(toolName, args, signal) {
      if (!/^[a-z][a-z0-9_]{0,63}$/.test(toolName)) throw new Error(`Invalid AI Modeler tool name "${toolName}".`)
      if (signal?.aborted) throw abortError(signal)

      const toolCallId = randomUUID()
      const turnId = activeTurnId(activeTurnFile)
      const params = {
        token,
        sessionId,
        toolCallId,
        ...(turnId ? { turnId } : {}),
        name: toolName,
        arguments: args,
        deadlineMs: Date.now() + operationTimeoutMs,
      }
      let cancelPromise
      const onAbort = () => {
        if (cancelPromise) return
        cancelPromise = request('tools/cancel', {
          token,
          sessionId,
          toolCallId,
          ...(turnId ? { turnId } : {}),
          deadlineMs: Date.now() + cancelTimeoutMs,
        }, cancelTimeoutMs).catch(() => undefined)
      }
      signal?.addEventListener('abort', onAbort, { once: true })
      try {
        const result = await request('tools/call', params, operationTimeoutMs, signal)
        if (signal?.aborted) {
          onAbort()
          await cancelPromise
          throw abortError(signal)
        }
        return result
      } catch (error) {
        if (signal?.aborted) {
          onAbort()
          await cancelPromise
          throw abortError(signal)
        }
        throw error
      } finally {
        signal?.removeEventListener('abort', onAbort)
      }
    },
  }
}

export function apply(ctx) {
  const registrationPath = process.env.AI_MODELER_NATIVE_TOOLS_FILE
  if (registrationPath) writeFileSync(resolve(registrationPath), JSON.stringify({ phase: 'apply-entered' }), { mode: 0o600 })
  const packageRoot = process.env.AI_MODELER_DSH_NODE_MODULES
  if (!packageRoot) throw new Error('AI Modeler DSH Node package root was not configured.')
  const requireFromDsh = createRequire(join(packageRoot, '@deepseek-ai', 'dsh', 'package.json'))
  const { defineTool } = requireFromDsh('@deepseek-ai/dsh-tools')
  if (typeof defineTool !== 'function') throw new Error('AI Modeler could not load DSH defineTool().')

  const schemas = readNativeToolSchemas()
  const client = createToolBridgeClient({
    address: process.env.AI_MODELER_TOOL_BRIDGE_ADDR,
    token: process.env.AI_MODELER_TOOL_BRIDGE_TOKEN,
    sessionId: process.env.AI_MODELER_TOOL_SESSION_ID,
    activeTurnFile: process.env.AI_MODELER_ACTIVE_TURN_FILE,
  })

  for (const tool of schemas) {
    const requiredKeys = new Set(Array.isArray(tool.inputSchema.required) ? tool.inputSchema.required : [])
    ctx.tools.register(defineTool({
      name: tool.name,
      description: tool.description,
      parameters: Object.fromEntries(Object.entries(tool.inputSchema.properties || {}).map(([key, value]) => [
        key, authorSchema(value, requiredKeys.has(key)),
      ])),
      output: {
        schema: { type: 'json' },
        render: (_args, value) => [
          { type: 'text', text: value.text },
          ...(value.imageAttachments || []).map(attachment => ({ type: 'image', attachment })),
        ],
      },
      timeoutMs: TOOL_TIMEOUT_MS,
      async execute(args, exec) {
        const result = await client.callTool(tool.name, args, exec.signal)
        if (!isRecord(result)) throw new Error(`AI Modeler tool bridge returned an invalid result for ${tool.name}.`)
        const images = Array.isArray(result.images) ? result.images : []
        const prepared = images.map((image, index) => {
          if (!['image/png', 'image/jpeg'].includes(image?.mediaType)
              || typeof image.data !== 'string'
              || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(image.data)) {
            throw new Error(`AI Modeler returned an invalid image attachment at images[${index}].`)
          }
          const data = Buffer.from(image.data, 'base64')
          if (data.byteLength === 0 || data.byteLength > MAX_IMAGE_BYTES) {
            throw new Error(`AI Modeler image result at images[${index}] exceeds the native attachment limit.`)
          }
          return { data, mediaType: image.mediaType }
        })
        const imageAttachments = prepared.length ? await ctx.attachments.saveImages(prepared) : []
        return {
          text: typeof result.text === 'string' ? result.text : JSON.stringify(result),
          payload: result.payload || {},
          imageAttachments,
        }
      },
    }))
  }
  if (registrationPath) {
    writeFileSync(resolve(registrationPath), JSON.stringify({
      schemaVersion: 1,
      phase: 'registered',
      registeredToolNames: schemas.map(tool => tool.name),
      registeredAt: new Date().toISOString(),
    }), { mode: 0o600 })
  }
}
