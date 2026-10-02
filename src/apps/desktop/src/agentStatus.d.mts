export type ModelAcceptanceView = {
  outcome: 'unavailable' | 'none' | 'candidate' | 'accepted'
  outcomeLabel: string
  finish: string
  geometry: string
  visual: string
}

export function modelAcceptanceView(model: any): ModelAcceptanceView
export function turnStatusView(status: unknown, stopReason?: unknown): { key: string; text: string }
