export interface DiagnosticStatusRow {
  key: string
  label: string
  value: string
  footerValue: string
  ready: boolean | null
}

export function diagnosticStatusRows(readiness: unknown): DiagnosticStatusRow[]
export function nativeSelftestMessage(report: unknown): string
