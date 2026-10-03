import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const manualOnline = appSource.match(/async function manualOnline\(\) \{[\s\S]*?\n\}/)?.[0] || ''
const onlineModal = appSource.match(/<div v-if="onlineOpen"[\s\S]*?<\/div><footer class="modal-footer">/)?.[0] || ''

test('manual online lookup calls the native command instead of the Python bridge', () => {
  assert.match(manualOnline, /invoke<any>\('native_online_ask', \{ query \}\)/)
  assert.doesNotMatch(manualOnline, /runBackendAction\(['"]online\.search/)
  assert.match(manualOnline, /onlineResult\.value = \{ ok: false, results: \[\], suggestion: null, reason, query, notes: \[\] \}/)
})

test('lookup modal presents legacy suggestion, dimensions, notes, and result rows', () => {
  assert.match(onlineModal, /onlineResult\.suggestion/)
  assert.match(onlineModal, /onlineResult\.params/)
  assert.match(onlineModal, /onlineResult\.notes/)
  assert.match(onlineModal, /onlineResult\.results/)
  assert.match(onlineModal, /onlineResult\.page_read_errors/)
  assert.match(appSource, /:disabled="!onlineResult\?\.suggestion"/)
})

test('returned suggestion is the text inserted into the modeling prompt', () => {
  const useOnlineSuggestion = appSource.match(/async function useOnlineSuggestion\(\) \{[\s\S]*?\n\}/)?.[0] || ''
  assert.match(useOnlineSuggestion, /onlineResult\.value\?\.suggestion/)
  assert.match(useOnlineSuggestion, /prompt\.value = String\(text\)/)
})
