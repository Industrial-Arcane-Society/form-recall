import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = path => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
const root = load('../../../../package.json')
const desktop = load('../package.json')
const base = load('../src-tauri/tauri.conf.json')
const dev = load('../src-tauri/tauri.dev.conf.json')
const viteConfig = readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8')

test('root desktop and browser commands remain distinct', () => {
  assert.equal(root.scripts.dev, 'node scripts/tauri-dev.mjs')
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
  assert.equal(base.build.devUrl, 'http://127.0.0.1:1420')
  assert.equal(base.app.windows.length, 1)
})
test('Tauri devUrl matches Vite IPv4 host and fixed port', () => {
  const host = viteConfig.match(/\bhost:\s*'([^']+)'/)?.[1]
  const port = Number(viteConfig.match(/\bport:\s*(\d+)/)?.[1])
  const devUrl = new URL(base.build.devUrl)

  assert.equal(host, '127.0.0.1')
  assert.equal(devUrl.hostname, host)
  assert.equal(Number(devUrl.port), port)
})
test('release configuration still requires all packaged resources and the staging build hook', () => {
  assert.deepEqual(base.bundle.resources, {
    'target/package-resources/app/': 'app/',
    'target/package-resources/python/': 'python/',
    'target/package-resources/catia-bootstrap/': 'catia-bootstrap/',
    'target/package-resources/occt-runtime/': 'occt-runtime/',
    'target/package-resources/RUNTIME-INFORMATION.txt': 'RUNTIME-INFORMATION.txt',
  })
  assert.equal(base.build.beforeBuildCommand, 'npm run build && node ..\\..\\legacy\\scripts\\build_desktop_resources.mjs')
})
