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
  stage?: string
  progress_percent?: string | number
  eta_seconds?: string | number
  tiles_written?: string | number
  estimated_tiles?: string | number
  error?: string
  cog_path?: string
  cog_rel_path?: string
  rescale_min?: string | number
  rescale_max?: string | number
  bounds_wgs84?: string
  cog_tile_url_template?: string
}

export type ProjectJob = {
  job_id: string
  kind: string
  file_name: string
  status: string
  updated_at?: string
  stage?: string
  progress_percent?: string | number
  eta_seconds?: string | number
  error?: string
  result_url?: string
}

export type ProjectFile = {
  dataset_id?: string
  name: string
  kind: string
  type: string
  layer_type?: string
  dataset_type?: string
  month?: string
  processed_size?: string
  upload_date?: string
  height_offset?: string | number
  cog_path?: string
  cog_rel_path?: string
  rescale_min?: string | number
  rescale_max?: string | number
  bounds_wgs84?: string
  source_crs?: string
  detected_epsg?: string
  manual_epsg?: string
  applied_epsg?: string
  size_bytes: string
  status: string
  updated_at?: string
  stage?: string
  progress_percent?: string | number
  eta_seconds?: string | number
  file_url: string
  download_url?: string
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
      const data = (await res.json()) as { detail?: unknown }
      if (Array.isArray(data.detail)) {
        detail = `: ${data.detail
          .map((item) => {
            if (item && typeof item === 'object' && 'msg' in item) return String(item.msg)
            return String(item)
          })
          .join(', ')}`
      } else if (data.detail) {
        detail = `: ${String(data.detail)}`
      }
    } catch {
      detail = ''
    }
    throw new Error(`Dataset upload failed (${res.status})${detail}`)
  }
  return (await res.json()) as ProcessDatasetResponse
}

export async function uploadDatasetChunk(chunkForm: FormData): Promise<Response> {
  return apiRequest('/api/upload-dataset-chunk', {
    method: 'POST',
    body: chunkForm,
  })
}

export async function completeDatasetUpload(payload: {
  filename: string
  totalChunks: number
  project_id: string
  dataset_type?: string
  month?: string
  created_at?: string
  epsg?: string
}): Promise<ProcessDatasetResponse> {
  const res = await apiRequest('/api/complete-dataset-upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    let detail = ''
    try {
      const data = (await res.json()) as { detail?: unknown }
      detail = data.detail ? `: ${String(data.detail)}` : ''
    } catch {
      detail = ''
    }
    throw new Error(`Dataset merge failed (${res.status})${detail}`)
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

export async function updateDatasetOwnerMetadata(
  projectId: string,
  datasetId: string,
  payload: { height_offset?: number },
): Promise<void> {
  const res = await apiRequest(
    `/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetId)}/metadata`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
  if (!res.ok) throw new Error(`Metadata update failed (${res.status})`)
  invalidateProjectDataCache(projectId)
}

export async function generateContours(
  projectId: string,
  payload: { dataset_id?: string; source_tif?: string; interval: number },
): Promise<ProcessDatasetResponse> {
  const res = await apiRequest(`/api/datasets/${encodeURIComponent(projectId)}/generate-contours`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`Contour generation failed (${res.status})`)
  invalidateProjectDataCache(projectId)
  return (await res.json()) as ProcessDatasetResponse
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback
  const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i)
  if (!match?.[1]) return fallback
  try {
    return decodeURIComponent(match[1].replace(/"/g, ''))
  } catch {
    return match[1].replace(/"/g, '')
  }
}

export async function exportDatasetGrid(
  projectId: string,
  datasetId: string,
  payload: { format: 'csv' | 'dxf'; interval: number; fileName?: string },
): Promise<string> {
  const params = new URLSearchParams({
    format: payload.format,
    interval: String(payload.interval),
  })
  const res = await apiRequest(
    `/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetId)}/grid-export?${params.toString()}`,
    { method: 'GET' },
  )
  if (!res.ok) {
    let detail = `Grid export failed (${res.status})`
    try {
      const data = (await res.json()) as { detail?: string }
      if (data.detail) detail = data.detail
    } catch {
      // keep default detail
    }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const fallback = `${payload.fileName || datasetId}_grid.${payload.format}`
  const filename = filenameFromDisposition(res.headers.get('content-disposition'), fallback)
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 5000)
  }
  invalidateProjectDataCache(projectId)
  return filename
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
