import { API_BASE, toSameOriginBackendUrl } from './apiBase'

export const ORTHO_RENDERER_VERSION = 'edge-padding-v7'

export function isRasterDatasetType(value?: string): boolean {
  const normalized = String(value || '').toLowerCase()
  return ['cog', 'ortho', 'orthomosaic', 'dtm', 'dsm', 'dem'].includes(normalized)
}

export function isRasterDirectDownloadUrl(url?: string): boolean {
  const normalized = String(url || '').trim().toLowerCase()
  if (!normalized) return false
  if (normalized.includes('/raw/download')) return true
  if (
    normalized.includes('/api/titiler/') ||
    normalized.includes('/api/dji-terra/') ||
    normalized.includes('/api/ortho-cog/')
  ) {
    return false
  }
  const pathOnly = normalized.split(/[?#]/, 1)[0] || ''
  return /\.tif(f)?$/i.test(pathOnly)
}

export function buildRasterTileUrl(layer: {
  url?: string
  layerType?: string
  datasetType?: string
  cogPath?: string
  cogRelPath?: string
  datasetId?: string
  cacheKey?: string
  rescaleMin?: number | string
  rescaleMax?: number | string
}): string {
  const sourcePath = String(layer.cogPath || '').trim()
  if (!sourcePath) {
    const fallback = toSameOriginBackendUrl(layer.url || '') || layer.url || ''
    return fallback && !isRasterDirectDownloadUrl(fallback) ? fallback : ''
  }
  const params = new URLSearchParams()
  params.set('url', sourcePath.replace(/\\/g, '/'))
  const rasterType = String(layer.layerType || layer.datasetType || '').toLowerCase()
  const min = Number(layer.rescaleMin)
  const max = Number(layer.rescaleMax)
  if (rasterType === 'ortho' || rasterType === 'orthomosaic') {
    params.set('renderer', ORTHO_RENDERER_VERSION)
    params.set('v', String(layer.cacheKey || layer.datasetId || layer.cogRelPath || '1'))
    return `${API_BASE}/api/ortho-cog/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${params.toString()}`
  }
  if ((rasterType === 'dtm' || rasterType === 'dsm' || rasterType === 'dem') && Number.isFinite(min) && Number.isFinite(max) && min !== max) {
    params.set('rescale', `${min},${max}`)
    return `${API_BASE}/api/dji-terra/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${params.toString()}`
  }
  return `${API_BASE}/api/titiler/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${params.toString()}`
}
