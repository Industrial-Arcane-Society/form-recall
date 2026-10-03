import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const appSource = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')

test('settings UI uses native Tauri commands for provider management', () => {
  for (const command of [
    'native_settings_load',
    'native_settings_save',
    'native_provider_templates',
    'native_provider_template',
    'native_provider_fetch_models',
  ]) {
    assert.ok(appSource.includes(`invoke<any>('${command}'`) || appSource.includes(`invoke<any[]>('${command}'`), `missing native invoke ${command}`)
  }
  for (const method of [
    'settings.get',
    'settings.save',
    'settings.provider_templates',
    'settings.provider_template',
    'settings.fetch_models',
  ]) {
    assert.ok(!appSource.includes(`backendCall('${method}'`), `settings request still uses Python bridge: ${method}`)
  }
})
