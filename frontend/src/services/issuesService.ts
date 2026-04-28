import { apiRequest, apiRequestJson } from './api'

export type SavedIssue = {
  id: number
  lat: number
  lng: number
  title: string
  description: string
  status: string
}

export type CreateIssuePayload = {
  lat: number
  lng: number
  title: string
  description: string
}

export async function listIssues(): Promise<SavedIssue[]> {
  return apiRequestJson<SavedIssue[]>('/api/issues')
}

export async function createIssue(payload: CreateIssuePayload): Promise<void> {
  const response = await apiRequest('/api/issues', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`)
  }
}
