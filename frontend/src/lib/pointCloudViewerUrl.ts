import { toSameOriginBackendUrl } from './apiBase'

export function isRawPointCloudUrl(url: string): boolean {
  const normalized = url.trim().toLowerCase().split(/[?#]/, 1)[0] || ''
  return /\.(las|laz)$/i.test(normalized) && !normalized.endsWith('.copc.laz')
}

export function isPointCloudViewerUrl(url: string): boolean {
  const normalized = url.trim().toLowerCase().split(/[?#]/, 1)[0] || ''
  return (normalized.includes('/droid-ept-viewer/') && url.includes('copc=')) || normalized.endsWith('.copc.laz')
}

export function normalizedPointCloudName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/\\/g, '/')
    .split('/')
    .pop()!
    .replace(/\.(copc\.laz|las|laz|json)$/i, '')
    .replace(/^(?:ept|copc|pointcloud|point-cloud|pc)(?=[0-9._\-\s])[\W_]*/i, '')
    .replace(/[\W_]*(?:ept|copc|pointcloud|point-cloud|pc)$/i, '')
    .replace(/[-_][a-f0-9]{8,}$/i, '')
    .replace(/[^a-z0-9]+/g, '')
}

export function resolveCopcApiUrl(url: string): string {
  const sameOriginUrl = toSameOriginBackendUrl(url) || url.trim()
  if (!sameOriginUrl) return ''

  const pathOnly = sameOriginUrl.split(/[?#]/, 1)[0] || ''
  if (pathOnly.toLowerCase().endsWith('.copc.laz')) {
    return pathOnly.startsWith('/api/') ? pathOnly : pathOnly.replace(/^\/data\//, '/api/data/')
  }

  const folderMatch = pathOnly.match(/\/(?:api\/)?data\/projects\/([^/]+)\/exports\/pointclouds\/([^/]+)\/?$/i)
  if (folderMatch) {
    const [, projectId, folder] = folderMatch
    return `/api/data/projects/${projectId}/exports/pointclouds/${folder}/output.copc.laz`
  }

  const copcParamMatch = sameOriginUrl.match(/[?&]copc=([^&]+)/i)
  if (copcParamMatch?.[1]) {
    try {
      const decoded = decodeURIComponent(copcParamMatch[1])
      return resolveCopcApiUrl(decoded) || decoded
    } catch {
      return resolveCopcApiUrl(copcParamMatch[1]) || copcParamMatch[1]
    }
  }

  return ''
}

export function copcApiUrlToViewerUrl(
  copcUrl: string,
  projectId: string,
  datasetId: string,
  displayName: string,
): string {
  const normalizedCopcUrl = resolveCopcApiUrl(copcUrl) || toSameOriginBackendUrl(copcUrl) || copcUrl
  const params = new URLSearchParams({
    copc: normalizedCopcUrl,
    project: projectId,
    dataset: datasetId || normalizedPointCloudName(displayName),
    name: displayName,
  })
  return `/droid-ept-viewer/index.html?${params.toString()}`
}

export function normalizePointCloudViewerUrl(
  url: string | undefined,
  projectId: string,
  datasetId: string,
  displayName: string,
): string {
  const sameOriginUrl = toSameOriginBackendUrl(url) || ''
  if (!sameOriginUrl || isRawPointCloudUrl(sameOriginUrl)) return ''

  if (isPointCloudViewerUrl(sameOriginUrl)) {
    if (sameOriginUrl.includes('/droid-ept-viewer/') && sameOriginUrl.includes('copc=')) {
      try {
        const parsed = new URL(sameOriginUrl, 'http://localhost')
        const copc = parsed.searchParams.get('copc') || ''
        const resolvedCopc = resolveCopcApiUrl(copc)
        if (resolvedCopc && resolvedCopc !== copc) {
          return copcApiUrlToViewerUrl(resolvedCopc, projectId, datasetId, displayName)
        }
      } catch {
        // Keep the original viewer URL when query parsing fails.
      }
    }
    return sameOriginUrl
  }

  const copcApiUrl = resolveCopcApiUrl(sameOriginUrl)
  if (copcApiUrl) {
    return copcApiUrlToViewerUrl(copcApiUrl, projectId, datasetId, displayName)
  }

  return ''
}
