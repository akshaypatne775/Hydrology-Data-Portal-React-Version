import { apiRequest, apiRequestJson } from './api'
const CACHE_TTL_MS = 8_000
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
  name: string
  kind: string
  type: string
  size_bytes: string
  status: string
  file_url: string
  layer_url: string
  file_path: string
  rel_path: string
}

export type DatasetMetadata = {
  filename: string
  epsg: string
}

export async function processDatasetTif(form: FormData): Promise<ProcessDatasetResponse> {
  const res = await apiRequest('/api/process-dataset', {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    throw new Error(`Dataset upload failed (${res.status})`)
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

export function invalidateProjectDataCache(projectId: string): void {
  projectFilesCache.delete(projectId)
  projectJobsCache.delete(projectId)
}
