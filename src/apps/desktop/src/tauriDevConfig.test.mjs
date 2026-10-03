import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = path => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
const root = load('../../../../package.json')
const desktop = load('../package.json')
const base = load('../src-tauri/tauri.conf.json')
const dev = load('../src-tauri/tauri.dev.conf.json')

test('root desktop and browser commands remain distinct', () => {
  assert.equal(root.scripts.dev, 'npm --prefix src/apps/desktop run tauri:dev')
  assert.equal(root.scripts['dev:web'], 'npm --prefix src/apps/desktop run dev')
  assert.equal(desktop.scripts.dev, 'vite')
  assert.equal(desktop.scripts['tauri:dev'], 'tauri dev --config src-tauri/tauri.dev.conf.json')
  assert.equal(desktop.scripts['tauri:build'], 'tauri build')
})
test('dev-only merge replaces installer resource mappings without changing launch hooks or windows', () => {
  assert.deepEqual(Object.keys(dev).sort(), ['$schema', 'bundle'])
  assert.deepEqual(Object.keys(dev.bundle), ['resources'])
  assert.deepEqual(dev.bundle.resources, [])
  assert.equal(base.build.beforeDevCommand, 'npm run dev')
  assert.equal(base.build.devUrl, 'http://localhost:1420')
  assert.equal(base.app.windows.length, 1)
})
test('release configuration still requires all packaged resources and the staging build hook', () => {
  assert.deepEqual(base.bundle.resources, {
    'target/package-resources/app/': 'app/',
    'target/package-resources/python/': 'python/',
    'target/package-resources/bootstrap/': 'bootstrap/',
    'target/package-resources/occt-runtime/': 'occt-runtime/',
    'target/package-resources/RUNTIME-INFORMATION.txt': 'RUNTIME-INFORMATION.txt',
  })
  assert.equal(base.build.beforeBuildCommand, 'npm run build && node ..\\..\\legacy\\scripts\\build_desktop_resources.mjs')
})
