import { apiRequest, apiRequestJson } from './api'
import type { Project } from './projectService'

export type AdminUserActivity = {
  user_id: number
  email: string
  role: string
  requested_role?: string
  approval_status?: string
  status: 'Active' | 'Offline'
  current_ip: string
  device_label?: string
  location?: string
  location_accuracy_m?: number
  unique_ip_count: number
  last_accessed_data: string
  last_seen_at: string
}

export type AdminDatasetMetadataPayload = {
  dataset_id: string
  name?: string
  date?: string
  status?: string
  dataset_type?: string
  month?: string
}

export async function getAdminUserActivity(): Promise<AdminUserActivity[]> {
  const data = await apiRequestJson<{ users: AdminUserActivity[] }>('/api/admin/users/activity')
  return data.users ?? []
}

export async function approveAdminUser(userId: number, role: 'admin' | 'user'): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  if (!res.ok) throw new Error(`Approve failed (${res.status})`)
}

export async function assignAdminUserRole(userId: number, role: 'admin' | 'user'): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/role`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  if (!res.ok) throw new Error(`Role update failed (${res.status})`)
}

export async function disapproveAdminUser(userId: number): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/disapprove`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`Disapprove failed (${res.status})`)
}

export async function deleteAdminUser(userId: number): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Delete user failed (${res.status})`)
}

export async function advancedDeleteAdminUser(userId: number): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/advanced`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Advanced delete failed (${res.status})`)
}

export async function getAdminProjectOverride(projectId: string): Promise<{ project: Project & { owner_user_id: number; owner_email: string } }> {
  return apiRequestJson(`/api/admin/override/project/${encodeURIComponent(projectId)}`)
}

export async function updateAdminProjectOverride(
  projectId: string,
  payload: Partial<Omit<Project, 'id'>>,
): Promise<{ project: Project & { owner_user_id: number; owner_email: string } }> {
  return apiRequestJson(`/api/admin/override/project/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function updateAdminDatasetMetadata(
  projectId: string,
  payload: AdminDatasetMetadataPayload,
): Promise<void> {
  const { dataset_id: datasetKey, ...body } = payload
  const res = await apiRequest(`/api/admin/projects/${encodeURIComponent(projectId)}/datasets/${encodeURIComponent(datasetKey)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Admin metadata update failed (${res.status})`)
}

export async function forceDeleteAdminDataset(projectId: string, datasetKey: string): Promise<void> {
  const res = await apiRequest(`/api/admin/projects/${encodeURIComponent(projectId)}/datasets/${encodeURIComponent(datasetKey)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Force delete failed (${res.status})`)
}
