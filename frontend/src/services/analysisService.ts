import { apiRequest, apiRequestJson } from './api'

export type ElevationResponse = {
  status: string
  dataset_id: string
  lat: number
  lng: number
  elevation: number
  unit: string
}

export type ProfilePoint = {
  lat: number
  lng: number
  distance_m: number
  elevation: number | null
}

export type ProfileResponse = {
  status: string
  dataset_id: string
  unit: string
  points: ProfilePoint[]
  length_m?: number | null
  min_elevation?: number | null
  max_elevation?: number | null
  avg_elevation?: number | null
  start_elevation?: number | null
  end_elevation?: number | null
  elevation_change?: number | null
  elevation_gain?: number | null
  elevation_loss?: number | null
  volume_above_min_m3?: number | null
  corridor_width_m?: number | null
}

export type VolumeBin = {
  label: string
  volume: number
}

export type DtmVolumeResponse = {
  status: string
  dataset_id: string
  scope: string
  base_elevation: number
  min_elevation: number
  max_elevation: number
  avg_elevation: number
  area_m2: number
  fill_volume_m3: number
  cut_volume_m3: number
  net_volume_m3: number
  cell_count: number
  bins: VolumeBin[]
  unit: string
}

export type CompareDataset = {
  dataset_id: string
  name: string
  dataset_type: string
  month: string
  status: string
  has_source: boolean
}

export type VolumeRow = {
  month: string
  label: string
  volume: number
  cut: number
  fill: number
  net: number
  area: number
  source: 'csv' | 'dtm'
}

export async function getElevation(
  projectId: string,
  datasetId: string,
  lat: number,
  lng: number,
): Promise<ElevationResponse> {
  return apiRequestJson<ElevationResponse>(
    `/api/analysis/${encodeURIComponent(projectId)}/elevation?dataset_id=${encodeURIComponent(datasetId)}&lat=${lat}&lng=${lng}`,
    { cache: 'no-store' },
  )
}

export async function getProfile(
  projectId: string,
  datasetId: string,
  points: Array<[number, number]>,
): Promise<ProfileResponse> {
  return apiRequestJson<ProfileResponse>(`/api/analysis/${encodeURIComponent(projectId)}/profile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id: datasetId, points, samples: 160, corridor_width_m: 1 }),
  })
}

export async function getDtmVolume(
  projectId: string,
  datasetId: string,
  payload: {
    points?: Array<[number, number]>
    circle_center?: [number, number]
    circle_radius_m?: number
    base_elevation?: number
  } = {},
): Promise<DtmVolumeResponse> {
  return apiRequestJson<DtmVolumeResponse>(`/api/analysis/${encodeURIComponent(projectId)}/volume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id: datasetId, ...payload }),
  })
}

export async function getCompareDatasets(projectId: string): Promise<CompareDataset[]> {
  const data = await apiRequestJson<{ datasets: CompareDataset[] }>(
    `/api/compare/${encodeURIComponent(projectId)}/datasets`,
    { cache: 'no-store' },
  )
  return data.datasets ?? []
}

export async function getVolumeCompare(
  projectId: string,
  datasetIds: string[],
): Promise<{ source: string; rows: VolumeRow[] }> {
  return apiRequestJson<{ source: string; rows: VolumeRow[] }>(
    `/api/compare/${encodeURIComponent(projectId)}/volume`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dataset_ids: datasetIds }),
    },
  )
}

export async function refreshCompareCache(projectId: string): Promise<{ removed: number }> {
  const res = await apiRequest(`/api/compare/${encodeURIComponent(projectId)}/refresh-if-changed`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`Refresh failed (${res.status})`)
  return (await res.json()) as { removed: number }
}
