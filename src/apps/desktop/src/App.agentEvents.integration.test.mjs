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
  acceptance_confirmed: false,
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
const scenario = rustChannelCapture?.scenario || 'stale-finish'
const uiScenario = process.env.AIMODELER_UI_SCENARIO || ''
const restoreDocument = {
  revision: 12,
  model_generation: 12,
  cad_revision: 7,
  geometry_fingerprint: 'restore-fingerprint',
  name: 'Restored accepted model',
  features: [{ index: 0, kind: 'box', name: 'Restored body', enabled: true, params: { length: 10, width: 8, height: 5 } }],
  stats: {},
  build_log: [],
  finish_summary: { accepted: true, finish_state: 'accepted', revision: 7, geometry_fingerprint: 'restore-fingerprint' },
}
const restoreSnapshot = {
  generation: 12,
  document: restoreDocument,
  acceptance_projection: {
    active_generation: uiScenario === 'active-model-restore-stale-projection' ? 11 : 12,
    revision: 7,
    geometry_fingerprint: 'restore-fingerprint',
    model: {
      exists: true,
      status: uiScenario === 'active-model-restore-candidate' ? 'candidate' : 'accepted',
      acceptance_confirmed: uiScenario !== 'active-model-restore-candidate',
      revision: 7,
      geometry_fingerprint: 'restore-fingerprint',
      finish_status: 'accepted',
      finish_summary: restoreDocument.finish_summary,
      geometric: { status: 'passed' },
      visual: { status: 'covered' },
    },
  },
}
let adjudicationSubmitted = null
let adjudicationConfirmed = false
const pendingAdjudication = {
  expected_generation: 12,
  expected_revision: 7,
  finding_id: 'finding-ui-1',
  finding_status: 'awaiting_verification',
  item: 'hole center offset',
  severity: 'high',
  previous_evidence_id: 'reference-hole-old',
  previous_observation_id: 'observation-old',
  candidate_evidence_id: 'reference-hole-new',
  candidate_observation_id: 'observation-new',
  affected_feature_ids: ['hole1'],
  binding: {
    evidence_id: 'reference-hole-new', observation_id: 'observation-new',
    reference_id: 'reference-1', source_sha256: 'a'.repeat(64),
    user_target_fingerprint: 'target-1', view: 'front',
  },
  reference_evidence: 'prior reference evidence',
  model_evidence: 'prior model evidence',
  candidate_reference_evidence: 'candidate reference evidence',
}

// Provide the Tauri transport expected by the real Channel/invoke implementations.
globalThis.Document = class Document {}; globalThis.ShadowRoot = class ShadowRoot {};
globalThis.window = globalThis
// Tauri's `isTauri()` checks this marker; __TAURI_INTERNALS__ only mocks IPC.
window.isTauri = true
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
    seenCommands.push(command)
    if (command === 'native_resource_status') return { available: true, root: 'fixture' }
    if (command === 'native_runtime_status') return { appVersion: 'test', dsh: { installed: true, state: 'ready' }, occt: { state: 'ready' }, catia: { state: 'unknown' } }
    if (command === 'native_about') return { product: 'AI Modeler', version: 'test' }
    if (command === 'native_settings_load') return { llm: { providers: [] }, online: { enabled: true }, viewer: {} }
    if (command === 'active_model_snapshot') return uiScenario.startsWith('active-model-restore') ? restoreSnapshot : null
    if (command === 'active_model_new') return { revision: 0, name: 'Part1', features: [], stats: {}, build_log: [] }
    if (command === 'agent_history') return { turns: [] }
    if (command === 'agent_finding_review_state') {
      initializationFinished = true
      if (uiScenario !== 'finding-adjudication' || adjudicationConfirmed) return { pending: [] }
      return { pending: [pendingAdjudication] }
    }
    if (command === 'agent_adjudicate_finding') {
      adjudicationSubmitted = args.request
      adjudicationConfirmed = true
      return { resolved: false, finding_status: 'awaiting_verification' }
    }
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
  if (uiScenario.startsWith('active-model-restore')) {
    assert.ok(seenCommands.includes('active_model_snapshot'), `startup should request the active-model snapshot: ${seenCommands.join(', ')}`)
    const summary = descendants(root).find(node => hasClass(node, 'acceptance-summary'))
    assert.ok(summary, 'startup restore renders the production acceptance summary')
    if (uiScenario === 'active-model-restore-accepted') {
      assert.match(renderedText(summary), /已验收/, 'current host projection must restore the accepted status')
    } else {
      assert.match(renderedText(summary), /候选模型/, 'unbound or candidate projection must stay a candidate')
      assert.doesNotMatch(renderedText(summary), /已验收/)
    }
    const restoredTitle = descendants(root).find(node => hasClass(node, 'document-title'))
    assert.match(renderedText(restoredTitle), /Restored accepted model/)
    console.log(`PASS: production Vue startup hydration rendered ${uiScenario}.`)
    app.unmount()
  } else {
  const textarea = descendants(root).find(node => node.type === 'textarea')
  assert.ok(textarea, 'mounted production Vue app renders the composer')
  textarea.props['onUpdate:modelValue'](expectedUserText)
  await nextTick()
  const sendButton = descendants(root).find(node => node.type === 'button' && hasClass(node, 'send-button'))
  assert.ok(sendButton, 'production composer renders the send button')
  await sendButton.props.onClick()
  await nextTick()

  if (uiScenario === 'finding-adjudication') {
    let confirmButton = descendants(root).find(node => node.type === 'button' && hasClass(node, 'finding-adjudication-confirm'))
    assert.ok(confirmButton, 'production Vue app exposes an explicit human confirmation button')
    assert.match(renderedText(confirmButton.parent), /仍保持未解决/)
    await confirmButton.props.onClick()
    await nextTick()
    assert.deepEqual(adjudicationSubmitted, {
      expected_generation: 12, expected_revision: 7, finding_id: 'finding-ui-1',
      candidate_evidence_id: 'reference-hole-new', candidate_observation_id: 'observation-new',
      reference_id: 'reference-1', source_sha256: 'a'.repeat(64),
      user_target_fingerprint: 'target-1', view: 'front', affected_feature_ids: ['hole1'],
    }, 'Vue submits only the uniquely displayed finding/candidate binding and CAS versions')
    assert.equal(adjudicationConfirmed, true)
    assert.equal(descendants(root).some(node => hasClass(node, 'finding-adjudication-confirm')), false)
    assert.equal(renderedText(root).includes('finding_status=resolved'), false)
    console.log('PASS: mounted production Vue App; human finding adjudication submits the exact CAS-bound candidate and retains awaiting-verification semantics.')
  }

  assert.ok(submittedRequestId, 'production submit handler calls agent_send')
  if (rustChannelCapture) {
    const firstEvent = rustChannelCapture.events[0]?.message
    assert.equal(submittedRequestId, firstEvent?.request_id, 'Rust event request id must match the production Vue turn')
    const terminalModel = rustChannelCapture.events.find(row => row.message?.event === 'agent.turn_complete')?.message?.data?.current_model
    if (scenario === 'stale-finish') {
      assert.equal(expectedModelDocument.finish_summary, null, 'host must hide the stale accepted finish report during model publication')
      assert.equal(expectedModelDocument.acceptance_confirmed, false, 'Rust model.changed event must not carry stale host acceptance')
      assert.equal(terminalModel?.status, 'candidate', 'Rust terminal snapshot must not accept the stale finish summary')
      assert.equal(terminalModel?.finish_status, 'not_finished')
      assert.equal(terminalModel?.finish_summary, null)
    } else if (scenario === 'pure-qa') {
      assert.equal(rustModelChange, undefined, 'pure Q&A must not publish a model.changed event')
      assert.equal(terminalModel?.exists, false, 'pure Q&A must leave the model absent')
    } else if (scenario === 'cad-tool-failure') {
      assert.equal(terminalModel?.status, 'candidate', 'a failed CAD tool must not become accepted')
      assert.equal(terminalModel?.finish_summary, null, 'failed CAD work must not carry accepted finish evidence')
      assert.equal(rustChannelCapture.result?.turn_status, 'stopped', 'tool failure must be reflected in the turn result')
    } else if (scenario === 'accepted-current') {
      assert.equal(terminalModel?.status, 'accepted')
      assert.equal(terminalModel?.acceptance_confirmed, true)
      assert.equal(expectedModelDocument.acceptance_confirmed, true, 'live model.changed status must come from host confirmation')
      assert.equal(terminalModel?.finish_summary?.accepted, true)
      assert.equal(terminalModel?.finish_summary?.revision, terminalModel?.revision)
      assert.equal(terminalModel?.finish_summary?.geometry_fingerprint, terminalModel?.geometry_fingerprint)
    } else {
      assert.fail(`unexpected Rust Channel fixture scenario: ${scenario}`)
    }
  }
  const rendered = descendants(root)
  const summary = rendered.find(node => hasClass(node, 'acceptance-summary'))
  if (scenario === 'pure-qa') {
    assert.match(renderedText(summary), /本轮回复完成/)
    assert.match(renderedText(summary), /尚无 CADPlan 模型/)
    assert.doesNotMatch(renderedText(summary), /候选模型|已验收/)
  } else if (scenario === 'cad-tool-failure') {
    assert.match(renderedText(summary), /本轮已停止（tool_error）/)
    assert.match(renderedText(summary), /候选模型/)
    assert.doesNotMatch(renderedText(summary), /已验收/, 'CAD tool failure must remain unaccepted in the production Vue UI')
    assert.ok(rendered.find(node => hasClass(node, 'chat-message') && renderedText(node).includes('工具：cad_preview_model · failed')), 'failed CAD tool must be visible in chat')
    assert.ok(rendered.find(node => hasClass(node, 'document-title') && renderedText(node).includes(expectedModelDocument.name)))
  } else if (scenario === 'accepted-current') {
    assert.match(renderedText(summary), /本轮回复完成/)
    assert.match(renderedText(summary), /已验收/)
  } else {
    assert.match(renderedText(summary), /本轮回复完成/)
    assert.match(renderedText(summary), /候选模型/)
    assert.doesNotMatch(renderedText(summary), /已验收/, 'stale accepted finish evidence must not appear as accepted in the production Vue UI')
  }
  if (scenario !== 'pure-qa') {
    const viewport = rendered.find(node => hasClass(node, 'headless-cad-viewport'))
    assert.equal(viewport.props['data-revision'], String(expectedModelDocument.revision))
    const modelTitle = rendered.find(node => hasClass(node, 'document-title'))
    assert.ok(renderedText(modelTitle).includes(expectedModelDocument.name))
    const featureRow = rendered.find(node => hasClass(node, 'feature-row'))
    assert.ok(renderedText(featureRow).includes(expectedModelDocument.features[0].name))
  }
  const expectedReply = rustChannelCapture?.result?.reply || 'Fixture response from Agent'
  assert.ok(rendered.find(node => hasClass(node, 'chat-message') && hasClass(node, 'assistant') && renderedText(node).includes(expectedReply)))
  console.log(rustChannelCapture
    ? `PASS: mounted production Vue App headlessly; Rust MockRuntime Channel capture scenario=${scenario} reached its Tauri Channel callback and rendered expected model/turn state. WebView delivery remains untested.`
    : 'PASS: mounted production Vue App headlessly; mocked agent_send IPC events rendered.')
  app.unmount()
  }
} finally {
  await fs.rm(compiledPath, { force: true })
}
