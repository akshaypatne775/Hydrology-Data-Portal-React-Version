export type UploadedTileset = {
  label: string
  url: string
}

function key(projectId: string): string {
  return `droidcloud.uploadedTilesets.${projectId}`
}

export function readUploadedTilesets(projectId: string): UploadedTileset[] {
  try {
    const raw = window.localStorage.getItem(key(projectId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as UploadedTileset[]
    if (!Array.isArray(parsed)) return []
    return parsed.filter((row) => row && typeof row.label === 'string' && typeof row.url === 'string')
  } catch {
    return []
  }
}

export function writeUploadedTilesets(projectId: string, rows: UploadedTileset[]): void {
  try {
    window.localStorage.setItem(key(projectId), JSON.stringify(rows))
  } catch {
    // Keep runtime resilient when storage quota is reached.
  }
}
