/** Vite: define VITE_API_URL in frontend/.env (trailing slash optional). */
export const API_BASE_URL = String(import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

const ACCESS_TOKEN_KEY = 'acquisition_hub_jwt'

/** @type {(() => void) | null} */
let unauthorizedHandler = null

export function registerUnauthorizedHandler(fn) {
  unauthorizedHandler = typeof fn === 'function' ? fn : null
}

export function getAccessToken() {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setAccessToken(token) {
  try {
    if (token) localStorage.setItem(ACCESS_TOKEN_KEY, token)
    else localStorage.removeItem(ACCESS_TOKEN_KEY)
  } catch {
    // ignore
  }
}

export function clearAccessToken() {
  setAccessToken('')
}

export function triggerUnauthorizedLogout() {
  clearAccessToken()
  unauthorizedHandler?.()
}

export function apiAuthHeaders() {
  const t = getAccessToken()
  if (!t) return {}
  return { Authorization: `Bearer ${t}` }
}

/**
 * Fetch with Bearer token; on 401 clears storage and runs the registered handler (logout + redirect).
 * @param {RequestInfo | URL} url
 * @param {RequestInit} [init]
 */
export async function authFetch(url, init = {}) {
  const headers = new Headers(init.headers ?? undefined)
  const bearer = getAccessToken()
  if (bearer) {
    headers.set('Authorization', `Bearer ${bearer}`)
  }
  if (init.body != null && typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(url, { ...init, headers })
  if (response.status === 401) {
    triggerUnauthorizedLogout()
  }
  return response
}

/** For `/document/...` URLs used in img/iframe src (no Authorization header). */
export function appendAuthTokenQuery(url) {
  if (url == null || url === '') return url
  const u = String(url)
  const t = getAccessToken()
  if (!t) return u
  const sep = u.includes('?') ? '&' : '?'
  return `${u}${sep}token=${encodeURIComponent(t)}`
}

export function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${p}`
}
