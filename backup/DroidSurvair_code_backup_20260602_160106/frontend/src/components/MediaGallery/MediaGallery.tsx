import { useMediaGallery } from '../../hooks/useMediaGallery'

import './MediaGallery.css'

export function MediaGallery() {
  const { items, loading, error } = useMediaGallery()

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
