import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import toast from 'react-hot-toast'
import 'leaflet/dist/leaflet.css'
import 'leaflet-draw'
import 'leaflet-draw/dist/leaflet.draw.css'
import { SHAPE_COLORS, toSqftFromSqm } from './shared/map/constants'
import useOrthoBoundaryOverlay from './shared/map/useOrthoBoundaryOverlay'

function FieldMapModal({
  isOpen,
  selectedStructures,
  onClose,
  onDone,
}) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const drawnItemsRef = useRef(null)
  const [activeStructure, setActiveStructure] = useState('')
  const [capturedShapes, setCapturedShapes] = useState([])
  const [liveLocation, setLiveLocation] = useState(null)
  const [spoofLocation, setSpoofLocation] = useState(null)
  const [isSpoofing, setIsSpoofing] = useState(false)
  const activeStructureRef = useRef('')
  const isSpoofingRef = useRef(false)

  useEffect(() => {
    if (!selectedStructures?.length) {
      setActiveStructure('')
      activeStructureRef.current = ''
      return
    }
    setActiveStructure(selectedStructures[0])
    activeStructureRef.current = selectedStructures[0]
  }, [selectedStructures])

  useOrthoBoundaryOverlay(mapInstanceRef)

  useEffect(() => {
    activeStructureRef.current = activeStructure
  }, [activeStructure])

  useEffect(() => {
    isSpoofingRef.current = isSpoofing
  }, [isSpoofing])

  useEffect(() => {
    if (!isOpen || !mapRef.current || mapInstanceRef.current) return undefined

    const map = L.map(mapRef.current, { maxZoom: 25 }).setView([20.5937, 78.9629], 5)
    mapInstanceRef.current = map
    const pointsPane = map.createPane('pointsPane')
    pointsPane.style.zIndex = 650
    pointsPane.style.pointerEvents = 'auto'
    L.tileLayer('http://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
      maxZoom: 25,
      maxNativeZoom: 20,
      subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    }).addTo(map)

    const drawnItems = L.featureGroup().addTo(map)
    drawnItemsRef.current = drawnItems
    const drawControl = new L.Control.Draw({
      edit: { featureGroup: drawnItems, remove: false },
      draw: { marker: false, circle: false, circlemarker: false, polyline: false },
    })
    map.addControl(drawControl)

    let liveMarker = null
    let liveCircle = null

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
          const nw = map.project(map.unproject(coords.multiplyBy(256), coords.z), coords.z)
          ctx.globalCompositeOperation = 'destination-in'
          ctx.beginPath()
          const geom = this.options.boundary.features[0].geometry
          const rings = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates
          rings.forEach((ring) => {
            ring[0].forEach((p, i) => {
              const pt = map.project([p[1], p[0]], coords.z).subtract(nw)
              if (i === 0) ctx.moveTo(pt.x, pt.y)
              else ctx.lineTo(pt.x, pt.y)
            })
          })
          ctx.fill()
        },
      })

      L.TileLayer.boundaryCanvas = (url, options) =>
        new L.TileLayer.BoundaryCanvas(url, options)
    }

    fetch('/ortho_data/Boundary.json')
      .then((res) => {
        if (!res.ok) throw new Error('Boundary data not found')
        return res.json()
      })
      .then((geojsonData) => {
        L.TileLayer.boundaryCanvas('/ortho_data/tiles/{z}/{x}/{y}.png', {
          boundary: geojsonData,
          maxNativeZoom: 21,
          maxZoom: 25,
        }).addTo(map)
        const boundaryLayer = L.geoJSON(geojsonData)
        map.fitBounds(boundaryLayer.getBounds(), { padding: [20, 20], maxZoom: 18 })
      })
      .catch(() => {})

    map.locate({ setView: true, maxZoom: 19, enableHighAccuracy: true, watch: true })
    map.on('locationfound', (e) => {
      if (spoofLocation) return
      setLiveLocation({ lat: e.latlng.lat, lng: e.latlng.lng })
      const radius = e.accuracy / 2
      if (liveMarker) {
        liveMarker.setLatLng(e.latlng)
        if (liveCircle) {
          liveCircle.setLatLng(e.latlng)
          liveCircle.setRadius(radius)
        }
      } else {
        liveMarker = L.circleMarker(e.latlng, {
          pane: 'pointsPane',
          radius: 6,
          fillColor: '#00d2ff',
          color: '#fff',
          weight: 2,
          fillOpacity: 0.9,
        }).addTo(map)
        liveCircle = L.circle(e.latlng, radius, {
          color: '#00d2ff',
          fillOpacity: 0.1,
          weight: 1,
        }).addTo(map)
      }
    })

    map.on('click', (e) => {
      if (!isSpoofingRef.current) return
      const point = { lat: e.latlng.lat, lng: e.latlng.lng }
      map.stopLocate()
      setSpoofLocation(point)
      if (liveMarker) {
        liveMarker.setLatLng(e.latlng)
        liveMarker.setStyle({ fillColor: '#e07a5f' })
      } else {
        liveMarker = L.circleMarker(e.latlng, {
          pane: 'pointsPane',
          radius: 6,
          fillColor: '#e07a5f',
          color: '#fff',
          weight: 2,
          fillOpacity: 0.9,
        }).addTo(map)
      }
      if (liveCircle) {
        liveCircle.setLatLng(e.latlng)
        liveCircle.setRadius(5)
        liveCircle.setStyle({ color: '#e07a5f', fillOpacity: 0.2, weight: 1 })
      } else {
        liveCircle = L.circle(e.latlng, 5, {
          color: '#e07a5f',
          fillOpacity: 0.2,
          weight: 1,
        }).addTo(map)
      }
      setIsSpoofing(false)
      toast.success('Location successfully set to manual pinpoint.')
    })

    map.on(L.Draw.Event.CREATED, (e) => {
      const layer = e.layer
      const areaSqm = L.GeometryUtil.geodesicArea(layer.getLatLngs()[0])
      const areaSqft = toSqftFromSqm(areaSqm)
      layer.setStyle({
        color: SHAPE_COLORS[activeStructureRef.current] || '#333',
        weight: 3,
        fillOpacity: 0.4,
      })
      drawnItems.addLayer(layer)

      setCapturedShapes((prev) => [
        ...prev,
        {
          leafletId: L.stamp(layer),
          type: activeStructureRef.current || 'Open Space',
          structureType: activeStructureRef.current || 'Open Space',
          geojson: layer.toGeoJSON(),
          area: areaSqft,
          areaSqft,
        },
      ])
    })

    map.on(L.Draw.Event.EDITED, (e) => {
      const updates = []
      e.layers.eachLayer((layer) => {
        const areaSqm = L.GeometryUtil.geodesicArea(layer.getLatLngs()[0])
        updates.push({
          leafletId: L.stamp(layer),
          geojson: layer.toGeoJSON(),
          area: toSqftFromSqm(areaSqm),
          areaSqft: toSqftFromSqm(areaSqm),
        })
      })
      setCapturedShapes((prev) =>
        prev.map((shape) => {
          const found = updates.find((u) => u.leafletId === shape.leafletId)
          return found ? { ...shape, ...found } : shape
        }),
      )
    })

    map.on(L.Draw.Event.DELETED, (e) => {
      const deletedIds = []
      e.layers.eachLayer((layer) => deletedIds.push(L.stamp(layer)))
      setCapturedShapes((prev) => prev.filter((shape) => !deletedIds.includes(shape.leafletId)))
    })

    return () => {
      map.stopLocate()
      map.remove()
      mapInstanceRef.current = null
      drawnItemsRef.current = null
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) {
      if (drawnItemsRef.current) {
        drawnItemsRef.current.clearLayers()
      }
      setCapturedShapes([])
      setLiveLocation(null)
      setSpoofLocation(null)
      setIsSpoofing(false)
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="map-modal" style={{ display: 'flex' }}>
      <div className="map-modal-header" style={{ flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
          <h3>
            <i className="fas fa-satellite"></i> Map
          </h3>
          <select
            value={activeStructure}
            onChange={(e) => setActiveStructure(e.target.value)}
            style={{
              padding: '6px',
              borderRadius: '4px',
              border: 'none',
              fontFamily: 'Montserrat',
              fontWeight: 600,
              color: '#0e3e49',
              outline: 'none',
            }}
          >
            <option value="">Select Structure...</option>
            {selectedStructures.map((s) => (
              <option key={s} value={s}>
                Draw: {s}
              </option>
            ))}
          </select>
          <button
            type="button"
            id="btnSpoofLoc"
            onClick={() => setIsSpoofing((prev) => !prev)}
            style={{
              background: isSpoofing ? '#e07a5f' : '#1a5c6b',
              padding: '6px 12px',
              fontSize: '0.8em',
            }}
          >
            <i className={isSpoofing ? 'fas fa-times' : 'fas fa-crosshairs'}></i>{' '}
            {isSpoofing ? 'Tap Map to Set Location' : 'Edit Location'}
          </button>
        </div>
        <button
          type="button"
          onClick={() =>
            onDone({
              coordinates: spoofLocation || liveLocation,
              capturedShapes,
            })
          }
        >
          <i className="fas fa-check"></i> Done
        </button>
        <button type="button" onClick={onClose} style={{ background: '#6c757d' }}>
          Close
        </button>
      </div>
      <div id="mobileMap" ref={mapRef}></div>
    </div>
  )
}

export default FieldMapModal
