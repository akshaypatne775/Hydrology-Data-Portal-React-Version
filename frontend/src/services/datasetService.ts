import { apiRequest, apiRequestJson } from './api'
const CACHE_TTL_MS = 30_000
const projectFilesCache = new Map<string, { ts: number; data: ProjectFile[] }>()
const projectJobsCache = new Map<string, { ts: number; data: ProjectJob[] }>()

export type ProcessDatasetResponse = {
  status: string
  message: string
  project_id: string
  dataset_id: string
  dataset_name: string
  cog_path: string
  cog_tile_url_template: string
}

export type DatasetStatusResponse = {
  status: string
  updated_at?: string
  dataset_id?: string
  dataset_name?: string
  layer_type?: string
  error?: string
  cog_path?: string
  cog_tile_url_template?: string
}

export type ProjectJob = {
  job_id: string
  kind: string
  file_name: string
  status: string
  updated_at?: string
  error?: string
  result_url?: string
}

export type ProjectFile = {
  dataset_id?: string
  name: string
  kind: string
  type: string
  dataset_type?: string
  month?: string
  size_bytes: string
  status: string
  file_url: string
  layer_url: string
  file_path: string
  rel_path: string
  raw_rel_path?: string
}

export type DatasetMetadata = {
  filename: string
  epsg: string
}

export type SyncManualDatasetsResponse = {
  status: string
  message: string
  new_count: string
}

export type OpenManualFolderResponse = {
  status: string
  message: string
  folder_path: string
}

export type CropMaskResponse = {
  status: 'success' | 'none'
  source?: 'kml' | 'draw'
  updated_at?: string
  points: Array<[number, number]>
}

export async function processDatasetTif(form: FormData): Promise<ProcessDatasetResponse> {
  const res = await apiRequest('/api/process-dataset', {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const data = (await res.json()) as { detail?: string }
      detail = data.detail ? `: ${data.detail}` : ''
    } catch {
      detail = ''
    }
    throw new Error(`Dataset upload failed (${res.status})${detail}`)
  }
  return (await res.json()) as ProcessDatasetResponse
}

export async function readDatasetMetadata(form: FormData): Promise<DatasetMetadata> {
  const res = await apiRequest('/api/dataset-metadata', {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    throw new Error(`Dataset metadata read failed (${res.status})`)
  }
  return (await res.json()) as DatasetMetadata
}

export async function getDatasetStatus(
  projectId: string,
  datasetId: string,
): Promise<DatasetStatusResponse> {
  return apiRequestJson<DatasetStatusResponse>(
    `/api/dataset-status/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetId)}`,
    { cache: 'no-store' },
  )
}

export async function getProjectJobs(projectId: string): Promise<ProjectJob[]> {
  const cached = projectJobsCache.get(projectId)
  if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
    return cached.data
  }
  const data = await apiRequestJson<{ jobs: ProjectJob[] }>(
    `/api/jobs/${encodeURIComponent(projectId)}`,
    { cache: 'no-store' },
  )
  const next = data.jobs ?? []
  projectJobsCache.set(projectId, { ts: Date.now(), data: next })
  return next
}

export async function getProjectFiles(projectId: string): Promise<ProjectFile[]> {
  const cached = projectFilesCache.get(projectId)
  if (cached && Date.now() - cached.ts < CACHE_TTL_MS) {
    return cached.data
  }
  const data = await apiRequestJson<{ files: ProjectFile[] }>(
    `/api/projects/${encodeURIComponent(projectId)}/files`,
    { cache: 'no-store' },
  )
  const next = data.files ?? []
  projectFilesCache.set(projectId, { ts: Date.now(), data: next })
  return next
}

export async function deleteProjectFile(projectId: string, relPath: string): Promise<void> {
  const res = await apiRequest(`/api/projects/${encodeURIComponent(projectId)}/files`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_path: relPath }),
  })
  if (!res.ok) {
    throw new Error(`Delete failed (${res.status})`)
  }
  projectFilesCache.delete(projectId)
}

export async function updateDatasetMetadata(
  projectId: string,
  payload: { dataset_id: string; month?: string; dataset_type?: string },
): Promise<void> {
  const res = await apiRequest(`/api/datasets/${encodeURIComponent(projectId)}/metadata`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Metadata update failed (${res.status})`)
  invalidateProjectDataCache(projectId)
}

export async function syncManualDatasetFolders(
  projectId: string,
): Promise<SyncManualDatasetsResponse> {
  const res = await apiRequest(`/api/datasets/${encodeURIComponent(projectId)}/sync`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`Sync failed (${res.status})`)
  }
  const data = (await res.json()) as SyncManualDatasetsResponse
  invalidateProjectDataCache(projectId)
  return data
}

export async function openManualDatasetFolder(
  projectId: string,
): Promise<OpenManualFolderResponse> {
  const res = await apiRequest(`/api/datasets/${encodeURIComponent(projectId)}/open-manual-folder`, {
    method: 'POST',
  })
  if (!res.ok) {
    throw new Error(`Open folder failed (${res.status})`)
  }
  return (await res.json()) as OpenManualFolderResponse
}

export async function getDatasetCropMask(
  projectId: string,
  tileFolder: string,
): Promise<CropMaskResponse> {
  return apiRequestJson<CropMaskResponse>(
    `/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(tileFolder)}/crop-mask`,
    { cache: 'no-store' },
  )
}

export async function saveDatasetCropMaskKml(
  projectId: string,
  tileFolder: string,
  file: File,
): Promise<CropMaskResponse> {
  const form = new FormData()
  form.append('file', file)
  const res = await apiRequest(
    `/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(tileFolder)}/crop-mask/kml`,
    {
      method: 'POST',
      body: form,
    },
  )
  if (!res.ok) throw new Error(`KML crop save failed (${res.status})`)
  return (await res.json()) as CropMaskResponse
}

export async function saveDatasetCropMaskDraw(
  projectId: string,
  tileFolder: string,
  points: Array<[number, number]>,
): Promise<CropMaskResponse> {
  const res = await apiRequest(
    `/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(tileFolder)}/crop-mask/draw`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ points }),
    },
  )
  if (!res.ok) throw new Error(`Draw crop save failed (${res.status})`)
  return (await res.json()) as CropMaskResponse
}

export function invalidateProjectDataCache(projectId: string): void {
  projectFilesCache.delete(projectId)
  projectJobsCache.delete(projectId)
}
