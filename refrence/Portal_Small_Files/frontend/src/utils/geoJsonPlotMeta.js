/**
 * Ensures each saved GeoJSON Feature carries propertyId + structureType in properties
 * so map tooltips can show the correct plot per polygon (Leaflet per-layer tooltips).
 */
export function embedPlotMetaInGeoJson(geojsonInput, propertyId, structureType) {
  const pid = String(propertyId ?? '').trim()
  const st = String(structureType ?? '').trim()
  const meta = { propertyId: pid, structureType: st }

  let g = geojsonInput
  if (typeof g === 'string') {
    try {
      g = JSON.parse(g)
    } catch {
      g = null
    }
  }
  if (!g || typeof g !== 'object') {
    return JSON.stringify({ type: 'Feature', properties: { ...meta }, geometry: null })
  }

  const stampFeature = (feat) => {
    if (!feat || feat.type !== 'Feature') return feat
    return {
      ...feat,
      properties: { ...(feat.properties || {}), ...meta },
    }
  }

  if (g.type === 'FeatureCollection' && Array.isArray(g.features)) {
    return JSON.stringify({
      ...g,
      features: g.features.map((f) => {
        if (f?.type === 'Feature') return stampFeature(f)
        return { type: 'Feature', properties: { ...meta }, geometry: f }
      }),
    })
  }
  if (g.type === 'Feature') {
    return JSON.stringify(stampFeature(g))
  }
  return JSON.stringify({ type: 'Feature', properties: { ...meta }, geometry: g })
}
