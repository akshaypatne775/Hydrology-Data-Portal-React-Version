import { useEffect, useMemo, useState } from 'react'
import { getProjectFiles, type ProjectFile } from '../../services/datasetService'
import './DownloadsPanel.css'

type DownloadCategory = 'Raw Survey Data' | 'Web-Optimized Data' | 'Reports'

type DownloadItem = {
  id: string
  name: string
  size: string
  format: string
  category: DownloadCategory
  href: string
}

type DownloadsPanelProps = {
  projectId?: string
}

const CATEGORY_ORDER: DownloadCategory[] = ['Raw Survey Data', 'Web-Optimized Data', 'Reports']

function humanSize(sizeBytes: string): string {
  const n = Number(sizeBytes)
  if (!Number.isFinite(n) || n <= 0) return '--'
  const gb = n / (1024 * 1024 * 1024)
  if (gb >= 1) return `${gb.toFixed(2)} GB`
  const mb = n / (1024 * 1024)
  return `${mb.toFixed(1)} MB`
}

export function DownloadsPanel({ projectId }: DownloadsPanelProps) {
  const [items, setItems] = useState<DownloadItem[]>([])

  const grouped = useMemo(
    () =>
      CATEGORY_ORDER.map((category) => ({
        category,
        items: items.filter((item) => item.category === category),
      })),
    [items],
  )

  useEffect(() => {
    if (!projectId) {
      setItems([])
      return
    }
    let cancelled = false
    const load = async () => {
      try {
        const files = await getProjectFiles(projectId)
        if (cancelled) return
        const mapped: DownloadItem[] = files.map((file: ProjectFile) => {
          const category: DownloadCategory =
            file.kind === 'Reports'
              ? 'Reports'
              : file.kind === 'Web-Optimized Data'
                ? 'Web-Optimized Data'
                : 'Raw Survey Data'
          return {
            id: `${file.name}-${file.type}`,
            name: file.name,
            size: humanSize(file.size_bytes),
            format: file.type.toUpperCase(),
            category,
            href: file.file_url,
          }
        })
        setItems(mapped)
      } catch {
        setItems([])
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [projectId])

  return (
    <section className="dlp-root">
      <header className="dlp-head">
        <h3>Client Downloads</h3>
        <p>Download processed reports and project datasets prepared for client delivery.</p>
      </header>

      <div className="dlp-grid">
        {grouped.map(({ category, items: categoryItems }) => (
          <article key={category} className="dlp-card">
            <header className="dlp-card__head">
              <h4>{category}</h4>
            </header>

            <ul className="dlp-list">
              {categoryItems.map((item) => (
                  <li key={item.id} className="dlp-item">
                    <div className="dlp-item__meta">
                      <p className="dlp-item__name">{item.name}</p>
                      <p className="dlp-item__sub">
                        <span>{item.format}</span>
                        <span>{item.size}</span>
                      </p>
                    </div>
                    <button
                      type="button"
                      className="dlp-download"
                      onClick={() => window.open(item.href, '_blank', 'noopener,noreferrer')}
                    >
                      <i className="fa-solid fa-download" aria-hidden />
                      Download
                    </button>
                  </li>
                ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  )
}

export default DownloadsPanel
