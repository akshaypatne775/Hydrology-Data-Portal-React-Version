import { memo } from 'react'
import { CircleMarker, Polygon, Popup } from 'react-leaflet'

export type HydrologyPoint = {
  id: string
  name: string
  lat: number
  lng: number
  metric: string
  value: string
}

export type HydrologyZone = {
  id: string
  name: string
  risk: string
  coordinates: [number, number][]
}

type HydrologyDataLayerProps = {
  points: HydrologyPoint[]
  zones: HydrologyZone[]
}

export const HydrologyDataLayer = memo(function HydrologyDataLayer({ points, zones }: HydrologyDataLayerProps) {
  return (
    <>
      {zones.map((zone) => (
        <Polygon
          key={zone.id}
          positions={zone.coordinates}
          pathOptions={{
            color: '#0e3e49',
            weight: 2,
            fillColor: '#14b8a6',
            fillOpacity: 0.12,
          }}
        >
          <Popup>
            <div className="mv-hydro-popup">
              <h4>{zone.name}</h4>
              <p>Risk band: {zone.risk}</p>
            </div>
          </Popup>
        </Polygon>
      ))}

      {points.map((point) => (
        <CircleMarker
          key={point.id}
          center={[point.lat, point.lng]}
          radius={7}
          pathOptions={{
            color: '#0e3e49',
            weight: 2,
            fillColor: '#22d3ee',
            fillOpacity: 0.9,
          }}
        >
          <Popup>
            <div className="mv-hydro-popup">
              <h4>{point.name}</h4>
              <p>
                {point.metric}: {point.value}
              </p>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </>
  )
})

export default HydrologyDataLayer
