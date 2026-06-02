import { useCallback, useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import toast from 'react-hot-toast'
import 'leaflet/dist/leaflet.css'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'
import { hasStoredFileRef } from '../utils/storedFileRef'
import useDocumentViewer from '../hooks/useDocumentViewer'
import DocumentViewerOverlay from './shared/overlays/DocumentViewerOverlay'
import { SHAPE_COLORS } from './shared/map/constants'
import useOrthoBoundaryOverlay from './shared/map/useOrthoBoundaryOverlay'

function normalizeAcquisitionStage(properties = {}) {
  const stage = properties.acquisitionStage || properties.legalStatus || ''
  if (stage === 'Legal') return 'Notice 37(2) Distribution'
  if (stage === 'Illegal / Encroached') return 'Dispute'
  return stage
}

function acquisitionStageFillColor(stage) {
  const stageMap = {
    'Samarpan Received': '#8e44ad',
    'Field Survey': '#2980b9',
    'Owner Verification': '#f39c12',
    'Payment Processing': '#e67e22',
    'Land Possession': '#16a085',
    'Complete (Land Bank)': '#2ecc71',
    Dispute: '#e74c3c',
    'On Hold': '#7f8c8d',
  }
  return stageMap[stage] || '#0e3e49'
}

function propInt(v, fallback = 0) {
  if (v == null || v === '') return fallback
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function escapeTooltipHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function formatMeasuredArea(areaSqm, unit) {
  const sqm = Number(areaSqm) || 0
  switch (unit) {
    case 'sqkm':
      return `${(sqm / 1_000_000).toFixed(6)} sq km`
    case 'hectare':
      return `${(sqm / 10_000).toFixed(4)} ha`
    case 'acre':
      return `${(sqm / 4046.8564224).toFixed(4)} acre`
    case 'sqft':
      return `${(sqm * 10.7639).toFixed(2)} sq ft`
    case 'sqm':
    default:
      return `${sqm.toFixed(2)} sq m`
  }
}

function MapView({
  surveys,
  shapes,
  mapResetNonce = 0,
  isDrawingMode = false,
  activeDrawType = '',
  isAddMode = false,
  onMapClickForSurvey,
  onEditSurvey,
  onAssignShapeClick,
  onCapturedShapesChange,
  highlightedImportedIds = [],
  focusImportedNonce = 0,
  focusAssignedPropertyId = '',
  focusAssignedNonce = 0,
  showUnassignedShapes = false,
  showImportedShapes = true,
  isMeasureMode = false,
  measureUnit = 'sqft',
  measureResetNonce = 0,
  onMeasureCancel,
  onMeasureStartEdit,
  onDeleteSurvey,
  onShapeAdminEdit,
  draftPropertyIdForMap = '',
}) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const shapesLayerRef = useRef(null)
  const pointsLayerRef = useRef(null)
  const drawnItemsLayerRef = useRef(null)
  const draftPropertyIdRef = useRef('')
  const activeDrawTypeRef = useRef(activeDrawType)
  const isAddModeRef = useRef(isAddMode)
  const isMeasureModeRef = useRef(isMeasureMode)
  const measureUnitRef = useRef(measureUnit)
  const onMapClickForSurveyRef = useRef(onMapClickForSurvey)
  const onEditSurveyRef = useRef(onEditSurvey)
  const onMeasureCancelRef = useRef(onMeasureCancel)
  const onMeasureStartEditRef = useRef(onMeasureStartEdit)
  const measurePointsRef = useRef([])
  const measureLineRef = useRef(null)
  const measurePolyRef = useRef(null)
  const measureHistoryLayerRef = useRef(null)
  const editingMeasureIndexRef = useRef(null)
  const activePolygonDrawRef = useRef(null)
  const [capturedShapes, setCapturedShapes] = useState([])
  const [activePlot, setActivePlot] = useState(null)
  const { viewingDocUrl, viewingDocType, openDocument, closeDocument } = useDocumentViewer()
  const [measureAreaSqm, setMeasureAreaSqm] = useState(0)
  const [measureHistory, setMeasureHistory] = useState([])
  const [measurePointCount, setMeasurePointCount] = useState(0)
  const [editingMeasureIndex, setEditingMeasureIndex] = useState(null)

  useOrthoBoundaryOverlay(mapInstanceRef)

  useEffect(() => {
    editingMeasureIndexRef.current = editingMeasureIndex
  }, [editingMeasureIndex])

  useEffect(() => {
    setActivePlot((prev) => {
      if (!prev?.propertyId) return prev
      const features = surveys?.features || []
      const f = features.find(
        (x) =>
          String(x?.properties?.propertyId || '').trim() === String(prev.propertyId || '').trim(),
      )
      if (f?.properties) {
        const p = f.properties
        return {
          ...prev,
          ...p,
          lat: prev.lat,
          lng: prev.lng,
        }
      }
      return null
    })
  }, [surveys])

  const handleMarkerClick = (properties, latlng) => {
    setActivePlot({
      ...(properties || {}),
      lat: Number(latlng?.lat),
      lng: Number(latlng?.lng),
    })
    const map = mapInstanceRef.current
    if (!map || !latlng) return

    const propertyId = String(properties?.propertyId || '').trim()
    const mappedShapes = (Array.isArray(shapes) ? shapes : []).filter(
      (s) => String(s?.propertyId || '').trim() === propertyId,
    )

    let targetBounds = null
    mappedShapes.forEach((shape) => {
      let geojsonData = shape?.geoJson
      if (typeof geojsonData === 'string') {
        try {
          geojsonData = JSON.parse(geojsonData)
        } catch {
          geojsonData = null
        }
      }
      if (!geojsonData) return
      try {
        const b = L.geoJSON(geojsonData).getBounds()
        if (b?.isValid?.()) {
          if (!targetBounds) {
            targetBounds = b
          } else {
            targetBounds.extend(b)
          }
        }
      } catch {
        // ignore invalid geometry
      }
    })

    if (targetBounds?.isValid?.()) {
      const currentBounds = map.getBounds()
      const currentCenter = map.getCenter()
      const targetCenter = targetBounds.getCenter()
      const targetZoom = map.getBoundsZoom(targetBounds, true, [50, 50])
      const zoomDiff = Math.abs(map.getZoom() - targetZoom)
      const centerDistance = currentCenter.distanceTo(targetCenter)
      const alreadyVisible = currentBounds.contains(targetBounds)

      // Avoid earthquake-like camera movement when already near/in-view.
      if (alreadyVisible && zoomDiff < 0.75 && centerDistance < 40) {
        return
      }

      map.flyToBounds(targetBounds, {
        padding: [50, 50],
        maxZoom: 20,
        animate: true,
        duration: 0.9,
      })
      return
    }

    const currentCenter = map.getCenter()
    const centerDistance = currentCenter.distanceTo(latlng)
    const alreadyNear = centerDistance < 40 && map.getZoom() >= 18
    if (alreadyNear) return

    map.flyTo(latlng, Math.max(map.getZoom(), 18), { animate: true, duration: 0.8 })
  }

  const handleViewDocument = (ref) => {
    if (!openDocument(ref)) {
      toast.error('Could not open file. Data might be corrupted or missing.')
    }
  }

  useEffect(() => {
    activeDrawTypeRef.current = activeDrawType
  }, [activeDrawType])

  useEffect(() => {
    isAddModeRef.current = isAddMode
    const map = mapInstanceRef.current
    if (map?._container) {
      map._container.style.cursor = isMeasureMode ? 'crosshair' : isAddMode ? 'crosshair' : ''
    }
  }, [isAddMode, isMeasureMode])

  useEffect(() => {
    isMeasureModeRef.current = isMeasureMode
    const map = mapInstanceRef.current
    if (!map) return
    if (isMeasureMode) {
      map.doubleClickZoom.disable()
    } else {
      map.doubleClickZoom.enable()
    }
  }, [isMeasureMode])

  useEffect(() => {
    measureUnitRef.current = measureUnit
  }, [measureUnit])

  useEffect(() => {
    onMapClickForSurveyRef.current = onMapClickForSurvey
  }, [onMapClickForSurvey])

  useEffect(() => {
    onEditSurveyRef.current = onEditSurvey
  }, [onEditSurvey])

  useEffect(() => {
    draftPropertyIdRef.current = String(draftPropertyIdForMap || '').trim()
  }, [draftPropertyIdForMap])

  useEffect(() => {
    onMeasureCancelRef.current = onMeasureCancel
  }, [onMeasureCancel])

  useEffect(() => {
    onMeasureStartEditRef.current = onMeasureStartEdit
  }, [onMeasureStartEdit])

  useEffect(() => {
    if (onCapturedShapesChange) onCapturedShapesChange(capturedShapes)
  }, [capturedShapes, onCapturedShapesChange])

  useEffect(() => {
    if (mapResetNonce > 0) {
      if (drawnItemsLayerRef.current) {
        drawnItemsLayerRef.current.clearLayers()
      }
      setCapturedShapes([])
    }
  }, [mapResetNonce])

  useEffect(() => {
    const map = mapInstanceRef.current
    const pane = map?.getPane?.('shapesPane')
    if (!pane) return
    pane.style.pointerEvents = isDrawingMode && !isMeasureMode ? 'none' : 'auto'
  }, [isDrawingMode, isMeasureMode])

  const refreshDraftDrawTooltips = useCallback(() => {
    const g = drawnItemsLayerRef.current
    if (!g) return
    const label = draftPropertyIdRef.current || 'Set Plot ID in form'
    g.eachLayer((layer) => {
      if (!layer.getLatLngs) return
      const lid = L.stamp(layer)
      const found = capturedShapes.find((s) => s.leafletId === lid)
      const st = found?.structureType || activeDrawTypeRef.current || 'Open Space'
      layer.unbindTooltip()
      layer.bindTooltip(
        `<div style="text-align:center"><b>Draft · ${label}</b><br/>${st}<br/><span style="font-size:11px;opacity:.88">New boundary</span></div>`,
        { sticky: true, direction: 'center' },
      )
    })
  }, [capturedShapes])

  useEffect(() => {
    if (!isDrawingMode) return
    refreshDraftDrawTooltips()
  }, [isDrawingMode, draftPropertyIdForMap, activeDrawType, refreshDraftDrawTooltips])

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) {
      return undefined
    }

    const map = L.map(mapRef.current, {
      zoomControl: true,
      maxZoom: 25,
      zoomSnap: 0.1,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 120,
    }).setView([20.5937, 78.9629], 5)

    mapInstanceRef.current = map

    // Register BoundaryCanvas tile layer once on Leaflet namespace.
    if (!L.TileLayer.BoundaryCanvas) {
      L.TileLayer.BoundaryCanvas = L.TileLayer.extend({
        options: { boundary: null },
        createTile(coords, done) {
          const tile = document.createElement('canvas')
          const ctx = tile.getContext('2d')
          tile.width = tile.height = 256
          const img = new Image()
          img.onload = function onLoad() {
            ctx.drawImage(img, 0, 0)
            this._maskTile(tile, coords)
            done(null, tile)
          }.bind(this)
          img.src = this.getTileUrl(coords)
          return tile
        },
        _maskTile(tile, coords) {
          const ctx = tile.getContext('2d')
          const nw = map
            .project(map.unproject(coords.multiplyBy(256), coords.z), coords.z)
          ctx.globalCompositeOperation = 'destination-in'
          ctx.beginPath()
          const geom = this.options.boundary.features[0].geometry
          const rings = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates
          rings.forEach((ring) => {
            ring[0].forEach((p, i) => {
              const pt = map.project([p[1], p[0]], coords.z).subtract(nw)
              if (i === 0) {
                ctx.moveTo(pt.x, pt.y)
              } else {
                ctx.lineTo(pt.x, pt.y)
              }
            })
          })
          ctx.fill()
        },
      })

      L.TileLayer.boundaryCanvas = (url, options) =>
        new L.TileLayer.BoundaryCanvas(url, options)
    }

    L.tileLayer('http://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
      maxZoom: 25,
      maxNativeZoom: 20,
      subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    }).addTo(map)

    map.createPane('shapesPane')
    map.getPane('shapesPane').style.zIndex = 400
    map.createPane('draftDrawPane')
    map.getPane('draftDrawPane').style.zIndex = 450
    const pointsPane = map.createPane('pointsPane')
    pointsPane.style.zIndex = 650
    pointsPane.style.pointerEvents = 'auto'

    const drawnItems = L.featureGroup({ pane: 'draftDrawPane' }).addTo(map)
    drawnItemsLayerRef.current = drawnItems

    const drawControl = new L.Control.Draw({
      edit: { featureGroup: drawnItems, remove: false },
      draw: { marker: false, circle: false, circlemarker: false, polyline: false },
    })
    map.addControl(drawControl)

    map.on(L.Draw.Event.CREATED, (e) => {
      const layer = e.layer
      drawnItems.addLayer(layer)

      const areaSqm = L.GeometryUtil.geodesicArea(layer.getLatLngs()[0])
      const areaSqft = parseFloat((areaSqm * 10.7639).toFixed(2))
      const leafletId = L.stamp(layer)
      const structureType = activeDrawTypeRef.current || 'Open Space'
      const shapeColor = SHAPE_COLORS[structureType] || '#333'
      if (layer.setStyle) {
        layer.setStyle({ color: shapeColor, weight: 3, fillOpacity: 0.4 })
      }

      setCapturedShapes((prev) => [
        ...prev,
        {
          leafletId,
          structureType,
          geojson: layer.toGeoJSON(),
          areaSqft,
        },
      ])

      const draftLabel = draftPropertyIdRef.current || 'Set Plot ID in form'
      layer.bindTooltip(
        `<div style="text-align:center"><b>Draft · ${draftLabel}</b><br/>${structureType}<br/><span style="font-size:11px;opacity:.88">New boundary</span></div>`,
        { sticky: true, direction: 'center' },
      )

      // Stop polygon draw mode right after one shape creation to avoid sticky draw cursor.
      if (activePolygonDrawRef.current) {
        activePolygonDrawRef.current.disable()
        activePolygonDrawRef.current = null
      }
      if (map?._container) {
        map._container.style.cursor = isMeasureModeRef.current || isAddModeRef.current ? 'crosshair' : ''
      }
    })

    map.on(L.Draw.Event.EDITED, (e) => {
      const updatedShapes = []
      e.layers.eachLayer((layer) => {
        const leafletId = L.stamp(layer)
        let areaSqft = 0

        if (layer.getLatLngs) {
          const areaSqm = L.GeometryUtil.geodesicArea(layer.getLatLngs()[0])
          areaSqft = parseFloat((areaSqm * 10.7639).toFixed(2))
        }

        updatedShapes.push({
          leafletId,
          geojson: layer.toGeoJSON(),
          areaSqft,
        })
      })

      if (updatedShapes.length === 0) {
        return
      }

      setCapturedShapes((prev) =>
        prev.map((shape) => {
          const updated = updatedShapes.find((s) => s.leafletId === shape.leafletId)
          return updated ? { ...shape, ...updated, structureType: shape.structureType } : shape
        }),
      )
    })

    map.on(L.Draw.Event.DELETED, (e) => {
      const deletedIds = []
      e.layers.eachLayer((layer) => {
        deletedIds.push(L.stamp(layer))
      })

      if (deletedIds.length === 0) {
        return
      }

      setCapturedShapes((prev) => prev.filter((shape) => !deletedIds.includes(shape.leafletId)))
    })

    map.on('click', (e) => {
      if (isMeasureModeRef.current) {
        if (measurePointsRef.current.length === 0) {
          if (measureLineRef.current) measureLineRef.current.setLatLngs([])
          if (measurePolyRef.current) measurePolyRef.current.setLatLngs([])
          setMeasureAreaSqm(0)
        }
        measurePointsRef.current = [...measurePointsRef.current, e.latlng]
        setMeasurePointCount(measurePointsRef.current.length)
        const points = measurePointsRef.current
        if (measureLineRef.current) measureLineRef.current.setLatLngs(points)
        if (measurePolyRef.current && points.length >= 3) {
          measurePolyRef.current.setLatLngs([points])
          const areaSqm = L.GeometryUtil.geodesicArea(points)
          setMeasureAreaSqm(Number.isFinite(areaSqm) ? areaSqm : 0)
        }
        return
      }
      if (!isAddModeRef.current) return
      if (onMapClickForSurveyRef.current) {
        onMapClickForSurveyRef.current(e.latlng)
      }
    })

    map.on('mousemove', (e) => {
      if (!isMeasureModeRef.current) return
      const points = measurePointsRef.current
      if (points.length === 0) return
      const preview = [...points, e.latlng]
      if (measureLineRef.current) measureLineRef.current.setLatLngs(preview)
      if (measurePolyRef.current && preview.length >= 3) {
        measurePolyRef.current.setLatLngs([preview])
        const areaSqm = L.GeometryUtil.geodesicArea(preview)
        setMeasureAreaSqm(Number.isFinite(areaSqm) ? areaSqm : 0)
      }
    })

    map.on('dblclick', (e) => {
      if (!isMeasureModeRef.current) return
      const points = [...measurePointsRef.current]
      const last = points[points.length - 1]
      if (e?.latlng && (!last || last.distanceTo(e.latlng) > 0.25)) {
        points.push(e.latlng)
      }
      measurePointsRef.current = points
      if (measureLineRef.current) measureLineRef.current.setLatLngs(points)
      if (measurePolyRef.current && points.length >= 3) {
        measurePolyRef.current.setLatLngs([points])
        const areaSqm = L.GeometryUtil.geodesicArea(points)
        const finalArea = Number.isFinite(areaSqm) ? areaSqm : 0
        setMeasureAreaSqm(finalArea)
        if (finalArea > 0) {
          const finalPoints = points.map((p) => ({ lat: Number(p.lat), lng: Number(p.lng) }))
          setMeasureHistory((prev) => {
            const editIdx = editingMeasureIndexRef.current
            if (editIdx !== null && prev[editIdx]) {
              return prev.map((m, idx) =>
                idx === editIdx ? { areaSqm: finalArea, points: finalPoints } : m,
              )
            }
            return [...prev, { areaSqm: finalArea, points: finalPoints }]
          })
        }
      }
      setEditingMeasureIndex(null)
      setMeasurePointCount(0)
      // Finish current measurement on double-click like Google Earth
      isMeasureModeRef.current = false
      onMeasureCancelRef.current?.()
    })

    shapesLayerRef.current = L.featureGroup().addTo(map)
    pointsLayerRef.current = L.featureGroup().addTo(map)
    measureHistoryLayerRef.current = L.featureGroup().addTo(map)
    measureLineRef.current = L.polyline([], {
      color: '#f1c40f',
      weight: 3,
      dashArray: '6,6',
      pane: 'pointsPane',
    }).addTo(map)
    measurePolyRef.current = L.polygon([], {
      color: '#f39c12',
      weight: 2,
      fillColor: '#f1c40f',
      fillOpacity: 0.2,
      pane: 'shapesPane',
    }).addTo(map)

    fetch('/ortho_data/Boundary.json')
      .then((res) => {
        if (!res.ok) {
          throw new Error('Boundary data not found')
        }
        return res.json()
      })
      .then((geojsonData) => {
        const boundaryLayer = L.geoJSON(geojsonData, {
          style: { color: '#00d2ff', weight: 2, fillOpacity: 0, dashArray: '5, 10' },
        })
        const maskedOrtho = L.TileLayer.boundaryCanvas('/ortho_data/tiles/{z}/{x}/{y}.png', {
          boundary: geojsonData,
          maxNativeZoom: 21,
          maxZoom: 25,
        })
        const overlays = {
          "<span style='color: #00d2ff; font-weight: bold;'><i class='fas fa-layer-group'></i> Ortho Overlay</span>":
            maskedOrtho,
        }
        L.control.layers(null, overlays, { collapsed: false, position: 'topright' }).addTo(map)
        maskedOrtho.addTo(map)
        map.fitBounds(boundaryLayer.getBounds(), { padding: [50, 50], maxZoom: 18 })
      })
      .catch((err) => {
        console.log('Ortho data not found. Upload tiles in /ortho_data/ folder.', err.message)
      })

    const t = setTimeout(() => {
      if (mapRef.current) {
        map.invalidateSize()
      }
    }, 300)

    let resizeObserver
    if (typeof ResizeObserver !== 'undefined' && mapRef.current) {
      const el = mapRef.current
      resizeObserver = new ResizeObserver(() => {
        map.invalidateSize()
      })
      resizeObserver.observe(el)
    }

    return () => {
      clearTimeout(t)
      resizeObserver?.disconnect()
      drawnItemsLayerRef.current = null
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove()
        mapInstanceRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!measureResetNonce) return
    measurePointsRef.current = []
    if (measureLineRef.current) measureLineRef.current.setLatLngs([])
    if (measurePolyRef.current) measurePolyRef.current.setLatLngs([])
    setMeasureAreaSqm(0)
    setMeasureHistory([])
    setMeasurePointCount(0)
    setEditingMeasureIndex(null)
  }, [measureResetNonce])

  useEffect(() => {
    const handleEsc = (event) => {
      if (event.key !== 'Escape') return
      if (!isMeasureModeRef.current) return
      measurePointsRef.current = []
      if (measureLineRef.current) measureLineRef.current.setLatLngs([])
      if (measurePolyRef.current) measurePolyRef.current.setLatLngs([])
      const lastArea = measureHistory.length > 0 ? measureHistory[measureHistory.length - 1].areaSqm : 0
      setMeasureAreaSqm(lastArea)
      setMeasurePointCount(0)
      setEditingMeasureIndex(null)
      isMeasureModeRef.current = false
      onMeasureCancelRef.current?.()
    }
    window.addEventListener('keydown', handleEsc)
    return () => window.removeEventListener('keydown', handleEsc)
  }, [measureHistory])

  const handleEditSavedMeasurement = (index) => {
    const item = measureHistory[index]
    if (!item || !Array.isArray(item.points) || item.points.length < 3) return
    const points = item.points.map((p) => L.latLng(Number(p.lat), Number(p.lng)))
    measurePointsRef.current = points
    if (measureLineRef.current) measureLineRef.current.setLatLngs(points)
    if (measurePolyRef.current) measurePolyRef.current.setLatLngs([points])
    setMeasureAreaSqm(Number(item.areaSqm || 0))
    setMeasurePointCount(points.length)
    setEditingMeasureIndex(index)
    isMeasureModeRef.current = true
    onMeasureStartEditRef.current?.()
  }

  useEffect(() => {
    const map = mapInstanceRef.current
    const historyLayer = measureHistoryLayerRef.current
    if (!map || !historyLayer) return

    historyLayer.clearLayers()
    measureHistory.forEach((item, idx) => {
      if (!Array.isArray(item?.points) || item.points.length < 3) return
      const latlngs = item.points.map((p) => [Number(p.lat), Number(p.lng)])
      const isEditing = editingMeasureIndex === idx
      L.polygon(latlngs, {
        pane: 'shapesPane',
        color: isEditing ? '#f39c12' : '#f1c40f',
        weight: isEditing ? 3 : 2,
        fillColor: isEditing ? '#f39c12' : '#f1c40f',
        fillOpacity: isEditing ? 0.3 : 0.18,
        dashArray: isEditing ? undefined : '4,4',
      })
        .bindTooltip(
          `${idx + 1}. ${formatMeasuredArea(Number(item.areaSqm || 0), measureUnit)}`,
          { permanent: false, direction: 'center' },
        )
        .addTo(historyLayer)
    })
  }, [measureHistory, editingMeasureIndex, measureUnit])

  const handleStartNewMeasurement = () => {
    measurePointsRef.current = []
    if (measureLineRef.current) measureLineRef.current.setLatLngs([])
    if (measurePolyRef.current) measurePolyRef.current.setLatLngs([])
    const lastArea = measureHistory.length > 0 ? Number(measureHistory[measureHistory.length - 1].areaSqm || 0) : 0
    setMeasureAreaSqm(lastArea)
    setMeasurePointCount(0)
    setEditingMeasureIndex(null)
    isMeasureModeRef.current = true
    onMeasureStartEditRef.current?.()
  }

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !isDrawingMode || !activeDrawType) return

    setTimeout(() => {
      const handler = new L.Draw.Polygon(map)
      activePolygonDrawRef.current = handler
      handler.enable()
    }, 80)
  }, [isDrawingMode, activeDrawType])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !shapesLayerRef.current || !pointsLayerRef.current) {
      return
    }

    shapesLayerRef.current.clearLayers()
    pointsLayerRef.current.clearLayers()

    ;(Array.isArray(shapes) ? shapes : []).forEach((shape) => {
      let geojsonData = shape?.geoJson
      if (typeof geojsonData === 'string') {
        try {
          geojsonData = JSON.parse(geojsonData)
        } catch {
          geojsonData = null
        }
      }
      if (!geojsonData) {
        return
      }

      const isUnassigned =
        shape?.structureType === 'Unassigned' || String(shape?.propertyId || '').startsWith('PENDING_')
      const isHighlightedImport = highlightedImportedIds.includes(shape?.propertyId)
      if (isHighlightedImport && !showImportedShapes) return
      if (!isHighlightedImport && isUnassigned && !showUnassignedShapes) return

      const color = SHAPE_COLORS[shape.structureType] || '#333'
      const shapeSnapshot = shape
      const rowPid = String(shapeSnapshot?.propertyId ?? 'N/A')
      const rowSt = String(shapeSnapshot?.structureType ?? '')

      const layer = L.geoJSON(geojsonData, {
        pane: 'shapesPane',
        style: isHighlightedImport
          ? {
              fillColor: '#ffeb3b',
              color: '#f1c40f',
              weight: 4,
              fillOpacity: 0.7,
              dashArray: '3, 2',
            }
          : isUnassigned
            ? {
                fillColor: '#95a5a6',
                color: '#7f8c8d',
                weight: 2,
                dashArray: '5, 5',
                fillOpacity: 0.5,
              }
            : { fillColor: color, color, weight: 3, fillOpacity: 0.4 },
        onEachFeature: (feature, lyr) => {
          const fp = feature?.properties || {}
          const fromGeoPid = fp.propertyId != null && String(fp.propertyId).trim() !== '' ? String(fp.propertyId).trim() : ''
          const fromGeoSt =
            fp.structureType != null && String(fp.structureType).trim() !== '' ? String(fp.structureType).trim() : ''
          const tipPid = escapeTooltipHtml(rowPid || fromGeoPid)
          const tipSt = escapeTooltipHtml(rowSt || fromGeoSt)

          if (isUnassigned) {
            lyr.on('click', () => {
              if (onAssignShapeClick) {
                onAssignShapeClick(shapeSnapshot)
              }
            })
          } else {
            lyr.on('click', (e) => {
              L.DomEvent.stopPropagation(e)
              if (typeof onShapeAdminEdit === 'function') {
                onShapeAdminEdit(shapeSnapshot)
              }
            })
            lyr.bindTooltip(
              `<div style="text-align:center"><b>${tipPid}</b><br/>${tipSt}<br/><span style="font-size:11px;opacity:.88">Click to edit / delete</span></div>`,
              { sticky: true, direction: 'center' },
            )
          }
        },
      })
      layer.__shapePropertyId = shape?.propertyId || ''
      layer.addTo(shapesLayerRef.current)
    })

    const surveyLayer = L.geoJSON(surveys || { type: 'FeatureCollection', features: [] }, {
      pane: 'pointsPane',
      pointToLayer: (feature, latlng) => {
        const stage = normalizeAcquisitionStage(feature?.properties || {})
        const color = acquisitionStageFillColor(stage)
        return L.circleMarker(latlng, {
          pane: 'markerPane',
          radius: 8,
          fillColor: color,
          color: '#fff',
          weight: 2,
          fillOpacity: 0.9,
        })
      },
      onEachFeature: (feature, layer) => {
        const p = feature.properties || {}
        const plotShapes = (Array.isArray(shapes) ? shapes : []).filter(
          (s) => String(s?.propertyId || '').trim() === String(p?.propertyId || '').trim(),
        )

        if (plotShapes.length > 0) {
          // keep for hover behavior parity; details are shown in side card
        }

        layer.on('mouseover', function onHover() {
          this.setStyle({ radius: 11, weight: 3 })
        })
        layer.on('mouseout', function onOut() {
          this.setStyle({ radius: 8, weight: 2 })
        })
        layer.on('click', function onClick() {
          const target = this.getLatLng()
          handleMarkerClick(p, target)
        })
      },
    }).addTo(pointsLayerRef.current)

    if (surveyLayer) {
      surveyLayer.bringToFront()
    }

    pointsLayerRef.current.eachLayer((layer) => {
      if (layer.bringToFront) {
        layer.bringToFront()
      }
    })

  }, [
    surveys,
    shapes,
    onAssignShapeClick,
    onShapeAdminEdit,
    highlightedImportedIds,
    showImportedShapes,
    showUnassignedShapes,
  ])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !shapesLayerRef.current || highlightedImportedIds.length === 0) return

    let importedBounds = null
    shapesLayerRef.current.eachLayer((layer) => {
      if (!layer.getBounds) return
      const featurePropId = layer.__shapePropertyId
      if (!featurePropId || !highlightedImportedIds.includes(featurePropId)) return
      const b = layer.getBounds()
      importedBounds = importedBounds ? importedBounds.extend(b) : b
    })

    if (importedBounds && importedBounds.isValid()) {
      map.fitBounds(importedBounds, { padding: [40, 40], maxZoom: 20 })
    }
  }, [focusImportedNonce, highlightedImportedIds, shapes])

  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !focusAssignedPropertyId || !focusAssignedNonce) return

    const features = surveys?.features || []
    const targetFeature = features.find(
      (f) => String(f?.properties?.propertyId || '') === String(focusAssignedPropertyId),
    )
    const coords = targetFeature?.geometry?.coordinates
    if (!targetFeature || !Array.isArray(coords) || coords.length < 2) return

    const latlng = L.latLng(Number(coords[1]), Number(coords[0]))
    if (!Number.isFinite(latlng.lat) || !Number.isFinite(latlng.lng)) return
    handleMarkerClick(targetFeature.properties || {}, latlng)
  }, [focusAssignedNonce, focusAssignedPropertyId, surveys])

  const documentRows = [
    { label: 'Samarpan receipt', key: 'samarpanFileB64' },
    { label: 'Aadhar', key: 'aadharFileB64' },
    { label: 'PAN', key: 'panFileB64' },
    { label: 'Bank details', key: 'bankFileB64' },
    { label: 'Owner verification', key: 'ownerVerifFileB64' },
    { label: 'Field survey', key: 'surveyFileB64' },
  ]

  return (
    <div
      className="map-view-root"
      style={{
        position: 'relative',
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        margin: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        id="map"
        ref={mapRef}
        style={{
          flex: 1,
          width: '100%',
          minHeight: 0,
          margin: 0,
          padding: 0,
          border: 'none',
          outline: 'none',
          zIndex: 1,
        }}
      ></div>
      {activePlot && (
        <div
          style={{
            position: 'absolute',
            bottom: '20px',
            left: '20px',
            zIndex: 1000,
            width: '320px',
            maxHeight: '75vh',
            overflowY: 'auto',
            backgroundColor: 'white',
            borderRadius: '8px',
            padding: '15px',
            boxShadow: '0 4px 15px rgba(0,0,0,0.2)',
          }}
        >
          <button
            type="button"
            onClick={() => setActivePlot(null)}
            style={{
              position: 'absolute',
              top: '8px',
              right: '10px',
              border: 'none',
              background: 'transparent',
              fontSize: '16px',
              cursor: 'pointer',
            }}
          >
            ×
          </button>
          {hasStoredFileRef(activePlot.photoB64) && (
            <div style={{ marginBottom: '10px' }}>
              <img
                src={activePlot.photoB64}
                alt="Site"
                style={{
                  width: '100%',
                  height: '140px',
                  objectFit: 'cover',
                  borderRadius: '6px',
                  border: '1px solid #d8e2e6',
                  cursor: 'pointer',
                  background: '#f3f6f8',
                }}
                onClick={() => handleViewDocument(activePlot.photoB64)}
              />
            </div>
          )}
          <h3 style={{ marginTop: 0, color: '#0e3e49' }}>Plot: {activePlot.propertyId || '-'}</h3>
          <div className="popup-row">
            <b>Owner:</b> <span>{activePlot.ownerName || '-'}</span>
          </div>
          <div className="popup-row">
            <b>Structure:</b> <span>{activePlot.structureType || '-'}</span>
          </div>
          <div className="popup-row">
            <b>Area:</b> <span>{activePlot.areaSqft || 0} SqFt</span>
          </div>
          <div className="popup-row">
            <b>Compensation:</b>{' '}
            <span>₹ {Number(activePlot.moneyDistributed || 0).toLocaleString('en-IN')}</span>
          </div>
          <div className="popup-row">
            <b>Trees:</b> <span>{propInt(activePlot.numberOfTrees, 0)}</span>
          </div>

          <div style={{ marginTop: '10px', fontWeight: 700, color: '#0e3e49' }}>
            Documents
          </div>
          {documentRows.map((doc) => (
            <div key={doc.key} className="popup-row" style={{ alignItems: 'center', gap: '8px' }}>
              <b>{doc.label}</b>
              {hasStoredFileRef(activePlot[doc.key]) ? (
                <span>
                  ✅ Uploaded{' '}
                  <button
                    type="button"
                    className="btn-tool"
                    style={{ padding: '4px 8px', marginLeft: '6px' }}
                    onClick={() => handleViewDocument(activePlot[doc.key])}
                  >
                    View File
                  </button>
                </span>
              ) : (
                <span>❌ Not Uploaded</span>
              )}
            </div>
          ))}

          <button
            type="button"
            className="btn-tool"
            style={{ width: '100%', marginTop: '10px', background: '#e07a5f' }}
            onClick={() => onEditSurveyRef.current && onEditSurveyRef.current(activePlot)}
          >
            <i className="fas fa-edit"></i> Edit Survey
          </button>
          {typeof onDeleteSurvey === 'function' && activePlot.dbId ? (
            <button
              type="button"
              className="btn-tool"
              style={{ width: '100%', marginTop: '8px', background: '#c0392b', color: '#fff' }}
              onClick={() => onDeleteSurvey(activePlot)}
            >
              <i className="fas fa-trash-alt"></i> Delete survey record
            </button>
          ) : null}
        </div>
      )}
      {(isMeasureMode || measureHistory.length > 0) && (
        <div
          style={{
            position: 'absolute',
            top: '16px',
            left: '16px',
            zIndex: 1200,
            background: 'rgba(14,62,73,0.92)',
            color: '#fff',
            borderRadius: '8px',
            padding: '10px 12px',
            minWidth: '220px',
            boxShadow: '0 6px 16px rgba(0,0,0,0.25)',
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: '4px' }}>
            <i className="fas fa-ruler-combined"></i> Live Area Measure
          </div>
          <div style={{ fontSize: '0.88rem', opacity: 0.95 }}>
            {isMeasureMode && measurePointCount >= 3
              ? `Current: ${formatMeasuredArea(measureAreaSqm, measureUnit)}`
              : `Last: ${formatMeasuredArea(measureAreaSqm, measureUnit)}`}
          </div>
          {measureHistory.length > 0 && (
            <div
              style={{
                marginTop: '8px',
                borderTop: '1px solid rgba(255,255,255,0.25)',
                paddingTop: '6px',
                maxHeight: '160px',
                overflowY: 'auto',
              }}
            >
              <div style={{ fontSize: '0.78rem', fontWeight: 700, marginBottom: '4px' }}>
                Saved Measurements
              </div>
              {measureHistory.map((m, idx) => (
                <button
                  key={`${m.areaSqm}-${idx}`}
                  type="button"
                  onClick={() => handleEditSavedMeasurement(idx)}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    fontSize: '0.78rem',
                    marginBottom: '3px',
                    border: '1px solid rgba(255,255,255,0.25)',
                    borderRadius: '4px',
                    background: editingMeasureIndex === idx ? 'rgba(241, 196, 15, 0.35)' : 'rgba(255,255,255,0.08)',
                    color: '#fff',
                    padding: '5px 6px',
                    cursor: 'pointer',
                  }}
                >
                  {idx + 1}. {formatMeasuredArea(m.areaSqm, measureUnit)}
                  {editingMeasureIndex === idx ? ' (editing)' : ''}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
            <button
              type="button"
              onClick={handleStartNewMeasurement}
              style={{
                border: '1px solid rgba(255,255,255,0.35)',
                borderRadius: '4px',
                background: 'rgba(241, 196, 15, 0.25)',
                color: '#fff',
                padding: '5px 8px',
                cursor: 'pointer',
                fontSize: '0.78rem',
                fontWeight: 700,
              }}
            >
              <i className="fas fa-plus"></i> New Measure
            </button>
            {editingMeasureIndex !== null && (
              <button
                type="button"
                onClick={() => {
                  setEditingMeasureIndex(null)
                  setMeasurePointCount(0)
                  measurePointsRef.current = []
                  if (measureLineRef.current) measureLineRef.current.setLatLngs([])
                  if (measurePolyRef.current) measurePolyRef.current.setLatLngs([])
                  isMeasureModeRef.current = false
                  onMeasureCancelRef.current?.()
                }}
                style={{
                  border: '1px solid rgba(255,255,255,0.35)',
                  borderRadius: '4px',
                  background: 'rgba(220, 38, 38, 0.35)',
                  color: '#fff',
                  padding: '5px 8px',
                  cursor: 'pointer',
                  fontSize: '0.78rem',
                  fontWeight: 700,
                }}
              >
                <i className="fas fa-times"></i> Exit Edit
              </button>
            )}
          </div>
          {isMeasureMode && (
            <div style={{ fontSize: '0.76rem', opacity: 0.85, marginTop: '6px' }}>
              Click map to add points, move mouse for live preview, double click to finish.
            </div>
          )}
        </div>
      )}
      <DocumentViewerOverlay
        isOpen={Boolean(viewingDocUrl)}
        url={viewingDocUrl}
        contentType={viewingDocType}
        onClose={closeDocument}
        backdropClass="owners-modal-backdrop"
        cardClass="owners-modal-card"
        cardStyle={{ width: 'min(1100px, 96vw)', height: '90vh', padding: 0 }}
        bodyStyle={{ height: 'calc(90vh - 48px)' }}
      />
    </div>
  )
}

export default MapView
