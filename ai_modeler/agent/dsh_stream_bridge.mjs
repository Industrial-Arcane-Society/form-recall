// Exposes transient DSH assistant-stream frames to the host UI through an
// app-owned JSONL side channel. stdout is reserved for the SDK JSON-RPC stream.
import fs from 'node:fs'

export const name = 'ai-modeler-live-stream-bridge'
export const inject = ['agents']

export function apply(ctx) {
  const target = process.env.AI_MODELER_STREAM_FILE
  if (!target) return

  ctx.on('agent/assistant-stream', ({ frame }) => {
    if (!frame || frame.type !== 'chunk' || !frame.chunk) return
    const kind = String(frame.chunk.type || '')
    if (!['reasoning-delta', 'thinking-delta', 'text-delta'].includes(kind)) return
    // Forward only activity metadata. The desktop UI needs to know that the
    // provider is streaming, not persist or display private reasoning text.
    const text = String(frame.chunk.text || frame.chunk.delta || '')
    const payload = JSON.stringify({ at: Date.now(), kind, characters: text.length }) + '\n'
    try {
      fs.appendFileSync(target, payload, { encoding: 'utf8' })
    } catch (error) {
      console.error('[ai-modeler-live-stream-bridge] unable to write stream frame:', error)
    }
  })
}
