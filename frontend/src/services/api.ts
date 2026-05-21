import { API_BASE } from '../lib/apiBase'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function buildUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${normalizedPath}`
}

function deviceLabel(): string {
  const ua = navigator.userAgent
  const os = ua.includes('Windows') ? 'Windows' : ua.includes('Mac OS') ? 'macOS' : ua.includes('Android') ? 'Android' : ua.includes('iPhone') || ua.includes('iPad') ? 'iOS' : 'Unknown OS'
  const browser = ua.includes('Edg/') ? 'Edge' : ua.includes('Chrome/') ? 'Chrome' : ua.includes('Firefox/') ? 'Firefox' : ua.includes('Safari/') ? 'Safari' : 'Browser'
  return `${browser} on ${os}`
}

function activityHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    'X-Droid-Device': deviceLabel(),
  }
  try {
    const raw = window.localStorage.getItem('droid:location')
    if (raw) {
      const parsed = JSON.parse(raw) as { lat?: number; lng?: number; accuracy?: number }
      if (typeof parsed.lat === 'number' && typeof parsed.lng === 'number') {
        headers['X-Droid-Lat'] = String(parsed.lat)
        headers['X-Droid-Lng'] = String(parsed.lng)
        if (typeof parsed.accuracy === 'number') headers['X-Droid-Location-Accuracy'] = String(parsed.accuracy)
      }
    }
  } catch {
    // keep request usable if local storage is unavailable
  }
  return headers
}

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string; message?: string }
    return data.detail || data.message || `Request failed (${response.status})`
  } catch {
    return `Request failed (${response.status})`
  }
}

export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  try {
    const headers = new Headers(activityHeaders())
    if (init.headers) {
      new Headers(init.headers).forEach((value, key) => headers.set(key, value))
    }
    const response = await fetch(buildUrl(path), {
      credentials: 'include',
      ...init,
      headers,
    })
    return response
  } catch (error) {
    throw new Error(
      error instanceof Error ? error.message : 'Network request failed',
    )
  }
}

export async function apiRequestJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  try {
    const response = await apiRequest(path, init)
    if (!response.ok) {
      throw new ApiError(await parseError(response), response.status)
    }
    return (await response.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new Error(error instanceof Error ? error.message : 'Request failed')
  }
}
