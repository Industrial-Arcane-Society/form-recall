import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { createRenderer, nextTick } from 'vue'

const sourcePath = path.join(process.cwd(), 'src', 'App.vue')
const compiledPath = path.join(process.cwd(), 'src', `.App.agent-events-${process.pid}.mjs`)
const tauriCallbacks = new Map()
const seenCommands = []
let nextCallbackId = 1
let submittedRequestId = ''
let initializationFinished = false
const rustChannelCapture = process.env.AIMODELER_AGENT_IPC_CAPTURE
  ? JSON.parse(process.env.AIMODELER_AGENT_IPC_CAPTURE)
  : null

Object.defineProperty(globalThis, 'crypto', {
  configurable: true,
  value: {
    randomUUID: () => rustChannelCapture?.events?.[0]?.message?.request_id || 'headless-agent-ipc',
  },
})

const changedDocument = {
  revision: 7,
  cad_revision: 7,
  geometry_fingerprint: 'event-fixture-fingerprint',
  name: 'Event fixture bracket',
  features: [{ index: 0, kind: 'box', name: 'Base plate', enabled: true, params: { length: 80, width: 40, height: 12 } }],
  stats: { faces: 6, triangles: 12 },
  build_log: ['Base plate committed'],
  geometric_status: 'passed',
  visual_status: 'not_reviewed',
  finish_summary: {
    accepted: false,
    finish_state: 'candidate_holes_unverified',
    revision: 7,
    geometry_fingerprint: 'event-fixture-fingerprint',
  },
}
const currentModel = {
  exists: true,
  status: 'candidate',
  revision: 7,
  geometry_fingerprint: 'event-fixture-fingerprint',
  finish_status: 'candidate_holes_unverified',
  finish_summary: changedDocument.finish_summary,
  geometric: { status: 'passed' },
  visual: { status: 'not_reviewed' },
}
const rustModelChange = rustChannelCapture?.events?.find(row => row.message?.event === 'model.changed')?.message
const expectedModelDocument = rustModelChange?.data || changedDocument
const expectedUserText = rustChannelCapture?.events?.find(row => row.message?.event === 'agent.message')?.message?.data?.text || 'Create a bracket'

// Provide the Tauri transport expected by the real Channel/invoke implementations.
globalThis.Document = class Document {}; globalThis.ShadowRoot = class ShadowRoot {};
globalThis.window = globalThis
window.location = { search: '' }
window.addEventListener = () => {}
window.removeEventListener = () => {}
window.__TAURI_INTERNALS__ = {
  transformCallback(callback) {
    const id = nextCallbackId++
    tauriCallbacks.set(id, callback)
    return id
  },
  unregisterCallback(id) { tauriCallbacks.delete(id) },
  convertFileSrc(filePath) { return filePath },
  invoke: async (command, args = {}) => {
    if (command === 'native_resource_status') return { available: true, root: 'fixture' }
    if (command === 'native_runtime_status') return { appVersion: 'test', dsh: { installed: true, state: 'ready' }, occt: { state: 'ready' }, catia: { state: 'unknown' } }
    if (command === 'native_settings_load') { initializationFinished = true; return { llm: { providers: [] }, online: { enabled: true }, viewer: {} } }
    if (command === 'active_model_snapshot') return null
    if (command === 'active_model_new') return { revision: 0, name: 'Part1', features: [], stats: {}, build_log: [] }
    if (command === 'agent_history') return { turns: [] }
    if (command === 'agent_send') {
      submittedRequestId = args.requestId
      const callback = tauriCallbacks.get(args.onEvent.id)
      assert.ok(callback, 'App must pass a registered Tauri Channel callback to agent_send')
      if (rustChannelCapture) {
        assert.ok(Array.isArray(rustChannelCapture.events), 'Rust fixture must provide captured Channel events')
        for (const event of rustChannelCapture.events) callback(event)
        return rustChannelCapture.result
      }
      callback({ index: 0, message: { event: 'model.changed', request_id: submittedRequestId, data: changedDocument } })
      callback({ index: 1, message: { event: 'agent.turn_complete', request_id: submittedRequestId, data: { turn_status: 'completed', stop_reason: 'end_turn', current_model: currentModel } } })
      return { reply: 'Fixture response from Agent', turn_status: 'completed', current_model: currentModel }
    }
    throw new Error(`Unexpected Tauri command in test: ${command}`)
  },
}

const { descriptor, errors } = parse(await fs.readFile(sourcePath, 'utf8'), { filename: sourcePath })
assert.deepEqual(errors, [])
let componentModule = compileScript(descriptor, { id: 'agent-events-integration', inlineTemplate: true }).content
componentModule = componentModule.replace(
  "import { confirm, message, open, save } from '@tauri-apps/plugin-dialog'",
  'const confirm = async () => true; const message = async () => true; const open = async () => null; const save = async () => null',
)
componentModule = componentModule.replace("import type { AttachmentItem } from './components/AttachmentPreview.vue'", 'type AttachmentItem = any')
for (const name of ['AttachmentPreview', 'DependencySetup', 'ModelStats', 'UiIcon']) {
  componentModule = componentModule.replace(
    `import ${name} from './components/${name}.vue'`,
    `const ${name} = _defineComponent({ setup() { return () => null } })`,
  )
}
componentModule = componentModule.replace(
  "const CadViewport = defineAsyncComponent(() => import('./components/CadViewport.vue'))",
  'const CadViewport = _defineComponent({ props: ["revision"], setup(props) { return () => _createElementVNode("div", { class: "headless-cad-viewport", "data-revision": String(props.revision) }) } })',
).replaceAll('import.meta.env.DEV', 'false')
assert.ok(!componentModule.includes("from './components/"), 'only rendering-only child components are stubbed')
await fs.writeFile(compiledPath, ts.transpileModule(componentModule, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText, 'utf8')

function makeNode(type, text = '') {
  return { type, text, props: {}, children: [], parent: null, style: {}, getRootNode() { return {} }, addEventListener() {}, removeEventListener() {} }
}
function insert(child, parent, anchor) {
  if (child.parent) {
    const oldIndex = child.parent.children.indexOf(child)
    if (oldIndex >= 0) child.parent.children.splice(oldIndex, 1)
  }
  child.parent = parent
  const anchorIndex = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(anchorIndex < 0 ? parent.children.length : anchorIndex, 0, child)
}
const renderer = createRenderer({
  createElement: type => makeNode(type),
  createText: text => makeNode('#text', text),
  createComment: text => makeNode('#comment', text),
  setText(target, text) { target.text = text },
  setElementText(target, text) { target.text = text; target.children = [] },
  patchProp(target, key, _previous, next) { target.props[key] = next; if (key === "style") target.style = typeof next === "object" ? next : {} },
  insert,
  remove(target) {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1)
    target.parent = null
  },
  parentNode(target) { return target.parent },
  nextSibling(target) { return target.parent?.children[target.parent.children.indexOf(target) + 1] || null },
  insertStaticContent(content, parent, anchor) {
    const item = makeNode('#static', content)
    insert(item, parent, anchor)
    return [item, item]
  },
})
function descendants(node, result = []) {
  if (!node) return result
  result.push(node)
  for (const child of node.children || []) descendants(child, result)
  return result
}
function renderedText(node) { return [node?.text || '', ...(node?.children || []).map(renderedText)].join('') }
function hasClass(node, name) { return String(node.props?.class || '').split(/\s+/).includes(name) }

try {
  const { default: App } = await import(pathToFileURL(compiledPath).href + `?run=${Date.now()}`)
  const root = makeNode('root')
  const app = renderer.createApp(App)
  app.mount(root)
  for (let attempt = 0; attempt < 100 && !initializationFinished; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 0))
  }
  await nextTick()
  const textarea = descendants(root).find(node => node.type === 'textarea')
  assert.ok(textarea, 'mounted production Vue app renders the composer')
  textarea.props['onUpdate:modelValue'](expectedUserText)
  await nextTick()
  const sendButton = descendants(root).find(node => node.type === 'button' && hasClass(node, 'send-button'))
  assert.ok(sendButton, 'production composer renders the send button')
  await sendButton.props.onClick()
  await nextTick()

  assert.ok(submittedRequestId, 'production submit handler calls agent_send')
  if (rustChannelCapture) {
    assert.equal(submittedRequestId, rustModelChange.request_id, 'Rust event request id must match the production Vue turn')
  }
  const rendered = descendants(root)
  const summary = rendered.find(node => hasClass(node, 'acceptance-summary'))
  assert.match(renderedText(summary), /本轮回复完成/)
  assert.match(renderedText(summary), /候选模型/)
  const viewport = rendered.find(node => hasClass(node, 'headless-cad-viewport'))
  assert.equal(viewport.props['data-revision'], String(expectedModelDocument.revision))
  const modelTitle = rendered.find(node => hasClass(node, 'document-title'))
  assert.ok(renderedText(modelTitle).includes(expectedModelDocument.name))
  const featureRow = rendered.find(node => hasClass(node, 'feature-row'))
  assert.ok(renderedText(featureRow).includes(expectedModelDocument.features[0].name))
  const expectedReply = rustChannelCapture?.result?.reply || 'Fixture response from Agent'
  assert.ok(rendered.find(node => hasClass(node, 'chat-message') && hasClass(node, 'assistant') && renderedText(node).includes(expectedReply)))
  console.log(rustChannelCapture
    ? `PASS: mounted production Vue App headlessly; Rust MockRuntime Channel capture was passed into its Tauri Channel callback; revision ${expectedModelDocument.revision}/document, feature row, turn completion, candidate status, and reply rendered. WebView delivery remains untested.`
    : 'PASS: mounted production Vue App headlessly; mocked agent_send IPC events rendered.')
  app.unmount()
} finally {
  await fs.rm(compiledPath, { force: true })
}
