import { useEffect, useRef } from 'react'
import L from 'leaflet'

function LeafletMapShell({ onMapReady, id = 'map', style }) {
  const mapElRef = useRef(null)
  const mapInstanceRef = useRef(null)

  useEffect(() => {
    if (!mapElRef.current || mapInstanceRef.current) return
    const map = L.map(mapElRef.current, {
      zoomControl: true,
      maxZoom: 25,
      zoomSnap: 0.1,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 120,
    }).setView([20.5937, 78.9629], 5)
    mapInstanceRef.current = map
    onMapReady?.(map)
    return () => {
      map.remove()
      mapInstanceRef.current = null
    }
  }, [onMapReady])

  return <div id={id} ref={mapElRef} style={style} />
}

export default LeafletMapShell
