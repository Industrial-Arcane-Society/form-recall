import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { findTauriCli, formatInstallHelp, runTauriDev } from './tauri-dev.mjs'

const testDir = path.dirname(fileURLToPath(import.meta.url))
const scriptPath = path.join(testDir, 'tauri-dev.mjs')
const installCommand = 'npm ci --prefix src/apps/desktop'

function fixture(t, { nativeBinding = true, cliEntry = true, cliVersion = '2.12.1' } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aimodeler-tauri-dev-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))

  const desktopDir = path.join(root, 'src', 'apps', 'desktop')
  const cliDir = path.join(desktopDir, 'node_modules', '@tauri-apps', 'cli')
  const nativeDir = path.join(desktopDir, 'node_modules', '@tauri-apps', 'cli-win32-x64-msvc')
  mkdirSync(cliDir, { recursive: true })
  writeFileSync(path.join(desktopDir, 'package.json'), JSON.stringify({
    devDependencies: { '@tauri-apps/cli': '^2.12.1' },
  }))
  writeFileSync(path.join(desktopDir, 'package-lock.json'), JSON.stringify({
    lockfileVersion: 3,
    packages: {
      'node_modules/@tauri-apps/cli': { version: '2.12.1' },
      'node_modules/@tauri-apps/cli-win32-x64-msvc': { version: '2.12.1' },
    },
  }))
  writeFileSync(path.join(cliDir, 'package.json'), JSON.stringify({
    name: '@tauri-apps/cli',
    version: cliVersion,
    main: 'main.js',
    bin: { tauri: 'tauri.js' },
    optionalDependencies: { '@tauri-apps/cli-win32-x64-msvc': '2.12.1' },
  }))
  writeFileSync(path.join(cliDir, 'main.js'), '// fixture')
  if (cliEntry) {
    writeFileSync(path.join(cliDir, 'tauri.js'), [
      "const fs = require('node:fs')",
      "fs.writeFileSync(process.env.AIMODELER_TAURI_TEST_OUTPUT, JSON.stringify({ args: process.argv.slice(2), cwd: process.cwd() }))",
      'process.exit(Number(process.env.AIMODELER_TAURI_TEST_EXIT_CODE || 0))',
    ].join('\n'))
  }
  if (nativeBinding) {
    mkdirSync(nativeDir, { recursive: true })
    writeFileSync(path.join(nativeDir, 'package.json'), JSON.stringify({
      name: '@tauri-apps/cli-win32-x64-msvc',
      version: '2.12.1',
      main: 'cli.win32-x64-msvc.node',
    }))
    writeFileSync(path.join(nativeDir, 'cli.win32-x64-msvc.node'), 'fixture')
  }
  return { root, desktopDir }
}

test('no desktop node_modules fails fast with the exact clean-install command', (t) => {
  const { root, desktopDir } = fixture(t)
  rmSync(path.join(desktopDir, 'node_modules'), { recursive: true, force: true })

  const result = findTauriCli(desktopDir, { platform: 'win32', arch: 'x64' })
  assert.equal(result.ok, false)
  assert.match(result.reason, /未安装/)
  assert.ok(formatInstallHelp(result.reason).includes(installCommand))

  // Execute a copy inside the isolated fixture. Running the repository script
  // would resolve its real repo root and accidentally use the host's installed
  // desktop node_modules, making this missing-install case environment-dependent.
  const fixtureScripts = path.join(root, 'scripts')
  mkdirSync(fixtureScripts, { recursive: true })
  const fixtureScript = path.join(fixtureScripts, 'tauri-dev.mjs')
  writeFileSync(fixtureScript, readFileSync(scriptPath, 'utf8'))
  const run = spawnSync(process.execPath, [fixtureScript, '--version'], {
    cwd: root,
    encoding: 'utf8',
  })
  assert.equal(run.status, 1)
  assert.match(run.stderr, /npm ci --prefix src\/apps\/desktop/)
  assert.match(run.stderr, /npm run dev/)
})

test('partial install with a missing platform binding asks for npm ci', (t) => {
  const { desktopDir } = fixture(t, { nativeBinding: false })
  const result = findTauriCli(desktopDir, { platform: 'win32', arch: 'x64' })

  assert.equal(result.ok, false)
  assert.match(result.reason, /cli-win32-x64-msvc 未安装/)
  assert.match(formatInstallHelp(result.reason), /npm ci --prefix src\/apps\/desktop/)
})

test('partial install with an absent CLI entry asks for npm ci', (t) => {
  const { desktopDir } = fixture(t, { cliEntry: false })
  const result = findTauriCli(desktopDir, { platform: 'win32', arch: 'x64' })

  assert.equal(result.ok, false)
  assert.match(result.reason, /缺少 CLI 入口文件/)
})

test('stale installed CLI version is treated as an incomplete install', (t) => {
  const { desktopDir } = fixture(t, { cliVersion: '2.11.0' })
  const result = findTauriCli(desktopDir, { platform: 'win32', arch: 'x64' })

  assert.equal(result.ok, false)
  assert.match(result.reason, /已安装 2\.11\.0，lockfile 要求 2\.12\.1/)
})

test('installed CLI receives dev args and its exit code is preserved', async (t) => {
  const { root, desktopDir } = fixture(t)
  const cli = findTauriCli(desktopDir, { platform: 'win32', arch: 'x64' })
  assert.equal(cli.ok, true)

  const outputPath = path.join(root, 'invocation.json')
  const exitCode = await runTauriDev({
    cliEntry: cli.cliEntry,
    desktopDir,
    args: ['--verbose', '--no-watch'],
    env: {
      ...process.env,
      AIMODELER_TAURI_TEST_OUTPUT: outputPath,
      AIMODELER_TAURI_TEST_EXIT_CODE: '23',
    },
    stdio: 'ignore',
  })

  assert.equal(exitCode, 23)
  assert.deepEqual(JSON.parse(readFileSync(outputPath, 'utf8')), {
    args: ['dev', '--config', 'src-tauri/tauri.dev.conf.json', '--verbose', '--no-watch'],
    cwd: desktopDir,
  })
})
