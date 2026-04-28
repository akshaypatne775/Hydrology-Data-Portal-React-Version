import { getTileBaseUrl } from '../components/MapViewer/tileSources'

function getRuntimeEnvVar(name: string): string | undefined {
  const viteVal = import.meta.env[name as keyof ImportMetaEnv]
  if (typeof viteVal === 'string' && viteVal.trim()) {
    return viteVal.trim()
  }
  const processEnv = (globalThis as { process?: { env?: Record<string, string | undefined> } })
    .process?.env
  const processVal = processEnv?.[name]
  return processVal?.trim() || undefined
}

function defaultApiBase(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, '')
  }
  return 'http://127.0.0.1:8000'
}

/**
 * Origin of the FastAPI app (chunk upload, merge, status, issues, …).
 *
 * - `VITE_API_BASE_URL` / `process.env.VITE_API_BASE_URL` when set
 * - Else derived from `VITE_TILE_BASE_URL` / `VITE_S3_TILE_BASE_URL` by removing a trailing `/tiles`
 * - Else browser origin (same-host deployment)
 */
export function getApiBaseUrl(): string {
  const explicit = getRuntimeEnvVar('VITE_API_BASE_URL')
  if (explicit) {
    return explicit.replace(/\/+$/, '')
  }

  const tileBase = getTileBaseUrl()?.replace(/\/+$/, '')
  if (!tileBase) {
    return defaultApiBase()
  }

  if (tileBase.toLowerCase().endsWith('/tiles')) {
    return tileBase.slice(0, -'/tiles'.length)
  }

  try {
    return new URL(tileBase).origin
  } catch {
    return defaultApiBase()
  }
}

/** Deployment-ready API origin resolved from environment. */
export const API_BASE = getApiBaseUrl()

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return fetch(`${API_BASE}${normalizedPath}`, {
    credentials: 'include',
    ...init,
  })
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init)
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const data = (await res.json()) as { detail?: string }
      if (data?.detail) detail = data.detail
    } catch {
      // no-op: keep default detail
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

/** Human-readable hint when fetch fails (backend down, wrong host, CORS, etc.). */
export function formatApiNetworkError(apiBase: string, cause: unknown): string {
  const isTypeError = cause instanceof TypeError
  const msg = cause instanceof Error ? cause.message : ''
  const looksLikeNetwork =
    isTypeError &&
    (msg === 'Failed to fetch' ||
      msg.includes('fetch') ||
      msg.includes('NetworkError') ||
      msg.includes('Network request failed'))

  if (looksLikeNetwork) {
    return `Cannot reach the API at ${apiBase}. Start the FastAPI server (e.g. uvicorn) on that host/port, or set VITE_API_BASE_URL in .env.local if the API runs elsewhere.`
  }

  return cause instanceof Error ? cause.message : 'Request failed.'
}
