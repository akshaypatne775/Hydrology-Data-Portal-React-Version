import { useEffect } from 'react'
import L from 'leaflet'

function useOrthoBoundaryOverlay(mapRef) {
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
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
            done(null, tile)
          }
          img.src = this.getTileUrl(coords)
          return tile
        },
      })
      L.TileLayer.boundaryCanvas = (url, options) => new L.TileLayer.BoundaryCanvas(url, options)
    }
  }, [mapRef])
}

export default useOrthoBoundaryOverlay
