/**
 * Generic XYZ tile URLs for any HTTP origin: FastAPI StaticFiles, MinIO, S3 website
 * endpoints, CDNs, etc. No assumption about “bucket” hostnames or AWS URL shape.
 *
 * Template built as:
 *   {tileBase}/{prefix}/{z}/{x}/{y}.{ext}
 *
 * Example (local):
 *   http://localhost:8000/tiles/orthomosaic/{z}/{x}/{y}.png
 *
 * Env:
 *   - Prefer VITE_TILE_BASE_URL; otherwise VITE_S3_TILE_BASE_URL (legacy name).
 *   - Prefixes VITE_S3_* are folder names under that base (name kept for compatibility).
 */

export type BaseLayerId = 'orthomosaic' | 'dem' | 'dtm'

/** Flood return period folders under the tile root. */
export type FloodReturnPeriod = '1in25' | '1in50' | '1in100'

const env = import.meta.env

function trimSlash(s: string): string {
  return s.replace(/\/+$/, '').replace(/^\/+/, '')
}

function runtimeApiBase(): string {
  const viteVal = env.VITE_API_BASE_URL?.trim()
  if (viteVal) return viteVal
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, '')
  }
  return 'http://127.0.0.1:8000'
}

/** Resolved HTTP root for tiles (local API, S3 static site, CDN, …). */
export function getTileBaseUrl(): string | undefined {
  const primary = env.VITE_TILE_BASE_URL?.trim()
  const legacy = env.VITE_S3_TILE_BASE_URL?.trim()
  return primary || legacy || undefined
}

/** True when a custom tile base URL is configured. */
export function hasCustomTileBase(): boolean {
  return Boolean(getTileBaseUrl())
}

export function getTileExtension(): string {
  return (env.VITE_S3_TILE_EXT ?? 'png').replace(/^\./, '')
}

/**
 * Leaflet XYZ template `{z}/{x}/{y}` — plain URL concatenation only
 * (no S3-specific signing or virtual-hosted-style rules).
 */
export function buildXyzTemplate(
  baseUrl: string,
  prefix: string,
  ext: string = getTileExtension(),
): string {
  const base = trimSlash(baseUrl)
  const path = trimSlash(prefix)
  return `${base}/${path}/{z}/{x}/{y}.${ext}`
}

const OSM_HOST_MARKER = 'openstreetmap.org'

/**
 * Cache-bust query on custom templates. OSM unchanged.
 * Optional VITE_TILE_CACHE_BUST; else defaults to "1".
 */
export function withTileCacheBust(urlTemplate: string): string {
  if (!urlTemplate.includes('{z}')) return urlTemplate
  if (urlTemplate.includes(OSM_HOST_MARKER)) return urlTemplate
  const bust = env.VITE_TILE_CACHE_BUST?.trim() || '1'
  const sep = urlTemplate.includes('?') ? '&' : '?'
  return `${urlTemplate}${sep}v=${encodeURIComponent(bust)}`
}

/** Path segment(s) under the tile base for each layer (no leading “tiles/” — base URL already includes /tiles when local). */
function defaultPrefix(layer: BaseLayerId): string {
  if (layer === 'orthomosaic') {
    return env.VITE_S3_ORTHO_PREFIX?.trim() || 'orthomosaic'
  }
  if (layer === 'dem') {
    return env.VITE_S3_DEM_PREFIX?.trim() || 'dem'
  }
  return env.VITE_S3_DTM_PREFIX?.trim() || 'dtm'
}

/** XYZ URL template for a base imagery / elevation layer. */
export function getBaseLayerTileUrl(layer: BaseLayerId): string | null {
  const base = getTileBaseUrl()
  if (!base) return null
  return buildXyzTemplate(base, defaultPrefix(layer))
}

/** XYZ URL template for flood hazard tiles from local FastAPI tiles endpoint. */
export function getFloodOverlayTileUrl(period: FloodReturnPeriod): string | null {
  const explicitFloodBase = env.VITE_FLOOD_TILE_BASE_URL?.trim()
  const tileBase = getTileBaseUrl()
  const apiBase = runtimeApiBase()
  const resolvedFloodBase =
    explicitFloodBase || (tileBase ? `${trimSlash(tileBase)}/flood` : `${apiBase}/tiles/flood`)
  if (!resolvedFloodBase) return null
  return `${resolvedFloodBase.replace(/\/+$/, '')}/${period}/{z}/{x}/{y}.png`
}

/** OpenStreetMap fallback when no custom tile base is configured. */
export const OSM_FALLBACK_URL =
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'

export function getBaseLayerUrlOrFallback(layer: BaseLayerId): string {
  const raw = getBaseLayerTileUrl(layer) ?? OSM_FALLBACK_URL
  return withTileCacheBust(raw)
}

/** Flood template with cache bust (same rules as base layers). */
export function getFloodOverlayTileUrlWithBust(
  period: FloodReturnPeriod,
): string | null {
  const raw = getFloodOverlayTileUrl(period)
  if (!raw) return null
  return withTileCacheBust(raw)
}

export function getDefaultMapCenter(): [number, number] {
  const lat = Number(env.VITE_MAP_DEFAULT_LAT ?? '51.505')
  const lng = Number(env.VITE_MAP_DEFAULT_LNG ?? '-0.09')
  if (Number.isFinite(lat) && Number.isFinite(lng)) return [lat, lng]
  return [51.505, -0.09]
}

export function getDefaultZoom(): number {
  const z = Number(env.VITE_MAP_DEFAULT_ZOOM ?? '13')
  return Number.isFinite(z) ? z : 13
}
