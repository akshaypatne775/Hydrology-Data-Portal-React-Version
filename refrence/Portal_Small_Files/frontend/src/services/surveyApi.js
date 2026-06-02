import { apiUrl, authFetch } from '../apiConfig'

async function requestJson(path, payload) {
  const isPost = payload != null
  const response = await authFetch(apiUrl(path), {
    method: isPost ? 'POST' : 'GET',
    ...(isPost
      ? {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }
      : {}),
  })
  if (!response.ok) {
    let detail = `Request failed: ${path}`
    try {
      const errJson = await response.json()
      detail = errJson?.detail || detail
    } catch {
      // ignore parse failure
    }
    throw new Error(detail)
  }
  return response.json().catch(() => ({}))
}

/** @param {{ limit?: number, offset?: number, bbox?: string }} [params] — bbox: minLng,minLat,maxLng,maxLat */
export function getSurveys(params = {}) {
  const q = new URLSearchParams()
  q.set('limit', String(params.limit ?? 500))
  q.set('offset', String(params.offset ?? 0))
  if (params.bbox) q.set('bbox', params.bbox)
  return requestJson(`/get-surveys?${q.toString()}`)
}

/**
 * Fetches every survey feature via limit/offset pages (yields between requests to keep the UI responsive).
 * @param {{ pageSize?: number, bbox?: string }} [options]
 * @returns {Promise<object[]>} GeoJSON features
 */
export async function getAllSurveyFeaturesMerged(options = {}) {
  const pageSize = Math.min(Math.max(Number(options.pageSize) || 500, 1), 2000)
  const { bbox } = options
  const features = []
  let offset = 0
  let hasMore = true
  while (hasMore) {
    const page = await getSurveys({ limit: pageSize, offset, bbox })
    const batch = Array.isArray(page.features) ? page.features : []
    features.push(...batch)
    hasMore = page.hasMore === true
    offset += batch.length
    if (batch.length === 0) break
    if (hasMore) {
      await new Promise((r) => setTimeout(r, 0))
    }
  }
  return features
}
export const getShapes = () => requestJson('/get-shapes')
export const saveSurvey = (payload) => requestJson('/save-survey', payload)
export const saveShape = (payload) => requestJson('/save-shape', payload)
export const updateShape = (payload) => requestJson('/update-shape', payload)
export const deleteShape = (id) => requestJson('/delete-shape', { id })
export const updateShapeAssignment = (payload) => requestJson('/update-shape-assignment', payload)
export const updateSurvey = (payload) => requestJson('/update-survey', payload)
export const deleteSurvey = (id) => requestJson('/delete-survey', { id })
