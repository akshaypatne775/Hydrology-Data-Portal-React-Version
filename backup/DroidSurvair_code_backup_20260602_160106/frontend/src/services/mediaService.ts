import { apiRequestJson } from './api'

export type MediaType = 'image' | 'video'

export type MediaItem = {
  filename: string
  type: MediaType
  url: string
}

export async function listMedia(): Promise<MediaItem[]> {
  const data = await apiRequestJson<{ media: MediaItem[] }>('/api/media')
  return data.media ?? []
}
