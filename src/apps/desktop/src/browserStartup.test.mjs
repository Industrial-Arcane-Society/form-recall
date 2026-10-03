import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

// Execute the production startup function with isolated ref / IPC dependencies.
// This does not create a Tauri window, load settings or call a provider.
const source = readFileSync(new URL('./App.vue', import.meta.url), 'utf8').replaceAll('\r\n', '\n')
const startup = source.match(/async function startApplication\(\) \{[\s\S]*?\n\}\n\nfunction onDependenciesInstalled/)[0]
  .replace(/\n\nfunction onDependenciesInstalled$/, '')
  .replaceAll('invoke<any>', 'invoke')
function context(desktop) {
  const calls = [], statuses = []
  return {
    desktop, calls, statuses,
    backendInfo: { value: '' }, settingsDraft: { value: null }, runtimeReadiness: { value: null },
    dependencySetupError: { value: '' }, needsDependencySetup: { value: false },
    ensureLlmDraft: value => value,
    setStatus: value => statuses.push(value),
    initialize: async () => calls.push('initialize'),
    invoke: async command => { calls.push(command); return command === 'native_resource_status' ? { available: true } : { dsh: { installed: true } } },
    errorMessage: error => error.message,
  }
}
test('browser startup never touches native resources, runtime, settings or CAD initialization', async () => {
  const state = context(false)
  await runInNewContext(`(${startup})`, state)()
  assert.deepEqual(state.calls, [])
  assert.equal(state.backendInfo.value, '浏览器预览')
  assert.match(state.statuses[0], /浏览器预览已就绪/)
  assert.equal(state.runtimeReadiness.value, null, 'browser must not claim to detect a local runtime')
  assert.equal(state.needsDependencySetup.value, false)
  assert.equal(state.settingsDraft.value.viewer.grid, true)
})
test('desktop startup continues through native resources, runtime and initialization', async () => {
  const state = context(true)
  await runInNewContext(`(${startup})`, state)()
  assert.deepEqual(state.calls, ['native_resource_status', 'native_runtime_status', 'initialize'])
  assert.equal(state.backendInfo.value, '')
})
test('desktop startup failure still opens the dependency error flow', async () => {
  const state = context(true)
  state.invoke = async () => { throw new Error('fixture runtime unavailable') }
  await runInNewContext(`(${startup})`, state)()
  assert.equal(state.needsDependencySetup.value, true)
  assert.equal(state.dependencySetupError.value, 'fixture runtime unavailable')
})
test('browser submit guard returns before runtime setup, busy state or model calls', async () => {
  const guard = source.match(/async function submitPrompt\(\) \{[\s\S]*?\n  if \(busy\.value/)[0]
    .replace(/\n  if \(busy\.value$/, '\n}')
  const state = context(false)
  await runInNewContext(`(${guard})`, state)()
  assert.match(state.statuses[0], /浏览器预览不提供本地建模/)
  assert.deepEqual(state.calls, [])
  assert.match(source, /const canSubmit = computed\(\(\) => desktop &&/)
  assert.match(source, /v-if="desktop" class="runtime-indicators"/)
})
