import { apiRequest, apiRequestJson } from './api'

export type CameraView = {
  id: string
  name: string
  lat: number
  lng: number
  height: number
  heading: number
  pitch: number
  roll: number
  created_at?: string
  updated_at?: string
}

export type CameraViewPayload = Omit<CameraView, 'id' | 'created_at' | 'updated_at'>

export async function getCameraViews(projectId: string): Promise<CameraView[]> {
  const data = await apiRequestJson<{ views: CameraView[] }>(
    `/api/projects/${encodeURIComponent(projectId)}/camera-views`,
    { cache: 'no-store' },
  )
  return data.views ?? []
}

export async function saveCameraView(
  projectId: string,
  payload: CameraViewPayload,
): Promise<CameraView> {
  return apiRequestJson<CameraView>(`/api/projects/${encodeURIComponent(projectId)}/camera-views`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deleteCameraView(projectId: string, viewId: string): Promise<void> {
  const res = await apiRequest(
    `/api/projects/${encodeURIComponent(projectId)}/camera-views/${encodeURIComponent(viewId)}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`Camera view delete failed (${res.status})`)
}
