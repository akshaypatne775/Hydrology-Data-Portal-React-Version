import { useEffect, useMemo, useRef, useState } from 'react'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import './GlobeViewer.css'

Cesium.Ion.defaultAccessToken = ''

type ColorMode = 'RGB' | 'Elevation'

type GlobePosition = {
  lat: number | null
  lng: number | null
  elevation: number | null
}

const TILE_ROOT = 'http://localhost:8000/tiles'

export function GlobeViewer() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<Cesium.Viewer | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pointSize, setPointSize] = useState(3)
  const [colorMode, setColorMode] = useState<ColorMode>('RGB')
  const [uploadLabel, setUploadLabel] = useState('Upload LAS/LAZ')
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

  useEffect(() => {
    const host = containerRef.current
    if (!host) return
    const terrainUrl = `${TILE_ROOT}/terrain`
    const hasTerrain = Boolean(terrainUrl)

    const viewer = new Cesium.Viewer(host, {
      animation: false,
      timeline: false,
      sceneModePicker: true,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: true,
      navigationHelpButton: true,
      infoBox: false,
      selectionIndicator: false,
      shouldAnimate: true,
      imageryProvider: false,
    } as Cesium.Viewer.ConstructorOptions)
    viewerRef.current = viewer
    viewer.imageryLayers.removeAll()
    viewer.imageryLayers.addImageryProvider(
      new Cesium.OpenStreetMapImageryProvider({
        url: 'https://a.tile.openstreetmap.org/',
      }),
    )

    if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = true
    if (viewer.scene.sun) viewer.scene.sun.show = false
    if (viewer.scene.moon) viewer.scene.moon.show = true
    viewer.scene.skyBox = Cesium.SkyBox.createEarthSkyBox()
    viewer.scene.globe.enableLighting = false
    viewer.scene.highDynamicRange = false
    viewer.scene.backgroundColor = Cesium.Color.BLACK

    if (hasTerrain && Cesium.CesiumTerrainProvider?.fromUrl) {
      void Cesium.CesiumTerrainProvider.fromUrl(terrainUrl)
        .then((terrainProvider: Cesium.TerrainProvider) => {
          viewer.terrainProvider = terrainProvider
        })
        .catch(() => {
          // Local terrain is optional; globe still works on ellipsoid fallback.
        })
    }

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas)
    handler.setInputAction((movement: Cesium.ScreenSpaceEventHandler.MotionEvent) => {
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

    return () => {
      handler.destroy()
      viewerRef.current?.destroy()
      viewerRef.current = null
    }
  }, [])

  return (
    <div className="gv-root d3d-viewer-wrapper">
      <div className="gv-canvas" ref={containerRef} />

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

      <div className="gv-nav-readout" aria-live="polite">
        <i className="fa-solid fa-location-crosshairs" aria-hidden />
        <span>{positionLabel}</span>
      </div>
    </div>
  )
}

export default GlobeViewer
