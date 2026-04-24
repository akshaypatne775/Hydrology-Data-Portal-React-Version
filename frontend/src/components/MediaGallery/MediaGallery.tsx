import { useEffect, useState } from 'react'

import './MediaGallery.css'

type MediaType = 'image' | 'video'

type MediaItem = {
  filename: string
  type: MediaType
  url: string
}

type MediaResponse = {
  media: MediaItem[]
}

const MEDIA_URL = 'http://localhost:8000/api/media'

export function MediaGallery() {
  const [items, setItems] = useState<MediaItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadMedia() {
      setLoading(true)
      setError(null)
      try {
        const response = await fetch(MEDIA_URL)
        if (!response.ok) {
          throw new Error(`Request failed (${response.status})`)
        }
        const data = (await response.json()) as MediaResponse
        if (cancelled) return
        setItems(data.media ?? [])
      } catch (e) {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Failed to load media')
        setItems([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadMedia()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <section className="mg-root" aria-labelledby="mg-title">
      <header className="mg-header">
        <h2 id="mg-title" className="mg-title">
          Project Media Gallery
        </h2>
      </header>

      {loading ? (
        <p className="mg-state" role="status" aria-live="polite">
          Loading media...
        </p>
      ) : null}

      {error && !loading ? (
        <p className="mg-state mg-state--error" role="alert">
          {error}
        </p>
      ) : null}

      {!loading && !error && items.length === 0 ? (
        <p className="mg-state">No media found in the media folder.</p>
      ) : null}

      {!loading && !error && items.length > 0 ? (
        <div className="mg-grid">
          {items.map((item) => (
            <article
              key={item.filename}
              className={`mg-card mg-card--${item.type}`}
            >
              {item.type === 'image' ? (
                <img
                  className="mg-media"
                  src={item.url}
                  alt={item.filename}
                  loading="lazy"
                />
              ) : (
                <video className="mg-media" controls preload="metadata">
                  <source src={item.url} />
                  Your browser does not support the video tag.
                </video>
              )}
              <p className="mg-name" title={item.filename}>
                {item.filename}
              </p>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  )
}

export default MediaGallery
