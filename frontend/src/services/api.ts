import { API_BASE, formatApiNetworkError } from '../lib/apiBase'
import { getDeviceLabel, readCurrentDeviceLocation } from '../utils/locationSession'

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

function activityHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    'X-Droid-Device': getDeviceLabel(),
  }
  try {
    const location = readCurrentDeviceLocation()
    if (location) {
      headers['X-Droid-Lat'] = String(location.lat)
      headers['X-Droid-Lng'] = String(location.lng)
      if (typeof location.accuracy === 'number') headers['X-Droid-Location-Accuracy'] = String(location.accuracy)
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
    throw new Error(formatApiNetworkError(API_BASE, error))
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
