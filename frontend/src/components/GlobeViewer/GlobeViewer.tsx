import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import './GlobeViewer.css'

const CESIUM_ION_TOKEN = (import.meta.env.VITE_CESIUM_ION_TOKEN ?? '').trim()
const HAS_VALID_ION_TOKEN =
  CESIUM_ION_TOKEN.length > 0 && CESIUM_ION_TOKEN !== 'APNA_TOKEN_YAHAN_PASTE_KAREIN'

type ColorMode = 'RGB' | 'Elevation'

type GlobePosition = {
  lat: number | null
  lng: number | null
  elevation: number | null
}

export function GlobeViewer() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Cesium.Viewer | null>(null)
  const pointCloudRef = useRef<Cesium.Cesium3DTileset | null>(null)
  const orthomosaicLayerRef = useRef<Cesium.ImageryLayer | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pointSize, setPointSize] = useState(3)
  const [colorMode, setColorMode] = useState<ColorMode>('RGB')
  const [uploadLabel, setUploadLabel] = useState('Upload LAS/LAZ')
  const [viewerError, setViewerError] = useState<string | null>(null)
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
      viewer.scene.primitives.add(tileset)
      pointCloudRef.current = tileset
      await viewer.zoomTo(tileset)
      setViewerError(null)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to load point cloud tileset'
      setViewerError(message)
      console.error('Point cloud load failed:', error)
    }
  }, [])

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
      </section>

      <section className="d3d-layer-panel" aria-label="Data layer actions">
        <p className="d3d-layer-panel__title">3D Data Layers</p>
        <button
          type="button"
          className="d3d-layer-panel__btn"
          onClick={() =>
            loadPointCloud('http://localhost:8000/tiles/pointclouds/sample/tileset.json')
          }
        >
          Load Sample LAS Data
        </button>
        <button
          type="button"
          className="d3d-layer-panel__btn d3d-layer-panel__btn--ghost"
          onClick={() =>
            loadOrthomosaic('http://localhost:8000/tiles/orthomosaic/{z}/{x}/{y}.png')
          }
        >
          Load Orthomosaic
        </button>
        <button
          type="button"
          className="d3d-layer-panel__btn d3d-layer-panel__btn--ghost"
          onClick={() =>
            loadOrthomosaic('http://localhost:8000/tiles/dtm/{z}/{x}/{y}.png')
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
