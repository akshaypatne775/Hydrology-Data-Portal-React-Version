import { API_BASE_URL } from '../apiConfig'

/** Matches backend survey file fields: data URLs, http(s) file URLs, uploads/ paths, or legacy long Base64. */

function guessContentTypeFromUrl(u) {
  const path = String(u).split('?')[0].toLowerCase()
  if (/\.(jpe?g)$/.test(path)) return 'image/jpeg'
  if (/\.png$/.test(path)) return 'image/png'
  if (/\.gif$/.test(path)) return 'image/gif'
  if (/\.webp$/.test(path)) return 'image/webp'
  if (/\.pdf$/.test(path)) return 'application/pdf'
  return 'application/octet-stream'
}

export function hasStoredFileRef(s) {
  if (typeof s !== 'string') return false
  const v = s.trim()
  if (!v) return false
  if (v.startsWith('data:')) return true
  if (v.startsWith('http://') || v.startsWith('https://')) return true
  if (v.startsWith('uploads/')) return true
  return v.length > 100
}

export function resolveMediaForViewer(s) {
  if (!hasStoredFileRef(s)) return null
  const v = String(s).trim()
  if (v.startsWith('http://') || v.startsWith('https://')) {
    return { blobUrl: v, contentType: guessContentTypeFromUrl(v) }
  }
  if (v.startsWith('uploads/')) {
    const origin = API_BASE_URL.replace(/\/$/, '')
    const url = `${origin}/${v.replace(/^\//, '')}`
    return { blobUrl: url, contentType: guessContentTypeFromUrl(url) }
  }
  if (!v.startsWith('data:')) {
    try {
      const byteCharacters = atob(v.includes(',') ? v.split(',').pop() : v)
      const byteNumbers = new Array(byteCharacters.length)
      for (let i = 0; i < byteCharacters.length; i++) byteNumbers[i] = byteCharacters.charCodeAt(i)
      const blob = new Blob([new Uint8Array(byteNumbers)], { type: 'application/pdf' })
      return { blobUrl: URL.createObjectURL(blob), contentType: 'application/pdf' }
    } catch {
      return null
    }
  }
  const block = v.split(';')
  const contentType = block[0]?.split(':')[1] || 'application/pdf'
  const realData = v.includes(',') ? v.split(',')[1] : v
  let byteCharacters
  try {
    byteCharacters = atob(realData)
  } catch {
    return null
  }
  const byteNumbers = new Array(byteCharacters.length)
  for (let i = 0; i < byteCharacters.length; i++) byteNumbers[i] = byteCharacters.charCodeAt(i)
  const blob = new Blob([new Uint8Array(byteNumbers)], { type: contentType })
  return { blobUrl: URL.createObjectURL(blob), contentType }
}

export function revokeViewerUrlIfBlob(url) {
  if (typeof url === 'string' && url.startsWith('blob:')) URL.revokeObjectURL(url)
}
