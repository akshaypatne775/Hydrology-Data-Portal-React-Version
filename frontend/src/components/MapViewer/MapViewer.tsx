import 'leaflet/dist/leaflet.css'
import './MapViewer.css'

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
} from 'react'
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polygon,
  Popup,
  Polyline,
  TileLayer,
  useMap,
  useMapEvents,
} from 'react-leaflet'
import { createIssue, listIssues, type SavedIssue } from '../../services/issuesService'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { API_BASE } from '../../lib/apiBase'
import { getLatestCogLayer } from '../../utils/datasetLayerStorage'
import HydrologyDataLayer, {
  type HydrologyPoint,
  type HydrologyZone,
} from './HydrologyDataLayer'

import {
  type FloodReturnPeriod,
  getDefaultMapCenter,
  getDefaultZoom,
  getFloodOverlayTileUrlWithBust,
  hasCustomTileBase,
  OSM_FALLBACK_URL,
} from './tileSources'

export type { FloodReturnPeriod } from './tileSources'

type MeasureMode = 'none' | 'distance' | 'area'
type IssueDraft = {
  lat: number
  lng: number
  title: string
  description: string
}

const FLOOD_LABELS: Record<FloodReturnPeriod, string> = {
  '1in25': '1 : 25 years',
  '1in50': '1 : 50 years',
  '1in100': '1 : 100 years',
}

const HYDROLOGY_POINTS: HydrologyPoint[] = []
const HYDROLOGY_ZONES: HydrologyZone[] = []

function formatLengthM(meters: number): string {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`
  return `${meters.toFixed(1)} m`
}

function formatAreaM2(m2: number): string {
  if (m2 >= 1_000_000) return `${(m2 / 1_000_000).toFixed(2)} km²`
  if (m2 >= 10_000) return `${(m2 / 10_000).toFixed(2)} ha`
  return `${m2.toFixed(0)} m²`
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

function MapSyncBridge({ isA, lockRef, mapARef, mapBRef }: SyncRefs & { isA: boolean }) {
  const map = useMap()
  const selfRef = isA ? mapARef : mapBRef
  const peerRef = isA ? mapBRef : mapARef

  useEffect(() => {
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
    if (mode === 'area' && !areaFrozen) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    return () => {
      map.doubleClickZoom.enable()
    }
  }, [map, mode, areaFrozen])

  useMapEvents({
    click(e) {
      if (!enabled || mode === 'none') return
      if (mode === 'area' && areaFrozen) return
      if (mode === 'distance' || mode === 'area') onAddPoint(e.latlng)
    },
    dblclick(e) {
      if (!enabled || mode !== 'area' || areaFrozen) return
      e.originalEvent.preventDefault()
      if (points.length >= 3) onCloseRing()
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

function isStaticXyzTileTemplate(url: string): boolean {
  return (
    (url.includes('/tiles/') || url.includes('/data/')) &&
    url.includes('{z}/{x}/{y}.png') &&
    !url.includes('/api/cog/')
  )
}

function tileTemplateToStaticBase(url: string): string | null {
  const suffix = '{z}/{x}/{y}.png'
  if (!url.endsWith(suffix)) return null
  return url.slice(0, -suffix.length)
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
  activeLayers,
  projectId,
}: {
  activeLayers: ReturnType<typeof useWorkspaceContext>['activeLayers']
  projectId?: string
}) {
  const map = useMap()
  const lastFitKeyRef = useRef<string | null>(null)

  useEffect(() => {
    if (!projectId) return
    const activeCog = activeLayers.find(
      (layer) => layer.projectId === projectId && layer.layerType === 'cog',
    )
    if (!activeCog) return

    const rawPath = activeCog.rawPath ?? null
    const tileUrl = activeCog.url
    const fitKey = rawPath ?? (isStaticXyzTileTemplate(tileUrl) ? tileUrl : null)
    if (!fitKey) return

    const dedupeKey = `${projectId}:${fitKey}`
    if (dedupeKey === lastFitKeyRef.current) return
    lastFitKeyRef.current = dedupeKey

    if (rawPath) {
      void fetch(`${API_BASE}/api/cog/info?url=${encodeURIComponent(rawPath)}`, {
        credentials: 'include',
      })
        .then((res) => res.json() as Promise<{ bounds?: [number, number, number, number] }>)
        .then((data) => {
          if (data?.bounds) {
            const [minX, minY, maxX, maxY] = data.bounds
            map.fitBounds(
              [
                [minY, minX],
                [maxY, maxX],
              ],
              { padding: [24, 24] },
            )
          }
        })
        .catch(() => {
          // Ignore auto-zoom failure and keep the map usable.
        })
      return
    }

    const base = tileTemplateToStaticBase(tileUrl)
    if (!base) return

    void (async () => {
      try {
        const kmlRes = await fetch(`${base}doc.kml`, { credentials: 'include' })
        if (kmlRes.ok) {
          const b = parseKmlLatLonBounds(await kmlRes.text())
          if (b) {
            map.fitBounds(b, { padding: [24, 24] })
            return
          }
        }
        const tmrRes = await fetch(`${base}tilemapresource.xml`, { credentials: 'include' })
        if (tmrRes.ok) {
          const b = parseTileMapResourceBounds(await tmrRes.text())
          if (b) map.fitBounds(b, { padding: [24, 24] })
        }
      } catch {
        // Ignore auto-zoom failure and keep the map usable.
      }
    })()
  }, [activeLayers, map, projectId])

  return null
}

interface MapPaneProps {
  floodEnabled: boolean
  floodPeriod: FloodReturnPeriod
  /** 0–100: boosts flood tile opacity + drives parent veil (placeholder hydraulics). */
  floodSimulationLevel: number
  measureMode: MeasureMode
  measureActive: boolean
  measurePoints: LatLng[]
  areaFrozen: boolean
  onMeasureAdd: (ll: LatLng) => void
  onMeasureCloseRing: () => void
  issueMode: boolean
  onIssuePick: (ll: LatLng) => void
  issues: SavedIssue[]
  showHydrologyData: boolean
  cogTileUrl: string | null
  sync?: SyncRefs & { isA: boolean }
}

function floodTileOpacity(
  floodEnabled: boolean,
  floodUrl: string | null,
  simulationLevel: number,
): number {
  if (!floodEnabled || !floodUrl) return 0
  return Math.min(1, Math.max(0, simulationLevel / 100))
}

function MapPane({
  floodEnabled,
  floodPeriod,
  floodSimulationLevel,
  measureMode,
  measureActive,
  measurePoints,
  areaFrozen,
  onMeasureAdd,
  onMeasureCloseRing,
  issueMode,
  onIssuePick,
  issues,
  showHydrologyData,
  cogTileUrl,
  sync,
}: MapPaneProps) {
  const baseUrl = useMemo(() => OSM_FALLBACK_URL, [])
  const floodUrl = useMemo(
    () =>
      floodEnabled || floodSimulationLevel > 0
        ? getFloodOverlayTileUrlWithBust(floodPeriod)
        : null,
    [floodEnabled, floodPeriod, floodSimulationLevel],
  )
  const floodOpacity = useMemo(
    () => floodTileOpacity(floodEnabled, floodUrl, floodSimulationLevel),
    [floodEnabled, floodUrl, floodSimulationLevel],
  )

  const showFloodLayer = Boolean(floodUrl) && floodSimulationLevel > 0

  return (
    <>
      {/*
        URL includes ?v=… cache-bust on custom bases (tileSources.withTileCacheBust).
        Bump VITE_TILE_CACHE_BUST after regenerating local tiles; Leaflet fetches XYZ per zoom/pan.
      */}
      <TileLayer
        key={baseUrl}
        attribution={
          hasCustomTileBase()
            ? '&copy; Project ortho / elevation tiles'
            : '&copy; OpenStreetMap'
        }
        url={baseUrl}
        maxZoom={22}
        maxNativeZoom={20}
      />
      {cogTileUrl ? (
        <TileLayer
          key={`cog-${cogTileUrl}`}
          url={cogTileUrl}
          opacity={0.82}
          maxZoom={22}
          maxNativeZoom={22}
        />
      ) : null}
      {showFloodLayer ? (
        <TileLayer
          key={`flood-${floodPeriod}-${floodUrl}`}
          url={floodUrl!}
          opacity={floodOpacity}
          maxZoom={22}
          maxNativeZoom={20}
        />
      ) : null}
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
          {measureMode === 'distance' && measurePoints.length > 0 ? (
            <Polyline
              positions={measurePoints}
              pathOptions={{ color: '#0e3e49', weight: 3, dashArray: '6 4' }}
            />
          ) : null}
          {measureMode === 'area' && measurePoints.length > 0 ? (
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
      {showHydrologyData ? (
        <HydrologyDataLayer points={HYDROLOGY_POINTS} zones={HYDROLOGY_ZONES} />
      ) : null}
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

export type MapViewerProps = {
  /** 0–100 from HydrologyStats flood slider; placeholder for inundation visual. */
  floodSimulationLevel?: number
  projectId?: string
}

export function MapViewer({ floodSimulationLevel = 0, projectId }: MapViewerProps) {
  const { activeLayers } = useWorkspaceContext()
  const center = useMemo(() => getDefaultMapCenter(), [])
  const zoom = useMemo(() => getDefaultZoom(), [])
  const customTilesReady = hasCustomTileBase()

  const [floodOn, setFloodOn] = useState(false)
  const [floodPeriod, setFloodPeriod] = useState<FloodReturnPeriod>('1in25')
  const [splitView, setSplitView] = useState(false)
  const [measureMode, setMeasureMode] = useState<MeasureMode>('none')
  const [points, setPoints] = useState<LatLng[]>([])
  const [areaFrozen, setAreaFrozen] = useState(false)
  const [issueMode, setIssueMode] = useState(false)
  const [showHydrologyData, setShowHydrologyData] = useState(true)
  const [cogTileUrl, setCogTileUrl] = useState<string | null>(null)
  const [issueDraft, setIssueDraft] = useState<IssueDraft | null>(null)
  const [issueSubmitting, setIssueSubmitting] = useState(false)
  const [issueError, setIssueError] = useState<string | null>(null)
  const [issues, setIssues] = useState<SavedIssue[]>([])
  const [issuesRefreshTick, setIssuesRefreshTick] = useState(0)

  const syncLockRef = useRef(false)
  const mapARef = useRef<L.Map | null>(null)
  const mapBRef = useRef<L.Map | null>(null)
  const syncRefs = useMemo(
    () => ({
      lockRef: syncLockRef,
      mapARef,
      mapBRef,
    }),
    [],
  )

  const distanceM = useMemo(
    () => (measureMode === 'distance' ? totalPathLengthM(points) : 0),
    [measureMode, points],
  )
  const areaM2 = useMemo(
    () => (measureMode === 'area' ? ringAreaM2(points) : 0),
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

  const setTool = useCallback((mode: MeasureMode) => {
    setIssueMode(false)
    setIssueDraft(null)
    setIssueError(null)
    setMeasureMode((prev) => {
      const next = prev === mode ? 'none' : mode
      return next
    })
    setPoints([])
    setAreaFrozen(false)
  }, [])

  const toggleIssueMode = useCallback(() => {
    setMeasureMode('none')
    setPoints([])
    setAreaFrozen(false)
    setIssueError(null)
    setIssueDraft(null)
    setIssueMode((prev) => !prev)
  }, [])

  useEffect(() => {
    if (measureMode === 'none') {
      setPoints([])
      setAreaFrozen(false)
    }
  }, [measureMode])

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

  const measureActive = measureMode !== 'none' && !splitView && !issueMode

  const mapProps = {
    center,
    zoom,
    scrollWheelZoom: true,
    className: 'mv-leaflet',
  } as const

  useEffect(() => {
    if (!projectId) {
      setCogTileUrl(null)
      return
    }
    const layer = activeLayers.find(
      (item) => item.projectId === projectId && item.layerType === 'cog',
    )
    if (layer?.url) {
      console.log('🗺️ [MAP DEBUG] Requesting Tile URL:', layer.url)
      setCogTileUrl(layer.url)
      return
    }
    const latest = getLatestCogLayer(projectId)
    setCogTileUrl(latest?.tileUrl ?? null)
  }, [activeLayers, projectId])

  const activeCogLayers = useMemo(
    () =>
      activeLayers.filter(
        (item) => item.projectId === projectId && item.layerType === 'cog' && Boolean(item.url),
      ),
    [activeLayers, projectId],
  )

  return (
    <div className="mv-root">
      {!customTilesReady ? (
        <div className="mv-banner" role="status">
          <i className="fa-solid fa-circle-info" aria-hidden />
          <span>
            Set <code className="mv-banner__code">VITE_TILE_BASE_URL</code> or{' '}
            <code className="mv-banner__code">VITE_S3_TILE_BASE_URL</code>{' '}
            (e.g. FastAPI <code className="mv-banner__code">/data</code>) to load
            processed raster and flood layers. Showing OSM fallback for base map.
          </span>
        </div>
      ) : null}

      <div className="mv-chrome">
        <div className="mv-panel mv-panel--layers">
          <p className="mv-panel__title">Layers</p>
          <fieldset className="mv-fieldset">
            <legend className="mv-legend">Processed Raster Layers</legend>
            {activeCogLayers.length > 0 ? (
              activeCogLayers.map((layer) => (
                <button
                  key={layer.id}
                  type="button"
                  className={layer.url === cogTileUrl ? 'mv-tool mv-tool--active' : 'mv-tool'}
                  onClick={() => setCogTileUrl(layer.url)}
                >
                  {layer.name}
                </button>
              ))
            ) : (
              <p className="mv-hud__hint">Select a Web-Ready TIFF in Datasets panel first.</p>
            )}
          </fieldset>

          <fieldset className="mv-fieldset">
            <legend className="mv-legend">Flood risk overlay</legend>
            <label className="mv-check">
              <input
                type="checkbox"
                checked={floodOn}
                disabled={!customTilesReady}
                onChange={(e) => setFloodOn(e.target.checked)}
              />
              <span>Show flood tiles</span>
            </label>
            <div className="mv-flood-grid">
              {(Object.keys(FLOOD_LABELS) as FloodReturnPeriod[]).map((p) => (
                <label key={p} className="mv-radio mv-radio--compact">
                  <input
                    type="radio"
                    name="mv-flood-period"
                    disabled={!floodOn || !customTilesReady}
                    checked={floodPeriod === p}
                    onChange={() => setFloodPeriod(p)}
                  />
                  <span>{FLOOD_LABELS[p]}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="mv-fieldset">
            <legend className="mv-legend">Hydrology data</legend>
            <label className="mv-check">
              <input
                type="checkbox"
                checked={showHydrologyData}
                onChange={(e) => setShowHydrologyData(e.target.checked)}
              />
              <span>Show hydrology points & zones</span>
            </label>
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
            disabled={splitView}
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
            disabled={splitView}
            onClick={() => setTool('area')}
            title="Click vertices; double-click to finish polygon."
          >
            <i className="fa-solid fa-draw-polygon" aria-hidden />
            Area
          </button>
          <button
            type="button"
            className={issueMode && !splitView ? 'mv-tool mv-tool--active' : 'mv-tool'}
            disabled={splitView}
            onClick={toggleIssueMode}
            title="Click map to place an issue marker and submit details."
          >
            <i className="fa-solid fa-location-dot" aria-hidden />
            Report Issue
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
            title="Side-by-side comparison (views stay in sync)."
          >
            <i className="fa-solid fa-columns" aria-hidden />
            Split view
          </button>
          {measureMode !== 'none' && !splitView ? (
            <button
              type="button"
              className="mv-tool mv-tool--ghost"
              onClick={clearMeasure}
            >
              Clear drawing
            </button>
          ) : null}
        </div>
      </div>

      {measureMode !== 'none' && !splitView ? (
        <div className="mv-hud" aria-live="polite">
          {measureMode === 'distance' ? (
            <span>
              Distance: <strong>{formatLengthM(distanceM)}</strong>
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

      <div className={splitView ? 'mv-maps mv-maps--split' : 'mv-maps'}>
        <div className="mv-map-wrap">
          {splitView ? <span className="mv-map-label">Primary</span> : null}
          <div className="mv-map-canvas">
            <MapContainer {...mapProps} style={{ height: '100%', width: '100%' }}>
              <MapController activeLayers={activeLayers} projectId={projectId} />
              <MapPane
                floodEnabled={floodOn && customTilesReady}
                floodPeriod={floodPeriod}
                floodSimulationLevel={floodSimulationLevel}
                measureMode={measureMode}
                measureActive={measureActive}
                measurePoints={points}
                areaFrozen={areaFrozen}
                onMeasureAdd={onMeasureAdd}
                onMeasureCloseRing={onMeasureCloseRing}
                issueMode={issueMode && !splitView}
                onIssuePick={onIssuePick}
                issues={issues}
                showHydrologyData={showHydrologyData}
                cogTileUrl={cogTileUrl}
                sync={splitView ? { ...syncRefs, isA: true } : undefined}
              />
            </MapContainer>
            {issueMode && !splitView ? (
              <div className="mv-issue-hint" aria-live="polite">
                Click on the map to place an issue pin.
              </div>
            ) : null}
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

        {splitView ? (
          <div className="mv-map-wrap mv-map-wrap--compare">
            <div className="mv-compare-head">
              <span className="mv-map-label">Compare</span>
              <select
                className="mv-select"
                value={cogTileUrl ?? ''}
                onChange={(e) => setCogTileUrl(e.target.value || null)}
                aria-label="Comparison base layer"
              >
                <option value="">No overlay selected</option>
                {activeCogLayers.map((layer) => (
                  <option key={layer.id} value={layer.url}>
                    {layer.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="mv-map-canvas">
              <MapContainer {...mapProps} style={{ height: '100%', width: '100%' }}>
                <MapController activeLayers={activeLayers} projectId={projectId} />
                <MapPane
                  floodEnabled={floodOn && customTilesReady}
                  floodPeriod={floodPeriod}
                  floodSimulationLevel={floodSimulationLevel}
                  measureMode="none"
                  measureActive={false}
                  measurePoints={[]}
                  areaFrozen={false}
                  onMeasureAdd={() => {}}
                  onMeasureCloseRing={() => {}}
                  issueMode={false}
                  onIssuePick={() => {}}
                  issues={issues}
                  showHydrologyData={showHydrologyData}
                  cogTileUrl={cogTileUrl}
                  sync={{ ...syncRefs, isA: false }}
                />
              </MapContainer>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

export default MapViewer
