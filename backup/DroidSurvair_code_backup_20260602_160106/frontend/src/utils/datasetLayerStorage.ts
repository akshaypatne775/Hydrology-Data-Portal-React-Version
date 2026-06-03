type DatasetLayerEntry = {
  projectId: string
  datasetId: string
  fileName: string
  tileUrl: string
}

const KEY = 'droid-cloud-cog-layers-v1'

function readAll(): DatasetLayerEntry[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return []
    const data = JSON.parse(raw) as DatasetLayerEntry[]
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
}

function writeAll(items: DatasetLayerEntry[]): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    // no-op
  }
}

export function saveWebReadyCogLayer(
  projectId: string,
  datasetId: string,
  fileName: string,
  tileUrl: string,
): void {
  const next = readAll().filter((x) => !(x.projectId === projectId && x.datasetId === datasetId))
  next.unshift({ projectId, datasetId, fileName, tileUrl })
  writeAll(next)
}

export function getLatestCogLayer(projectId: string): DatasetLayerEntry | null {
  const hit = readAll().find((x) => x.projectId === projectId)
  return hit ?? null
}
