import { useCallback, useState } from 'react'
import { HydrologyStats } from './HydrologyStats/HydrologyStats'
import GlobeViewer from './GlobeViewer/GlobeViewer'
import { MediaGallery } from './MediaGallery/MediaGallery'
import { MapViewer } from './MapViewer/MapViewer'
import './Dashboard.css'

const NAV_ITEMS = [
  { id: 'overview', label: 'Dashboard Overview', icon: 'fa-solid fa-chart-line' },
  { id: 'map', label: 'Map Viewer', icon: 'fa-solid fa-map-location-dot' },
  { id: 'globe', label: 'Globe View', icon: 'fa-solid fa-earth-asia' },
  { id: 'analysis', label: 'Hydrology Analysis', icon: 'fa-solid fa-droplet' },
  { id: 'media', label: 'Media Gallery', icon: 'fa-solid fa-images' },
  { id: 'issues', label: 'Issue Tracker', icon: 'fa-solid fa-clipboard-list' },
  { id: 'downloads', label: 'Downloads', icon: 'fa-solid fa-file-arrow-down' },
] as const

const DASHBOARD_METRICS = [
  { label: 'Active Modeling Jobs', value: '08', meta: 'Across 3 basins' },
  { label: 'Validated Media Files', value: '214', meta: 'Images and field clips' },
  { label: 'Open Engineering Issues', value: '11', meta: 'Needs review this week' },
  { label: 'Release Packages', value: '06', meta: 'Ready for stakeholder export' },
]

const DASHBOARD_MODULES = [
  {
    id: 'analysis',
    title: 'Hydrology Analysis & Modeling',
    icon: 'fa-solid fa-droplet',
    description:
      'Run rainfall scenarios, inspect return-period behavior, and align outputs with map overlays.',
    action: 'Open analysis workspace',
  },
  {
    id: 'media',
    title: 'Media Repository Gallery',
    icon: 'fa-solid fa-images',
    description:
      'Review imagery and site evidence in a structured repository for documentation workflows.',
    action: 'Explore media repository',
  },
  {
    id: 'issues',
    title: 'Project Issue Tracking',
    icon: 'fa-solid fa-clipboard-list',
    description:
      'Track field observations, assign priority, and maintain an auditable issue register.',
    action: 'Manage issue register',
  },
  {
    id: 'downloads',
    title: 'Download Management Center',
    icon: 'fa-solid fa-file-arrow-down',
    description:
      'Prepare polished output bundles for technical review, delivery, and archival.',
    action: 'Access delivery packages',
  },
] as const

export function Dashboard() {
  const [activeId, setActiveId] = useState<string>('overview')
  const [shareCopied, setShareCopied] = useState(false)
  const [floodSimulationLevel, setFloodSimulationLevel] = useState(0)

  const handleShare = useCallback(() => {
    const url = `${window.location.origin}${window.location.pathname}`
    const flashCopied = () => {
      setShareCopied(true)
      window.setTimeout(() => setShareCopied(false), 2200)
    }
    if (navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(url).then(flashCopied).catch(() => {
        window.prompt('Copy white-label link:', url)
      })
    } else {
      window.prompt('Copy white-label link:', url)
    }
  }, [])

  return (
    <div className="ds-dashboard">
      <aside className="ds-sidebar" aria-label="Droid Survair navigation">
        <div className="ds-sidebar__brand">
          <div className="ds-sidebar__brand-mark">
            <span className="ds-sidebar__logo" aria-hidden>
              <i className="fa-solid fa-layer-group" />
            </span>
            <div>
              <p className="ds-sidebar__title">Droid Survair</p>
              <p className="ds-sidebar__tagline">Survey · Map · Insight</p>
            </div>
          </div>
        </div>

        <nav className="ds-sidebar__nav">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={
                activeId === item.id
                  ? 'ds-sidebar__link ds-sidebar__link--active'
                  : 'ds-sidebar__link'
              }
              onClick={(e) => {
                e.preventDefault()
                setActiveId(item.id)
              }}
            >
              <i className={item.icon} aria-hidden />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>

        <div className="ds-sidebar__footer">Droid Survair · v1</div>
      </aside>

      <div className="ds-main">
        <header className="ds-topbar">
          <div className="ds-topbar__project">
            <span className="ds-topbar__label">Project</span>
            <h1 className="ds-topbar__name">
              964 Acres Hydrology Project
            </h1>
          </div>

          <div className="ds-topbar__actions">
            <button
              type="button"
              className={
                shareCopied
                  ? 'ds-share ds-share--copied'
                  : 'ds-share'
              }
              onClick={handleShare}
              title="Copy white-label link to this view"
            >
              <i className="fa-solid fa-link" aria-hidden />
              {shareCopied ? 'Copied' : 'Share'}
            </button>

            <div className="ds-profile" role="group" aria-label="User profile">
              <div className="ds-profile__avatar" aria-hidden>
                DS
              </div>
              <div className="ds-profile__meta">
                <span className="ds-profile__name">Survey Lead</span>
                <span className="ds-profile__role">Field ops</span>
              </div>
            </div>
          </div>
        </header>

        <main className="ds-content">
          {activeId === 'overview' ? (
            <section className="ds-overview" aria-label="Dashboard overview">
              <article className="ds-overview-hero">
                <div>
                  <p className="ds-overview-hero__kicker">Operations Command</p>
                  <h2 className="ds-overview-hero__title">
                    Hydrology Intelligence Workspace
                  </h2>
                  <p className="ds-overview-hero__text">
                    Coordinate analysis, media evidence, issue logs, and delivery
                    packages from one professional control center.
                  </p>
                </div>
                <div className="ds-overview-hero__chips" aria-hidden>
                  <span>Model Ready</span>
                  <span>Quality Assured</span>
                  <span>Client Delivery</span>
                </div>
              </article>

              <div className="ds-overview-metrics">
                {DASHBOARD_METRICS.map((metric) => (
                  <article key={metric.label} className="ds-overview-metric">
                    <p className="ds-overview-metric__label">{metric.label}</p>
                    <p className="ds-overview-metric__value">{metric.value}</p>
                    <p className="ds-overview-metric__meta">{metric.meta}</p>
                  </article>
                ))}
              </div>

              <div className="ds-module-grid">
                {DASHBOARD_MODULES.map((module) => (
                  <article key={module.id} className="ds-module-card">
                    <div className="ds-module-card__icon" aria-hidden>
                      <i className={module.icon} />
                    </div>
                    <h3 className="ds-module-card__title">{module.title}</h3>
                    <p className="ds-module-card__text">{module.description}</p>
                    <button
                      type="button"
                      className="ds-module-card__action"
                      onClick={() => setActiveId(module.id)}
                    >
                      {module.action}
                    </button>
                  </article>
                ))}
              </div>
            </section>
          ) : (
            <div
              className={
                activeId === 'analysis'
                  ? 'ds-map-shell ds-map-shell--viewer ds-map-shell--analysis'
                  : 'ds-map-shell ds-map-shell--viewer'
              }
            >
              <div className="ds-map-toolbar">
                <h2 className="ds-map-toolbar__title">
                  {activeId === 'analysis'
                    ? 'Hydrology analysis workspace'
                    : activeId === 'globe'
                      ? '3D globe workspace'
                    : activeId === 'media'
                      ? 'Project media gallery'
                      : 'Live map canvas'}
                </h2>
                <span className="ds-map-toolbar__badge">
                  {activeId === 'analysis'
                    ? 'Stats · Map'
                    : activeId === 'globe'
                      ? 'CesiumJS · 3D'
                    : activeId === 'media'
                      ? 'Images · Videos'
                      : 'React Leaflet'}
                </span>
              </div>
              {activeId === 'analysis' ? (
                <div className="ds-analysis-split">
                  <HydrologyStats
                    floodSimulationLevel={floodSimulationLevel}
                    onFloodSimulationChange={setFloodSimulationLevel}
                  />
                  <div
                    className="ds-map-body"
                    role="region"
                    aria-label="Map viewer"
                  >
                    <MapViewer
                      floodSimulationLevel={floodSimulationLevel}
                    />
                  </div>
                </div>
              ) : activeId === 'media' ? (
                <MediaGallery />
              ) : activeId === 'globe' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="3D globe viewer"
                >
                  <GlobeViewer />
                </div>
              ) : (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Map viewer"
                >
                  <MapViewer floodSimulationLevel={floodSimulationLevel} />
                </div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default Dashboard
