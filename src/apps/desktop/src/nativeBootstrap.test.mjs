import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const setup = readFileSync(new URL('./components/DependencySetup.vue', import.meta.url), 'utf8')

test('desktop startup uses native resources, readiness, settings, and the active CAD model', () => {
  assert.match(app, /invoke<any>\('native_resource_status'\)/)
  assert.match(app, /invoke<any>\('native_runtime_status'\)/)
  assert.match(app, /invoke<any>\('native_settings_load'\)/)
  assert.match(app, /invoke<any>\('active_model_snapshot'\)/)
  assert.match(app, /invoke<DocumentState>\('active_model_new'/)
  assert.doesNotMatch(app, /backendCall\('system\.(?:handshake|initialize)'\)/)
  assert.match(app, /mode\.value === 'agent' && !runtimeReadiness\.value\?\.dsh\?\.installed/)
  assert.match(app, /本地 CAD 模型和 Quick 模式仍可继续使用/)
  assert.match(app, /runtimeReadiness\?\.occt/)
  assert.match(app, /runtimeReadiness\?\.catia/)
})

test('first-run setup installs only the app-private DSH runtime', () => {
  assert.match(setup, /invoke\('install_dsh_runtime'/)
  assert.doesNotMatch(setup, /install_dependencies|selectedMirror|Python 下载镜像/)
})
