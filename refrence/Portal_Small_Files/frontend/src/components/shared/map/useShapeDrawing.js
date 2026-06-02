import { useCallback } from 'react'
import { SHAPE_COLORS, toSqftFromSqm } from './constants'

function useShapeDrawing() {
  const styleForStructure = useCallback((structureType) => {
    const color = SHAPE_COLORS[structureType] || '#333'
    return { color, weight: 3, fillOpacity: 0.4 }
  }, [])

  const toShapeSnapshot = useCallback((layer, structureType) => {
    const areaSqm = layer?.getLatLngs ? window.L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]) : 0
    const areaSqft = toSqftFromSqm(areaSqm)
    return {
      leafletId: window.L.stamp(layer),
      structureType: structureType || 'Open Space',
      type: structureType || 'Open Space',
      geojson: layer.toGeoJSON(),
      area: areaSqft,
      areaSqft,
    }
  }, [])

  return { styleForStructure, toShapeSnapshot }
}

export default useShapeDrawing
