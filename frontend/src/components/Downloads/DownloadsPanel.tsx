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

function buildMockItems(projectId: string): DownloadItem[] {
  const pid = projectId.slice(0, 8).toUpperCase()
  return [
    {
      id: 'raw-1',
      name: `${pid}_master_survey_14gb.las`,
      size: '14.0 GB',
      format: 'LAS',
      category: 'Raw Survey Data',
      href: '#',
    },
    {
      id: 'raw-2',
      name: `${pid}_terrain_model_5gb.tif`,
      size: '5.0 GB',
      format: 'GeoTIFF',
      category: 'Raw Survey Data',
      href: '#',
    },
    {
      id: 'web-1',
      name: `${pid}_xyz_tiles_web_bundle.zip`,
      size: '1.3 GB',
      format: 'ZIP',
      category: 'Web-Optimized Data',
      href: '#',
    },
    {
      id: 'report-1',
      name: `${pid}_hydrology_summary_report.pdf`,
      size: '6.8 MB',
      format: 'PDF',
      category: 'Reports',
      href: '#',
    },
    {
      id: 'report-2',
      name: `${pid}_terrain_quality_assurance.pdf`,
      size: '4.2 MB',
      format: 'PDF',
      category: 'Reports',
      href: '#',
    },
  ]
}

const CATEGORY_ORDER: DownloadCategory[] = ['Raw Survey Data', 'Web-Optimized Data', 'Reports']

export function DownloadsPanel({ projectId }: DownloadsPanelProps) {
  const items = buildMockItems(projectId || 'project')

  return (
    <section className="dlp-root">
      <header className="dlp-head">
        <h3>Client Downloads</h3>
        <p>Download processed reports and project datasets prepared for client delivery.</p>
      </header>

      <div className="dlp-grid">
        {CATEGORY_ORDER.map((category) => (
          <article key={category} className="dlp-card">
            <header className="dlp-card__head">
              <h4>{category}</h4>
            </header>

            <ul className="dlp-list">
              {items
                .filter((item) => item.category === category)
                .map((item) => (
                  <li key={item.id} className="dlp-item">
                    <div className="dlp-item__meta">
                      <p className="dlp-item__name">{item.name}</p>
                      <p className="dlp-item__sub">
                        <span>{item.format}</span>
                        <span>{item.size}</span>
                      </p>
                    </div>
                    <a className="dlp-download" href={item.href} download>
                      <i className="fa-solid fa-download" aria-hidden />
                      Download
                    </a>
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
