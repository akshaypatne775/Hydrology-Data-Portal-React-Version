export type UploadedTileset = {
  label: string
  url: string
}

function key(projectId: string): string {
  return `droidcloud.uploadedTilesets.${projectId}`
}

function isRawPointCloudUrl(url: string): boolean {
  return /\.(las|laz)(?:[?#].*)?$/i.test(url.trim())
}

export function readUploadedTilesets(projectId: string): UploadedTileset[] {
  try {
    const raw = window.localStorage.getItem(key(projectId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as UploadedTileset[]
    if (!Array.isArray(parsed)) return []
    const cleaned = parsed.filter((row) => (
      row &&
      typeof row.label === 'string' &&
      typeof row.url === 'string' &&
      row.url.trim() &&
      !isRawPointCloudUrl(row.url)
    ))
    if (cleaned.length !== parsed.length) writeUploadedTilesets(projectId, cleaned)
    return cleaned
  } catch {
    return []
  }
}

export function writeUploadedTilesets(projectId: string, rows: UploadedTileset[]): void {
  try {
    window.localStorage.setItem(key(projectId), JSON.stringify(rows.filter((row) => !isRawPointCloudUrl(row.url))))
  } catch {
    // Keep runtime resilient when storage quota is reached.
  }
}
