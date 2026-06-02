import { API_BASE_URL, appendAuthTokenQuery } from '../apiConfig'
import { hasStoredFileRef } from './storedFileRef'

function boolLike(v) {
  return v === true || v === 1 || v === '1'
}

export function buildDocumentUrl(propertyId, docType) {
  const pid = encodeURIComponent(String(propertyId || '').trim())
  if (!pid || !docType) return ''
  return appendAuthTokenQuery(`${API_BASE_URL}/document/${pid}/${docType}`)
}

export function hasDocument(props, docDef) {
  if (!docDef?.b64Key) return false
  const hasRef = hasStoredFileRef(props?.[docDef.b64Key])
  if (!docDef.boolKey) return hasRef
  return boolLike(props?.[docDef.boolKey]) && hasRef
}
