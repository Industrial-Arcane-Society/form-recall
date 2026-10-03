import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = resolve(desktopRoot, '../../..')
const app = readFileSync(resolve(desktopRoot, 'src/App.vue'), 'utf8')
const rust = readFileSync(resolve(desktopRoot, 'src-tauri/src/lib.rs'), 'utf8')
const adapter = readFileSync(resolve(desktopRoot, 'src-tauri/src/native_catia_runtime.rs'), 'utf8')
const quick = readFileSync(resolve(desktopRoot, 'src-tauri/src/native_quick.rs'), 'utf8')
const geometry = readFileSync(resolve(desktopRoot, 'src-tauri/src/native_geometry_backend.rs'), 'utf8')
const pythonAdapter = readFileSync(resolve(repoRoot, 'src/legacy/ai_modeler/catia_com_adapter.py'), 'utf8')
const config = JSON.parse(readFileSync(resolve(desktopRoot, 'src-tauri/tauri.conf.json'), 'utf8'))

test('desktop UI no longer calls the Python backend bridge', () => {
  assert.doesNotMatch(app, /backendCall|backend_call|runBackendAction/)
  assert.doesNotMatch(rust, /async fn backend_call|fn ensure_backend|fn send_backend_call|struct BackendState/)
  assert.doesNotMatch(rust, /ai_modeler.*--ui-bridge|python_command|AIMODELER_PYTHON/)
})

test('model operations route through Rust active-model commands', () => {
  for (const command of [
    'active_model_update_feature',
    'active_model_save',
    'active_model_export',
    'active_model_export_snapshot',
    'active_model_push_catia',
  ]) assert.ok(app.includes(`'${command}'`), `missing native route: ${command}`)
  assert.match(app, /function cancelActive\(\)[\s\S]{0,500}快速模式正在执行单次本地构建/)
})

test('Python payload is CATIA-only, private, and installed on demand', () => {
  assert.match(rust, /\.join\("catia-python"\)/)
  assert.match(rust, /native_catia_runtime::prepare\(/)
  assert.doesNotMatch(rust, /python\.exe.*PATH|Command::new\("python/)
  assert.match(pythonAdapter, /win32com\.client|Dispatch/)
  assert.doesNotMatch(pythonAdapter, /from ai_modeler|import ai_modeler|cadquery|OCP/)
  assert.ok(config.bundle.resources['target/package-resources/python/'])
  assert.ok(config.bundle.resources['target/package-resources/catia-bootstrap/'])
})

test('headless smoke invokes the production native Quick builder without external runtimes', () => {
  const smoke = readFileSync(resolve(desktopRoot, 'src-tauri/src/headless_smoke.rs'), 'utf8')
  assert.match(smoke, /headless_quick_smoke/)
  assert.match(rust, /build_quick_active_model\(/)
  assert.doesNotMatch(quick, /Command::new|python\.exe|node\.exe/)
  assert.doesNotMatch(geometry, /Command::new|python\.exe|node\.exe/)
})
