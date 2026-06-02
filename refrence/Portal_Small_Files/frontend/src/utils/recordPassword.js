/** Property ID + owner name, every whitespace removed — must match typed confirmation. */
export function recordPasswordConfirmationExpected(plot) {
  const pid = String(plot?.propertyId ?? '').trim()
  const owner = String(plot?.ownerName ?? '').trim()
  return `${pid}${owner}`.replace(/\s/g, '')
}

export function normalizeRecordPasswordInput(value) {
  return String(value ?? '').replace(/\s/g, '')
}

export const deleteSurveyConfirmationExpected = recordPasswordConfirmationExpected

/** Canonical ID + owner for the survey row with this primary key (from get-surveys). */
export function plotForPasswordFromDbId(surveys, dbId) {
  const id = Number(dbId)
  if (!Number.isFinite(id) || id <= 0) return null
  const f = (surveys?.features || []).find((x) => Number(x?.properties?.dbId) === id)
  if (!f?.properties) return null
  const p = f.properties
  return {
    propertyId: String(p.propertyId ?? '').trim(),
    ownerName: String(p.ownerName ?? '').trim(),
  }
}

/**
 * Canonical ID + owner for surveys with this property_id (stable row if duplicates exist).
 * Used so polygon edit password matches the plot that shape belongs to, not another field.
 */
export function plotForPasswordFromPropertyId(surveys, propertyId) {
  const pid = String(propertyId ?? '').trim()
  if (!pid) return null
  const matches = (surveys?.features || []).filter(
    (x) => String(x?.properties?.propertyId ?? '').trim() === pid,
  )
  if (matches.length === 0) return null
  matches.sort((a, b) => Number(a.properties?.dbId || 0) - Number(b.properties?.dbId || 0))
  const p = matches[0].properties
  return {
    propertyId: String(p.propertyId ?? '').trim(),
    ownerName: String(p.ownerName ?? '').trim(),
  }
}
