export type WorkerState = 'running' | 'complete' | 'failed' | 'stopped' | 'historical'

export interface Worker {
  id: string
  kind: 'extract' | 'evaluate' | 'report'
  scope: string
  state: WorkerState
  returncode: number | null
  started_at: string
  finished_at: string | null
  stop_requested: boolean
  status: Record<string, unknown> | null
  control: { phase?: string; prompt?: string; signal?: string | null } | null
  progress: { current?: number; total?: number; errors?: number; model?: string | null; message?: string; phase?: string; credential_label?: string | null; request_started_at?: string | null; timeout_seconds?: number | null } | null
  history_key: string
}

export interface Providers {
  configured: Record<string, boolean>
  ollama: { online: boolean; models: string[] }
}

export interface DateInfo {
  date: string
  normalized: number
  extraction_status: string | null
  has_evaluation: boolean
}

export type ExtractionState = 'not_scanned' | 'in_progress' | 'complete' | 'accepted_with_gaps' | 'incomplete' | 'damaged'

export interface ExtractionDateStatus {
  date: string
  status: ExtractionState
  output_exists: boolean
  summary_exists: boolean
  last_updated: string | null
  active_snapshot_count: number
  active_details_fetched: number
  coverage_pct: number | null
  missing_details: number
  db_errors: number
  click_failures: number
  videos: number
  normalized_files: number
  has_evaluation: boolean
  message: string
}

export interface ExtractionMonthStatus {
  month: string | null
  counts: Record<string, number>
  dates: ExtractionDateStatus[]
}

export type EvaluationState = 'waiting_for_input' | 'partial_input' | 'not_started' | 'in_progress' | 'complete' | 'paused_quota' | 'needs_retry' | 'incomplete'

export interface EvaluationDateStatus {
  date: string; status: EvaluationState; input_status: string
  active_conversations: number; completed: number; pending: number; errors: number
  safe: number; unsafe: number; review_required: number; preflight: number
  coverage_pct: number; last_activity: string | null; stop_reason: string | null
  model_breakdown: Record<string, number>; stale_results: number
  run_count: number; has_history: boolean; message: string
}

export interface EvaluationMonthStatus {
  month: string | null
  counts: Record<string, number>
  dates: EvaluationDateStatus[]
}

export interface EvaluationHistory {
  date: string
  runs: Record<string, unknown>[]
  attempts: Record<string, unknown>[]
}

export type DependencyHealth = Record<'extract' | 'evaluate' | 'report', {
  ready: boolean
  missing: string[]
}>

export interface ReportInfo {
  name: string
  month: string
  size: number
  modified: number
  status: string
  canonical: boolean
  warnings: string[]
}

export type CredentialName = 'GEMINI_API_KEY' | 'GROQ_API_KEY' | 'CLOUDFLARE_API_TOKEN' | 'CLOUDFLARE_ACCOUNT_ID' | 'OPENROUTER_API_KEY'

export interface GeminiAccount {
  id: string
  label: string
  email: string
  enabled: boolean
  priority: number
  configured: boolean
}

const API = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  workers: () => request<Worker[]>('/workers'),
  dependencies: () => request<DependencyHealth>('/dependencies'),
  providers: () => request<Providers>('/providers'),
  credentials: () => request<Record<CredentialName, boolean>>('/settings/credentials'),
  saveCredential: (name: CredentialName, value: string) => request<{ name: string; configured: boolean }>(`/settings/credentials/${name}`, {
    method: 'PUT', body: JSON.stringify({ value }),
  }),
  removeCredential: (name: CredentialName) => request<{ name: string; configured: boolean }>(`/settings/credentials/${name}`, { method: 'DELETE' }),
  geminiAccounts: () => request<{ accounts: GeminiAccount[] }>('/settings/gemini-accounts'),
  addGeminiAccount: (payload: { label: string; email: string; api_key: string }) => request<{ account: GeminiAccount }>('/settings/gemini-accounts', { method: 'POST', body: JSON.stringify(payload) }),
  removeGeminiAccount: (id: string) => request<{ deleted: boolean; account_id: string }>(`/settings/gemini-accounts/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  dates: () => request<DateInfo[]>('/dates'),
  extractionStatus: (month?: string) => request<ExtractionMonthStatus>(`/extraction-status${month ? `?month=${encodeURIComponent(month)}` : ''}`),
  extractionDateStatus: (date: string) => request<ExtractionDateStatus>(`/extraction-status/${date}`),
  evaluationStatus: (month?: string) => request<EvaluationMonthStatus>(`/evaluation-status${month ? `?month=${encodeURIComponent(month)}` : ''}`),
  evaluationHistory: (date: string) => request<EvaluationHistory>(`/evaluation-status/${date}/history`),
  evaluationErrors: (date: string) => request<{ date: string; errors: Record<string, unknown>[] }>(`/evaluation-status/${date}/errors`),
  reports: () => request<ReportInfo[]>('/reports'),
  reportReadiness: (month: string) => request<{ ready: boolean; reason: string; source_date_folders: number; evaluator_result_files: number }>(`/reports/readiness?month=${encodeURIComponent(month)}`),
  start: (kind: Worker['kind'], payload: unknown) => request<Worker>(`/workers/${kind}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  stop: (id: string) => request<Worker>(`/workers/${id}/stop`, { method: 'POST' }),
  hideHistory: (id: string) => request<{ hidden: boolean; worker_id: string; hidden_count: number }>(`/workers/history/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  hiddenHistory: () => request<{ hidden: string[] }>('/workers/history/hidden'),
  restoreHistory: () => request<{ restored: number; hidden: string[] }>('/workers/history/restore', { method: 'POST' }),
  continue: (id: string) => request<Worker>(`/workers/${id}/continue`, { method: 'POST' }),
  shutdown: () => request<{ shutting_down: boolean }>('/shutdown', { method: 'POST' }),
  log: async (id: string) => {
    const response = await fetch(`${API}/workers/${id}/log`)
    if (!response.ok) throw new Error('Could not load worker log')
    return response.text()
  },
  reportUrl: (month: string) => `${API}/reports/${month}/download`,
  reportFileUrl: (filename: string) => `${API}/reports/file/${encodeURIComponent(filename)}`,
}
