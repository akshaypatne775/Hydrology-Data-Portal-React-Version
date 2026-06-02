import { apiUrl } from '../apiConfig'

/**
 * POST /api/login — no auth header. Returns access_token string.
 * @param {string} username
 * @param {string} password
 */
export async function loginRequest(username, password) {
  const response = await fetch(apiUrl('/api/login'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!response.ok) {
    let detail = 'Login failed'
    try {
      const errJson = await response.json()
      detail = errJson?.detail || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  const data = await response.json()
  const token = data?.access_token
  if (!token || typeof token !== 'string') {
    throw new Error('Invalid login response')
  }
  return token
}
