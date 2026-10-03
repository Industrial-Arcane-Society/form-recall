import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizeToolResultDiagnostic } from './dsh_native_tools.mjs'

test('Cordis tools/result fixture identifies output-contract failures without retaining content', () => {
  const rawMarkers = [
    'private-arguments-marker', 'private prompt marker', 'private result text',
    'C:\\private\\result.png', 'BASE64_PRIVATE_MARKER', 'private error message', 'private error reason',
  ]
  const record = summarizeToolResultDiagnostic({
    tool: 'cad_preview_model',
    stage: 'cordis_tools_result',
    isError: true,
    error: {
      message: rawMarkers[5],
      info: { name: 'ToolOutputError', code: 'INVALID_TOOL_OUTPUT', reason: rawMarkers[6] },
    },
    content: [
      { type: 'text', text: rawMarkers[2] },
      { type: 'image', attachment: {
        attachmentId: rawMarkers[3], mediaType: 'image/png', bytes: 4096, name: rawMarkers[3],
      } },
    ],
  })
  const serialized = JSON.stringify(record)

  assert.deepEqual(record, {
    tool: 'cad_preview_model',
    stage: 'cordis_tools_result',
    isError: true,
    content_blocks: [
      { type: 'text', text_utf8_bytes: Buffer.byteLength(rawMarkers[2], 'utf8') },
      { type: 'image', media_type: 'image/png' },
    ],
    error_type: 'ToolOutputError',
    error_code: 'INVALID_TOOL_OUTPUT',
  })
  for (const marker of rawMarkers) assert.equal(serialized.includes(marker), false)
})

test('native image decode fixture stores only media type and byte counts', () => {
  const marker = 'BASE64_PRIVATE_MARKER'
  const record = summarizeToolResultDiagnostic({
    tool: 'cad_get_reference_image',
    stage: 'native_image_decode',
    isError: false,
    decodedImages: [{ mediaType: 'image/jpeg', decodedBytes: 12_345, data: marker, path: 'C:\\private\\x.jpg' }],
    text: marker,
  })
  const serialized = JSON.stringify(record)

  assert.deepEqual(record, {
    tool: 'cad_get_reference_image',
    stage: 'native_image_decode',
    isError: false,
    text_utf8_bytes: Buffer.byteLength(marker, 'utf8'),
    images: [{ media_type: 'image/jpeg', decoded_bytes: 12_345 }],
  })
  assert.equal(serialized.includes(marker), false)
  assert.equal(serialized.includes('C:'), false)
})

test('diagnostics reduce unknown tool names, phases, and error identifiers to allowlisted values', () => {
  const record = summarizeToolResultDiagnostic({
    tool: 'private tool name',
    stage: 'private stage',
    isError: true,
    error: { message: 'private', info: { name: 'PrivateErrorType', code: 'PRIVATE_CODE' } },
    content: [{ type: 'private-content-type', text: 'private content' }],
  })

  assert.deepEqual(record, {
    tool: 'other', stage: 'other', isError: true,
    content_blocks: [{ type: 'other' }],
    error_type: 'other', error_code: 'other',
  })
  assert.equal(JSON.stringify(record).includes('private'), false)
})
