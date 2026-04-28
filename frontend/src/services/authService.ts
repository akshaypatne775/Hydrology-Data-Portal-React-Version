import { apiRequest, apiRequestJson } from './api'

export type AuthUser = { id: number; email: string }

export async function getCurrentUser(): Promise<AuthUser> {
  return apiRequestJson<AuthUser>('/api/auth/me')
}

export async function login(email: string, password: string): Promise<void> {
  await apiRequestJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export async function signup(email: string, password: string): Promise<void> {
  await apiRequestJson('/api/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export async function logout(): Promise<void> {
  await apiRequest('/api/auth/logout', { method: 'POST' })
}
