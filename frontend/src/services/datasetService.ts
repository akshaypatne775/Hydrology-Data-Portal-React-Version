import { apiRequest, apiRequestJson } from './api'

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

export async function getDatasetStatus(
  projectId: string,
  datasetId: string,
): Promise<DatasetStatusResponse> {
  return apiRequestJson<DatasetStatusResponse>(
    `/api/dataset-status/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetId)}`,
    { cache: 'no-store' },
  )
}
