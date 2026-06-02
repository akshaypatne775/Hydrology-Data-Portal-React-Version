import 'leaflet/dist/leaflet.css'
import './MapViewer.css'
import './Digitization.css'

import area from '@turf/area'
import { polygon as turfPolygon } from '@turf/helpers'
import type { LatLng } from 'leaflet'
import L from 'leaflet'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react'
import {
  CircleMarker,
  Circle,
  MapContainer,
  Marker,
  Pane,
  Polygon,
  Popup,
  Polyline,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import { createIssue, listIssues, type SavedIssue } from '../../services/issuesService'
import {
  createSpatialFeature,
  deleteSpatialFeature,
  importSpatialLayer,
  listSpatialLayers,
  updateSpatialFeature,
} from '../../services/spatialDataService'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { useModal } from '../../context/ModalContext'
import { API_BASE, toSameOriginBackendUrl } from '../../lib/apiBase'
import {
  getProjectFiles,
  getDatasetCropMask,
  saveDatasetCropMaskDraw,
  saveDatasetCropMaskKml,
  type ProjectFile,
} from '../../services/datasetService'
import {
  getDtmVolume,
  getElevation,
  getProfile,
  type DtmVolumeResponse,
  type ElevationResponse,
  type ProfileResponse,
} from '../../services/analysisService'

import {
  getDefaultMapCenter,
  getDefaultZoom,
  SATELLITE_FALLBACK_URL,
} from './tileSources'
import { DigitizationToolbar } from './DigitizationToolbar'
import { SpatialAssignmentModal } from './SpatialAssignmentModal'
import { SpatialFeatureLayer } from './SpatialFeatureLayer'
import { useDigitizationDrawing } from './useDigitizationDrawing'
import {
  type DigitizationMode,
  type GeoJsonFeature,
  type SpatialFeature,
  type SpatialLayer,
  type StructureType,
} from './spatialTypes'

type MeasureMode = 'none' | 'distance' | 'area' | 'profile' | 'volume-area' | 'volume-circle'
type ViewerLayer = {
  id: string
  projectId: string
  name: string
  url: string
  rawPath?: string
  layerType?: string
  cogPath?: string
  cogRelPath?: string
  rescaleMin?: number | string
  rescaleMax?: number | string
  boundsWgs84?: [number, number, number, number]
  datasetId?: string
  datasetType?: string
  month?: string
  cacheKey?: string
}
type BaseMapKey = 'esri-imagery' | 'osm' | 'carto-light' | 'carto-voyager' | 'esri-topo'
type BaseMapConfig = {
  key: BaseMapKey
  label: string
  url: string
  attribution: string
  maxNativeZoom: number
}

const BASE_MAPS: BaseMapConfig[] = [
  {
    key: 'esri-imagery',
    label: 'Esri Satellite',
    url: SATELLITE_FALLBACK_URL,
    attribution: 'Tiles &copy; Esri',
    maxNativeZoom: 19,
  },
  {
    key: 'osm',
    label: 'OpenStreetMap',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxNativeZoom: 19,
  },
  {
    key: 'carto-light',
    label: 'Carto Light',
    url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    maxNativeZoom: 20,
  },
  {
    key: 'carto-voyager',
    label: 'Carto Voyager',
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    maxNativeZoom: 20,
  },
  {
    key: 'esri-topo',
    label: 'Esri Topographic',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxNativeZoom: 19,
  },
]

const ORTHO_RENDERER_VERSION = 'edge-padding-v7'

function getBaseMap(key: BaseMapKey): BaseMapConfig {
  return BASE_MAPS.find((map) => map.key === key) ?? BASE_MAPS[0]!
}
type IssueDraft = {
  lat: number
  lng: number
  title: string
  description: string
}

function formatLengthM(meters: number): string {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`
  return `${meters.toFixed(1)} m`
}

function formatAreaM2(m2: number): string {
  if (m2 >= 1_000_000) return `${(m2 / 1_000_000).toFixed(2)} km²`
  if (m2 >= 10_000) return `${(m2 / 10_000).toFixed(2)} ha`
  return `${m2.toFixed(0)} m²`
}

function formatProfileValue(value?: number | null, suffix = 'm'): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value.toFixed(2)} ${suffix}`
}

function ringAreaM2(points: LatLng[]): number {
  if (points.length < 3) return 0
  const coords = points.map((p) => [p.lng, p.lat] as [number, number])
  const closed: [number, number][] = [...coords, coords[0]!]
  const poly = turfPolygon([closed])
  return area(poly)
}

function totalPathLengthM(points: LatLng[]): number {
  let sum = 0
  for (let i = 1; i < points.length; i++) {
    sum += points[i - 1]!.distanceTo(points[i]!)
  }
  return sum
}

type SyncRefs = {
  lockRef: MutableRefObject<boolean>
  mapARef: MutableRefObject<L.Map | null>
  mapBRef: MutableRefObject<L.Map | null>
}

function applyTileClip(
  map: L.Map,
  container: HTMLElement | null,
  footprint: LatLng[] | null,
): () => void {
  if (!container || !footprint || footprint.length < 3) {
    if (container) {
      container.style.clipPath = ''
      ;(container.style as CSSStyleDeclaration & { webkitClipPath?: string }).webkitClipPath = ''
    }
    return () => {}
  }

  const repaint = () => {
    const pts = footprint.map((ll) => map.latLngToLayerPoint(ll))
    const polygon = pts.map((p) => `${p.x}px ${p.y}px`).join(', ')
    const clipValue = `polygon(${polygon})`
    container.style.clipPath = clipValue
    ;(container.style as CSSStyleDeclaration & { webkitClipPath?: string }).webkitClipPath = clipValue
  }
  repaint()
  map.on('move zoom zoomend viewreset resize', repaint)
  return () => {
    map.off('move zoom zoomend viewreset resize', repaint)
    container.style.clipPath = ''
    ;(container.style as CSSStyleDeclaration & { webkitClipPath?: string }).webkitClipPath = ''
  }
}

function applySwipeClip(
  map: L.Map,
  container: HTMLElement | null,
  percent: number,
  side: 'left' | 'right',
): () => void {
  if (!container) return () => {}

  const clampedPercent = Math.max(5, Math.min(95, percent))
  const repaint = () => {
    const size = map.getSize()
    const splitX = (size.x * clampedPercent) / 100
    const topLeft = map.containerPointToLayerPoint([0, 0])
    const topSplit = map.containerPointToLayerPoint([splitX, 0])
    const bottomSplit = map.containerPointToLayerPoint([splitX, size.y])
    const bottomLeft = map.containerPointToLayerPoint([0, size.y])
    const topRight = map.containerPointToLayerPoint([size.x, 0])
    const bottomRight = map.containerPointToLayerPoint([size.x, size.y])
    const points =
      side === 'left'
        ? [topLeft, topSplit, bottomSplit, bottomLeft]
        : [topSplit, topRight, bottomRight, bottomSplit]
    const clipValue = `polygon(${points.map((p) => `${p.x}px ${p.y}px`).join(', ')})`
    container.style.clipPath = clipValue
    ;(container.style as CSSStyleDeclaration & { webkitClipPath?: string }).webkitClipPath = clipValue
  }

  repaint()
  map.on('move zoom zoomend viewreset resize', repaint)
  return () => {
    map.off('move zoom zoomend viewreset resize', repaint)
    container.style.clipPath = ''
    ;(container.style as CSSStyleDeclaration & { webkitClipPath?: string }).webkitClipPath = ''
  }
}

function MapSyncBridge({ isA, lockRef, mapARef, mapBRef }: SyncRefs & { isA: boolean }) {
  const map = useMap()
  const selfRef = isA ? mapARef : mapBRef
  const peerRef = isA ? mapBRef : mapARef

  useEffect(() => {
    // Leaflet map instances are intentionally stored in refs for split-view syncing.
    // eslint-disable-next-line react-hooks/immutability
    selfRef.current = map
    return () => {
      selfRef.current = null
    }
  }, [map, selfRef])

  useMapEvents({
    moveend() {
      if (lockRef.current) return
      const peer = peerRef.current
      if (!peer) return
      lockRef.current = true
      peer.setView(map.getCenter(), map.getZoom(), { animate: false })
      window.setTimeout(() => {
        lockRef.current = false
      }, 48)
    },
  })
  return null
}

function MeasureInteraction({
  mode,
  enabled,
  points,
  areaFrozen,
  onAddPoint,
  onCloseRing,
}: {
  mode: MeasureMode
  enabled: boolean
  points: LatLng[]
  areaFrozen: boolean
  onAddPoint: (ll: LatLng) => void
  onCloseRing: () => void
}) {
  const map = useMap()

  useEffect(() => {
    if ((mode === 'area' || mode === 'volume-area') && !areaFrozen) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    return () => {
      map.doubleClickZoom.enable()
    }
  }, [map, mode, areaFrozen])

  useMapEvents({
    click(e) {
      if (!enabled || mode === 'none') return
      if ((mode === 'area' || mode === 'volume-area') && areaFrozen) return
      if (mode === 'volume-circle' && points.length >= 2) return
      if (mode === 'distance' || mode === 'area' || mode === 'profile' || mode === 'volume-area' || mode === 'volume-circle') onAddPoint(e.latlng)
    },
    dblclick(e) {
      if (!enabled || (mode !== 'area' && mode !== 'profile' && mode !== 'volume-area') || areaFrozen) return
      e.originalEvent.preventDefault()
      if (((mode === 'area' || mode === 'volume-area') && points.length >= 3) || (mode === 'profile' && points.length >= 2)) onCloseRing()
    },
  })
  return null
}

function IssueInteraction({
  active,
  onPickPoint,
}: {
  active: boolean
  onPickPoint: (ll: LatLng) => void
}) {
  useMapEvents({
    click(e) {
      if (!active) return
      onPickPoint(e.latlng)
    },
  })
  return null
}

function DigitizationInteraction({
  mode,
  active,
  onAddPoint,
  onFinishDraft,
}: {
  mode: DigitizationMode
  active: boolean
  onAddPoint: (ll: LatLng) => void
  onFinishDraft: () => void
}) {
  const map = useMap()

  useEffect(() => {
    if (active) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    return () => {
      map.doubleClickZoom.enable()
    }
  }, [active, map])

  useMapEvents({
    click(e) {
      if (!active || mode === 'idle' || mode === 'edit') return
      onAddPoint(e.latlng)
    },
    dblclick(e) {
      if (!active || (mode !== 'polygon' && mode !== 'polyline')) return
      e.originalEvent.preventDefault()
      onFinishDraft()
    },
  })
  return null
}

function isStaticXyzTileTemplate(url: string): boolean {
  return (
    (url.includes('/tiles/') || url.includes('/data/')) &&
    url.includes('{z}/{x}/{y}.png') &&
    !url.includes('/api/cog/') &&
    !url.includes('/api/titiler/') &&
    !url.includes('/api/dji-terra/') &&
    !url.includes('/api/ortho-cog/')
  )
}

function tileTemplateToStaticBase(url: string): string | null {
  const suffix = '{z}/{x}/{y}.png'
  if (!url.endsWith(suffix)) return null
  return url.slice(0, -suffix.length)
}

function rasterSourceKeyFromTileUrl(url: string | null | undefined): string {
  if (!url) return ''
  try {
    const parsed = new URL(url, window.location.origin)
    return (parsed.searchParams.get('url') || parsed.pathname).replace(/\\/g, '/')
  } catch {
    return url.split('?')[0]?.replace(/\\/g, '/') || ''
  }
}

function normalizeRasterSourceKey(value: string | null | undefined): string {
  return (value || '').replace(/\\/g, '/').trim().toLowerCase()
}

function parseWgs84Bounds(value: string | undefined): [number, number, number, number] | undefined {
  if (!value) return undefined
  try {
    const parsed = JSON.parse(value) as unknown
    if (!Array.isArray(parsed) || parsed.length !== 4) return undefined
    const bounds = parsed.map((item) => Number(item)) as [number, number, number, number]
    if (!isLikelyWgs84Bounds(bounds)) return undefined
    return bounds
  } catch {
    return undefined
  }
}

function isLikelyWgs84Bounds(bounds: [number, number, number, number] | null | undefined): bounds is [number, number, number, number] {
  if (!bounds) return false
  const [west, south, east, north] = bounds
  return (
    [west, south, east, north].every(Number.isFinite) &&
    west >= -180 &&
    east <= 180 &&
    south >= -90 &&
    north <= 90 &&
    east > west &&
    north > south
  )
}

function wgs84ToLeafletBounds(bounds: [number, number, number, number] | null | undefined): [[number, number], [number, number]] | null {
  if (!isLikelyWgs84Bounds(bounds)) return null
  const [west, south, east, north] = bounds
  return [[south, west], [north, east]]
}

function leafletToWgs84Bounds(bounds: [[number, number], [number, number]] | null | undefined): [number, number, number, number] | null {
  if (!bounds) return null
  const [[south, west], [north, east]] = bounds
  const wgsBounds: [number, number, number, number] = [west, south, east, north]
  return isLikelyWgs84Bounds(wgsBounds) ? wgsBounds : null
}

function wgs84BoundsArea(bounds: [number, number, number, number]): number {
  const [west, south, east, north] = bounds
  return Math.max(0, east - west) * Math.max(0, north - south)
}

function intersectWgs84Bounds(
  first: [number, number, number, number],
  second: [number, number, number, number],
): [number, number, number, number] | null {
  const intersection: [number, number, number, number] = [
    Math.max(first[0], second[0]),
    Math.max(first[1], second[1]),
    Math.min(first[2], second[2]),
    Math.min(first[3], second[3]),
  ]
  return isLikelyWgs84Bounds(intersection) ? intersection : null
}

function getComparisonFocusBounds(
  primaryBounds: [number, number, number, number] | null | undefined,
  compareBounds: [number, number, number, number] | null | undefined,
): [[number, number], [number, number]] | null {
  const primary = isLikelyWgs84Bounds(primaryBounds) ? primaryBounds : null
  const compare = isLikelyWgs84Bounds(compareBounds) ? compareBounds : null
  if (primary && compare) {
    const intersection = intersectWgs84Bounds(primary, compare)
    const focus = intersection ?? (wgs84BoundsArea(primary) <= wgs84BoundsArea(compare) ? primary : compare)
    return wgs84ToLeafletBounds(focus)
  }
  return wgs84ToLeafletBounds(compare ?? primary)
}

function stableHash(value: string): string {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(31, hash) + value.charCodeAt(index)
  }
  return Math.abs(hash).toString(36)
}

type StaticTileMeta = {
  zoom_max?: number
  zoom_min?: number
  bounds_wgs84?: [number, number, number, number]
}

function buildTitilerTileUrl(layer: {
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
    return toSameOriginBackendUrl(layer.url || '') || layer.url || ''
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
  if ((rasterType === 'dtm' || rasterType === 'dsm') && Number.isFinite(min) && Number.isFinite(max) && min !== max) {
    params.set('rescale', `${min},${max}`)
    return `${API_BASE}/api/dji-terra/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${params.toString()}`
  }
  return `${API_BASE}/api/titiler/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${params.toString()}`
}

function dynamicCogNativeZoom(tileUrl: string): number {
  if (tileUrl.includes('/api/ortho-cog/')) return 30
  if (tileUrl.includes('/api/dji-terra/')) return 22
  if (tileUrl.includes('/api/titiler/')) return 22
  return 22
}

async function fetchStaticTileMeta(tileUrl: string): Promise<StaticTileMeta | null> {
  const base = tileTemplateToStaticBase(tileUrl)
  if (!base) return null
  try {
    const res = await fetch(`${base}tileset.json`, { credentials: 'include' })
    if (!res.ok) return null
    return (await res.json()) as StaticTileMeta
  } catch {
    return null
  }
}

function tileFolderFromTemplate(url: string): string | null {
  const path = (() => {
    try {
      return new URL(url).pathname
    } catch {
      return url.split('?')[0] ?? url
    }
  })()
  const segments = path.split('/').filter(Boolean)
  const processedIndex = segments.findIndex((segment) => segment === 'processed')
  const folderParts = processedIndex >= 0 ? segments.slice(processedIndex + 1, -3) : []
  if (folderParts.length === 0) return null
  const folder = folderParts.join('/')
  try {
    return decodeURIComponent(folder)
  } catch {
    return folder
  }
}

function ElevationInteraction({
  active,
  onPickPoint,
}: {
  active: boolean
  onPickPoint: (ll: LatLng) => void
}) {
  useMapEvents({
    click(e) {
      if (!active) return
      onPickPoint(e.latlng)
    },
  })
  return null
}

function UserLocationMarker({ position }: { position: LatLng | null }) {
  const map = useMap()

  useEffect(() => {
    if (!position) return
    map.flyTo(position, Math.max(map.getZoom(), 16), { duration: 0.85 })
  }, [map, position])

  if (!position) return null
  return (
    <CircleMarker
      center={position}
      radius={7}
      pathOptions={{
        color: '#ffffff',
        weight: 2,
        fillColor: '#0e3e49',
        fillOpacity: 1,
      }}
    >
      <Popup>Your current location</Popup>
    </CircleMarker>
  )
}

function parseKmlLatLonBounds(xml: string): [[number, number], [number, number]] | null {
  const grab = (tag: string) => {
    const m = xml.match(new RegExp(`<${tag}>\\s*([\\d.\\-+eE]+)\\s*</${tag}>`, 'i'))
    return m ? Number(m[1]) : NaN
  }
  const n = grab('north')
  const s = grab('south')
  const e = grab('east')
  const w = grab('west')
  if ([n, s, e, w].some((v) => Number.isNaN(v))) return null
  return [
    [s, w],
    [n, e],
  ]
}

function parseTileMapResourceBounds(xml: string): [[number, number], [number, number]] | null {
  const pick = (name: string) => {
    const m = xml.match(new RegExp(`${name}\\s*=\\s*"([^"]+)"`, 'i'))
    return m ? Number(m[1]) : NaN
  }
  const minx = pick('minx')
  const miny = pick('miny')
  const maxx = pick('maxx')
  const maxy = pick('maxy')
  if ([minx, miny, maxx, maxy].some((v) => Number.isNaN(v))) return null
  const sw = L.CRS.EPSG3857.unproject(L.point(minx, miny))
  const ne = L.CRS.EPSG3857.unproject(L.point(maxx, maxy))
  return [
    [sw.lat, sw.lng],
    [ne.lat, ne.lng],
  ]
}

function MapController({
  layers,
  projectId,
  selectedUrl,
  zoomTrigger,
  disabled = false,
}: {
  layers: ViewerLayer[]
  projectId?: string
  selectedUrl?: string | null
  zoomTrigger: number
  disabled?: boolean
}) {
  const map = useMap()

  useEffect(() => {
    if (disabled) return
    if (!projectId) return
    const activeCog = layers.find((layer) => layer.projectId === projectId && layer.url === selectedUrl) ?? layers[0]
    if (!activeCog) return

    const rawPath = activeCog.rawPath ?? activeCog.cogPath ?? null
    const tileUrl = activeCog.url
    const fitKey = rawPath ?? activeCog.boundsWgs84?.join(',') ?? (isStaticXyzTileTemplate(tileUrl) ? tileUrl : null)
    if (!fitKey) return

    let cancelled = false
    let didFit = false
    type FitBounds = [[number, number], [number, number]]
    const fitNow = (bounds: FitBounds | null) => {
      if (!bounds || cancelled || didFit) return
      didFit = true
      map.invalidateSize(false)
      map.fitBounds(bounds, { padding: [24, 24], animate: false })
      window.setTimeout(() => {
        if (!cancelled) map.fitBounds(bounds, { padding: [24, 24], animate: false })
      }, 80)
    }
    const wgs84Bounds = (bounds?: [number, number, number, number] | null): FitBounds | null => {
      if (!bounds) return null
      const [minX, minY, maxX, maxY] = bounds
      return [
        [minY, minX],
        [maxY, maxX],
      ]
    }

    const metadataBounds = wgs84Bounds(activeCog.boundsWgs84)
    if (metadataBounds) {
      fitNow(metadataBounds)
      return () => {
        cancelled = true
      }
    }

    if (rawPath) {
      void fetch(`${API_BASE}/api/titiler/info?url=${encodeURIComponent(rawPath)}`, {
        credentials: 'include',
      })
        .then((res) => res.json() as Promise<{ bounds?: [number, number, number, number] }>)
        .then((data) => {
          const infoBounds = isLikelyWgs84Bounds(data?.bounds) ? data.bounds : null
          fitNow(wgs84Bounds(infoBounds))
        })
        .catch(() => {
          // Ignore auto-zoom failure and keep the map usable.
        })
      return () => {
        cancelled = true
      }
    }

    const base = tileTemplateToStaticBase(tileUrl)
    if (!base) return

    const datasetName = activeCog.name.replace(/\.tiff?$/i, '')
    const boundsUrl = `${API_BASE}/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetName)}/bounds`
    void (async () => {
      try {
        const res = await fetch(boundsUrl, { credentials: 'include' })
        if (cancelled) return
        if (res.ok) {
          const data = (await res.json()) as { bounds?: [number, number, number, number] | null }
          const backendFit = wgs84Bounds(data?.bounds)
          if (backendFit) {
            fitNow(backendFit)
            return
          }
        }
      } catch {
        // Fall back to local tile metadata only when backend bounds are unavailable.
      }

      if (cancelled || didFit) return

      const tilesetBounds = fetchStaticTileMeta(tileUrl)
        .then((meta) => {
          if (meta?.bounds_wgs84 && meta.bounds_wgs84.length === 4) {
            const [west, south, east, north] = meta.bounds_wgs84
            return [
              [south, west],
              [north, east],
            ] as FitBounds
          }
          return null
        })
        .catch(() => null)
      const kmlBounds = fetch(`${base}doc.kml`, { credentials: 'include' })
        .then(async (res) => (res.ok ? parseKmlLatLonBounds(await res.text()) : null))
        .catch(() => null)
      const tileMapResourceBounds = fetch(`${base}tilemapresource.xml`, { credentials: 'include' })
        .then(async (res) => (res.ok ? parseTileMapResourceBounds(await res.text()) : null))
        .catch(() => null)

      try {
        const fallbackBounds = await Promise.any(
          [tilesetBounds, kmlBounds, tileMapResourceBounds].map((promise) => (
            promise.then((bounds) => (bounds ? bounds : Promise.reject(new Error('No bounds'))))
          )),
        )
        fitNow(fallbackBounds)
      } catch {
        // Ignore auto-zoom fallback failure and keep the map usable.
      }
    })()

    return () => {
      cancelled = true
    }
  }, [disabled, layers, map, projectId, selectedUrl, zoomTrigger])

  return null
}

function ActiveLayerBoundsController({
  bounds,
  triggerKey,
}: {
  bounds: [[number, number], [number, number]] | null
  triggerKey: string
}) {
  const map = useMap()

  useEffect(() => {
    if (!bounds) return
    let cancelled = false
    const fit = () => {
      if (cancelled) return
      map.invalidateSize(false)
      map.fitBounds(bounds, { padding: [24, 24], animate: false })
    }
    fit()
    const timers = [100, 350, 900].map((delay) => window.setTimeout(fit, delay))
    return () => {
      cancelled = true
      timers.forEach((timer) => window.clearTimeout(timer))
    }
  }, [bounds, map, triggerKey])

  return null
}

interface MapPaneProps {
  measureMode: MeasureMode
  measureActive: boolean
  measurePoints: LatLng[]
  circleRadiusM: number
  areaFrozen: boolean
  onMeasureAdd: (ll: LatLng) => void
  onMeasureCloseRing: () => void
  issueMode: boolean
  onIssuePick: (ll: LatLng) => void
  elevationMode: boolean
  onElevationPick: (ll: LatLng) => void
  issues: SavedIssue[]
  cropEnabled: boolean
  cropFootprint?: LatLng[] | null
  cogBounds?: [[number, number], [number, number]] | null
  cogTileUrl: string | null
  comparisonPrimaryClipPercent?: number | null
  baseMap: BaseMapConfig
  userLocation: LatLng | null
  sync?: SyncRefs & { isA: boolean }
  spatialLayers: SpatialLayer[]
  selectedSpatialFeatureId: string | null
  digitizationMode: DigitizationMode
  digitizationActive: boolean
  draftPoints: LatLng[]
  onDigitizationAddPoint: (ll: LatLng) => void
  onDigitizationFinishDraft: () => void
  onSpatialFeatureClick: (feature: SpatialFeature) => void
  onSpatialGeometryChange: (feature: SpatialFeature, geojson: GeoJsonFeature) => void
}

function MapPane({
  measureMode,
  measureActive,
  measurePoints,
  circleRadiusM,
  areaFrozen,
  onMeasureAdd,
  onMeasureCloseRing,
  issueMode,
  onIssuePick,
  elevationMode,
  onElevationPick,
  issues,
  cropEnabled,
  cropFootprint,
  cogBounds,
  cogTileUrl,
  comparisonPrimaryClipPercent,
  baseMap,
  userLocation,
  sync,
  spatialLayers,
  selectedSpatialFeatureId,
  digitizationMode,
  digitizationActive,
  draftPoints,
  onDigitizationAddPoint,
  onDigitizationFinishDraft,
  onSpatialFeatureClick,
  onSpatialGeometryChange,
}: MapPaneProps) {
  return (
    <>
      {/*
        URL includes ?v=… cache-bust on custom bases (tileSources.withTileCacheBust).
        Bump VITE_TILE_CACHE_BUST after regenerating local tiles; Leaflet fetches XYZ per zoom/pan.
      */}
      <TileLayer
        key={baseMap.key}
        attribution={baseMap.attribution}
        url={baseMap.url}
        maxZoom={30}
        maxNativeZoom={baseMap.maxNativeZoom}
        updateWhenIdle={false}
        updateWhenZooming
        keepBuffer={6}
        detectRetina={false}
        crossOrigin
      />
      {cogTileUrl ? (
        <OrthomosaicTileLayerWithOptions
          tileUrl={cogTileUrl}
          cropEnabled={cropEnabled}
          cropFootprint={cropFootprint}
          bounds={cogBounds ?? undefined}
          comparisonPrimaryClipPercent={comparisonPrimaryClipPercent}
        />
      ) : null}
      {cropFootprint && cropFootprint.length >= 3 ? (
        <Polygon
          positions={cropFootprint}
          pathOptions={{
            color: cropEnabled ? '#22c55e' : '#f59e0b',
            weight: 3,
            opacity: 0.95,
            fillColor: cropEnabled ? '#22c55e' : '#f59e0b',
            fillOpacity: 0.08,
          }}
        />
      ) : null}
      <DigitizationInteraction
        mode={digitizationMode}
        active={digitizationActive}
        onAddPoint={onDigitizationAddPoint}
        onFinishDraft={onDigitizationFinishDraft}
      />
      <SpatialFeatureLayer
        layers={spatialLayers}
        selectedFeatureId={selectedSpatialFeatureId}
        editMode={digitizationMode === 'edit'}
        onFeatureClick={onSpatialFeatureClick}
        onGeometryChange={onSpatialGeometryChange}
      />
      {draftPoints.length > 0 && digitizationMode === 'polyline' ? (
        <Polyline
          positions={draftPoints}
          pathOptions={{ color: '#2dd4bf', weight: 3, dashArray: '6 4' }}
        />
      ) : null}
      {draftPoints.length > 0 && digitizationMode === 'polygon' ? (
        <Polygon
          positions={draftPoints}
          pathOptions={{
            color: '#2dd4bf',
            weight: 3,
            fillColor: '#0e3e49',
            fillOpacity: 0.22,
            dashArray: '6 4',
          }}
        />
      ) : null}
      {draftPoints.map((point, index) => (
        <CircleMarker
          key={`digitization-draft-${index}-${point.lat.toFixed(6)}-${point.lng.toFixed(6)}`}
          center={point}
          radius={5}
          pathOptions={{
            color: '#ffffff',
            weight: 2,
            fillColor: '#2dd4bf',
            fillOpacity: 1,
          }}
        />
      ))}
      {measureActive ? (
        <>
          <MeasureInteraction
            mode={measureMode}
            enabled={measureActive}
            points={measurePoints}
            areaFrozen={areaFrozen}
            onAddPoint={onMeasureAdd}
            onCloseRing={onMeasureCloseRing}
          />
          {(measureMode === 'distance' || measureMode === 'profile') && measurePoints.length > 0 ? (
            <Polyline
              positions={measurePoints}
              pathOptions={{ color: measureMode === 'profile' ? '#be123c' : '#0e3e49', weight: 3, dashArray: '6 4' }}
            />
          ) : null}
          {(measureMode === 'area' || measureMode === 'volume-area') && measurePoints.length > 0 ? (
            <Polygon
              positions={measurePoints}
              pathOptions={{
                color: '#0e3e49',
                weight: 2,
                fillColor: '#14b8a6',
                fillOpacity: 0.25,
              }}
            />
          ) : null}
          {measureMode === 'volume-circle' && measurePoints.length > 0 ? (
            <Circle
              center={measurePoints[0]!}
              radius={circleRadiusM || 1}
              pathOptions={{
                color: '#7c3aed',
                weight: 2,
                fillColor: '#8b5cf6',
                fillOpacity: 0.18,
              }}
            />
          ) : null}
          {measurePoints.map((p, i) => (
            <CircleMarker
              key={`${p.lat.toFixed(5)}-${p.lng.toFixed(5)}-${i}`}
              center={p}
              radius={5}
              pathOptions={{
                color: '#0e3e49',
                weight: 2,
                fillColor: '#fff',
                fillOpacity: 1,
              }}
            />
          ))}
        </>
      ) : null}
      <IssueInteraction active={issueMode} onPickPoint={onIssuePick} />
      <ElevationInteraction active={elevationMode} onPickPoint={onElevationPick} />
      <UserLocationMarker position={userLocation} />
      {issues.map((issue) => (
        <Marker key={issue.id} position={[issue.lat, issue.lng]}>
          <Popup>
            <div className="mv-issue-popup">
              <h4 className="mv-issue-popup__title">{issue.title}</h4>
              <p className="mv-issue-popup__desc">{issue.description}</p>
              <span className="mv-issue-popup__badge">{issue.status}</span>
            </div>
          </Popup>
        </Marker>
      ))}
      {sync ? (
        <MapSyncBridge
          isA={sync.isA}
          lockRef={sync.lockRef}
          mapARef={sync.mapARef}
          mapBRef={sync.mapBRef}
        />
      ) : null}
    </>
  )
}

function OrthomosaicTileLayerWithOptions({
  tileUrl,
  cropEnabled,
  cropFootprint,
  bounds,
  comparisonPrimaryClipPercent,
}: {
  tileUrl: string
  cropEnabled: boolean
  cropFootprint?: LatLng[] | null
  bounds?: [[number, number], [number, number]]
  comparisonPrimaryClipPercent?: number | null
}) {
  const map = useMap()
  const paneName = useMemo(() => `orthomosaic-crop-pane-${stableHash(tileUrl)}`, [tileUrl])
  const [nativeZoom, setNativeZoom] = useState(22)
  const [nativeMinZoom, setNativeMinZoom] = useState(0)
  const isDynamicCogLayer =
    tileUrl.includes('/api/titiler/') || tileUrl.includes('/api/dji-terra/') || tileUrl.includes('/api/ortho-cog/')
  const effectiveNativeZoom = isDynamicCogLayer ? dynamicCogNativeZoom(tileUrl) : nativeZoom
  const effectiveNativeMinZoom = isDynamicCogLayer ? 0 : nativeMinZoom
  const effectiveMaxZoom = isDynamicCogLayer ? 30 : 30
  const effectiveKeepBuffer = isDynamicCogLayer ? 4 : 6
  const effectiveOpacity = isDynamicCogLayer ? 1 : 0.9
  const boundsKey = bounds ? bounds.flat().map((value) => value.toFixed(7)).join(',') : 'unbounded'
  const tileBounds = isDynamicCogLayer ? undefined : bounds

  useEffect(() => {
    let cancelled = false
    if (isDynamicCogLayer) {
      return () => {
        cancelled = true
      }
    }
    void fetchStaticTileMeta(tileUrl).then((meta) => {
      if (cancelled) return
      const zoomMax = Number(meta?.zoom_max)
      const zoomMin = Number(meta?.zoom_min)
      if (Number.isFinite(zoomMax) && zoomMax >= 0) {
        setNativeZoom(Math.max(0, Math.min(30, Math.round(zoomMax))))
      } else {
        setNativeZoom(22)
      }
      if (Number.isFinite(zoomMin) && zoomMin >= 0) {
        setNativeMinZoom(Math.max(0, Math.min(30, Math.round(zoomMin))))
      } else {
        setNativeMinZoom(0)
      }
    })
    return () => {
      cancelled = true
    }
  }, [isDynamicCogLayer, tileUrl])

  useEffect(() => {
    const pane = map.getPane(paneName) ?? null
    if (comparisonPrimaryClipPercent != null) {
      return applySwipeClip(map, pane, comparisonPrimaryClipPercent, 'left')
    }
    return applyTileClip(map, pane, cropEnabled ? cropFootprint ?? null : null)
  }, [comparisonPrimaryClipPercent, cropEnabled, cropFootprint, map, paneName])

  return (
    <Pane name={paneName} style={{ zIndex: 220 }}>
      <TileLayer
        key={`cog-${ORTHO_RENDERER_VERSION}-${tileUrl}-${boundsKey}-${cropEnabled ? 'crop' : 'full'}`}
        url={tileUrl}
        opacity={effectiveOpacity}
        maxZoom={effectiveMaxZoom}
        maxNativeZoom={effectiveNativeZoom}
        minNativeZoom={effectiveNativeMinZoom}
        bounds={tileBounds}
        noWrap
        updateWhenIdle={false}
        updateWhenZooming
        keepBuffer={effectiveKeepBuffer}
        detectRetina={false}
        crossOrigin
      />
    </Pane>
  )
}

function ComparisonSwipeLayer({
  tileUrl,
  percent,
}: {
  tileUrl: string
  percent: number
}) {
  const map = useMap()
  const paneName = useMemo(() => `comparison-swipe-pane-${stableHash(tileUrl)}`, [tileUrl])
  const clampedPercent = Math.max(5, Math.min(95, percent))

  useEffect(() => {
    const pane = map.getPane(paneName)
    if (pane) pane.style.pointerEvents = 'none'
    const cleanup = applySwipeClip(map, pane ?? null, clampedPercent, 'right')
    return () => {
      cleanup()
      if (pane) pane.style.pointerEvents = ''
    }
  }, [clampedPercent, map, paneName])

  return (
    <Pane name={paneName} style={{ zIndex: 260, pointerEvents: 'none' }}>
      <TileLayer
        key={`comparison-${ORTHO_RENDERER_VERSION}-${tileUrl}`}
        url={tileUrl}
        opacity={1}
        maxZoom={30}
        maxNativeZoom={dynamicCogNativeZoom(tileUrl)}
        minNativeZoom={0}
        noWrap
        updateWhenIdle={false}
        updateWhenZooming
        keepBuffer={4}
        detectRetina={false}
        crossOrigin
      />
    </Pane>
  )
}

function ProfileChart({
  result,
  svgRef,
}: {
  result: ProfileResponse
  svgRef: RefObject<SVGSVGElement | null>
}) {
  const values = result.points.filter((p) => p.elevation != null)
  if (values.length === 0) {
    return (
      <div className="mv-profile-empty">
        No valid DTM elevation samples found on this line.
      </div>
    )
  }
  const maxDist = Math.max(...values.map((p) => p.distance_m), 1)
  const elevations = values.map((p) => Number(p.elevation))
  const minElev = Math.min(...elevations)
  const maxElev = Math.max(...elevations)
  const span = Math.max(maxElev - minElev, 1)
  const points = values
    .map((p) => {
      const x = 44 + (p.distance_m / maxDist) * 680
      const y = 218 - ((Number(p.elevation) - minElev) / span) * 170
      return `${x},${y}`
    })
    .join(' ')
  return (
    <svg ref={svgRef} className="mv-profile-chart" viewBox="0 0 760 260" role="img" aria-label="Elevation profile graph">
      <rect x="0" y="0" width="760" height="260" fill="#ffffff" />
      <line x1="44" y1="218" x2="724" y2="218" stroke="#cbd5e1" />
      <line x1="44" y1="48" x2="44" y2="218" stroke="#cbd5e1" />
      <text x="44" y="238" fill="#475569" fontSize="12">0 m</text>
      <text x="660" y="238" fill="#475569" fontSize="12">{maxDist.toFixed(0)} m</text>
      <text x="8" y="54" fill="#475569" fontSize="12">{maxElev.toFixed(2)} m</text>
      <text x="8" y="218" fill="#475569" fontSize="12">{minElev.toFixed(2)} m</text>
      <polyline points={points} fill="none" stroke="#be123c" strokeWidth="3" />
    </svg>
  )
}

export type MapViewerProps = {
  projectId?: string
}

export function MapViewer({ projectId }: MapViewerProps) {
  const modal = useModal()
  const { activeLayers } = useWorkspaceContext()
  const center = useMemo(() => getDefaultMapCenter(), [])
  const zoom = useMemo(() => getDefaultZoom(), [])

  const [splitView, setSplitView] = useState(false)
  const [measureMode, setMeasureMode] = useState<MeasureMode>('none')
  const [points, setPoints] = useState<LatLng[]>([])
  const [areaFrozen, setAreaFrozen] = useState(false)
  const [issueMode, setIssueMode] = useState(false)
  const [elevationMode, setElevationMode] = useState(false)
  const [cropMode, setCropMode] = useState<'off' | 'kml' | 'draw'>('off')
  const [cropEnabled, setCropEnabled] = useState(false)
  const [baseMapKey, setBaseMapKey] = useState<BaseMapKey>('esri-imagery')
  const [cogTileUrl, setCogTileUrl] = useState<string | null>(null)
  const [zoomTrigger, setZoomTrigger] = useState(0)
  const [compareCogTileUrl, setCompareCogTileUrl] = useState<string | null>(null)
  const [compareSliderPercent, setCompareSliderPercent] = useState(50)
  const [projectFiles, setProjectFiles] = useState<ProjectFile[]>([])
  const [projectLayersLoading, setProjectLayersLoading] = useState(false)
  const [cropMaskPoints, setCropMaskPoints] = useState<LatLng[] | null>(null)
  const [cropBusy, setCropBusy] = useState(false)
  const kmlInputRef = useRef<HTMLInputElement | null>(null)
  const [cogBounds, setCogBounds] = useState<[[number, number], [number, number]] | null>(null)
  const [compareCogBounds, setCompareCogBounds] = useState<[[number, number], [number, number]] | null>(null)
  const [issueDraft, setIssueDraft] = useState<IssueDraft | null>(null)
  const [issueSubmitting, setIssueSubmitting] = useState(false)
  const [issueError, setIssueError] = useState<string | null>(null)
  const [issues, setIssues] = useState<SavedIssue[]>([])
  const [issuesRefreshTick, setIssuesRefreshTick] = useState(0)
  const [analysisDatasetId, setAnalysisDatasetId] = useState('')
  const [elevationResult, setElevationResult] = useState<ElevationResponse | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null)
  const [volumeResult, setVolumeResult] = useState<DtmVolumeResponse | null>(null)
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [userLocation, setUserLocation] = useState<LatLng | null>(null)
  const [spatialLayers, setSpatialLayers] = useState<SpatialLayer[]>([])
  const [spatialBusy, setSpatialBusy] = useState(false)
  const [selectedSpatialFeature, setSelectedSpatialFeature] = useState<SpatialFeature | null>(null)
  const profileChartRef = useRef<SVGSVGElement | null>(null)
  const spatialImportInputRef = useRef<HTMLInputElement | null>(null)
  const mapCanvasRef = useRef<HTMLDivElement | null>(null)

  const selectedBaseMap = useMemo(() => getBaseMap(baseMapKey), [baseMapKey])

  const distanceM = useMemo(
    () => (measureMode === 'distance' || measureMode === 'profile' ? totalPathLengthM(points) : 0),
    [measureMode, points],
  )

  const areaM2 = useMemo(
    () => (measureMode === 'area' || measureMode === 'volume-area' ? ringAreaM2(points) : 0),
    [measureMode, points],
  )
  const circleRadiusM = useMemo(
    () => (measureMode === 'volume-circle' && points.length >= 2 ? points[0]!.distanceTo(points[1]!) : 0),
    [measureMode, points],
  )

  const clearMeasure = useCallback(() => {
    setPoints([])
    setAreaFrozen(false)
  }, [])

  const clearIssueMode = useCallback(() => {
    setIssueMode(false)
    setIssueDraft(null)
    setIssueError(null)
    setIssueSubmitting(false)
  }, [])

  const clearAnalysisResults = useCallback(() => {
    setElevationResult(null)
    setProfileResult(null)
    setVolumeResult(null)
    setAnalysisError(null)
  }, [])

  const downloadTextFile = useCallback((filename: string, content: string, type: string) => {
    const blob = new Blob([content], { type })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }, [])

  const exportCurrentDrawingKml = useCallback(async () => {
    if (points.length === 0) {
      await modal.alert('No drawing found', 'Draw a distance line or area polygon before exporting.')
      return
    }
    const coords = points.map((point) => `${point.lng},${point.lat},0`).join(' ')
    const isPolygon = ['area', 'volume-area'].includes(measureMode) && points.length >= 3
    const geometry = isPolygon
      ? `<Polygon><outerBoundaryIs><LinearRing><coordinates>${coords} ${points[0]!.lng},${points[0]!.lat},0</coordinates></LinearRing></outerBoundaryIs></Polygon>`
      : `<LineString><tessellate>1</tessellate><coordinates>${coords}</coordinates></LineString>`
    downloadTextFile(
      'droid-drawing.kml',
      `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Droid Drawing</name>${geometry}</Placemark></Document></kml>`,
      'application/vnd.google-earth.kml+xml',
    )
  }, [downloadTextFile, measureMode, modal, points])

  const exportCurrentDrawingCsv = useCallback(async () => {
    if (points.length === 0) {
      await modal.alert('No drawing found', 'Draw a distance line or area polygon before exporting.')
      return
    }
    const rows = ['vertex,lat,lng']
    points.forEach((point, index) => rows.push(`${index + 1},${point.lat},${point.lng}`))
    downloadTextFile('droid-drawing-coordinates.csv', rows.join('\n'), 'text/csv')
  }, [downloadTextFile, modal, points])

  const setTool = useCallback((mode: MeasureMode) => {
    setIssueMode(false)
    setElevationMode(false)
    setIssueDraft(null)
    setIssueError(null)
    clearAnalysisResults()
    setMeasureMode((prev) => {
      const next = prev === mode ? 'none' : mode
      return next
    })
    setPoints([])
    setAreaFrozen(false)
  }, [clearAnalysisResults])

  const toggleIssueMode = useCallback(() => {
    setMeasureMode('none')
    setElevationMode(false)
    setPoints([])
    setAreaFrozen(false)
    setIssueError(null)
    setIssueDraft(null)
    setIssueMode((prev) => !prev)
  }, [])

  const toggleElevationMode = useCallback(() => {
    setMeasureMode('none')
    setPoints([])
    setAreaFrozen(false)
    setIssueMode(false)
    setIssueDraft(null)
    setAnalysisError(null)
    clearAnalysisResults()
    setElevationMode((prev) => !prev)
  }, [clearAnalysisResults])

  const findMyLocation = useCallback(async () => {
    if (!navigator.geolocation) {
      await modal.alert('Location unavailable', 'This browser does not support location access.')
      return
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setUserLocation(L.latLng(position.coords.latitude, position.coords.longitude))
      },
      () => {
        void modal.alert('Location blocked', 'Location permission was not granted. You can still use the dataset map normally.')
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
    )
  }, [modal])

  const replaceSpatialFeatureInState = useCallback((feature: SpatialFeature) => {
    setSpatialLayers((prev) =>
      prev.map((layer) =>
        layer.id === feature.layer_id
          ? {
              ...layer,
              features: layer.features.map((item) => (item.id === feature.id ? feature : item)),
              updated_at: feature.updated_at,
            }
          : layer,
      ),
    )
    setSelectedSpatialFeature((prev) => (prev?.id === feature.id ? feature : prev))
  }, [])

  const appendSpatialFeatureToState = useCallback((feature: SpatialFeature) => {
    setSpatialLayers((prev) => {
      const hasLayer = prev.some((layer) => layer.id === feature.layer_id)
      if (!hasLayer) {
        return [
          ...prev,
          {
            id: feature.layer_id,
            project_id: feature.project_id,
            name: 'Drawn Shapes',
            source_type: feature.source_type,
            created_at: feature.created_at,
            updated_at: feature.updated_at,
            features: [feature],
          },
        ]
      }
      return prev.map((layer) =>
        layer.id === feature.layer_id
          ? {
              ...layer,
              features: [...layer.features, feature],
              updated_at: feature.updated_at,
            }
          : layer,
      )
    })
  }, [])

  const removeSpatialFeatureFromState = useCallback((featureId: string) => {
    setSpatialLayers((prev) =>
      prev.map((layer) => ({
        ...layer,
        features: layer.features.filter((feature) => feature.id !== featureId),
      })),
    )
  }, [])

  const handleDigitizationDraftComplete = useCallback(
    async (geojson: GeoJsonFeature) => {
      if (!projectId) {
        await modal.alert('No project selected', 'Open a project before saving spatial data.')
        return
      }
      setSpatialBusy(true)
      try {
        const feature = await createSpatialFeature(projectId, {
          layer_name: 'Drawn Shapes',
          geojson,
          structure_type: 'Unassigned',
          source_type: 'drawn',
        })
        appendSpatialFeatureToState(feature)
        setSelectedSpatialFeature(feature)
      } catch (error) {
        await modal.alert('Spatial save failed', error instanceof Error ? error.message : 'Could not save shape.')
      } finally {
        setSpatialBusy(false)
      }
    },
    [appendSpatialFeatureToState, modal, projectId],
  )

  const digitization = useDigitizationDrawing({
    disabled: splitView,
    onDraftComplete: handleDigitizationDraftComplete,
  })

  const handleDigitizationModeChange = useCallback(
    (nextMode: DigitizationMode) => {
      setMeasureMode('none')
      setPoints([])
      setAreaFrozen(false)
      setIssueMode(false)
      setIssueDraft(null)
      setIssueError(null)
      setElevationMode(false)
      setSelectedSpatialFeature(null)
      digitization.setMode(nextMode)
    },
    [digitization.setMode],
  )

  const handleSpatialImport = useCallback(
    async (file: File) => {
      if (!projectId) {
        await modal.alert('No project selected', 'Open a project before importing spatial files.')
        return
      }
      setSpatialBusy(true)
      try {
        const layer = await importSpatialLayer(projectId, file)
        setSpatialLayers((prev) => [...prev.filter((item) => item.id !== layer.id), layer])
        setSelectedSpatialFeature(layer.features[0] ?? null)
        await modal.alert('Spatial import complete', `${layer.features.length} shape(s) imported from ${file.name}.`)
      } catch (error) {
        await modal.alert('Spatial import failed', error instanceof Error ? error.message : 'Could not import file.')
      } finally {
        setSpatialBusy(false)
      }
    },
    [modal, projectId],
  )

  const handleSpatialAssignmentSave = useCallback(
    async (payload: { plot_id: string; owner_name: string; structure_type: StructureType }) => {
      if (!projectId || !selectedSpatialFeature) return
      setSpatialBusy(true)
      try {
        const feature = await updateSpatialFeature(projectId, selectedSpatialFeature.id, payload)
        replaceSpatialFeatureInState(feature)
        setSelectedSpatialFeature(feature)
      } catch (error) {
        await modal.alert('Assignment save failed', error instanceof Error ? error.message : 'Could not save assignment.')
      } finally {
        setSpatialBusy(false)
      }
    },
    [modal, projectId, replaceSpatialFeatureInState, selectedSpatialFeature],
  )

  const handleSpatialFeatureDelete = useCallback(
    async (feature: SpatialFeature) => {
      if (!projectId) return
      const confirmed = await modal.confirm('Delete shape', 'This will permanently remove the selected spatial shape.')
      if (!confirmed) return
      setSpatialBusy(true)
      try {
        await deleteSpatialFeature(projectId, feature.id)
        removeSpatialFeatureFromState(feature.id)
        setSelectedSpatialFeature(null)
      } catch (error) {
        await modal.alert('Delete failed', error instanceof Error ? error.message : 'Could not delete shape.')
      } finally {
        setSpatialBusy(false)
      }
    },
    [modal, projectId, removeSpatialFeatureFromState],
  )

  const handleSpatialGeometryChange = useCallback(
    async (feature: SpatialFeature, geojson: GeoJsonFeature) => {
      if (!projectId) return
      setSpatialBusy(true)
      try {
        const updated = await updateSpatialFeature(projectId, feature.id, { geojson })
        replaceSpatialFeatureInState(updated)
      } catch (error) {
        await modal.alert('Geometry save failed', error instanceof Error ? error.message : 'Could not save edited geometry.')
      } finally {
        setSpatialBusy(false)
      }
    },
    [modal, projectId, replaceSpatialFeatureInState],
  )

  const handleSpatialFeatureClick = useCallback(
    (feature: SpatialFeature) => {
      setSelectedSpatialFeature(feature)
    },
    [],
  )

  useEffect(() => {
    if (measureMode === 'none') {
      setPoints([])
      setAreaFrozen(false)
    }
  }, [measureMode])

  useEffect(() => {
    if (!splitView) return
    digitization.cancelDigitization()
    setMeasureMode('none')
    setIssueMode(false)
    setElevationMode(false)
    setCropEnabled(false)
    setCropMode('off')
    clearAnalysisResults()
    setPoints([])
    setAreaFrozen(false)
  }, [clearAnalysisResults, digitization.cancelDigitization, splitView])

  useEffect(() => {
    if (!projectId) {
      setProjectFiles([])
      setProjectLayersLoading(false)
      return
    }
    let cancelled = false
    setProjectLayersLoading(true)
    void getProjectFiles(projectId)
      .then((files) => {
        if (!cancelled) setProjectFiles(files)
      })
      .catch(() => {
        if (!cancelled) setProjectFiles([])
      })
      .finally(() => {
        if (!cancelled) setProjectLayersLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId) {
      setSpatialLayers([])
      setSelectedSpatialFeature(null)
      return
    }
    let cancelled = false
    void listSpatialLayers(projectId)
      .then((layers) => {
        if (!cancelled) setSpatialLayers(layers)
      })
      .catch(() => {
        if (!cancelled) setSpatialLayers([])
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    let cancelled = false

    async function loadIssues() {
      try {
        const data = await listIssues()
        if (!cancelled) {
          setIssues(data ?? [])
        }
      } catch {
        if (!cancelled) {
          setIssues([])
        }
      }
    }

    void loadIssues()
    return () => {
      cancelled = true
    }
  }, [issuesRefreshTick])

  const onMeasureAdd = useCallback(
    (ll: LatLng) => {
      setPoints((prev) => [...prev, ll])
    },
    [],
  )

  const onMeasureCloseRing = useCallback(() => {
    setAreaFrozen(true)
  }, [])

  const onIssuePick = useCallback((ll: LatLng) => {
    setIssueDraft({
      lat: ll.lat,
      lng: ll.lng,
      title: '',
      description: '',
    })
    setIssueError(null)
  }, [])

  const projectCogLayers = useMemo<ViewerLayer[]>(() => {
    const fromFiles = projectFiles
      .filter((file) => file.layer_url && file.status === 'Web-Ready' && file.type === 'cog')
      .map<ViewerLayer>((file) => {
        const bounds = parseWgs84Bounds(file.bounds_wgs84)
        const layerType = file.layer_type || (['dtm', 'dsm', 'ortho'].includes(String(file.dataset_type).toLowerCase())
          ? String(file.dataset_type).toUpperCase().replace('ORTHO', 'Ortho')
          : 'cog')
        const baseLayer = {
          id: file.dataset_id || file.rel_path || file.name,
          projectId: projectId || '',
          name: file.name,
          url: toSameOriginBackendUrl(file.layer_url) || file.layer_url,
          rawPath: file.cog_path,
          layerType,
          cogPath: file.cog_path,
          cogRelPath: file.cog_rel_path,
          rescaleMin: file.rescale_min,
          rescaleMax: file.rescale_max,
          boundsWgs84: bounds,
          datasetId: file.dataset_id,
          datasetType: file.dataset_type,
          month: file.month,
          cacheKey: file.updated_at || file.dataset_id || file.cog_rel_path,
        }
        return {
          ...baseLayer,
          url: buildTitilerTileUrl(baseLayer),
        }
      })
    const fileLayerByDatasetId = new Map<string, ViewerLayer>()
    fromFiles.forEach((layer) => {
      if (layer.datasetId) fileLayerByDatasetId.set(layer.datasetId, layer)
    })
    const fileLayerBySource = new Map<string, ViewerLayer>()
    fromFiles.forEach((layer) => {
      ;[layer.cogPath, layer.cogRelPath, rasterSourceKeyFromTileUrl(layer.url)].forEach((key) => {
        const normalized = normalizeRasterSourceKey(key)
        if (normalized) fileLayerBySource.set(normalized, layer)
      })
    })
    const fromContext = activeLayers
      .filter((item) => (
        item.projectId === projectId &&
        ['cog', 'Ortho', 'DTM', 'DSM'].includes(String(item.layerType)) &&
        Boolean(item.url)
      ))
      .map((item) => {
        const baseLayer = {
          id: item.id,
          projectId: item.projectId,
          name: item.name,
          url: item.url,
          rawPath: item.rawPath || item.cogPath,
          layerType: item.layerType,
          cogPath: item.cogPath,
          cogRelPath: item.cogRelPath,
          rescaleMin: item.rescaleMin,
          rescaleMax: item.rescaleMax,
          boundsWgs84: item.boundsWgs84,
          datasetId: item.datasetId,
          datasetType: item.datasetType,
          month: item.month,
          cacheKey: item.datasetId || item.cogRelPath,
        }
        const sourceMatch =
          fileLayerByDatasetId.get(String(item.datasetId || '')) ??
          fileLayerBySource.get(normalizeRasterSourceKey(item.cogPath || item.rawPath)) ??
          fileLayerBySource.get(normalizeRasterSourceKey(item.cogRelPath)) ??
          fileLayerBySource.get(normalizeRasterSourceKey(rasterSourceKeyFromTileUrl(item.url)))
        const enrichedLayer = {
          ...baseLayer,
          boundsWgs84: baseLayer.boundsWgs84 ?? sourceMatch?.boundsWgs84,
          cogPath: baseLayer.cogPath || sourceMatch?.cogPath,
          cogRelPath: baseLayer.cogRelPath || sourceMatch?.cogRelPath,
          rescaleMin: baseLayer.rescaleMin ?? sourceMatch?.rescaleMin,
          rescaleMax: baseLayer.rescaleMax ?? sourceMatch?.rescaleMax,
          datasetId: baseLayer.datasetId || sourceMatch?.datasetId,
          datasetType: baseLayer.datasetType || sourceMatch?.datasetType,
          cacheKey: baseLayer.cacheKey || sourceMatch?.cacheKey,
        }
        return {
          ...enrichedLayer,
          url: buildTitilerTileUrl(enrichedLayer),
        }
      })
    const seen = new Set<string>()
    return [...fromFiles, ...fromContext].filter((layer) => {
      const key = layer.datasetId || layer.url || layer.id
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [activeLayers, projectFiles, projectId])

  const activeAnalysisLayer = useMemo(
    () =>
      projectCogLayers.find(
        (item) =>
          item.datasetId === analysisDatasetId &&
          (item.datasetType === 'dtm' || item.datasetType === 'dsm'),
      ) ?? null,
    [analysisDatasetId, projectCogLayers],
  )

  const analysisLayers = useMemo(
    () =>
      projectCogLayers.filter(
        (item) =>
          (item.datasetType === 'dtm' || item.datasetType === 'dsm') &&
          item.datasetId,
      ),
    [projectCogLayers],
  )

  const selectedPrimaryCogLayer = useMemo(
    () =>
      projectCogLayers.find((layer) => layer.url === cogTileUrl) ??
      projectCogLayers.find((layer) => rasterSourceKeyFromTileUrl(layer.url) === rasterSourceKeyFromTileUrl(cogTileUrl)) ??
      null,
    [cogTileUrl, projectCogLayers],
  )

  const selectedCompareCogLayer = useMemo(
    () =>
      projectCogLayers.find((layer) => layer.url === compareCogTileUrl) ??
      projectCogLayers.find((layer) => rasterSourceKeyFromTileUrl(layer.url) === rasterSourceKeyFromTileUrl(compareCogTileUrl)) ??
      null,
    [compareCogTileUrl, projectCogLayers],
  )

  const comparisonFitBounds = useMemo(() => {
    if (!splitView) return null
    return getComparisonFocusBounds(
      selectedPrimaryCogLayer?.boundsWgs84 ?? leafletToWgs84Bounds(cogBounds),
      selectedCompareCogLayer?.boundsWgs84 ?? leafletToWgs84Bounds(compareCogBounds),
    )
  }, [cogBounds, compareCogBounds, selectedCompareCogLayer, selectedPrimaryCogLayer, splitView])

  const comparisonFitKey = useMemo(
    () => [
      'compare',
      cogTileUrl ?? 'none',
      compareCogTileUrl ?? 'none',
      comparisonFitBounds?.flat().map((value) => value.toFixed(7)).join(',') ?? 'no-bounds',
    ].join('|'),
    [cogTileUrl, compareCogTileUrl, comparisonFitBounds],
  )

  useEffect(() => {
    if (!analysisDatasetId && analysisLayers[0]?.datasetId) {
      setAnalysisDatasetId(analysisLayers[0].datasetId)
    }
  }, [analysisDatasetId, analysisLayers])

  const onElevationPick = useCallback(
    async (ll: LatLng) => {
      if (!projectId || !activeAnalysisLayer?.datasetId || analysisBusy) return
      setAnalysisBusy(true)
      setAnalysisError(null)
      try {
        const res = await getElevation(projectId, activeAnalysisLayer.datasetId, ll.lat, ll.lng)
        setElevationResult(res)
      } catch (error) {
        setAnalysisError(error instanceof Error ? error.message : 'Elevation check failed')
      } finally {
        setAnalysisBusy(false)
      }
    },
    [activeAnalysisLayer?.datasetId, analysisBusy, projectId],
  )

  const runProfile = useCallback(async () => {
    if (!projectId || !activeAnalysisLayer?.datasetId || points.length < 2 || analysisBusy) return
    setAnalysisBusy(true)
    setAnalysisError(null)
    try {
      const payload = points.map((p) => [p.lat, p.lng] as [number, number])
      const res = await getProfile(projectId, activeAnalysisLayer.datasetId, payload)
      setProfileResult(res)
    } catch (error) {
      setAnalysisError(error instanceof Error ? error.message : 'Profile generation failed')
    } finally {
      setAnalysisBusy(false)
    }
  }, [activeAnalysisLayer?.datasetId, analysisBusy, points, projectId])

  const runVolume = useCallback(
    async (scope: 'overall' | 'area' | 'circle') => {
      if (!projectId || !activeAnalysisLayer?.datasetId || analysisBusy) return
      if (scope === 'area' && points.length < 3) return
      if (scope === 'circle' && (points.length < 2 || circleRadiusM <= 0)) return
      setAnalysisBusy(true)
      setAnalysisError(null)
      try {
        const payload =
          scope === 'area'
            ? { points: points.map((p) => [p.lat, p.lng] as [number, number]) }
            : scope === 'circle'
              ? { circle_center: [points[0]!.lat, points[0]!.lng] as [number, number], circle_radius_m: circleRadiusM }
              : {}
        const res = await getDtmVolume(projectId, activeAnalysisLayer.datasetId, payload)
        setVolumeResult(res)
      } catch (error) {
        setAnalysisError(error instanceof Error ? error.message : 'Volume calculation failed')
      } finally {
        setAnalysisBusy(false)
      }
    },
    [activeAnalysisLayer?.datasetId, analysisBusy, circleRadiusM, points, projectId],
  )

  useEffect(() => {
    if (measureMode !== 'profile' || points.length < 2 || !activeAnalysisLayer?.datasetId) return
    const timer = window.setTimeout(() => {
      void runProfile()
    }, 700)
    return () => window.clearTimeout(timer)
  }, [activeAnalysisLayer?.datasetId, measureMode, points, runProfile])

  const exportProfileCsv = useCallback(() => {
    if (!profileResult) return
    const header = 'distance_m,lat,lng,elevation_m\n'
    const rows = profileResult.points
      .map((p) => `${p.distance_m.toFixed(3)},${p.lat},${p.lng},${p.elevation ?? ''}`)
      .join('\n')
    const url = URL.createObjectURL(new Blob([header + rows], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'dtm-profile.csv'
    a.click()
    URL.revokeObjectURL(url)
  }, [profileResult])

  const exportProfilePng = useCallback(() => {
    const svg = profileChartRef.current
    if (!svg) return
    const data = new XMLSerializer().serializeToString(svg)
    const img = new Image()
    const url = URL.createObjectURL(new Blob([data], { type: 'image/svg+xml;charset=utf-8' }))
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = 760
      canvas.height = 260
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      const a = document.createElement('a')
      a.href = canvas.toDataURL('image/png')
      a.download = 'dtm-profile.png'
      a.click()
    }
    img.src = url
  }, [])

  const saveDrawCrop = useCallback(async () => {
    if (!projectId || !cogTileUrl || points.length < 3 || cropBusy) return
    const tileFolder = tileFolderFromTemplate(cogTileUrl)
    if (!tileFolder) return
    setCropBusy(true)
    try {
      const payload = points.map((p) => [p.lat, p.lng] as [number, number])
      const res = await saveDatasetCropMaskDraw(projectId, tileFolder, payload)
      const ll = res.points.map((p) => L.latLng(Number(p[0]), Number(p[1])))
      setCropMaskPoints(ll.length >= 3 ? ll : null)
      setCropEnabled(true)
      await modal.alert('Crop saved', 'Crop shape saved to database.')
    } catch (e) {
      await modal.alert('Crop save failed', e instanceof Error ? e.message : 'Failed to save drawn crop.')
    } finally {
      setCropBusy(false)
    }
  }, [cogTileUrl, cropBusy, modal, points, projectId])

  const importKmlCrop = useCallback(
    async (file: File) => {
      if (!projectId || !cogTileUrl || cropBusy) return
      const tileFolder = tileFolderFromTemplate(cogTileUrl)
      if (!tileFolder) return
      setCropBusy(true)
      try {
        const res = await saveDatasetCropMaskKml(projectId, tileFolder, file)
        const ll = res.points.map((p) => L.latLng(Number(p[0]), Number(p[1])))
        setCropMaskPoints(ll.length >= 3 ? ll : null)
        setCropEnabled(true)
        setCropMode('off')
        await modal.alert('KML crop saved', 'KML crop shape saved to database.')
      } catch (e) {
        await modal.alert('KML import failed', e instanceof Error ? e.message : 'KML import failed.')
      } finally {
        setCropBusy(false)
      }
    },
    [cogTileUrl, cropBusy, modal, projectId],
  )

  const onIssueSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault()
      if (!issueDraft || issueSubmitting) return
      setIssueSubmitting(true)
      setIssueError(null)
      try {
        await createIssue({
          lat: issueDraft.lat,
          lng: issueDraft.lng,
          title: issueDraft.title,
          description: issueDraft.description,
        })
        setIssuesRefreshTick((prev) => prev + 1)
        clearIssueMode()
      } catch (error) {
        setIssueError(
          error instanceof Error ? error.message : 'Failed to report issue',
        )
      } finally {
        setIssueSubmitting(false)
      }
    },
    [clearIssueMode, issueDraft, issueSubmitting],
  )

  const measureActive = measureMode !== 'none' && !splitView && !issueMode && !elevationMode

  const mapProps = {
    center,
    zoom,
    scrollWheelZoom: true,
    preferCanvas: true,
    fadeAnimation: false,
    markerZoomAnimation: false,
    wheelDebounceTime: 40,
    className: 'mv-leaflet',
  } as const

  const selectPrimaryLayer = useCallback(
    (url: string | null) => {
      setCogTileUrl(url)
      setCogBounds(null)
      setCropMaskPoints(null)
      setCropEnabled(false)
      setZoomTrigger((prev) => prev + 1)
      clearAnalysisResults()
    },
    [clearAnalysisResults],
  )

  const updateCompareSliderFromClientX = useCallback((clientX: number) => {
    const rect = mapCanvasRef.current?.getBoundingClientRect()
    if (!rect || rect.width <= 0) return
    const next = ((clientX - rect.left) / rect.width) * 100
    setCompareSliderPercent(Math.max(5, Math.min(95, next)))
  }, [])

  const onCompareSliderPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      event.preventDefault()
      updateCompareSliderFromClientX(event.clientX)
      const onMove = (moveEvent: PointerEvent) => {
        updateCompareSliderFromClientX(moveEvent.clientX)
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [updateCompareSliderFromClientX],
  )

  useEffect(() => {
    if (!projectId) {
      setCogTileUrl(null)
      setCompareCogTileUrl(null)
      setCogBounds(null)
      setCompareCogBounds(null)
      return
    }
    if (cogTileUrl && !projectCogLayers.some((layer) => layer.url === cogTileUrl)) {
      const currentSource = rasterSourceKeyFromTileUrl(cogTileUrl)
      const replacement =
        projectCogLayers.find((layer) => rasterSourceKeyFromTileUrl(layer.url) === currentSource) ??
        projectCogLayers[0]
      setCogTileUrl(replacement?.url ?? null)
    }
    if (!cogTileUrl && projectCogLayers[0]?.url) {
      setCogTileUrl(projectCogLayers[0].url)
    }
    if (compareCogTileUrl && !projectCogLayers.some((layer) => layer.url === compareCogTileUrl)) {
      const currentSource = rasterSourceKeyFromTileUrl(compareCogTileUrl)
      const replacement = projectCogLayers.find((layer) => rasterSourceKeyFromTileUrl(layer.url) === currentSource)
      setCompareCogTileUrl(replacement?.url ?? null)
    }
    if (!compareCogTileUrl) {
      const compareLayer =
        projectCogLayers.find((layer) => layer.url !== (cogTileUrl || projectCogLayers[0]?.url) && (layer.datasetType === 'dtm' || layer.datasetType === 'dsm')) ??
        projectCogLayers.find((layer) => layer.url !== (cogTileUrl || projectCogLayers[0]?.url))
      if (compareLayer?.url) setCompareCogTileUrl(compareLayer.url)
    }
    if (projectCogLayers.length === 0) {
      setCogTileUrl(null)
      setCompareCogTileUrl(null)
      setCogBounds(null)
      setCompareCogBounds(null)
    }
  }, [cogTileUrl, compareCogTileUrl, projectCogLayers, projectId])

  useEffect(() => {
    if (!projectId || !cogTileUrl) {
      setCogBounds(null)
      return
    }
    setCogBounds(null)
    const currentSource = rasterSourceKeyFromTileUrl(cogTileUrl)
    const layer =
      projectCogLayers.find((item) => item.url === cogTileUrl) ??
      projectCogLayers.find((item) => currentSource && rasterSourceKeyFromTileUrl(item.url) === currentSource) ??
      null
    if (layer?.boundsWgs84) {
      const [minX, minY, maxX, maxY] = layer.boundsWgs84
      setCogBounds([[minY, minX], [maxY, maxX]])
      return
    }
    const sourcePath = layer?.cogPath || currentSource
    if (sourcePath) {
      let cancelled = false
      void fetch(`${API_BASE}/api/ortho-cog/bounds?url=${encodeURIComponent(sourcePath)}`, { credentials: 'include' })
        .then((res) => res.ok ? res.json() as Promise<{ bounds?: [number, number, number, number] | null }> : null)
        .then((data) => {
          if (cancelled || !data?.bounds) return
          const [minX, minY, maxX, maxY] = data.bounds
          setCogBounds([[minY, minX], [maxY, maxX]])
        })
        .catch(() => {
          if (!cancelled) setCogBounds(null)
        })
      return () => {
        cancelled = true
      }
    }
    const datasetName = layer?.name?.replace(/\.tiff?$/i, '')
    if (!datasetName) {
      setCogBounds(null)
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const boundsUrl = `${API_BASE}/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetName)}/bounds`
        const res = await fetch(boundsUrl, { credentials: 'include' })
        if (!res.ok) return
        const data = (await res.json()) as { bounds?: [number, number, number, number] | null }
        if (cancelled || !data?.bounds) return
        const [minX, minY, maxX, maxY] = data.bounds
        setCogBounds([[minY, minX], [maxY, maxX]])
      } catch {
        if (!cancelled) setCogBounds(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [cogTileUrl, projectCogLayers, projectId])

  useEffect(() => {
    if (!projectId || !compareCogTileUrl) {
      setCompareCogBounds(null)
      return
    }
    setCompareCogBounds(null)
    const currentSource = rasterSourceKeyFromTileUrl(compareCogTileUrl)
    const layer =
      projectCogLayers.find((item) => item.url === compareCogTileUrl) ??
      projectCogLayers.find((item) => currentSource && rasterSourceKeyFromTileUrl(item.url) === currentSource) ??
      null
    if (layer?.boundsWgs84) {
      const [minX, minY, maxX, maxY] = layer.boundsWgs84
      setCompareCogBounds([[minY, minX], [maxY, maxX]])
      return
    }
    const sourcePath = layer?.cogPath || currentSource
    if (sourcePath) {
      let cancelled = false
      void fetch(`${API_BASE}/api/ortho-cog/bounds?url=${encodeURIComponent(sourcePath)}`, { credentials: 'include' })
        .then((res) => res.ok ? res.json() as Promise<{ bounds?: [number, number, number, number] | null }> : null)
        .then((data) => {
          if (cancelled || !data?.bounds) return
          const [minX, minY, maxX, maxY] = data.bounds
          setCompareCogBounds([[minY, minX], [maxY, maxX]])
        })
        .catch(() => {
          if (!cancelled) setCompareCogBounds(null)
        })
      return () => {
        cancelled = true
      }
    }
    const datasetName = layer?.name?.replace(/\.tiff?$/i, '')
    if (!datasetName) {
      setCompareCogBounds(null)
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const boundsUrl = `${API_BASE}/api/datasets/${encodeURIComponent(projectId)}/${encodeURIComponent(datasetName)}/bounds`
        const res = await fetch(boundsUrl, { credentials: 'include' })
        if (!res.ok) return
        const data = (await res.json()) as { bounds?: [number, number, number, number] | null }
        if (cancelled || !data?.bounds) return
        const [minX, minY, maxX, maxY] = data.bounds
        setCompareCogBounds([[minY, minX], [maxY, maxX]])
      } catch {
        if (!cancelled) setCompareCogBounds(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [compareCogTileUrl, projectCogLayers, projectId])

  useEffect(() => {
    if (!projectId || !cogTileUrl) {
      setCropMaskPoints(null)
      return
    }
    setCropMaskPoints(null)
    const tileFolder = tileFolderFromTemplate(cogTileUrl)
    if (!tileFolder) {
      setCropMaskPoints(null)
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const res = await getDatasetCropMask(projectId, tileFolder)
        if (cancelled || !res.points?.length) {
          if (!cancelled) setCropMaskPoints(null)
          return
        }
        const ll = res.points
          .map((p) => L.latLng(Number(p[0]), Number(p[1])))
          .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng))
        setCropMaskPoints(ll.length >= 3 ? ll : null)
      } catch {
        if (!cancelled) setCropMaskPoints(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [cogTileUrl, projectId])

  const activeCogLayers = projectCogLayers
  const digitizationToolActive = digitization.mode !== 'idle'

  return (
    <div className="mv-root">
      <div className="mv-chrome">
        <div className="mv-panel mv-panel--layers">
          <p className="mv-panel__title">Data Highlights</p>
          <fieldset className="mv-fieldset">
            <legend className="mv-legend">Base Map</legend>
            <select
              className="mv-select mv-select--compact"
              value={baseMapKey}
              onChange={(event) => setBaseMapKey(event.target.value as BaseMapKey)}
              aria-label="Base map"
            >
              {BASE_MAPS.map((map) => (
                <option key={map.key} value={map.key}>
                  {map.label}
                </option>
              ))}
            </select>
          </fieldset>
          <fieldset className="mv-fieldset">
            <legend className="mv-legend">Project 2D Layers</legend>
            {activeCogLayers.length > 0 ? (
              activeCogLayers.map((layer) => (
                <button
                  key={layer.id}
                  type="button"
                  className={layer.url === cogTileUrl ? 'mv-tool mv-tool--active' : 'mv-tool'}
                  onClick={() => selectPrimaryLayer(layer.url)}
                  title={`${layer.datasetType?.toUpperCase() || 'LAYER'}${layer.month ? ` · ${layer.month}` : ''}`}
                >
                  <span className="mv-layer-type">{layer.datasetType || 'layer'}</span>
                  {layer.name}
                </button>
              ))
            ) : projectLayersLoading ? (
              <p className="mv-hud__hint">Loading project layers...</p>
            ) : (
              <p className="mv-hud__hint">No Web-Ready Ortho/DTM/DSM layers found in this project.</p>
            )}
          </fieldset>
        </div>

        <div className="mv-toolbar" role="toolbar" aria-label="Map tools">
          <button
            type="button"
            className={
              measureMode === 'distance' && !splitView
                ? 'mv-tool mv-tool--active'
                : 'mv-tool'
            }
            disabled={splitView || digitizationToolActive}
            onClick={() => setTool('distance')}
            title="Click the map to add vertices; total length updates live."
          >
            <i className="fa-solid fa-ruler" aria-hidden />
            Distance
          </button>
          <button
            type="button"
            className={
              measureMode === 'area' && !splitView
                ? 'mv-tool mv-tool--active'
                : 'mv-tool'
            }
            disabled={splitView || digitizationToolActive}
            onClick={() => setTool('area')}
            title="Click vertices; double-click to finish polygon."
          >
            <i className="fa-solid fa-draw-polygon" aria-hidden />
            Area
          </button>
          <button
            type="button"
            className={elevationMode && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView || digitizationToolActive || !activeAnalysisLayer}
            onClick={toggleElevationMode}
            title="Click DTM/DSM to read elevation."
          >
            <i className="fa-solid fa-mountain" aria-hidden />
            Elevation
          </button>
          <button
            type="button"
            className={measureMode === 'profile' && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView || digitizationToolActive || !activeAnalysisLayer}
            onClick={() => setTool('profile')}
            title="Draw a line on DTM/DSM to generate elevation profile."
          >
            <i className="fa-solid fa-chart-line" aria-hidden />
            Profile
          </button>
          <button
            type="button"
            className="mv-tool"
            disabled={splitView || digitizationToolActive || !activeAnalysisLayer || analysisBusy}
            onClick={() => void runVolume('overall')}
            title="Calculate total loaded DTM/DSM volume above minimum elevation."
          >
            <i className="fa-solid fa-cubes-stacked" aria-hidden />
            Overall Volume
          </button>
          <button
            type="button"
            className={measureMode === 'volume-area' && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView || digitizationToolActive || !activeAnalysisLayer}
            onClick={() => setTool('volume-area')}
            title="Draw polygon area for DTM/DSM volume."
          >
            <i className="fa-solid fa-vector-square" aria-hidden />
            Area Volume
          </button>
          <button
            type="button"
            className={measureMode === 'volume-circle' && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView || digitizationToolActive || !activeAnalysisLayer}
            onClick={() => setTool('volume-circle')}
            title="Click center and edge to calculate circular DTM/DSM volume."
          >
            <i className="fa-regular fa-circle" aria-hidden />
            Circle Volume
          </button>
          {analysisLayers.length > 0 ? (
            <select
              className="mv-select"
              value={analysisDatasetId}
              onChange={(e) => setAnalysisDatasetId(e.target.value)}
              aria-label="DTM or DSM analysis layer"
            >
              {analysisLayers.map((layer) => (
                <option key={layer.id} value={layer.datasetId}>
                  {layer.name}
                </option>
              ))}
            </select>
          ) : null}
          <button
            type="button"
            className={issueMode && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView || digitizationToolActive}
            onClick={toggleIssueMode}
            title="Click map to place an issue marker and submit details."
          >
            <i className="fa-solid fa-location-dot" aria-hidden />
            Report Issue
          </button>
          <DigitizationToolbar
            mode={digitization.mode}
            disabled={splitView}
            busy={spatialBusy}
            canFinishDraft={digitization.canFinishDraft}
            importInputRef={spatialImportInputRef}
            onModeChange={handleDigitizationModeChange}
            onFinishDraft={digitization.finishDraft}
            onClearDraft={digitization.clearDraft}
            onImportFile={(file) => void handleSpatialImport(file)}
          />
          <button
            type="button"
            className="mv-tool"
            disabled={splitView}
            onClick={() => void findMyLocation()}
            title="Ask permission and zoom to your current location."
          >
            <i className="fa-solid fa-crosshairs" aria-hidden />
            Find My Location
          </button>
          <button
            type="button"
            className={splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            onClick={() => {
              setSplitView((v) => !v)
              setMeasureMode('none')
              setPoints([])
              setIssueMode(false)
              setIssueDraft(null)
              setIssueError(null)
            }}
            title="Swipe comparison between selected project layers."
          >
            <i className="fa-solid fa-code-compare" aria-hidden />
            Compare Slider
          </button>
          <button
            type="button"
            className={cropEnabled ? 'mv-tool mv-tool--active' : 'mv-tool'}
            onClick={() => setCropEnabled((v) => !v)}
            disabled={!cogTileUrl || !cropMaskPoints}
            title="Saved crop mask apply/remove karein."
          >
            <i className="fa-solid fa-crop-simple" aria-hidden />
            {cropEnabled ? 'Crop ON' : 'Apply Crop'}
          </button>
          <select
            className="mv-select"
            value={cropMode}
            onChange={(e) => {
              const mode = e.target.value as 'off' | 'kml' | 'draw'
              setCropMode(mode)
              if (mode === 'draw') {
                setTool('area')
              }
            }}
            aria-label="Crop source mode"
          >
            <option value="off">Crop Source: Off</option>
            <option value="kml">KML Border Import</option>
            <option value="draw">Draw Border</option>
          </select>
          {cropMode === 'kml' ? (
            <>
              <input
                ref={kmlInputRef}
                type="file"
                accept=".kml,.xml"
                style={{ display: 'none' }}
                onChange={(event) => {
                  const f = event.target.files?.[0]
                  if (f) void importKmlCrop(f)
                  event.currentTarget.value = ''
                }}
              />
              <button
                type="button"
                className="mv-tool"
                disabled={!cogTileUrl || cropBusy}
                onClick={() => kmlInputRef.current?.click()}
              >
                {cropBusy ? 'Saving KML...' : 'Import KML'}
              </button>
            </>
          ) : null}
          {cropMode === 'draw' ? (
            <button
              type="button"
              className="mv-tool"
              disabled={!cogTileUrl || points.length < 3 || cropBusy}
              onClick={() => void saveDrawCrop()}
            >
              {cropBusy ? 'Saving Shape...' : 'Save Drawn Shape'}
            </button>
          ) : null}
          <div className="mv-toolbar__context" aria-live="polite">
            {measureMode === 'profile' ? (
              <button
                type="button"
                className="mv-tool"
                disabled={!activeAnalysisLayer || points.length < 2 || analysisBusy}
                onClick={() => void runProfile()}
              >
                {analysisBusy ? 'Generating...' : 'Generate Profile'}
              </button>
            ) : measureMode === 'volume-area' ? (
              <button
                type="button"
                className="mv-tool"
                disabled={!activeAnalysisLayer || points.length < 3 || analysisBusy}
                onClick={() => void runVolume('area')}
              >
                {analysisBusy ? 'Calculating...' : 'Calculate Area Volume'}
              </button>
            ) : measureMode === 'volume-circle' ? (
              <button
                type="button"
                className="mv-tool"
                disabled={!activeAnalysisLayer || points.length < 2 || analysisBusy}
                onClick={() => void runVolume('circle')}
              >
                {analysisBusy ? 'Calculating...' : 'Calculate Circle Volume'}
              </button>
            ) : (
              <span className="mv-toolbar__placeholder" aria-hidden />
            )}
            <button
              type="button"
              className="mv-tool mv-tool--ghost"
              onClick={clearMeasure}
              disabled={measureMode === 'none' || splitView}
            >
              Clear drawing
            </button>
            <div className="mv-export-actions" aria-label="Export drawings">
              <button
                type="button"
                className="mv-tool mv-tool--ghost"
                onClick={() => void exportCurrentDrawingKml()}
                disabled={points.length === 0 || splitView}
              >
                <i className="fa-solid fa-file-export" aria-hidden />
                Export KML
              </button>
              <button
                type="button"
                className="mv-tool mv-tool--ghost"
                onClick={() => void exportCurrentDrawingCsv()}
                disabled={points.length === 0 || splitView}
              >
                <i className="fa-solid fa-table" aria-hidden />
                Export CSV
              </button>
            </div>
          </div>
        </div>
      </div>

      {measureMode !== 'none' && !splitView ? (
        <div className="mv-hud" aria-live="polite">
          {measureMode === 'distance' || measureMode === 'profile' ? (
            <span>
              {measureMode === 'profile' ? 'Profile length' : 'Distance'}: <strong>{formatLengthM(distanceM)}</strong>
            </span>
          ) : measureMode === 'volume-circle' ? (
            <span>
              Circle radius: <strong>{formatLengthM(circleRadiusM)}</strong>
              <span className="mv-hud__hint"> · Click center then edge</span>
            </span>
          ) : (
            <span>
              Area: <strong>{formatAreaM2(areaM2)}</strong>
              {!areaFrozen ? (
                <span className="mv-hud__hint"> · Double-click to finish</span>
              ) : (
                <span className="mv-hud__hint"> · Finished — Clear to reset</span>
              )}
            </span>
          )}
        </div>
      ) : null}
      {elevationResult || analysisError || profileResult || volumeResult ? (
        <div className="mv-analysis-card">
          <button
            type="button"
            className="mv-analysis-card__close"
            onClick={clearAnalysisResults}
            aria-label="Close analysis results"
            title="Close"
          >
            <i className="fa-solid fa-xmark" aria-hidden />
          </button>
          {analysisError ? <p className="mv-analysis-card__error">{analysisError}</p> : null}
          {elevationResult ? (
            <p>
              Elevation: <strong>{elevationResult.elevation.toFixed(3)} {elevationResult.unit}</strong>
              <span> Lat {elevationResult.lat.toFixed(6)}, Lng {elevationResult.lng.toFixed(6)}</span>
            </p>
          ) : null}
          {profileResult ? (
            <>
              <div className="mv-profile-summary">
                <article>
                  <span>Length</span>
                  <strong>{formatLengthM(profileResult.length_m ?? distanceM)}</strong>
                </article>
                <article>
                  <span>Min / Max</span>
                  <strong>
                    {formatProfileValue(profileResult.min_elevation)} / {formatProfileValue(profileResult.max_elevation)}
                  </strong>
                </article>
                <article>
                  <span>Average</span>
                  <strong>{formatProfileValue(profileResult.avg_elevation)}</strong>
                </article>
                <article>
                  <span>Change</span>
                  <strong>{formatProfileValue(profileResult.elevation_change)}</strong>
                </article>
                <article>
                  <span>Gain / Loss</span>
                  <strong>
                    {formatProfileValue(profileResult.elevation_gain)} / {formatProfileValue(profileResult.elevation_loss)}
                  </strong>
                </article>
                <article>
                  <span>Volume est.</span>
                  <strong>{formatProfileValue(profileResult.volume_above_min_m3, 'm3')}</strong>
                </article>
              </div>
              <ProfileChart result={profileResult} svgRef={profileChartRef} />
              <div className="mv-analysis-card__actions">
                <button type="button" className="mv-tool" onClick={exportProfileCsv}>Export CSV</button>
                <button type="button" className="mv-tool" onClick={exportProfilePng}>Export PNG</button>
              </div>
            </>
          ) : null}
          {volumeResult ? (
            <>
              <div className="mv-profile-summary">
                <article><span>Volume</span><strong>{formatProfileValue(volumeResult.fill_volume_m3, 'm3')}</strong></article>
                <article><span>Area</span><strong>{formatAreaM2(volumeResult.area_m2)}</strong></article>
                <article><span>Base</span><strong>{formatProfileValue(volumeResult.base_elevation)}</strong></article>
                <article><span>Min / Max</span><strong>{formatProfileValue(volumeResult.min_elevation)} / {formatProfileValue(volumeResult.max_elevation)}</strong></article>
                <article><span>Average</span><strong>{formatProfileValue(volumeResult.avg_elevation)}</strong></article>
                <article><span>Cells</span><strong>{volumeResult.cell_count}</strong></article>
              </div>
              {volumeResult.bins.length > 0 ? (
                <div className="mv-volume-chart" aria-label="DTM volume graph">
                  {volumeResult.bins.map((bin) => {
                    const max = Math.max(...volumeResult.bins.map((b) => b.volume), 1)
                    return (
                      <div key={bin.label} className="mv-volume-bar">
                        <span style={{ height: Math.max(6, (bin.volume / max) * 92) }} title={`${bin.label}: ${bin.volume.toFixed(2)} m3`} />
                        <small>{bin.label}</small>
                      </div>
                    )
                  })}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      <div className={splitView ? 'mv-maps mv-maps--compare' : 'mv-maps'}>
        <div className="mv-map-wrap">
          {splitView ? (
            <div className="mv-compare-head mv-compare-head--swipe">
              <label className="mv-compare-control">
                <span>Primary</span>
                <select
                  className="mv-select"
                  value={cogTileUrl ?? ''}
                  onChange={(e) => selectPrimaryLayer(e.target.value || null)}
                  aria-label="Primary layer"
                >
                  <option value="">No overlay selected</option>
                  {activeCogLayers.map((layer) => (
                    <option key={layer.id} value={layer.url}>
                      {layer.datasetType ? `${layer.datasetType.toUpperCase()} - ` : ''}{layer.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mv-compare-control">
                <span>Compare</span>
                <select
                  className="mv-select"
                  value={compareCogTileUrl ?? ''}
                  onChange={(e) => setCompareCogTileUrl(e.target.value || null)}
                  aria-label="Comparison layer"
                >
                  <option value="">No overlay selected</option>
                  {activeCogLayers.map((layer) => (
                    <option key={layer.id} value={layer.url}>
                      {layer.datasetType ? `${layer.datasetType.toUpperCase()} - ` : ''}{layer.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          <div className="mv-map-canvas" ref={mapCanvasRef}>
            <MapContainer {...mapProps} style={{ height: '100%', width: '100%' }}>
              <MapController
                layers={activeCogLayers}
                projectId={projectId}
                selectedUrl={cogTileUrl}
                zoomTrigger={zoomTrigger}
                disabled={splitView}
              />
              <ActiveLayerBoundsController
                bounds={splitView ? comparisonFitBounds : cogBounds}
                triggerKey={splitView ? comparisonFitKey : `${cogTileUrl || 'none'}-${zoomTrigger}`}
              />
              <MapPane
                measureMode={measureMode}
                measureActive={measureActive && !digitizationToolActive}
                measurePoints={points}
                circleRadiusM={circleRadiusM}
                areaFrozen={areaFrozen}
                onMeasureAdd={onMeasureAdd}
                onMeasureCloseRing={onMeasureCloseRing}
                issueMode={issueMode && !splitView}
                onIssuePick={onIssuePick}
                elevationMode={elevationMode && !splitView}
                onElevationPick={onElevationPick}
                issues={issues}
                cropEnabled={cropEnabled && !splitView}
                cropFootprint={splitView ? null : cropMaskPoints}
                cogBounds={cogBounds}
                cogTileUrl={cogTileUrl}
                comparisonPrimaryClipPercent={splitView ? compareSliderPercent : null}
                baseMap={selectedBaseMap}
                userLocation={userLocation}
                spatialLayers={spatialLayers}
                selectedSpatialFeatureId={selectedSpatialFeature?.id ?? null}
                digitizationMode={digitization.mode}
                digitizationActive={!splitView && (digitization.mode === 'polygon' || digitization.mode === 'polyline' || digitization.mode === 'marker')}
                draftPoints={digitization.draftPoints}
                onDigitizationAddPoint={digitization.addDraftPoint}
                onDigitizationFinishDraft={digitization.finishDraft}
                onSpatialFeatureClick={handleSpatialFeatureClick}
                onSpatialGeometryChange={(feature, geojson) => void handleSpatialGeometryChange(feature, geojson)}
              />
              {splitView && compareCogTileUrl ? (
                <ComparisonSwipeLayer
                  tileUrl={compareCogTileUrl}
                  percent={compareSliderPercent}
                />
              ) : null}
            </MapContainer>
            {splitView ? (
              <div className="mv-swipe-overlay" aria-hidden={false}>
                <span className="mv-swipe-label mv-swipe-label--primary">Primary</span>
                <span className="mv-swipe-label mv-swipe-label--compare">Compare</span>
                <button
                  type="button"
                  className="mv-swipe-handle"
                  style={{ left: `${compareSliderPercent}%` }}
                  onPointerDown={onCompareSliderPointerDown}
                  aria-label="Drag to compare selected layers"
                >
                  <span aria-hidden />
                </button>
                <input
                  className="mv-swipe-range"
                  type="range"
                  min="5"
                  max="95"
                  value={Math.round(compareSliderPercent)}
                  onChange={(event) => setCompareSliderPercent(Number(event.target.value))}
                  aria-label="Comparison reveal percentage"
                />
              </div>
            ) : null}
            {digitization.isDrawing && !splitView ? (
              <div className="digitization-hint" aria-live="polite">
                {digitization.mode === 'marker'
                  ? 'Click map to place marker.'
                  : 'Click map to add vertices. Double-click or Finish to save.'}
              </div>
            ) : null}
            {issueMode && !splitView ? (
              <div className="mv-issue-hint" aria-live="polite">
                Click on the map to place an issue pin.
              </div>
            ) : null}
            <SpatialAssignmentModal
              feature={digitization.mode === 'edit' ? null : selectedSpatialFeature}
              saving={spatialBusy}
              onClose={() => setSelectedSpatialFeature(null)}
              onSave={(payload) => void handleSpatialAssignmentSave(payload)}
              onDelete={(feature) => void handleSpatialFeatureDelete(feature)}
            />
            {issueDraft ? (
              <div className="mv-issue-modal" role="dialog" aria-modal="true">
                <form className="mv-issue-form" onSubmit={onIssueSubmit}>
                  <p className="mv-issue-form__title">Report issue</p>
                  <p className="mv-issue-form__coords">
                    Lat {issueDraft.lat.toFixed(6)}, Lng {issueDraft.lng.toFixed(6)}
                  </p>
                  <label className="mv-issue-form__label" htmlFor="mv-issue-title">
                    Issue Title
                  </label>
                  <input
                    id="mv-issue-title"
                    className="mv-issue-form__input"
                    type="text"
                    value={issueDraft.title}
                    onChange={(event) =>
                      setIssueDraft((prev) =>
                        prev
                          ? {
                              ...prev,
                              title: event.target.value,
                            }
                          : prev,
                      )
                    }
                    required
                  />
                  <label
                    className="mv-issue-form__label"
                    htmlFor="mv-issue-description"
                  >
                    Description
                  </label>
                  <textarea
                    id="mv-issue-description"
                    className="mv-issue-form__input mv-issue-form__input--textarea"
                    value={issueDraft.description}
                    onChange={(event) =>
                      setIssueDraft((prev) =>
                        prev
                          ? {
                              ...prev,
                              description: event.target.value,
                            }
                          : prev,
                      )
                    }
                    required
                  />
                  {issueError ? <p className="mv-issue-form__error">{issueError}</p> : null}
                  <div className="mv-issue-form__actions">
                    <button
                      type="button"
                      className="mv-tool mv-tool--ghost"
                      onClick={clearIssueMode}
                      disabled={issueSubmitting}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="mv-tool mv-tool--active"
                      disabled={issueSubmitting}
                    >
                      {issueSubmitting ? 'Submitting...' : 'Submit issue'}
                    </button>
                  </div>
                </form>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

export default MapViewer
