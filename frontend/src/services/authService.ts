import { apiRequest, apiRequestJson } from './api'

export type AuthUser = {
  id: number
  email: string
  role?: 'admin' | 'user' | string
  approval_status?: 'approved' | 'pending' | string
  can_access_catalog?: boolean
  hidden_tabs?: string[]
}
export type SignupResponse = { status: string; email: string; requested_role?: string }

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

export async function signup(email: string, password: string): Promise<SignupResponse> {
  return apiRequestJson<SignupResponse>('/api/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export async function requestAdminAccess(email: string, password: string): Promise<SignupResponse> {
  return apiRequestJson<SignupResponse>('/api/auth/request-admin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
}

export async function logout(): Promise<void> {
  await apiRequest('/api/auth/logout', { method: 'POST' })
}
