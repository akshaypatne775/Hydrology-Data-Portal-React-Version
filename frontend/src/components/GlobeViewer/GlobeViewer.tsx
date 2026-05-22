import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { API_BASE, toSameOriginBackendUrl } from '../../lib/apiBase'
import { useUploadContext } from '../../context/UploadContext'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { useModal } from '../../context/ModalContext'
import { getPointCloudStatus } from '../../services/pointCloudService'
import { getProjectFiles, updateDatasetOwnerMetadata, type ProjectFile } from '../../services/datasetService'
import './GlobeViewer.css'
import {
  readUploadedTilesets,
  writeUploadedTilesets,
  type UploadedTileset,
} from '../../utils/pointCloudStorage'
import {
  deleteCameraView,
  getCameraViews,
  saveCameraView,
  type CameraView,
} from '../../services/cameraViewService'

const CESIUM_ION_TOKEN = (import.meta.env.VITE_CESIUM_ION_TOKEN ?? '').trim()
const HAS_VALID_ION_TOKEN =
  CESIUM_ION_TOKEN.length > 0 && CESIUM_ION_TOKEN !== 'APNA_TOKEN_YAHAN_PASTE_KAREIN'

type ColorMode = 'RGB' | 'Elevation'
type ImageryMode = 'earth' | 'none'

type GlobePosition = {
  lat: number | null
  lng: number | null
  elevation: number | null
}

type ModelTilesetEntry = {
  tileset: Cesium.Cesium3DTileset
  latitude: number
  longitude: number
  sourceHeight: number
}

type ViewerLayerKind = 'model' | 'pointcloud'

type ViewerDataOption = {
  id: string
  name: string
  kind: ViewerLayerKind
  url: string
  datasetId?: string
  height_offset?: number | string
}

type DrawMode = 'none' | 'point' | 'polyline'

type DrawPoint = {
  lat: number
  lng: number
  height: number
}

type DrawGeometry = {
  id: string
  type: 'Point' | 'LineString'
  points: DrawPoint[]
}

const TILESET_MAX_WAIT_MS = 2 * 60 * 60 * 1000
const TILESET_POLL_MS = 2000
const MODEL_TILE_CACHE_BYTES = 1024 * 1024 * 1024
const MODEL_TILE_CACHE_OVERFLOW_BYTES = 1024 * 1024 * 1024
const MODEL_SCREEN_SPACE_ERROR = 2
const MODEL_GROUND_CLEARANCE_METERS = 80

function resolveLayerDatasetId(layer?: { datasetId?: unknown; id?: unknown } | null): string {
  const direct = String(layer?.datasetId || '').trim()
  if (direct) return direct
  const rawId = String(layer?.id || '').trim()
  if (!rawId) return ''
  const cleaned = rawId.replace(/^active:/i, '')
  const parts = cleaned.split(':').map((part) => part.trim()).filter(Boolean)
  return parts.at(-1) || cleaned
}

function applyModelHeightOffset(entry: ModelTilesetEntry, offsetMeters: number): void {
  const surface = Cesium.Cartesian3.fromRadians(entry.longitude, entry.latitude, 0)
  const offset = Cesium.Cartesian3.fromRadians(entry.longitude, entry.latitude, offsetMeters)
  const translation = Cesium.Cartesian3.subtract(offset, surface, new Cesium.Cartesian3())
  entry.tileset.modelMatrix = Cesium.Matrix4.fromTranslation(translation)
}

function projectIdFromTilesetUrl(tilesetUrl: string): string | null {
  try {
    const segments = new URL(tilesetUrl).pathname.split('/').filter(Boolean)
    const idx = segments.indexOf('pointclouds')
    if (idx >= 0 && segments[idx + 1]) {
      return decodeURIComponent(segments[idx + 1]!)
    }
  } catch {
    return null
  }
  return null
}

function tilesetIdFromTilesetUrl(tilesetUrl: string): string | null {
  try {
    const segments = new URL(tilesetUrl).pathname.split('/').filter(Boolean)
    const idx = segments.indexOf('pointclouds')
    const maybeTilesetId = idx >= 0 ? segments[idx + 2] : null
    if (maybeTilesetId && maybeTilesetId !== 'tileset.json') {
      return decodeURIComponent(maybeTilesetId)
    }
  } catch {
    return null
  }
  return null
}

async function waitForPointCloudTileset(tilesetUrl: string): Promise<void> {
  const start = Date.now()
  const projectId = projectIdFromTilesetUrl(tilesetUrl)
  const tilesetId = tilesetIdFromTilesetUrl(tilesetUrl)

  while (Date.now() - start < TILESET_MAX_WAIT_MS) {
    if (projectId) {
      const data = await getPointCloudStatus(projectId, tilesetId ?? undefined)
      if (data) {
        if (data.failed) {
          throw new Error(
            data.error?.trim() || 'Point cloud conversion failed on the server.',
          )
        }
        if (data.ready) {
          return
        }
      }
    } else {
      const res = await fetch(tilesetUrl, {
        method: 'HEAD',
        cache: 'no-store',
        credentials: 'include',
      })
      if (res.ok) {
        return
      }
    }
    await new Promise((r) => window.setTimeout(r, TILESET_POLL_MS))
  }

  throw new Error(
    'Timed out waiting for tileset.json. Ensure py3dtiles is installed on the backend and conversion completed.',
  )
}

function buildPointCloudStyle(pointSize: number, colorMode: ColorMode): Cesium.Cesium3DTileStyle {
  if (colorMode === 'Elevation') {
    return new Cesium.Cesium3DTileStyle({
      pointSize: `${pointSize}`,
      color: {
        conditions: [
          ['${POSITION}[2] >= 200', 'color("white")'],
          ['${POSITION}[2] >= 100', 'color("yellow")'],
          ['${POSITION}[2] >= 50', 'color("orange")'],
          ['true', 'color("cyan")'],
        ],
      },
    })
  }
  return new Cesium.Cesium3DTileStyle({
    pointSize: `${pointSize}`,
  })
}

function formatMeasureDistance(meters: number): string {
  if (!Number.isFinite(meters) || meters <= 0) return '0.00 m'
  if (meters >= 1000) return `${(meters / 1000).toFixed(3)} km`
  return `${meters.toFixed(2)} m`
}

function escapeXml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

async function fetchImageryTileMeta(tileUrl: string): Promise<{ zoom_max?: number } | null> {
  const suffix = '{z}/{x}/{y}.png'
  if (!tileUrl.endsWith(suffix)) return null
  try {
    const res = await fetch(`${tileUrl.slice(0, -suffix.length)}tileset.json`, {
      credentials: 'include',
    })
    if (!res.ok) return null
    return (await res.json()) as { zoom_max?: number }
  } catch {
    return null
  }
}

function projectFileToViewerOption(file: ProjectFile): ViewerDataOption | null {
  if (!file.layer_url) return null
  const fileType = String(file.type).toLowerCase()
  if (file.type === '3DModel') {
    return {
      id: `model:${file.dataset_id || file.rel_path}`,
      name: file.name,
      kind: 'model',
      url: toSameOriginBackendUrl(file.layer_url) || file.layer_url,
      datasetId: file.dataset_id,
      height_offset: file.height_offset,
    }
  }
  if (fileType === 'pointcloud' && !file.layer_url.toLowerCase().endsWith('.html')) {
    return {
      id: `pointcloud:${file.dataset_id || file.rel_path}`,
      name: file.name,
      kind: 'pointcloud',
      url: toSameOriginBackendUrl(file.layer_url) || file.layer_url,
      datasetId: file.dataset_id,
    }
  }
  return null
}

type GlobeViewerProps = {
  projectId: string
}

export function GlobeViewer({ projectId }: GlobeViewerProps) {
  const modal = useModal()
  const { tasks } = useUploadContext()
  const { activeLayers } = useWorkspaceContext()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Cesium.Viewer | null>(null)
  const pointCloudRef = useRef<Cesium.Cesium3DTileset | null>(null)
  const modelTilesetsRef = useRef<Map<string, ModelTilesetEntry>>(new Map())
  const modelHeightOffsetRef = useRef(0)
  const orthomosaicLayerRef = useRef<Cesium.ImageryLayer | null>(null)
  const vectorSourcesRef = useRef<Map<string, Cesium.DataSource>>(new Map())
  const measurePointsRef = useRef<Cesium.Cartesian3[]>([])
  const measureEntityIdsRef = useRef<string[]>([])
  const drawEntityIdsRef = useRef<string[]>([])
  const draftLinePointsRef = useRef<DrawPoint[]>([])
  const [pointSize, setPointSize] = useState(3)
  const [colorMode, setColorMode] = useState<ColorMode>('RGB')
  const [imageryMode, setImageryMode] = useState<ImageryMode>('earth')
  const [distanceMeasureActive, setDistanceMeasureActive] = useState(false)
  const [drawMode, setDrawMode] = useState<DrawMode>('none')
  const [drawnGeometries, setDrawnGeometries] = useState<DrawGeometry[]>([])
  const [draftLineCount, setDraftLineCount] = useState(0)
  const [measureDistanceM, setMeasureDistanceM] = useState(0)
  const [viewerReady, setViewerReady] = useState(false)
  const [modelHeightOffset, setModelHeightOffset] = useState(0)
  const [viewerError, setViewerError] = useState<string | null>(null)
  const [pipelineNotice, setPipelineNotice] = useState<string | null>(null)
  const [uploadedTilesets, setUploadedTilesets] = useState<UploadedTileset[]>([])
  const [viewerDataOptions, setViewerDataOptions] = useState<ViewerDataOption[]>([])
  const [selectedViewerDataId, setSelectedViewerDataId] = useState('')
  const [loadedLayerKind, setLoadedLayerKind] = useState<ViewerLayerKind>('pointcloud')
  const [cameraViews, setCameraViews] = useState<CameraView[]>([])
  const [selectedCameraViewId, setSelectedCameraViewId] = useState('')
  const [position, setPosition] = useState<GlobePosition>({
    lat: null,
    lng: null,
    elevation: null,
  })

  const positionLabel = useMemo(() => {
    if (position.lat == null || position.lng == null) {
      return 'Lat -- | Lng -- | Elev -- m'
    }
    const elev = position.elevation == null ? '--' : position.elevation.toFixed(2)
    return `Lat ${position.lat.toFixed(6)} | Lng ${position.lng.toFixed(6)} | Elev ${elev} m`
  }, [position.elevation, position.lat, position.lng])

  const activeModelLayers = useMemo(
    () =>
      activeLayers.filter(
        (layer) => layer.projectId === projectId && layer.layerType === '3DModel' && Boolean(layer.url),
      ),
    [activeLayers, projectId],
  )

  const activePointCloudLayer = useMemo(
    () =>
      activeLayers.find(
        (layer) =>
          layer.projectId === projectId &&
          String(layer.layerType).toLowerCase() === 'pointcloud' &&
          !layer.url.toLowerCase().endsWith('.html'),
      ),
    [activeLayers, projectId],
  )
  const activeVectorLayers = useMemo(
    () =>
      activeLayers.filter(
        (layer) => layer.projectId === projectId && layer.layerType === 'Vector' && Boolean(layer.url),
      ),
    [activeLayers, projectId],
  )

  const activeControlMode: ViewerLayerKind = activeModelLayers.length > 0 || loadedLayerKind === 'model' ? 'model' : 'pointcloud'
  const selectedViewerData = useMemo(
    () => viewerDataOptions.find((item) => item.id === selectedViewerDataId) ?? null,
    [selectedViewerDataId, viewerDataOptions],
  )
  const selectedCameraView = useMemo(
    () => cameraViews.find((view) => view.id === selectedCameraViewId) ?? null,
    [cameraViews, selectedCameraViewId],
  )

  const clearDistanceMeasurement = useCallback(() => {
    const viewer = viewerRef.current
    if (viewer) {
      for (const id of measureEntityIdsRef.current) {
        const entity = viewer.entities.getById(id)
        if (entity) viewer.entities.remove(entity)
      }
      viewer.scene.requestRender()
    }
    measureEntityIdsRef.current = []
    measurePointsRef.current = []
    setMeasureDistanceM(0)
  }, [])

  const refreshDistanceMeasurement = useCallback((points: Cesium.Cartesian3[]) => {
    const viewer = viewerRef.current
    if (!viewer) return
    for (const id of measureEntityIdsRef.current) {
      const entity = viewer.entities.getById(id)
      if (entity) viewer.entities.remove(entity)
    }
    measureEntityIdsRef.current = []

    let total = 0
    for (let i = 1; i < points.length; i += 1) {
      total += Cesium.Cartesian3.distance(points[i - 1]!, points[i]!)
    }
    setMeasureDistanceM(total)

    points.forEach((point, index) => {
      const entity = viewer.entities.add({
        id: `measure-point-${Date.now()}-${index}`,
        position: point,
        point: {
          pixelSize: 9,
          color: Cesium.Color.fromCssColorString('#14b8a6'),
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      })
      measureEntityIdsRef.current.push(entity.id)
    })

    if (points.length >= 2) {
      const line = viewer.entities.add({
        id: `measure-line-${Date.now()}`,
        polyline: {
          positions: points,
          width: 4,
          material: Cesium.Color.fromCssColorString('#14b8a6'),
          clampToGround: false,
        },
      })
      measureEntityIdsRef.current.push(line.id)
    }

    const lastPoint = points[points.length - 1]
    if (lastPoint) {
      const label = viewer.entities.add({
        id: `measure-label-${Date.now()}`,
        position: lastPoint,
        label: {
          text: `Distance ${formatMeasureDistance(total)}`,
          font: '600 14px Montserrat, sans-serif',
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -18),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      })
      measureEntityIdsRef.current.push(label.id)
    }
    viewer.scene.requestRender()
  }, [])

  const addDrawPointEntity = useCallback((point: DrawPoint) => {
    const viewer = viewerRef.current
    if (!viewer) return
    const entity = viewer.entities.add({
      id: `draw-point-${Date.now()}-${drawEntityIdsRef.current.length}`,
      position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, point.height),
      point: {
        pixelSize: 10,
        color: Cesium.Color.fromCssColorString('#f8fafc'),
        outlineColor: Cesium.Color.fromCssColorString('#0e3e49'),
        outlineWidth: 3,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      },
    })
    drawEntityIdsRef.current.push(entity.id)
  }, [])

  const redrawDraftLine = useCallback((points: DrawPoint[]) => {
    const viewer = viewerRef.current
    if (!viewer) return
    const oldDraft = viewer.entities.getById('draw-draft-line')
    if (oldDraft) viewer.entities.remove(oldDraft)
    if (points.length < 2) return
    viewer.entities.add({
      id: 'draw-draft-line',
      polyline: {
        positions: points.map((point) => Cesium.Cartesian3.fromDegrees(point.lng, point.lat, point.height)),
        width: 3,
        material: Cesium.Color.fromCssColorString('#ccfbf1'),
        clampToGround: true,
      },
    })
  }, [])

  const clearDrawings = useCallback(() => {
    const viewer = viewerRef.current
    if (viewer) {
      for (const id of drawEntityIdsRef.current) {
        const entity = viewer.entities.getById(id)
        if (entity) viewer.entities.remove(entity)
      }
      const draft = viewer.entities.getById('draw-draft-line')
      if (draft) viewer.entities.remove(draft)
    }
    drawEntityIdsRef.current = []
    draftLinePointsRef.current = []
    setDraftLineCount(0)
    setDrawnGeometries([])
  }, [])

  const finishDraftLine = useCallback(() => {
    const points = draftLinePointsRef.current
    if (points.length < 2) return
    const viewer = viewerRef.current
    const lineId = `draw-line-${Date.now()}`
    if (viewer) {
      const draft = viewer.entities.getById('draw-draft-line')
      if (draft) viewer.entities.remove(draft)
      const entity = viewer.entities.add({
        id: lineId,
        polyline: {
          positions: points.map((point) => Cesium.Cartesian3.fromDegrees(point.lng, point.lat, point.height)),
          width: 4,
          material: Cesium.Color.fromCssColorString('#14b8a6'),
          clampToGround: true,
        },
      })
      drawEntityIdsRef.current.push(entity.id)
    }
    setDrawnGeometries((prev) => [...prev, { id: lineId, type: 'LineString', points: [...points] }])
    draftLinePointsRef.current = []
    setDraftLineCount(0)
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

  const exportDrawingsKml = useCallback(() => {
    const placemarks = drawnGeometries.map((geom, index) => {
      const coords = geom.points.map((point) => `${point.lng},${point.lat},${point.height || 0}`).join(' ')
      if (geom.type === 'Point') {
        const point = geom.points[0]
        return `<Placemark><name>${escapeXml(`Point ${index + 1}`)}</name><Point><coordinates>${point?.lng},${point?.lat},${point?.height || 0}</coordinates></Point></Placemark>`
      }
      return `<Placemark><name>${escapeXml(`Line ${index + 1}`)}</name><LineString><tessellate>1</tessellate><coordinates>${coords}</coordinates></LineString></Placemark>`
    }).join('\n')
    downloadTextFile('droid-drawings.kml', `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>${placemarks}</Document></kml>`, 'application/vnd.google-earth.kml+xml')
  }, [downloadTextFile, drawnGeometries])

  const exportDrawingsCsv = useCallback(() => {
    const rows = ['id,type,vertex,lat,lng,height']
    drawnGeometries.forEach((geom) => {
      geom.points.forEach((point, index) => {
        rows.push(`${geom.id},${geom.type},${index + 1},${point.lat},${point.lng},${point.height}`)
      })
    })
    downloadTextFile('droid-drawings.csv', rows.join('\n'), 'text/csv')
  }, [downloadTextFile, drawnGeometries])

  const applyImageryMode = useCallback((mode: ImageryMode) => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.scene.globe.show = mode !== 'none'
    if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = mode !== 'none'
    if (viewer.scene.skyBox) {
      ;(viewer.scene.skyBox as Cesium.SkyBox & { show?: boolean }).show = mode !== 'none'
    }
    if (viewer.scene.moon) viewer.scene.moon.show = mode !== 'none'
    viewer.scene.backgroundColor = mode === 'none' ? Cesium.Color.fromCssColorString('#020617') : Cesium.Color.BLACK
    viewer.scene.requestRender()
  }, [])

  const setModelOffset = useCallback((nextOffset: number) => {
    const rounded = Math.round(nextOffset)
    modelHeightOffsetRef.current = rounded
    setModelHeightOffset(rounded)
    for (const entry of modelTilesetsRef.current.values()) {
      applyModelHeightOffset(entry, rounded)
    }
  }, [])

  const autoGroundModels = useCallback(() => {
    const firstEntry = modelTilesetsRef.current.values().next().value as ModelTilesetEntry | undefined
    if (!firstEntry) return
    setModelOffset(-firstEntry.sourceHeight + MODEL_GROUND_CLEARANCE_METERS)
  }, [setModelOffset])

  const saveModelHeightOffset = useCallback(async () => {
    const activeLayer = activeModelLayers[0]
    const datasetId = resolveLayerDatasetId(activeLayer)
    if (!datasetId) {
      await modal.alert('Height not saved', 'Open a saved 3D model from the Data Catalog before saving height.')
      return
    }
    try {
      await updateDatasetOwnerMetadata(projectId, datasetId, {
        height_offset: modelHeightOffset,
      })
      await modal.alert('Height saved', '3D model height offset saved. It will be restored on reload.')
    } catch (error) {
      await modal.alert('Height save failed', error instanceof Error ? error.message : 'Height save failed')
    }
  }, [activeModelLayers, modal, modelHeightOffset, projectId])

  const getPrimaryModelEntry = useCallback((): ModelTilesetEntry | undefined => (
    modelTilesetsRef.current.values().next().value as ModelTilesetEntry | undefined
  ), [])

  const getPrimaryCameraTarget = useCallback((): Cesium.Cesium3DTileset | null => {
    const model = getPrimaryModelEntry()
    if (model) return model.tileset
    return pointCloudRef.current
  }, [getPrimaryModelEntry])

  const flyToPreset = useCallback((preset: 'home' | 'top' | 'front' | 'back' | 'left' | 'right') => {
    const viewer = viewerRef.current
    const target = getPrimaryCameraTarget()
    if (!viewer || !target) return
    const sphere = target.boundingSphere
    if (preset === 'home') {
      void viewer.zoomTo(target)
      return
    }
    const headingByPreset = {
      top: 0,
      front: 0,
      back: 180,
      left: 270,
      right: 90,
    } satisfies Record<Exclude<typeof preset, 'home'>, number>
    const heading = Cesium.Math.toRadians(headingByPreset[preset])
    const pitch = Cesium.Math.toRadians(preset === 'top' ? -89 : -28)
    const range = Math.max(sphere.radius * (preset === 'top' ? 2.5 : 2.0), 300)
    viewer.camera.flyToBoundingSphere(sphere, {
      duration: 1.2,
      offset: new Cesium.HeadingPitchRange(heading, pitch, range),
    })
  }, [getPrimaryCameraTarget])

  const flyToCameraView = useCallback((view: CameraView) => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(view.lng, view.lat, view.height),
      orientation: {
        heading: Cesium.Math.toRadians(view.heading),
        pitch: Cesium.Math.toRadians(view.pitch),
        roll: Cesium.Math.toRadians(view.roll),
      },
      duration: 1.2,
    })
  }, [])

  const saveCurrentCamera = useCallback(async () => {
    const viewer = viewerRef.current
    if (!viewer) return
    const name = await modal.prompt('Save camera point', 'Camera point name', `View ${cameraViews.length + 1}`)
    if (!name?.trim()) return
    const cartographic = viewer.camera.positionCartographic
    try {
      const saved = await saveCameraView(projectId, {
        name: name.trim(),
        lat: Cesium.Math.toDegrees(cartographic.latitude),
        lng: Cesium.Math.toDegrees(cartographic.longitude),
        height: cartographic.height,
        heading: Cesium.Math.toDegrees(viewer.camera.heading),
        pitch: Cesium.Math.toDegrees(viewer.camera.pitch),
        roll: Cesium.Math.toDegrees(viewer.camera.roll),
      })
      setCameraViews((prev) => [saved, ...prev])
      setSelectedCameraViewId(saved.id)
    } catch (error) {
      setViewerError(error instanceof Error ? error.message : 'Failed to save camera point')
    }
  }, [cameraViews.length, modal, projectId])

  const deleteSelectedCamera = useCallback(async () => {
    if (!selectedCameraViewId) return
    try {
      await deleteCameraView(projectId, selectedCameraViewId)
      setCameraViews((prev) => prev.filter((view) => view.id !== selectedCameraViewId))
      setSelectedCameraViewId('')
    } catch (error) {
      setViewerError(error instanceof Error ? error.message : 'Failed to delete camera point')
    }
  }, [projectId, selectedCameraViewId])

  const loadPointCloud = useCallback(async (tilesetUrl: string) => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }

    try {
      if (pointCloudRef.current) {
        viewer.scene.primitives.remove(pointCloudRef.current)
        pointCloudRef.current = null
      }
      const tileset = await Cesium.Cesium3DTileset.fromUrl(tilesetUrl)
      tileset.maximumScreenSpaceError = 1
      tileset.dynamicScreenSpaceError = false
      tileset.style = buildPointCloudStyle(pointSize, colorMode)
      tileset.pointCloudShading = new Cesium.PointCloudShading({
        attenuation: true,
        maximumAttenuation: pointSize,
        geometricErrorScale: 1,
        eyeDomeLighting: true,
      })
      viewer.scene.primitives.add(tileset)
      pointCloudRef.current = tileset
      await viewer.zoomTo(tileset)
      setLoadedLayerKind('pointcloud')
      setViewerError(null)
      setPipelineNotice(null)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to load point cloud tileset'
      setViewerError(message)
      console.error('Point cloud load failed:', error)
    }
  }, [colorMode, pointSize])

  const loadPointCloudWhenReady = useCallback(
    async (tilesetUrl: string) => {
      setViewerError(null)
      setPipelineNotice('Generating 3D tiles on server… this can take several minutes.')
      try {
        await waitForPointCloudTileset(tilesetUrl)
        setPipelineNotice('Loading point cloud on globe…')
        await loadPointCloud(tilesetUrl)
      } catch (error) {
        const message =
          error instanceof Error ? error.message : 'Point cloud pipeline failed.'
        setViewerError(message)
        console.error('Point cloud pipeline failed:', error)
      } finally {
        setPipelineNotice(null)
      }
    },
    [loadPointCloud],
  )

  const loadOrthomosaic = useCallback((layerConfig: { url: string; projectId: string; name: string }) => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }
    const tileUrl = layerConfig.url

    void (async () => {
      try {
        const meta = await fetchImageryTileMeta(tileUrl)
        const zoomMax = Number(meta?.zoom_max)
        const maximumLevel = Number.isFinite(zoomMax) ? Math.max(0, Math.min(22, Math.round(zoomMax))) : 22

      if (orthomosaicLayerRef.current) {
        viewer.imageryLayers.remove(orthomosaicLayerRef.current, true)
      }
      const imageryProvider = new Cesium.UrlTemplateImageryProvider({
        url: tileUrl,
        maximumLevel,
        hasAlphaChannel: true,
      })
      const layer = new Cesium.ImageryLayer(imageryProvider)
      viewer.imageryLayers.add(layer)
      orthomosaicLayerRef.current = layer
      setViewerError(null)

      void (async () => {
        try {
          const boundsUrl = `${API_BASE}/api/datasets/${encodeURIComponent(layerConfig.projectId)}/${encodeURIComponent(layerConfig.name.replace(/\.tiff?$/i, ''))}/bounds`
          const res = await fetch(boundsUrl, { credentials: 'include' })
          const data = (await res.json()) as { bounds?: [number, number, number, number] | null }
          if (data && data.bounds) {
            const [minX, minY, maxX, maxY] = data.bounds
            viewer.camera.flyTo({
              destination: Cesium.Rectangle.fromDegrees(minX, minY, maxX, maxY),
              duration: 2.0,
            })
          }
        } catch (e) {
          console.error('Failed to fetch static bounds for zoom', e)
        }
      })()
      } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to load orthomosaic layer'
      setViewerError(message)
      console.error('Orthomosaic load failed:', error)
      }
    })()
  }, [])

  const load3DModel = useCallback(async (layer: { id: string; url: string; name: string; height_offset?: number | string }) => {
    const viewer = viewerRef.current
    if (!viewer) {
      return
    }
    if (modelTilesetsRef.current.has(layer.id)) return

    try {
      const tileset = await Cesium.Cesium3DTileset.fromUrl(layer.url, {
        maximumScreenSpaceError: MODEL_SCREEN_SPACE_ERROR,
        cacheBytes: MODEL_TILE_CACHE_BYTES,
        maximumCacheOverflowBytes: MODEL_TILE_CACHE_OVERFLOW_BYTES,
        skipLevelOfDetail: false,
        dynamicScreenSpaceError: false,
        foveatedScreenSpaceError: false,
        progressiveResolutionHeightFraction: 0,
        cullRequestsWhileMoving: false,
        preferLeaves: true,
        preloadFlightDestinations: true,
        backFaceCulling: false,
      })
      tileset.maximumScreenSpaceError = MODEL_SCREEN_SPACE_ERROR
      tileset.dynamicScreenSpaceError = false
      tileset.foveatedScreenSpaceError = false
      const cartographic = Cesium.Cartographic.fromCartesian(tileset.boundingSphere.center)
      const entry: ModelTilesetEntry = {
        tileset,
        latitude: cartographic.latitude,
        longitude: cartographic.longitude,
        sourceHeight: cartographic.height,
      }
      const autoGroundOffset = -cartographic.height + MODEL_GROUND_CLEARANCE_METERS
      const savedOffset = Number(layer.height_offset)
      const nextOffset = Number.isFinite(savedOffset) ? savedOffset : autoGroundOffset
      setModelOffset(nextOffset)
      applyModelHeightOffset(entry, nextOffset)
      const addedTileset = viewer.scene.primitives.add(tileset)
      entry.tileset = addedTileset
      modelTilesetsRef.current.set(layer.id, entry)
      await viewer.zoomTo(entry.tileset)
      setLoadedLayerKind('model')
      setViewerError(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load 3D model tileset'
      setViewerError(message)
      console.error('Error loading 3D Tileset:', error)
    }
  }, [setModelOffset])

  const clearLoadedModels = useCallback(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    for (const entry of modelTilesetsRef.current.values()) {
      viewer.scene.primitives.remove(entry.tileset)
    }
    modelTilesetsRef.current.clear()
  }, [])

  const loadViewerDataOption = useCallback((option: ViewerDataOption) => {
    if (option.kind === 'pointcloud') {
      clearLoadedModels()
      void loadPointCloudWhenReady(option.url)
      return
    }
    if (pointCloudRef.current && viewerRef.current) {
      viewerRef.current.scene.primitives.remove(pointCloudRef.current)
      pointCloudRef.current = null
    }
    clearLoadedModels()
    void load3DModel({
      id: option.id,
      name: option.name,
      url: option.url,
      height_offset: option.height_offset,
    })
  }, [clearLoadedModels, load3DModel, loadPointCloudWhenReady])

  // Example TiTiler COG XYZ layer (reference):
  // const cogLayer = new Cesium.UrlTemplateImageryProvider({
  //   url: 'http://localhost:8000/api/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=D:/Data/output.tif',
  // })

  useEffect(() => {
    const stored = readUploadedTilesets(projectId)
    setUploadedTilesets(stored)
    setViewerError(null)
    setPipelineNotice(null)

    let cancelled = false
    const restoreSavedTileset = async () => {
      try {
        const data = await getPointCloudStatus(projectId)
        if (!data || cancelled) return
        if (!cancelled && data.ready && data.tileset_url) {
          const readyUrl = data.tileset_url
          setUploadedTilesets((prev) => {
            const next = prev.some((item) => item.url === readyUrl)
              ? prev
              : [{ label: 'Saved Point Cloud', url: readyUrl }, ...prev]
            writeUploadedTilesets(projectId, next)
            return next
          })
        }
      } catch {
        // Keep UI usable even if restore check fails.
      }
    }

    void restoreSavedTileset()
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    let cancelled = false
    const loadViewerData = async () => {
      try {
        const files = await getProjectFiles(projectId)
        if (cancelled) return
        const fileOptions = files
          .map(projectFileToViewerOption)
          .filter((item): item is ViewerDataOption => Boolean(item))
        const storedPointClouds = uploadedTilesets
          .filter((item) => !item.url.toLowerCase().endsWith('.html'))
          .map((item) => ({
            id: `stored-pointcloud:${item.url}`,
            name: item.label,
            kind: 'pointcloud' as const,
            url: item.url,
          }))
        const activeOptions = activeLayers
          .filter(
            (layer) =>
              layer.projectId === projectId &&
              (layer.layerType === '3DModel' || String(layer.layerType).toLowerCase() === 'pointcloud') &&
              layer.url &&
              !layer.url.toLowerCase().endsWith('.html'),
          )
          .map((layer) => ({
            id: `active:${layer.id}`,
            name: layer.name,
            kind: layer.layerType === '3DModel' ? 'model' as const : 'pointcloud' as const,
            url: layer.url,
            datasetId: layer.datasetId,
            height_offset: layer.height_offset,
          }))
        const merged = [...activeOptions, ...fileOptions, ...storedPointClouds].filter(
          (item, index, arr) => arr.findIndex((candidate) => candidate.url === item.url) === index,
        )
        setViewerDataOptions(merged)
        setSelectedViewerDataId((current) => (
          merged.some((item) => item.id === current) ? current : merged[0]?.id || ''
        ))
      } catch {
        if (!cancelled) setViewerDataOptions([])
      }
    }
    void loadViewerData()
    return () => {
      cancelled = true
    }
  }, [activeLayers, projectId, uploadedTilesets])

  useEffect(() => {
    let cancelled = false
    const loadViews = async () => {
      try {
        const views = await getCameraViews(projectId)
        if (cancelled) return
        setCameraViews(views)
        setSelectedCameraViewId((current) => current || views[0]?.id || '')
      } catch {
        if (!cancelled) setCameraViews([])
      }
    }
    void loadViews()
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    const completed = tasks
      .filter(
        (task) =>
          task.projectId === projectId &&
          task.kind === 'pointcloud' &&
          task.state === 'success' &&
          task.resultUrl,
      )
      .map((task) => ({ label: task.fileName, url: task.resultUrl! }))
    if (completed.length === 0) return
    setUploadedTilesets((prev) => {
      const merged = [...completed, ...prev].filter(
        (row, index, arr) => arr.findIndex((item) => item.url === row.url) === index,
      )
      writeUploadedTilesets(projectId, merged)
      return merged
    })
  }, [projectId, tasks])

  useEffect(() => {
    if (!viewerReady) return
    if (activePointCloudLayer?.url) {
      void loadPointCloudWhenReady(activePointCloudLayer.url)
    }
  }, [activePointCloudLayer, loadPointCloudWhenReady, viewerReady])

  useEffect(() => {
    if (!viewerReady) return
    const cogLayer = activeLayers.find(
      (layer) => layer.projectId === projectId && layer.layerType === 'cog',
    )
    if (cogLayer?.url) {
      loadOrthomosaic(cogLayer)
    }
  }, [activeLayers, loadOrthomosaic, projectId, viewerReady])

  useEffect(() => {
    if (!viewerReady) return
    const viewer = viewerRef.current
    if (!viewer) return

    const activeIds = new Set(activeModelLayers.map((layer) => layer.id))

    for (const [id, entry] of modelTilesetsRef.current.entries()) {
      if (!activeIds.has(id)) {
        viewer.scene.primitives.remove(entry.tileset)
        modelTilesetsRef.current.delete(id)
      }
    }

    for (const layer of activeModelLayers) {
      void load3DModel(layer)
    }
  }, [activeModelLayers, load3DModel, viewerReady])

  const activeCogLayers = useMemo(
    () =>
      activeLayers.filter(
        (layer) => layer.projectId === projectId && layer.layerType === 'cog' && Boolean(layer.url),
      ),
    [activeLayers, projectId],
  )

  useEffect(() => {
    const tileset = pointCloudRef.current
    if (!tileset) return
    tileset.style = buildPointCloudStyle(pointSize, colorMode)
    tileset.pointCloudShading = new Cesium.PointCloudShading({
      attenuation: true,
      maximumAttenuation: pointSize,
      geometricErrorScale: 1,
      eyeDomeLighting: true,
    })
  }, [colorMode, pointSize])

  useEffect(() => {
    if (!viewerReady) return
    applyImageryMode(imageryMode)
  }, [applyImageryMode, imageryMode, viewerReady])

  useEffect(() => {
    if (!viewerReady || !distanceMeasureActive) return
    const viewer = viewerRef.current
    if (!viewer) return

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
    handler.setInputAction((click: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      const scene = viewer.scene
      let picked: Cesium.Cartesian3 | undefined
      if (scene.pickPositionSupported) {
        picked = scene.pickPosition(click.position)
      }
      if (!picked) {
        picked = viewer.camera.pickEllipsoid(click.position, scene.globe.ellipsoid) ?? undefined
      }
      if (!picked) return
      const nextPoints = [...measurePointsRef.current, Cesium.Cartesian3.clone(picked)]
      measurePointsRef.current = nextPoints
      refreshDistanceMeasurement(nextPoints)
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK)

    return () => handler.destroy()
  }, [distanceMeasureActive, refreshDistanceMeasurement, viewerReady])

  useEffect(() => {
    if (!viewerReady || drawMode === 'none') return
    const viewer = viewerRef.current
    if (!viewer) return
    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
    handler.setInputAction((click: Cesium.ScreenSpaceEventHandler.PositionedEvent) => {
      let picked: Cesium.Cartesian3 | undefined
      if (viewer.scene.pickPositionSupported) picked = viewer.scene.pickPosition(click.position)
      if (!picked) picked = viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid) ?? undefined
      if (!picked) return
      const cartographic = Cesium.Cartographic.fromCartesian(picked)
      const point = {
        lat: Cesium.Math.toDegrees(cartographic.latitude),
        lng: Cesium.Math.toDegrees(cartographic.longitude),
        height: cartographic.height || 0,
      }
      addDrawPointEntity(point)
      if (drawMode === 'point') {
        setDrawnGeometries((prev) => [...prev, { id: `draw-${Date.now()}`, type: 'Point', points: [point] }])
        return
      }
      draftLinePointsRef.current = [...draftLinePointsRef.current, point]
      setDraftLineCount(draftLinePointsRef.current.length)
      redrawDraftLine(draftLinePointsRef.current)
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
    handler.setInputAction(() => {
      finishDraftLine()
    }, Cesium.ScreenSpaceEventType.RIGHT_CLICK)
    return () => handler.destroy()
  }, [addDrawPointEntity, drawMode, finishDraftLine, redrawDraftLine, viewerReady])

  useEffect(() => {
    if (!viewerReady) return
    const viewer = viewerRef.current
    if (!viewer) return
    const activeIds = new Set(activeVectorLayers.map((layer) => layer.id))
    for (const [id, source] of vectorSourcesRef.current.entries()) {
      if (!activeIds.has(id)) {
        viewer.dataSources.remove(source, true)
        vectorSourcesRef.current.delete(id)
      }
    }
    activeVectorLayers.forEach((layer) => {
      if (vectorSourcesRef.current.has(layer.id)) return
      void (async () => {
        try {
          const lowerUrl = layer.url.toLowerCase()
          const ds = lowerUrl.endsWith('.kml')
            ? await Cesium.KmlDataSource.load(layer.url, { clampToGround: true })
            : await Cesium.GeoJsonDataSource.load(layer.url, {
              clampToGround: true,
              stroke: Cesium.Color.fromCssColorString('#0e3e49'),
              fill: Cesium.Color.fromCssColorString('#14b8a633'),
              strokeWidth: 2.5,
            })
          await viewer.dataSources.add(ds)
          vectorSourcesRef.current.set(layer.id, ds)
          viewer.flyTo(ds)
        } catch (error) {
          console.error('Vector layer load failed:', error)
          setViewerError(error instanceof Error ? error.message : 'Failed to load vector layer')
        }
      })()
    })
  }, [activeVectorLayers, viewerReady])

  useEffect(() => {
    const host = containerRef.current
    if (!host) return
    if (viewerRef.current) return
    let handler: Cesium.ScreenSpaceEventHandler | null = null
    let viewer: Cesium.Viewer | null = null

    try {
      Cesium.Ion.defaultAccessToken = HAS_VALID_ION_TOKEN ? CESIUM_ION_TOKEN : ''
      viewer = new Cesium.Viewer(host, {
        animation: false,
        timeline: false,
        sceneModePicker: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: true,
        navigationHelpButton: false,
        fullscreenButton: false,
        infoBox: false,
        selectionIndicator: false,
        shouldAnimate: true,
      } as Cesium.Viewer.ConstructorOptions)
      viewerRef.current = viewer
      viewer.resolutionScale = Math.min(window.devicePixelRatio || 1, 2)
      ;(viewer.scene as Cesium.Scene & { fxaa?: boolean }).fxaa = true
      setViewerError(
        HAS_VALID_ION_TOKEN
          ? null
          : 'Ion token missing/invalid. Showing OpenStreetMap fallback layer.',
      )

      if (!HAS_VALID_ION_TOKEN) {
        viewer.imageryLayers.removeAll()
        viewer.imageryLayers.addImageryProvider(
          new Cesium.OpenStreetMapImageryProvider({
            url: 'https://a.tile.openstreetmap.org/',
          }),
        )
      }

      if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = true
      if (viewer.scene.sun) viewer.scene.sun.show = false
      if (viewer.scene.moon) viewer.scene.moon.show = true
      viewer.scene.skyBox = Cesium.SkyBox.createEarthSkyBox()
      viewer.scene.globe.enableLighting = false
      viewer.scene.globe.depthTestAgainstTerrain = true
      viewer.scene.highDynamicRange = false
      viewer.scene.backgroundColor = Cesium.Color.BLACK

      handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
      handler.setInputAction((movement: Cesium.ScreenSpaceEventHandler.MotionEvent) => {
        if (!viewer) return
        const cartesian = viewer.camera.pickEllipsoid(
          movement.endPosition,
          viewer.scene.globe.ellipsoid,
        )

        if (!cartesian) {
          setPosition({ lat: null, lng: null, elevation: null })
          return
        }

        const cartographic = Cesium.Cartographic.fromCartesian(cartesian)
        const lat = Cesium.Math.toDegrees(cartographic.latitude)
        const lng = Cesium.Math.toDegrees(cartographic.longitude)
        const elevation = viewer.scene.globe.getHeight(cartographic) ?? 0
        setPosition({ lat, lng, elevation })
      }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)
      setViewerReady(true)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to initialize Cesium viewer'
      setViewerError(message)
      console.error('Cesium Viewer initialization failed:', error)
      if (viewer) {
        viewer.destroy()
        viewerRef.current = null
      }
      setViewerReady(false)
    }

    const modelTilesets = modelTilesetsRef.current
    const vectorSources = vectorSourcesRef.current
    return () => {
      handler?.destroy()
      if (pointCloudRef.current) {
        viewer?.scene.primitives.remove(pointCloudRef.current)
        pointCloudRef.current = null
      }
      for (const entry of modelTilesets.values()) {
        viewer?.scene.primitives.remove(entry.tileset)
      }
      modelTilesets.clear()
      if (orthomosaicLayerRef.current) {
        viewer?.imageryLayers.remove(orthomosaicLayerRef.current, true)
        orthomosaicLayerRef.current = null
      }
      for (const source of vectorSources.values()) {
        viewer?.dataSources.remove(source, true)
      }
      vectorSources.clear()
      for (const id of measureEntityIdsRef.current) {
        const entity = viewer?.entities.getById(id)
        if (entity) viewer?.entities.remove(entity)
      }
      measureEntityIdsRef.current = []
      measurePointsRef.current = []
      viewer?.destroy()
      viewerRef.current = null
      setViewerReady(false)
    }
  }, [])

  return (
    <div className="gv-root d3d-viewer-wrapper">
      <div className="gv-canvas" ref={containerRef} />
      {viewerError ? (
        <div className="gv-error" role="alert">
          Cesium Error: {viewerError}
        </div>
      ) : null}
      {pipelineNotice ? (
        <div className="gv-notice" role="status">
          {pipelineNotice}
        </div>
      ) : null}

      <section className="gv-panel" aria-label="3D viewer controls">
        <h3 className="gv-panel__title">
          {activeControlMode === 'model' ? '3D Model Controls' : 'Point Cloud Controls'}
        </h3>

        <label className="gv-field" htmlFor="gv-data-list">
          <span>3D Data</span>
          <select
            id="gv-data-list"
            value={selectedViewerDataId}
            onChange={(event) => setSelectedViewerDataId(event.target.value)}
          >
            {viewerDataOptions.length > 0 ? (
              viewerDataOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.kind === 'model' ? 'Model' : 'Point Cloud'} - {item.name}
                </option>
              ))
            ) : (
              <option value="">No 3D data found</option>
            )}
          </select>
        </label>
        <button
          type="button"
          className="gv-action"
          disabled={!selectedViewerData}
          onClick={() => {
            if (selectedViewerData) loadViewerDataOption(selectedViewerData)
          }}
        >
          Load Selected Data
        </button>

        {activeControlMode === 'model' ? (
          <>
            <label className="gv-field" htmlFor="gv-imagery-mode">
              <span>Imagery</span>
              <select
                id="gv-imagery-mode"
                value={imageryMode}
                onChange={(event) => setImageryMode(event.target.value as ImageryMode)}
              >
                <option value="earth">Earth Imagery</option>
                <option value="none">No Earth</option>
              </select>
            </label>

            <div className="d3d-height-control d3d-height-control--dark">
              <div className="d3d-height-control__head">
                <span>Model Height</span>
                <strong>{modelHeightOffset} m</strong>
              </div>
              <input
                type="range"
                min={-800}
                max={200}
                step={1}
                value={modelHeightOffset}
                onChange={(event) => setModelOffset(Number(event.target.value))}
                aria-label="3D model height offset"
              />
              <div className="d3d-height-control__actions">
                <button type="button" onClick={() => setModelOffset(modelHeightOffset - 10)}>
                  -10m
                </button>
                <button type="button" onClick={() => void autoGroundModels()}>
                  Auto Ground
                </button>
                <button type="button" onClick={() => void saveModelHeightOffset()}>
                  Save Height
                </button>
                <button type="button" onClick={() => setModelOffset(modelHeightOffset + 10)}>
                  +10m
                </button>
              </div>
            </div>

            {activeModelLayers[0] ? (
              <p className="gv-panel__hint">Active model: {activeModelLayers[0].name}</p>
            ) : null}
          </>
        ) : (
          <>
            <p className="gv-panel__hint">Choose a point cloud from the 3D Data list above.</p>

            <label className="gv-field" htmlFor="gv-point-size">
              <span>Point Size</span>
              <input
                id="gv-point-size"
                type="range"
                min={1}
                max={12}
                step={1}
                value={pointSize}
                onChange={(event) => setPointSize(Number(event.target.value))}
              />
              <strong>{pointSize}px</strong>
            </label>

            <label className="gv-field" htmlFor="gv-color-mode">
              <span>Color Mode</span>
              <select
                id="gv-color-mode"
                value={colorMode}
                onChange={(event) => setColorMode(event.target.value as ColorMode)}
              >
                <option value="RGB">RGB</option>
                <option value="Elevation">Elevation</option>
              </select>
            </label>
          </>
        )}

        <div className="gv-camera-controls">
          <p className="gv-camera-controls__title">Camera Switch</p>
          <div className="gv-camera-grid">
            {(['top', 'front', 'back', 'left', 'right', 'home'] as const).map((preset) => (
              <button key={preset} type="button" onClick={() => flyToPreset(preset)}>
                {preset}
              </button>
            ))}
          </div>
          <label className="gv-field" htmlFor="gv-camera-view-list">
            <span>Saved Camera Points</span>
            <select
              id="gv-camera-view-list"
              value={selectedCameraViewId}
              onChange={(event) => setSelectedCameraViewId(event.target.value)}
            >
              {cameraViews.length > 0 ? (
                cameraViews.map((view) => (
                  <option key={view.id} value={view.id}>
                    {view.name}
                  </option>
                ))
              ) : (
                <option value="">No saved camera points</option>
              )}
            </select>
          </label>
          <div className="gv-camera-actions">
            <button type="button" onClick={() => void saveCurrentCamera()}>
              Save
            </button>
            <button
              type="button"
              disabled={!selectedCameraView}
              onClick={() => {
                if (selectedCameraView) flyToCameraView(selectedCameraView)
              }}
            >
              Go
            </button>
            <button type="button" disabled={!selectedCameraViewId} onClick={() => void deleteSelectedCamera()}>
              Delete
            </button>
          </div>
        </div>

        {activeCogLayers.length > 0 ? (
          <div className="gv-overlay-actions">
            {activeCogLayers.map((layer) => (
              <button
                key={layer.id}
                type="button"
                className="gv-action gv-action--ghost"
                onClick={() => loadOrthomosaic(layer)}
              >
                Load {layer.name}
              </button>
            ))}
          </div>
        ) : null}

        <div className="gv-tools-panel" aria-label="Cesium measurement tools">
          <div className="gv-tools-panel__head">
            <span>Tools</span>
            <strong>{formatMeasureDistance(measureDistanceM)}</strong>
          </div>
          <button
            type="button"
            className={distanceMeasureActive ? 'gv-tool-button gv-tool-button--active' : 'gv-tool-button'}
            onClick={() => setDistanceMeasureActive((active) => !active)}
          >
            Measure Distance
          </button>
          <button
            type="button"
            className={drawMode === 'point' ? 'gv-tool-button gv-tool-button--active' : 'gv-tool-button'}
            onClick={() => setDrawMode((mode) => (mode === 'point' ? 'none' : 'point'))}
          >
            Point
          </button>
          <button
            type="button"
            className={drawMode === 'polyline' ? 'gv-tool-button gv-tool-button--active' : 'gv-tool-button'}
            onClick={() => setDrawMode((mode) => (mode === 'polyline' ? 'none' : 'polyline'))}
          >
            Polyline
          </button>
          <button type="button" className="gv-tool-button" disabled={draftLineCount < 2} onClick={finishDraftLine}>
            Finish Line
          </button>
          <select
            className="gv-tool-select"
            value=""
            onChange={(event) => {
              if (event.target.value === 'kml') exportDrawingsKml()
              if (event.target.value === 'csv') exportDrawingsCsv()
              event.currentTarget.value = ''
            }}
            aria-label="Export drawings"
            disabled={drawnGeometries.length === 0}
          >
            <option value="">Export</option>
            <option value="kml">Export as KML</option>
            <option value="csv">Export as CSV</option>
          </select>
          <button type="button" className="gv-tool-button gv-tool-button--ghost" onClick={clearDistanceMeasurement}>
            Clear
          </button>
          <button type="button" className="gv-tool-button gv-tool-button--ghost" onClick={clearDrawings}>
            Clear Drawings
          </button>
        </div>
      </section>

      <div className="gv-nav-readout" aria-live="polite">
        <i className="fa-solid fa-location-crosshairs" aria-hidden />
        <span>{positionLabel}</span>
      </div>
    </div>
  )
}

export default GlobeViewer
