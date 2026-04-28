import { useEffect, useState } from 'react'
import { listMedia, type MediaItem } from '../services/mediaService'

export function useMediaGallery() {
  const [items, setItems] = useState<MediaItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const rows = await listMedia()
        if (!cancelled) setItems(rows)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load media')
          setItems([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  return { items, loading, error }
}
