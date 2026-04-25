import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { getApiBaseUrl } from '../../lib/apiBase'
import {
  buildXyzTemplate,
  getTileBaseUrl,
  getTileExtension,
} from '../MapViewer/tileSources'
import './GlobeViewer.css'
import PointCloudUploader from './PointCloudUploader'

const CESIUM_ION_TOKEN = (import.meta.env.VITE_CESIUM_ION_TOKEN ?? '').trim()
const HAS_VALID_ION_TOKEN =
  CESIUM_ION_TOKEN.length > 0 && CESIUM_ION_TOKEN !== 'APNA_TOKEN_YAHAN_PASTE_KAREIN'

type ColorMode = 'RGB' | 'Elevation'

type GlobePosition = {
  lat: number | null
  lng: number | null
  elevation: number | null
}

type UploadedTileset = {
  label: string
  url: string
}

const TILESET_MAX_WAIT_MS = 2 * 60 * 60 * 1000
const TILESET_POLL_MS = 2000

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

async function waitForPointCloudTileset(tilesetUrl: string): Promise<void> {
  const start = Date.now()
  const apiOrigin = new URL(tilesetUrl).origin
  const projectId = projectIdFromTilesetUrl(tilesetUrl)

  while (Date.now() - start < TILESET_MAX_WAIT_MS) {
    if (projectId) {
      const res = await fetch(
        `${apiOrigin}/api/pointcloud-status/${encodeURIComponent(projectId)}`,
        { cache: 'no-store' },
      )
      if (res.ok) {
        const data = (await res.json()) as {
          ready?: boolean
          failed?: boolean
          error?: string
        }
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
      const res = await fetch(tilesetUrl, { method: 'HEAD', cache: 'no-store' })
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

export function GlobeViewer() {
  const tileRoot = useMemo(
    () => (getTileBaseUrl() ?? `${getApiBaseUrl()}/tiles`).replace(/\/+$/, ''),
    [],
  )
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Cesium.Viewer | null>(null)
  const pointCloudRef = useRef<Cesium.Cesium3DTileset | null>(null)
  const orthomosaicLayerRef = useRef<Cesium.ImageryLayer | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pointSize, setPointSize] = useState(3)
  const [colorMode, setColorMode] = useState<ColorMode>('RGB')
  const [uploadLabel, setUploadLabel] = useState('Upload LAS/LAZ')
  const [viewerError, setViewerError] = useState<string | null>(null)
  const [pipelineNotice, setPipelineNotice] = useState<string | null>(null)
  const [uploadedTilesets, setUploadedTilesets] = useState<UploadedTileset[]>([])
  const [selectedTilesetUrl, setSelectedTilesetUrl] = useState('')
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

  const loadPointCloud = useCallback(async (tilesetUrl: string) => {
    const viewer = viewerRef.current
    if (!viewer) {
      setViewerError('Viewer not ready. Please wait and try again.')
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

  const handleUploadComplete = useCallback(
    async (tilesetUrl: string, fileName: string) => {
      setUploadedTilesets((prev) => {
        const next = prev.filter((entry) => entry.url !== tilesetUrl)
        return [{ label: fileName, url: tilesetUrl }, ...next]
      })
      setSelectedTilesetUrl(tilesetUrl)
      await loadPointCloudWhenReady(tilesetUrl)
    },
    [loadPointCloudWhenReady],
  )

  const loadOrthomosaic = useCallback((tileUrl: string) => {
    const viewer = viewerRef.current
    if (!viewer) {
      setViewerError('Viewer not ready. Please wait and try again.')
      return
    }

    try {
      if (orthomosaicLayerRef.current) {
        viewer.imageryLayers.remove(orthomosaicLayerRef.current, true)
      }
      const layer = new Cesium.ImageryLayer(
        new Cesium.UrlTemplateImageryProvider({ url: tileUrl }),
      )
      viewer.imageryLayers.add(layer)
      orthomosaicLayerRef.current = layer
      setViewerError(null)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to load orthomosaic layer'
      setViewerError(message)
      console.error('Orthomosaic load failed:', error)
    }
  }, [])

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
    const host = containerRef.current
    if (!host) return
    if (viewerRef.current) return
    console.log('Cesium Viewer Initializing...')
    let handler: Cesium.ScreenSpaceEventHandler | null = null
    let viewer: Cesium.Viewer | null = null

    try {
      Cesium.Ion.defaultAccessToken = HAS_VALID_ION_TOKEN ? CESIUM_ION_TOKEN : ''
      viewer = new Cesium.Viewer(host, {
        animation: false,
        timeline: false,
        sceneModePicker: true,
        baseLayerPicker: HAS_VALID_ION_TOKEN,
        geocoder: false,
        homeButton: true,
        navigationHelpButton: true,
        infoBox: false,
        selectionIndicator: false,
        shouldAnimate: true,
      } as Cesium.Viewer.ConstructorOptions)
      viewerRef.current = viewer
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
      viewer.scene.globe.depthTestAgainstTerrain = false
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
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to initialize Cesium viewer'
      setViewerError(message)
      console.error('Cesium Viewer initialization failed:', error)
      if (viewer) {
        viewer.destroy()
        viewerRef.current = null
      }
    }

    return () => {
      handler?.destroy()
      if (pointCloudRef.current) {
        viewer?.scene.primitives.remove(pointCloudRef.current)
        pointCloudRef.current = null
      }
      if (orthomosaicLayerRef.current) {
        viewer?.imageryLayers.remove(orthomosaicLayerRef.current, true)
        orthomosaicLayerRef.current = null
      }
      viewer?.destroy()
      viewerRef.current = null
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

      <section className="gv-panel" aria-label="Point cloud controls">
        <h3 className="gv-panel__title">Point Cloud Controls</h3>
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

        <input
          ref={fileInputRef}
          type="file"
          accept=".las,.laz"
          className="gv-file-input"
          onChange={(event) => {
            const fileName = event.target.files?.[0]?.name
            setUploadLabel(fileName ?? 'Upload LAS/LAZ')
          }}
        />
        <button
          type="button"
          className="gv-upload"
          onClick={() => fileInputRef.current?.click()}
        >
          <i className="fa-solid fa-upload" aria-hidden />
          {uploadLabel}
        </button>

        <PointCloudUploader onUploadComplete={handleUploadComplete} />
      </section>

      <section className="d3d-layer-panel" aria-label="Data layer actions">
        <p className="d3d-layer-panel__title">3D Data Layers</p>

        {uploadedTilesets.length > 0 ? (
          <>
            <select
              className="d3d-layer-panel__select"
              value={selectedTilesetUrl}
              onChange={(event) => setSelectedTilesetUrl(event.target.value)}
              aria-label="Uploaded point cloud list"
            >
              {uploadedTilesets.map((item) => (
                <option key={item.url} value={item.url}>
                  {item.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="d3d-layer-panel__btn"
              onClick={() => {
                if (selectedTilesetUrl) {
                  void loadPointCloudWhenReady(selectedTilesetUrl)
                }
              }}
            >
              Show Uploaded Point Cloud
            </button>
          </>
        ) : (
          <p className="d3d-layer-panel__hint">Upload LAS/LAZ to see selectable point clouds.</p>
        )}

        <button
          type="button"
          className="d3d-layer-panel__btn"
          onClick={() => loadPointCloud(`${tileRoot}/pointclouds/sample/tileset.json`)}
        >
          Load Sample LAS Data
        </button>
        <button
          type="button"
          className="d3d-layer-panel__btn d3d-layer-panel__btn--ghost"
          onClick={() =>
            loadOrthomosaic(
              buildXyzTemplate(
                tileRoot,
                import.meta.env.VITE_S3_ORTHO_PREFIX?.trim() || 'orthomosaic',
                getTileExtension(),
              ),
            )
          }
        >
          Load Orthomosaic
        </button>
        <button
          type="button"
          className="d3d-layer-panel__btn d3d-layer-panel__btn--ghost"
          onClick={() =>
            loadOrthomosaic(
              buildXyzTemplate(
                tileRoot,
                import.meta.env.VITE_S3_DTM_PREFIX?.trim() || 'dtm',
                getTileExtension(),
              ),
            )
          }
        >
          Load DTM
        </button>
      </section>

      <div className="gv-nav-readout" aria-live="polite">
        <i className="fa-solid fa-location-crosshairs" aria-hidden />
        <span>{positionLabel}</span>
      </div>
    </div>
  )
}

export default GlobeViewer
