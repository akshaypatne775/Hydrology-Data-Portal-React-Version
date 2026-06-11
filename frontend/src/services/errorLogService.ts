import { apiRequest } from './api'

type ClientErrorLogPayload = {
  area: string
  message: string
  url?: string
  stack?: string
  project_id?: string
  dataset_id?: string
  extra?: Record<string, unknown>
}

const RECENT_LOGS = new Map<string, number>()
const LOG_DEDUPE_MS = 5000

export function logClientError(payload: ClientErrorLogPayload): void {
  const message = String(payload.message || '').trim()
  if (!message) return
  const key = `${payload.area}:${message}:${payload.url || ''}`.slice(0, 500)
  const now = Date.now()
  const last = RECENT_LOGS.get(key) || 0
  if (now - last < LOG_DEDUPE_MS) return
  RECENT_LOGS.set(key, now)

  void apiRequest('/api/client-error-log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      area: payload.area,
      message: message.slice(0, 12000),
      url: String(payload.url || window.location.href).slice(0, 1000),
      stack: String(payload.stack || '').slice(0, 6000),
      project_id: payload.project_id || '',
      dataset_id: payload.dataset_id || '',
      extra: payload.extra || {},
    }),
  }).catch(() => {
    // Logging must never break the user's workflow.
  })
}
