import L, { type LatLng } from 'leaflet'
import { Marker, Polygon, Polyline, Tooltip } from 'react-leaflet'
import {
  buildPointFeature,
  colorForStructure,
  featureStyle,
  positionToLatLng,
  type GeoJsonFeature,
  type SpatialFeature,
  type SpatialLayer,
} from './spatialTypes'

type SpatialFeatureLayerProps = {
  layers: SpatialLayer[]
  selectedFeatureId: string | null
  editMode: boolean
  onFeatureClick: (feature: SpatialFeature) => void
  onGeometryChange: (feature: SpatialFeature, geojson: GeoJsonFeature) => void
}

const vertexIcon = L.divIcon({
  className: 'spatial-vertex-handle',
  iconSize: [14, 14],
})

function spatialMarkerIcon(feature: SpatialFeature, selected: boolean) {
  const color = colorForStructure(feature.structure_type)
  return L.divIcon({
    className: selected ? 'spatial-point-marker spatial-point-marker--selected' : 'spatial-point-marker',
    html: `<span class="spatial-point-marker__pin" style="--spatial-marker-color: ${color};"><i class="fa-solid fa-location-dot" aria-hidden="true"></i></span>`,
    iconSize: [30, 38],
    iconAnchor: [15, 36],
    popupAnchor: [0, -34],
    tooltipAnchor: [0, -32],
  })
}

function coordinatesToPositions(coordinates: unknown): [number, number][] {
  if (!Array.isArray(coordinates)) return []
  return coordinates
    .map(positionToLatLng)
    .filter((point): point is [number, number] => Boolean(point))
}

function closeRing(positions: [number, number][]): [number, number][] {
  if (positions.length === 0) return []
  const first = positions[0]!
  const last = positions[positions.length - 1]!
  if (first[0] === last[0] && first[1] === last[1]) return positions
  return [...positions, first]
}

function openRing(positions: [number, number][]): [number, number][] {
  if (positions.length < 2) return positions
  const first = positions[0]!
  const last = positions[positions.length - 1]!
  if (first[0] === last[0] && first[1] === last[1]) return positions.slice(0, -1)
  return positions
}

function latLngToCoordinate(point: LatLng): [number, number] {
  return [point.lng, point.lat]
}

function withGeometry(feature: SpatialFeature, geometry: GeoJsonFeature['geometry']): GeoJsonFeature {
  return {
    type: 'Feature',
    properties: feature.geojson.properties ?? {},
    geometry,
  }
}

function FeatureTooltip({ feature }: { feature: SpatialFeature }) {
  return (
    <Tooltip sticky direction="top" className="spatial-feature-tooltip">
      <div>
        <strong>{feature.owner_name || 'Owner not assigned'}</strong>
        <span>Plot ID: {feature.plot_id || 'Not assigned'}</span>
        <span>Structure: {feature.structure_type || 'Unassigned'}</span>
      </div>
    </Tooltip>
  )
}

export function SpatialFeatureLayer({
  layers,
  selectedFeatureId,
  editMode,
  onFeatureClick,
  onGeometryChange,
}: SpatialFeatureLayerProps) {
  const features = layers.flatMap((layer) => layer.features)

  return (
    <>
      {features.flatMap((feature) => {
        const geometry = feature.geojson.geometry
        if (!geometry) return []
        const selected = selectedFeatureId === feature.id
        const canEditFeature = feature.can_edit !== false
        const style = featureStyle(feature, selected)
        const eventHandlers = {
          click: (event: L.LeafletMouseEvent) => {
            L.DomEvent.stopPropagation(event.originalEvent)
            onFeatureClick(feature)
          },
        }

        if (geometry.type === 'Point') {
          const position = positionToLatLng(geometry.coordinates)
          if (!position) return []
          return [
            <Marker
              key={feature.id}
              position={position}
              icon={spatialMarkerIcon(feature, selected)}
              draggable={selected && editMode && canEditFeature}
              eventHandlers={{
                ...eventHandlers,
                dragend: (event) => {
                  if (!canEditFeature) return
                  const point = event.target.getLatLng() as LatLng
                  onGeometryChange(feature, buildPointFeature(point))
                },
              }}
            >
              <FeatureTooltip feature={feature} />
            </Marker>,
          ]
        }

        if (geometry.type === 'LineString') {
          const positions = coordinatesToPositions(geometry.coordinates)
          if (positions.length < 2) return []
          const handles = selected && editMode && canEditFeature
            ? positions.map((position, index) => (
                <Marker
                  key={`${feature.id}-handle-${index}`}
                  position={position}
                  icon={vertexIcon}
                  draggable
                  eventHandlers={{
                    dragend: (event) => {
                      const nextPoint = event.target.getLatLng() as LatLng
                      const coordinates = positions.map((point, pointIndex) =>
                        pointIndex === index ? latLngToCoordinate(nextPoint) : [point[1], point[0]],
                      )
                      onGeometryChange(
                        feature,
                        withGeometry(feature, { type: 'LineString', coordinates }),
                      )
                    },
                  }}
                />
              ))
            : []
          return [
            <Polyline
              key={feature.id}
              positions={positions}
              pathOptions={style}
              eventHandlers={eventHandlers}
            >
              <FeatureTooltip feature={feature} />
            </Polyline>,
            ...handles,
          ]
        }

        if (geometry.type === 'Polygon') {
          if (!Array.isArray(geometry.coordinates)) return []
          const rawOuter = geometry.coordinates[0]
          const positions = openRing(coordinatesToPositions(rawOuter))
          if (positions.length < 3) return []
          const handles = selected && editMode && canEditFeature
            ? positions.map((position, index) => (
                <Marker
                  key={`${feature.id}-handle-${index}`}
                  position={position}
                  icon={vertexIcon}
                  draggable
                  eventHandlers={{
                    dragend: (event) => {
                      const nextPoint = event.target.getLatLng() as LatLng
                      const nextPositions = positions.map((point, pointIndex) =>
                        pointIndex === index ? [nextPoint.lat, nextPoint.lng] as [number, number] : point,
                      )
                      const outer = closeRing(nextPositions).map((point) => [point[1], point[0]])
                      const holes = Array.isArray(geometry.coordinates)
                        ? geometry.coordinates.slice(1)
                        : []
                      onGeometryChange(
                        feature,
                        withGeometry(feature, { type: 'Polygon', coordinates: [outer, ...holes] }),
                      )
                    },
                  }}
                />
              ))
            : []
          return [
            <Polygon
              key={feature.id}
              positions={positions}
              pathOptions={style}
              eventHandlers={eventHandlers}
            >
              <FeatureTooltip feature={feature} />
            </Polygon>,
            ...handles,
          ]
        }

        if (geometry.type === 'MultiLineString' && Array.isArray(geometry.coordinates)) {
          return geometry.coordinates.map((line, index) => {
            const positions = coordinatesToPositions(line)
            return (
              <Polyline
                key={`${feature.id}-${index}`}
                positions={positions}
                pathOptions={style}
                eventHandlers={eventHandlers}
              >
                <FeatureTooltip feature={feature} />
              </Polyline>
            )
          })
        }

        if (geometry.type === 'MultiPolygon' && Array.isArray(geometry.coordinates)) {
          return geometry.coordinates.map((polygon, index) => {
            const rings = Array.isArray(polygon) ? polygon.map(coordinatesToPositions) : []
            return (
              <Polygon
                key={`${feature.id}-${index}`}
                positions={rings}
                pathOptions={style}
                eventHandlers={eventHandlers}
              >
                <FeatureTooltip feature={feature} />
              </Polygon>
            )
          })
        }

        return []
      })}
    </>
  )
}
