import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const tauriRoot = path.join(desktopRoot, 'src-tauri')
const targetRoot = path.resolve(desktopRoot, process.env.CARGO_TARGET_DIR || path.join(tauriRoot, 'target'))
const appPath = path.join(targetRoot, 'debug', process.platform === 'win32'
  ? 'ai-modeler-webview-smoke.exe'
  : 'ai-modeler-webview-smoke')
const scenarios = [
  // The real active-model generation may be lower than the synthetic revisions
  // used below, so exercise it before those Channel-only model.changed fixtures.
  { prompt: '__f7_webview_smoke__:production-quick-preview', turn: '本轮回复完成', outcome: '候选模型', reply: 'WebView production Quick model completed', preview: true, production: true },
  { prompt: '__f7_webview_smoke__:pure-qa', turn: '本轮回复完成', outcome: '尚无 CADPlan 模型', reply: 'pure-qa' },
  { prompt: '__f7_webview_smoke__:cad-tool-failure', turn: '本轮已停止（tool_error）', outcome: '候选模型', reply: 'cad-tool-failure', feature: 'Fixture base', tool: '工具：cad_preview_model · failed' },
  { prompt: '__f7_webview_smoke__:accepted-current', turn: '本轮回复完成', outcome: '已验收', reply: 'accepted-current', feature: 'Fixture base' },
  { prompt: '__f7_webview_smoke__:stale-finish', turn: '本轮回复完成', outcome: '候选模型', reply: 'stale-finish', feature: 'Fixture base' },
]

function resolveExecutable(name, environmentKey) {
  const explicit = process.env[environmentKey]
  const extensions = process.platform === 'win32' ? ['', '.exe', '.cmd', '.bat'] : ['']
  const candidates = [
    explicit,
    ...String(process.env.PATH || '').split(path.delimiter).filter(Boolean)
      .flatMap(directory => extensions.map(extension => path.join(directory, `${name}${extension}`))),
    ...(process.platform === 'win32'
      ? extensions.map(extension => path.join(process.env.USERPROFILE || '', '.cargo', 'bin', `${name}${extension}`))
      : []),
  ].filter(Boolean).map(candidate => path.resolve(candidate))
  return candidates.find(candidate => path.isAbsolute(candidate) && isFile(candidate))
}

function isFile(candidate) {
  try {
    return fs.statSync(candidate).isFile()
  } catch {
    return false
  }
}

function run(command, args, cwd, env = process.env) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit', windowsHide: true, env })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status ?? 'unknown'}`)
}

async function reservePort() {
  const server = net.createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const port = server.address().port
  await new Promise(resolve => server.close(resolve))
  return port
}

function sleep(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

async function requestJson(baseUrl, method, route, body, timeoutMs = 15000) {
  const response = await fetch(new URL(route, baseUrl), {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json; charset=utf-8' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  })
  let payload
  try { payload = await response.json() } catch { payload = null }
  if (!response.ok || payload?.value?.error) {
    const error = payload?.value?.error || `HTTP ${response.status}`
    throw new Error(`WebDriver ${method} ${route} failed: ${error}`)
  }
  return payload?.value
}

async function execute(baseUrl, sessionId, script, args = [], asyncScript = false) {
  return requestJson(
    baseUrl,
    'POST',
    `/session/${sessionId}/execute/${asyncScript ? 'async' : 'sync'}`,
    { script, args },
  )
}

async function submitFixture(baseUrl, sessionId, prompt) {
  const result = await execute(baseUrl, sessionId, `const done = arguments[arguments.length - 1];
    const input = document.querySelector('textarea[aria-label="建模描述"]');
    if (!input) { done({ ok: false, reason: 'composer_missing' }); return; }
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(input, arguments[0]);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    Promise.resolve().then(() => {
      const button = document.querySelector('button.send-button');
      if (!button || button.disabled) { done({ ok: false, reason: 'send_not_ready' }); return; }
      button.click();
      done({ ok: true });
    });`, [prompt], true)
  assert.equal(result?.ok, true, `Could not submit fixture through the production composer: ${result?.reason || 'unknown'}`)
}

async function readUiState(baseUrl, sessionId) {
  return execute(baseUrl, sessionId, `return (() => {
    const rows = [...document.querySelectorAll('.acceptance-summary__row strong')];
    const messages = [...document.querySelectorAll('.chat-message')].map(node => node.innerText || '');
    const canvas = document.querySelector('.viewport-canvas canvas');
    let webgl = false;
    if (canvas) {
      const context = canvas.getContext('webgl2') || canvas.getContext('webgl');
      webgl = Boolean(context && !context.isContextLost());
    }
    return {
      ready: Boolean(document.querySelector('.app-frame') && document.querySelector('textarea[aria-label="建模描述"]')),
      turn: rows[0]?.innerText || '',
      outcome: rows[1]?.innerText || '',
      modelName: document.querySelector('.document-title strong')?.innerText || '',
      features: [...document.querySelectorAll('.feature-row')].map(node => node.innerText || ''),
      messages,
      acceptance: document.querySelector('.acceptance-summary')?.innerText || '',
      viewportStatus: document.querySelector('.canvas-status .status-message')?.innerText || '',
      canvasReady: Boolean(canvas && canvas.width > 0 && canvas.height > 0 && webgl),
    };
  })()`)
}

async function waitFor(baseUrl, sessionId, predicate, label, timeoutMs = 30000) {
  const until = Date.now() + timeoutMs
  let state
  while (Date.now() < until) {
    state = await readUiState(baseUrl, sessionId)
    if (predicate(state)) return state
    await sleep(150)
  }
  throw new Error(`Timed out waiting for ${label}; last state=${JSON.stringify({ turn: state?.turn, outcome: state?.outcome, modelName: state?.modelName, features: state?.features, viewportStatus: state?.viewportStatus, messages: state?.messages?.slice(-3) })}`)
}

function startDriver(driverPath, edgeDriverPath, port, nativePort) {
  const env = { ...process.env }
  if (edgeDriverPath) {
    env.PATH = `${path.dirname(edgeDriverPath)}${path.delimiter}${env.PATH || ''}`
  }
  const args = ['--port', String(port), '--native-port', String(nativePort)]
  if (edgeDriverPath) args.push('--native-driver', edgeDriverPath)
  const child = spawn(driverPath, args, { stdio: 'ignore', windowsHide: true, env })
  child.launchError = null
  child.on('error', error => { child.launchError = error })
  return child
}

async function waitForDriver(driver, baseUrl, timeoutMs = 20000) {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    if (driver.launchError) throw new Error(`Unable to start tauri-driver: ${driver.launchError.message}`)
    if (driver.exitCode !== null) throw new Error(`tauri-driver exited with status ${driver.exitCode}`)
    try {
      await requestJson(baseUrl, 'GET', '/status', undefined, 1000)
      return
    } catch {
      await sleep(150)
    }
  }
  throw new Error('Timed out waiting for tauri-driver WebDriver endpoint')
}

async function stopDriver(driver, sessionId, baseUrl) {
  if (sessionId) {
    try { await requestJson(baseUrl, 'DELETE', `/session/${sessionId}`, undefined, 5000) } catch {}
  }
  if (!driver || driver.exitCode !== null) return
  if (process.platform === 'win32' && Number.isInteger(driver.pid)) {
    spawnSync('taskkill.exe', ['/PID', String(driver.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
  } else {
    driver.kill('SIGTERM')
  }
  await Promise.race([
    new Promise(resolve => driver.once('exit', resolve)),
    sleep(5000),
  ])
  if (driver.exitCode === null) driver.kill('SIGKILL')
}

async function main() {
  if (process.platform !== 'win32') {
    throw new Error('This acceptance runner currently requires Windows WebView2 and tauri-driver.')
  }

  const tauriDriver = resolveExecutable('tauri-driver', 'AIMODELER_TAURI_DRIVER')
  const edgeDriver = resolveExecutable('msedgedriver', 'AIMODELER_EDGE_DRIVER')
  if (!tauriDriver || !edgeDriver) {
    throw new Error('Missing WebDriver prerequisites. Install tauri-driver and the Microsoft Edge WebDriver matching the installed WebView2 Runtime; set AIMODELER_TAURI_DRIVER / AIMODELER_EDGE_DRIVER if they are outside PATH.')
  }

  const occtManifest = process.env.AIMODELER_OCCT_MANIFEST
    || [
      path.join(tauriRoot, 'target', 'f4-occt', 'runtime-manifest.json'),
      path.join(tauriRoot, 'target', 'package-resources', 'occt-runtime', 'runtime-manifest.json'),
    ].find(isFile)
  if (!occtManifest || !isFile(occtManifest)) {
    throw new Error('Missing verified OCCT runtime manifest. Set AIMODELER_OCCT_MANIFEST to an existing OCCT 7.9.3 runtime-manifest.json; the production geometry scenario will not substitute a synthetic GLB.')
  }
  process.env.AIMODELER_OCCT_MANIFEST = path.resolve(occtManifest)

  if (process.env.npm_execpath) {
    run(process.execPath, [process.env.npm_execpath, 'run', 'build'], desktopRoot)
  } else {
    const npm = resolveExecutable('npm', 'AIMODELER_NPM')
    if (!npm) throw new Error('npm was not found; build the production frontend before running this harness.')
    const result = spawnSync(npm, ['run', 'build'], { cwd: desktopRoot, stdio: 'inherit', windowsHide: true, shell: npm.endsWith('.cmd') })
    if (result.error) throw result.error
    if (result.status !== 0) throw new Error(`npm run build exited with status ${result.status ?? 'unknown'}`)
  }
  run('cargo', [
    'build',
    '--manifest-path', path.join(tauriRoot, 'Cargo.toml'),
    '--bin', 'ai-modeler-webview-smoke',
    '--features', 'agent-webview-smoke,tauri/custom-protocol',
  ], desktopRoot, { ...process.env, TAURI_CONFIG: JSON.stringify({ bundle: { resources: [] } }) })
  assert.ok(isFile(appPath), `Smoke application was not built at ${appPath}`)

  const port = await reservePort()
  let nativePort = await reservePort()
  while (nativePort === port) nativePort = await reservePort()
  const baseUrl = `http://127.0.0.1:${port}`
  const driver = startDriver(tauriDriver, edgeDriver, port, nativePort)
  let sessionId = ''

  try {
    await waitForDriver(driver, baseUrl)
    const session = await requestJson(baseUrl, 'POST', '/session', {
      capabilities: {
        alwaysMatch: {
          browserName: 'wry',
          'tauri:options': { application: appPath },
        },
      },
    }, 120000)
    sessionId = session?.sessionId || ''
    assert.ok(sessionId, 'tauri-driver did not return a WebDriver session')

    await waitFor(baseUrl, sessionId, state => state.ready, 'the production Vue application to mount', 45000)
    for (const scenario of scenarios) {
      await submitFixture(baseUrl, sessionId, scenario.prompt)
      const state = await waitFor(
        baseUrl,
        sessionId,
        current => current.messages.some(message => message.includes('处理失败：'))
          || (current.turn === scenario.turn
            && current.outcome === scenario.outcome
            && current.messages.some(message => message.includes(scenario.production
              ? scenario.reply
              : `WebView fixture completed: ${scenario.reply}`))
            && (!scenario.preview || (current.viewportStatus === '三维预览已更新' && current.canvasReady))),
        `${scenario.reply} result through the production Vue handler`,
        scenario.production ? 90000 : 30000,
      )
      const failure = state.messages.find(message => message.includes('处理失败：'))
      assert.equal(failure, undefined, `production scenario failed before model.changed: ${failure || ''}`)
      assert.equal(state.turn, scenario.turn)
      assert.equal(state.outcome, scenario.outcome)
      if (scenario.feature) {
        assert.ok(state.modelName.includes(`F7 WebView fixture`))
        assert.ok(state.features.some(feature => feature.includes(scenario.feature)))
      }
      if (scenario.tool) assert.ok(state.messages.some(message => message.includes(scenario.tool)))
      if (scenario.preview) {
        if (scenario.production) {
          assert.ok(state.modelName && !state.modelName.includes('F7 WebView fixture'), 'the production Quick builder should publish its committed active model')
          assert.ok(state.features.length > 0, 'the OCCT-generated model should publish production CAD features')
        }
        assert.equal(state.viewportStatus, '三维预览已更新', 'the production read_asset + GLTFLoader path should report a loaded mesh')
        assert.equal(state.canvasReady, true, 'the production CAD viewport should have an active WebGL canvas')
      }
      if (scenario.reply === 'stale-finish') {
        assert.ok(state.acceptance.includes('候选模型'))
        assert.ok(!state.acceptance.includes('已验收'))
      }
      if (scenario.preview) {
        console.log(scenario.production
          ? 'PASS: production Quick parser -> OCCT -> committed active model -> model.changed -> read_asset IPC -> GLTFLoader -> CAD viewport/WebGL canvas'
          : 'PASS: production read_asset IPC -> GLTFLoader -> CAD viewport/WebGL canvas')
      } else {
        console.log(`PASS: hidden Tauri WebView delivered Rust Channel events for ${scenario.reply}`)
      }
    }
  } finally {
    await stopDriver(driver, sessionId, baseUrl)
  }
  console.log('PASS: real Windows WebView2 + Tauri Channel + production Vue handler; no DSH/provider/CATIA used.')
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
