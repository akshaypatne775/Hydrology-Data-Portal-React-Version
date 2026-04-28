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
    const response = await fetch(buildUrl(path), {
      credentials: 'include',
      ...init,
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
