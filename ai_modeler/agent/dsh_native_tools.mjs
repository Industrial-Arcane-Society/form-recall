// Native Cordis tool registration for the DSH Agent. Python work crosses a
// private one-shot JSONL boundary; no MCP client, server, or protocol is used.
import { spawn, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

export const name = 'ai-modeler-native-cordis-tools'
export const inject = ['tools', 'attachments']

const PYTHON = process.env.AI_MODELER_PYTHON || 'python'
const PROJECT_ROOT = process.env.AI_MODELER_PROJECT_ROOT || process.cwd()
const TIMEOUT_MS = 210_000

function loadSchemas() {
  const proc = spawnSync(PYTHON, ['-u', '-m', 'ai_modeler.agent.dsh_tool_worker', '--schemas'], {
    cwd: PROJECT_ROOT,
    env: process.env,
    encoding: 'utf8',
    timeout: 20_000,
    windowsHide: true,
    maxBuffer: 4 * 1024 * 1024,
  })
  if (proc.error) throw proc.error
  if (proc.status !== 0) throw new Error(`AI Modeler schema worker exited ${proc.status}: ${proc.stderr || ''}`)
  const response = JSON.parse(proc.stdout.trim())
  if (response?.ok !== true || !Array.isArray(response.schemas)) throw new Error('AI Modeler returned invalid native tool schemas.')
  return response.schemas
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
  if (constraints.length) {
    annotations.description = `${annotations.description ? `${annotations.description} ` : ''}Constraints: ${constraints.join(', ')}.`
  }
  let node
  if (Array.isArray(schema.oneOf)) {
    node = { oneOf: schema.oneOf.map(branch => authorSchema(branch)) }
  } else if (schema.type === 'object') {
    const requiredKeys = new Set(Array.isArray(schema.required) ? schema.required : [])
    const properties = Object.fromEntries(Object.entries(schema.properties || {}).map(([key, value]) => [key, authorSchema(value, requiredKeys.has(key))]))
    node = { type: 'object', properties, additionalProperties: schema.additionalProperties === true }
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

function executeWorker(toolName, args, signal) {
  if (signal.aborted) return Promise.reject(signal.reason || new Error('Tool call cancelled.'))
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ['-u', '-m', 'ai_modeler.agent.dsh_tool_worker'], {
      cwd: PROJECT_ROOT,
      env: process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    let aborted = false
    const cleanup = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
    }
    const fail = error => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const abort = () => {
      aborted = true
      if (!child.stdin.destroyed) child.stdin.write(JSON.stringify({ cancel: true }) + '\n')
    }
    const timer = setTimeout(() => {
      child.kill()
      fail(new Error(`AI Modeler tool ${toolName} timed out after ${TIMEOUT_MS}ms.`))
    }, TIMEOUT_MS)
    signal.addEventListener('abort', abort, { once: true })
    child.on('error', fail)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('close', code => {
      if (settled) return
      if (code !== 0) return fail(new Error(`AI Modeler tool worker exited ${code}: ${stderr.slice(0, 1200)}`))
      try {
        const response = JSON.parse(stdout.trim())
        if (!response?.ok) return fail(new Error(response?.error?.message || 'AI Modeler tool failed.'))
        if (aborted) return fail(signal.reason || new Error('Tool call cancelled.'))
        settled = true
        cleanup()
        resolve(response.result)
      } catch (error) {
        fail(new Error(`AI Modeler tool worker returned invalid JSON: ${String(error)}; ${stderr.slice(0, 500)}`))
      }
    })
    child.stdin.write(JSON.stringify({ name: toolName, arguments: args }) + '\n')
  })
}

export function apply(ctx) {
  const registrationPath = process.env.AI_MODELER_NATIVE_TOOLS_FILE
  if (registrationPath) writeFileSync(resolve(registrationPath), JSON.stringify({ phase: 'apply-entered' }), { mode: 0o600 })
  const packageRoot = process.env.AI_MODELER_DSH_NODE_MODULES
  if (!packageRoot) throw new Error('AI Modeler DSH Node package root was not configured.')
  const requireFromDsh = createRequire(join(packageRoot, '@deepseek-ai', 'dsh', 'package.json'))
  const { defineTool } = requireFromDsh('@deepseek-ai/dsh-tools')
  const schemas = loadSchemas()
  for (const tool of schemas) {
    if (!tool || typeof tool.name !== 'string' || typeof tool.description !== 'string'
        || !tool.inputSchema || tool.inputSchema.type !== 'object') {
      throw new Error('AI Modeler contains an invalid native tool schema.')
    }
    ctx.tools.register(defineTool({
      name: tool.name,
      description: tool.description,
      parameters: Object.fromEntries(Object.entries(tool.inputSchema.properties || {}).map(([key, value]) => [
        key, authorSchema(value, Array.isArray(tool.inputSchema.required) && tool.inputSchema.required.includes(key)),
      ])),
      output: {
        schema: { type: 'json' },
        render: (_args, value) => [
          { type: 'text', text: value.text },
          ...(value.imageAttachments || []).map(attachment => ({ type: 'image', attachment })),
        ],
      },
      timeoutMs: TIMEOUT_MS,
      async execute(args, exec) {
        const result = await executeWorker(tool.name, args, exec.signal)
        const images = Array.isArray(result.images) ? result.images : []
        const prepared = images.map(image => {
          if (!['image/png', 'image/jpeg'].includes(image?.mediaType)
              || typeof image.data !== 'string') throw new Error('AI Modeler returned an invalid image attachment.')
          const data = Buffer.from(image.data, 'base64')
          if (data.byteLength === 0 || data.byteLength > 20 * 1024 * 1024) {
            throw new Error('AI Modeler image result exceeds the native attachment limit.')
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
