import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const app = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const tauri = readFileSync(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8')
const handlers = tauri.match(/tauri::generate_handler!\[([\s\S]*?)\]/)?.[1] ?? ''

test('parts and text examples use native Rust commands instead of Python bridge routes', () => {
  assert.match(app, /parts\.value = await invoke\('native_parts_list', \{ query: partsQuery\.value \}\)/)
  assert.match(app, /sampleRows\.value = await invoke\('native_samples_list'\)/)
  assert.doesNotMatch(app, /backendCall\('parts\.list'/)
  assert.doesNotMatch(app, /backendCall\('samples\.list'/)
  assert.match(handlers, /\bnative_parts_list\b/)
  assert.match(handlers, /\bnative_samples_list\b/)
  assert.match(tauri, /fn native_parts_list\([^)]*\)[\s\S]*?engine::native_parts_catalog::list_parts\(query\.as_deref\(\)\)/)
  assert.match(tauri, /fn native_samples_list\(\)[\s\S]*?engine::native_parts_catalog::list_samples\(\)/)
})

test('catalog UI states that descriptive entries do not promise geometry support', () => {
  assert.match(app, /零件目录/)
  assert.match(app, /不代表几何内核已支持对应零件建模/)
  assert.doesNotMatch(app, /标准零件库/)
  assert.doesNotMatch(app, /正在读取参数化零件定义/)
})
