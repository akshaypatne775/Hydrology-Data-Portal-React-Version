import { apiRequest, apiRequestJson } from './api'
import type { GeoJsonFeature, SpatialFeature, SpatialLayer, StructureType } from '../components/MapViewer/spatialTypes'

export type SpatialLayersResponse = {
  layers: SpatialLayer[]
}

export type SpatialFeaturePayload = {
  layer_id?: string
  layer_name?: string
  geojson: GeoJsonFeature
  plot_id?: string
  owner_name?: string
  structure_type?: StructureType
  source_type?: string
}

export type SpatialFeaturePatchPayload = {
  geojson?: GeoJsonFeature
  plot_id?: string
  owner_name?: string
  structure_type?: StructureType
}

export async function listSpatialLayers(projectId: string): Promise<SpatialLayer[]> {
  const data = await apiRequestJson<SpatialLayersResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-layers`,
    { cache: 'no-store' },
  )
  return data.layers ?? []
}

export async function createSpatialFeature(
  projectId: string,
  payload: SpatialFeaturePayload,
): Promise<SpatialFeature> {
  const response = await apiRequestJson<{ feature: SpatialFeature }>(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-features`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
  return response.feature
}

export async function updateSpatialFeature(
  projectId: string,
  featureId: string,
  payload: SpatialFeaturePatchPayload,
): Promise<SpatialFeature> {
  const response = await apiRequestJson<{ feature: SpatialFeature }>(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-features/${encodeURIComponent(featureId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
  return response.feature
}

export async function deleteSpatialFeature(projectId: string, featureId: string): Promise<void> {
  const response = await apiRequest(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-features/${encodeURIComponent(featureId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) {
    let detail = `Delete failed (${response.status})`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) detail = data.detail
    } catch {
      // keep default detail
    }
    throw new Error(detail)
  }
}

export async function deleteSpatialLayer(projectId: string, layerId: string): Promise<void> {
  const response = await apiRequest(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-layers/${encodeURIComponent(layerId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) {
    let detail = `Layer delete failed (${response.status})`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) detail = data.detail
    } catch {
      // keep default detail
    }
    throw new Error(detail)
  }
}

export async function importSpatialLayer(projectId: string, file: File): Promise<SpatialLayer> {
  const form = new FormData()
  form.append('file', file)
  const response = await apiRequest(
    `/api/projects/${encodeURIComponent(projectId)}/spatial-import`,
    {
      method: 'POST',
      body: form,
    },
  )
  if (!response.ok) {
    let detail = `Import failed (${response.status})`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) detail = data.detail
    } catch {
      // Keep default detail.
    }
    throw new Error(detail)
  }
  const data = (await response.json()) as { layer: SpatialLayer }
  return data.layer
}
