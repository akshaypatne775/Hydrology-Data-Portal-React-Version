import type { LatLng } from 'leaflet'

export type StructureType =
  | 'Residential'
  | 'Commercial'
  | 'Road'
  | 'Water Body'
  | 'Industrial'
  | 'Open Space'
  | 'Unassigned'

export type DigitizationMode = 'idle' | 'polygon' | 'polyline' | 'marker' | 'edit'

export type GeoJsonGeometry = {
  type: string
  coordinates: unknown
}

export type GeoJsonFeature = {
  type: 'Feature'
  properties: Record<string, unknown>
  geometry: GeoJsonGeometry | null
}

export type SpatialFeature = {
  id: string
  project_id: string
  layer_id: string
  owner_user_id?: number
  geometry_type: string
  geojson: GeoJsonFeature
  plot_id: string
  owner_name: string
  structure_type: StructureType
  fill_color: string
  stroke_color: string
  source_type: string
  can_edit?: boolean
  can_delete?: boolean
  created_at: string
  updated_at: string
}

export type SpatialLayer = {
  id: string
  project_id: string
  name: string
  source_type: string
  created_at: string
  updated_at: string
  features: SpatialFeature[]
}

export const STRUCTURE_TYPES: StructureType[] = [
  'Residential',
  'Commercial',
  'Road',
  'Water Body',
  'Industrial',
  'Open Space',
  'Unassigned',
]

export const STRUCTURE_COLORS: Record<StructureType, string> = {
  Residential: '#22c55e',
  Commercial: '#ef4444',
  Road: '#64748b',
  'Water Body': '#2563eb',
  Industrial: '#f97316',
  'Open Space': '#16a34a',
  Unassigned: '#f59e0b',
}

export function colorForStructure(structureType: string): string {
  return STRUCTURE_COLORS[structureType as StructureType] ?? STRUCTURE_COLORS.Unassigned
}

export function normalizeStructureType(value: string): StructureType {
  return STRUCTURE_TYPES.includes(value as StructureType) ? (value as StructureType) : 'Unassigned'
}

export function latLngToPosition(point: LatLng): [number, number] {
  return [point.lng, point.lat]
}

export function positionToLatLng(position: unknown): [number, number] | null {
  if (!Array.isArray(position) || position.length < 2) return null
  const lng = Number(position[0])
  const lat = Number(position[1])
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  return [lat, lng]
}

export function buildPointFeature(point: LatLng): GeoJsonFeature {
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Point',
      coordinates: latLngToPosition(point),
    },
  }
}

export function buildLineFeature(points: LatLng[]): GeoJsonFeature {
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: points.map(latLngToPosition),
    },
  }
}

export function buildPolygonFeature(points: LatLng[]): GeoJsonFeature {
  const coordinates = points.map(latLngToPosition)
  if (coordinates.length > 0) {
    const first = coordinates[0]!
    const last = coordinates[coordinates.length - 1]!
    if (first[0] !== last[0] || first[1] !== last[1]) {
      coordinates.push(first)
    }
  }
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'Polygon',
      coordinates: [coordinates],
    },
  }
}

export function featureStyle(feature: SpatialFeature, selected = false) {
  const color = colorForStructure(feature.structure_type)
  return {
    color,
    fillColor: color,
    weight: selected ? 4 : 3,
    opacity: 0.95,
    fillOpacity: feature.geometry_type.includes('Line') ? 0 : selected ? 0.5 : 0.36,
    dashArray: feature.structure_type === 'Unassigned' ? '6 5' : undefined,
  }
}
