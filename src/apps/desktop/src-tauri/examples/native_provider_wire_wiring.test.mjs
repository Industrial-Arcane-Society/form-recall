import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const runner = readFileSync(new URL('./native_agent_coldstart_acceptance.mjs', import.meta.url), 'utf8')
const host = readFileSync(new URL('../../../../core/cad-engine/src/agent_host.rs', import.meta.url), 'utf8')

test('cold-start runner supplies only the selected provider route identity to the wire observer', () => {
  assert.match(runner, /AI_MODELER_PROVIDER_BASE_URL:\s*provider\.base_url/)
  assert.match(runner, /AI_MODELER_PROVIDER_MODEL:\s*provider\.model/)
  assert.match(runner, /AIMODELER_LLM_API_KEY:\s*apiKey/)
})

test('production DSH child inherits provider route identity from native host environment', () => {
  assert.match(host, /OsString::from\("AI_MODELER_PROVIDER_BASE_URL"\)[\s\S]{0,160}provider\.base_url\.clone\(\)/)
  assert.match(host, /OsString::from\("AI_MODELER_PROVIDER_MODEL"\)[\s\S]{0,160}provider\.model\.clone\(\)/)
})
