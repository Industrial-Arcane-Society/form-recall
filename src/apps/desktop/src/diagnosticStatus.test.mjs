import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { diagnosticStatusRows, nativeSelftestMessage } from './diagnosticStatus.mjs'

const appSource = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const legacyBridge = readFileSync(new URL('../../../legacy/ai_modeler/ui/bridge.py', import.meta.url), 'utf8')
const rustDiagnostics = readFileSync(new URL('../../../core/cad-engine/src/native_diagnostics.rs', import.meta.url), 'utf8')
const rustLib = readFileSync(new URL('../../../core/cad-engine/src/lib.rs', import.meta.url), 'utf8')
const agentHost = readFileSync(new URL('../../../core/cad-engine/src/agent_host.rs', import.meta.url), 'utf8')
const tauri = readFileSync(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8')
const handlers = tauri.match(/tauri::generate_handler!\[([\s\S]*?)\]/)?.[1] ?? ''

test('native rows keep external MCP, Cordis, CATIA registration and runtime states separate', () => {
  const rows = diagnosticStatusRows({
    dsh: { installed: true, state: 'installed' },
    occt: { available: true, verified: true, loaded: false, state: 'runtime_verified' },
    catia: { registered: true, connected: null, connectionChecked: false, state: 'com_registered' },
    cordis: { available: true, schema_count: 25, transport: 'native_cordis_plugin', connectivity_checked: false },
    externalMcp: { configured: false, configured_server_count: 0, connected: null, checked: false, state: 'not_configured' },
    python: { available: false, required: false },
  })

  assert.deepEqual(rows.map((row) => row.key), ['dsh', 'occt', 'catia', 'external-mcp', 'cordis', 'legacy-python'])
  assert.equal(rows.find((row) => row.key === 'dsh').value, '已安装并校验')
  assert.equal(rows.find((row) => row.key === 'occt').value, '校验通过（未加载）')
  assert.equal(rows.find((row) => row.key === 'catia').value, '已注册（未探测连接）')
  assert.equal(rows.find((row) => row.key === 'external-mcp').value, '未配置')
  assert.equal(rows.find((row) => row.key === 'external-mcp').ready, null)
  assert.equal(rows.find((row) => row.key === 'cordis').value, '25 个 schema（非连通状态）')
  assert.equal(rows.find((row) => row.key === 'cordis').ready, true)
})

test('legacy Python diagnostics contract is migrated to native commands field by field', () => {
  assert.match(legacyBridge, /"system\.about"\s*:\s*return\s*\{"app_version"\s*:\s*__version__/)
  assert.match(legacyBridge, /"python_version"\s*:\s*sys\.version\.split\(\)\[0\]/)
  assert.match(legacyBridge, /"system\.selftest"\s*:\s*return\s*self\._selftest\(\)/)
  assert.match(legacyBridge, /"system\.open_path"/)
  assert.match(legacyBridge, /return\s+\{"opened"\s*:\s*path\}/)
  assert.match(legacyBridge, /return\s+\{"lines"\s*:\s*lines,\s*"ok"\s*:/)

  assert.match(appSource, /invoke<any>\('native_about'\)/)
  assert.match(appSource, /invoke<any>\('native_selftest'\)/)
  assert.ok(/invoke\('native_open_help_path'/.test(appSource), 'help paths must use the Rust opener')
  for (const command of ['native_about', 'native_selftest', 'native_open_help_path']) {
    assert.match(handlers, new RegExp(`\\b${command}\\b`), `missing Tauri registration: ${command}`)
    assert.match(tauri, new RegExp(`engine::${command}\\(`), `missing core call: ${command}`)
  }
  assert.match(rustDiagnostics, /"app_version":\s*app_version/)
  assert.match(rustDiagnostics, /"lines":\s*lines/)
  assert.ok(/"opened": path\.to_string_lossy\(\)/.test(rustLib), 'Rust opener must preserve the old opened result field')
  assert.ok(!/handshake\?\.python_version/.test(appSource), 'about UI must show the Rust app version only')
  assert.ok(!/runBackendAction\('system\.(?:about|selftest|open_path)'/.test(appSource), 'diagnostics must not use the Python bridge')
  assert.ok(!/runBackendAction\('mcp\.(?:info|test)'/.test(appSource), 'MCP diagnostics must not use legacy Python tools')
  assert.match(agentHost, /"mcpServers"\s*:\s*\[\]/, 'external MCP status must track the empty Agent Host server list')
})

test('selftest message preserves the old UI lines and ok fields', () => {
  const legacyUiResult = { lines: ['几何内核    : OK', '自然语言引擎: OK'], ok: true }
  assert.equal(nativeSelftestMessage(legacyUiResult), '几何内核    : OK\n自然语言引擎: OK\n\n状态：通过')
  assert.equal(nativeSelftestMessage({ lines: ['DSH Runtime: 未安装'], ok: false }), 'DSH Runtime: 未安装\n\n状态：有项目未通过')
})
