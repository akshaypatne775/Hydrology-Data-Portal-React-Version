import { apiRequest, apiRequestJson } from './api'
import type { Project } from './projectService'

export type AdminUserActivity = {
  user_id: number
  email: string
  role: string
  requested_role?: string
  approval_status?: string
  can_access_catalog?: boolean
  can_upload_data?: boolean
  location_required?: boolean
  hidden_tabs?: string[]
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
  height_offset?: number
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

export async function setAdminUserCatalogAccess(userId: number, enabled: boolean): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/catalog-access`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Data Catalog access update failed (${res.status})`)
}

export async function setAdminUserUploadAccess(userId: number, enabled: boolean): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/upload-access`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`User upload access update failed (${res.status})`)
}

export async function setAdminUserLocationRequired(userId: number, enabled: boolean): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/location-required`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Location requirement update failed (${res.status})`)
}

export async function setAdminUserHiddenTabs(userId: number, hiddenTabs: string[]): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/hidden-tabs`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hidden_tabs: hiddenTabs }),
  })
  if (!res.ok) throw new Error(`Hidden tabs update failed (${res.status})`)
}

export async function resetAdminUserPassword(userId: number, password: string): Promise<void> {
  const res = await apiRequest(`/api/admin/users/${encodeURIComponent(String(userId))}/password`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) throw new Error(`Password reset failed (${res.status})`)
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
  const { dataset_id: datasetKey, name, ...rest } = payload
  if (name?.trim()) {
    const renameRes = await apiRequest(
      `/api/admin/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetKey)}/rename`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      },
    )
    if (!renameRes.ok) throw new Error(`Admin dataset rename failed (${renameRes.status})`)
  }
  const body = rest
  if (!Object.values(body).some((value) => value !== undefined)) return
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
  if (!res.ok) {
    const detail = await res.json().catch(() => null) as { detail?: string } | null
    throw new Error(detail?.detail || `Force delete failed (${res.status})`)
  }
}

export type AdminManualBulkImportTask = {
  source_folder: string
  kind: 'las' | 'ortho' | 'dtm' | 'dsm'
}

export type AdminLocateSourceMode = 'folder' | 'file'

export async function adminManualBulkImport(
  projectId: string,
  payload: { tasks: AdminManualBulkImportTask[]; max_parallel?: number },
): Promise<{ status: string; message: string; project_id: string; task_count: number; file_count?: number }> {
  return apiRequestJson('/api/admin/manual-bulk-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      ...payload,
    }),
  })
}

export async function adminLocateFolder(
  initialPath = '',
  options?: { kind?: AdminManualBulkImportTask['kind']; mode?: AdminLocateSourceMode },
): Promise<{ status: string; folder_path: string }> {
  return apiRequestJson('/api/admin/locate-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      initial_path: initialPath,
      kind: options?.kind || '',
      mode: options?.mode || 'folder',
    }),
  })
}
