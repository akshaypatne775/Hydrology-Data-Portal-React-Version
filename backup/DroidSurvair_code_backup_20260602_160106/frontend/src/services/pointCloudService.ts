import { apiRequest } from './api'

export type PointCloudStatus = {
  ready?: boolean
  failed?: boolean
  error?: string
  tileset_url?: string
}

export async function getPointCloudStatus(
  projectId: string,
  tilesetId?: string,
): Promise<PointCloudStatus | null> {
  const query = tilesetId ? `?tileset_id=${encodeURIComponent(tilesetId)}` : ''
  const res = await apiRequest(`/api/pointcloud-status/${encodeURIComponent(projectId)}${query}`, {
    cache: 'no-store',
  })
  if (!res.ok) return null
  return (await res.json()) as PointCloudStatus
}

export async function uploadChunk(chunkForm: FormData): Promise<Response> {
  return apiRequest('/api/upload-chunk', {
    method: 'POST',
    body: chunkForm,
  })
}

export async function completeUpload(payload: {
  filename: string
  totalChunks: number
  project_id: string
}): Promise<Response> {
  return apiRequest('/api/complete-upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
