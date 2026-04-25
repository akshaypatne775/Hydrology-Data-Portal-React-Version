import { useCallback, useEffect, useState } from 'react'
import { HydrologyStats } from './HydrologyStats/HydrologyStats'
import GlobeViewer from './GlobeViewer/GlobeViewer'
import { MediaGallery } from './MediaGallery/MediaGallery'
import { MapViewer } from './MapViewer/MapViewer'
import { apiFetch, apiJson } from '../lib/apiBase'
import './Dashboard.css'

const DROID_CLOUD_LOGO_URL =
  'https://www.droidminingsolutions.com/wp-content/uploads/2026/04/ChatGPT-Image-Apr-25-2026-04_33_45-PM.png'

const NAV_ITEMS = [
  { id: 'projects', label: 'Projects', icon: 'fa-solid fa-folder-open' },
  { id: 'overview', label: 'Dashboard Overview', icon: 'fa-solid fa-chart-line' },
  { id: 'map', label: 'Map Viewer', icon: 'fa-solid fa-map-location-dot' },
  { id: 'globe', label: 'Globe View', icon: 'fa-solid fa-earth-asia' },
  { id: 'analysis', label: 'Hydrology Analysis', icon: 'fa-solid fa-droplet' },
  { id: 'media', label: 'Media Gallery', icon: 'fa-solid fa-images' },
  { id: 'issues', label: 'Issue Tracker', icon: 'fa-solid fa-clipboard-list' },
  { id: 'downloads', label: 'Downloads', icon: 'fa-solid fa-file-arrow-down' },
] as const

type Project = {
  id: string
  name: string
  location: string
  date: string
  status: string
  type: string
}

const DASHBOARD_METRICS = [
  { label: 'Active Modeling Jobs', value: '08', meta: 'Across 3 basins', icon: 'fa-solid fa-wave-square' },
  { label: 'Validated Media Files', value: '214', meta: 'Images and field clips', icon: 'fa-solid fa-photo-film' },
  { label: 'Open Engineering Issues', value: '11', meta: 'Needs review this week', icon: 'fa-solid fa-triangle-exclamation' },
  { label: 'Release Packages', value: '06', meta: 'Ready for stakeholder export', icon: 'fa-solid fa-box-archive' },
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

type DashboardProps = {
  user: { id: number; email: string }
  onLogout: () => void
}

export function Dashboard({ user, onLogout }: DashboardProps) {
  const [activeId, setActiveId] = useState<string>('projects')
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [projectsError, setProjectsError] = useState<string | null>(null)
  const [showCreateProject, setShowCreateProject] = useState(false)
  const [createForm, setCreateForm] = useState({
    name: '',
    location: '',
    date: '',
    status: 'Active',
    type: 'Drone Survey',
  })
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

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true)
    setProjectsError(null)
    try {
      const data = await apiJson<{ projects: Project[] }>('/api/projects')
      setProjects(data.projects)
      setSelectedProject((prev) =>
        prev ? data.projects.find((p) => p.id === prev.id) ?? null : null,
      )
    } catch (e) {
      setProjectsError(e instanceof Error ? e.message : 'Failed to load projects')
    } finally {
      setProjectsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  const createProject = async () => {
    try {
      const project = await apiJson<Project>('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(createForm),
      })
      setProjects((prev) => [project, ...prev])
      setSelectedProject(project)
      setShowCreateProject(false)
      setActiveId('overview')
      setCreateForm({
        name: '',
        location: '',
        date: '',
        status: 'Active',
        type: 'Drone Survey',
      })
    } catch (e) {
      setProjectsError(e instanceof Error ? e.message : 'Failed to create project')
    }
  }

  const handleLogout = async () => {
    await apiFetch('/api/auth/logout', { method: 'POST' })
    onLogout()
  }

  return (
    <div className="ds-dashboard">
      <aside className="ds-sidebar" aria-label="Droid Survair navigation">
        <div className="ds-sidebar__brand">
          <div className="ds-sidebar__brand-mark">
            <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="ds-sidebar__logo-img" />
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
                if (item.id === 'projects') {
                  setSelectedProject(null)
                }
                setActiveId(item.id)
              }}
            >
              <i className={item.icon} aria-hidden />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>

        <div className="ds-sidebar__footer">Droid Cloud · v1</div>
      </aside>

      <div className="ds-main">
        <header className="ds-topbar">
          <div className="ds-topbar__brand-logo-wrap">
            <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="ds-topbar__brand-logo" />
          </div>
          <div className="ds-topbar__project">
            <span className="ds-topbar__label">Project</span>
            <h1 className="ds-topbar__name">
              {selectedProject ? selectedProject.name : 'All Projects'}
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
                DC
              </div>
              <div className="ds-profile__meta">
                <span className="ds-profile__name">{user.email}</span>
                <span className="ds-profile__role">Droid Cloud User</span>
              </div>
            </div>
            <button type="button" className="ds-share" onClick={() => void handleLogout()}>
              Logout
            </button>
          </div>
        </header>

        <main className="ds-content">
          {activeId === 'projects' && !selectedProject ? (
            <section className="ds-projects" aria-label="Projects list">
              <header className="ds-projects__header">
                <p className="ds-projects__kicker">
                  <i className="fa-solid fa-folder-tree" aria-hidden /> Project Directory
                </p>
                <h2 className="ds-projects__title">Select a project workspace</h2>
                <button
                  type="button"
                  className="ds-project-card__open"
                  onClick={() => setShowCreateProject(true)}
                >
                  <i className="fa-solid fa-plus" aria-hidden /> Add Project
                </button>
              </header>
              {projectsLoading ? <p>Loading projects...</p> : null}
              {projectsError ? <p className="ds-projects__error">{projectsError}</p> : null}
              <div className="ds-project-grid">
                {projects.map((project) => (
                  <article key={project.id} className="ds-project-card">
                    <div className="ds-project-card__head">
                      <h3 className="ds-project-card__name">
                        <i className="fa-solid fa-diagram-project" aria-hidden /> {project.name}
                      </h3>
                      <span className="ds-project-card__status">{project.status}</span>
                    </div>
                    <p className="ds-project-card__meta">
                      <i className="fa-solid fa-location-dot" aria-hidden /> {project.location}
                    </p>
                    <p className="ds-project-card__meta">
                      <i className="fa-regular fa-calendar" aria-hidden /> {project.date}
                    </p>
                    <p className="ds-project-card__meta">
                      <i className="fa-solid fa-compass-drafting" aria-hidden /> {project.type}
                    </p>
                    <button
                      type="button"
                      className="ds-project-card__open"
                      onClick={() => {
                        setSelectedProject(project)
                        setActiveId('overview')
                      }}
                    >
                      <i className="fa-solid fa-arrow-up-right-from-square" aria-hidden />
                      Open Workspace
                    </button>
                  </article>
                ))}
              </div>
              {showCreateProject ? (
                <div className="ds-project-modal" role="dialog" aria-label="Create project">
                  <div className="ds-project-modal__card">
                    <h3>Create New Project</h3>
                    <label>
                      Name
                      <input
                        value={createForm.name}
                        onChange={(e) => setCreateForm((s) => ({ ...s, name: e.target.value }))}
                      />
                    </label>
                    <label>
                      Location
                      <input
                        value={createForm.location}
                        onChange={(e) => setCreateForm((s) => ({ ...s, location: e.target.value }))}
                      />
                    </label>
                    <label>
                      Date
                      <input
                        value={createForm.date}
                        onChange={(e) => setCreateForm((s) => ({ ...s, date: e.target.value }))}
                        placeholder="April 2026"
                      />
                    </label>
                    <label>
                      Type
                      <input
                        value={createForm.type}
                        onChange={(e) => setCreateForm((s) => ({ ...s, type: e.target.value }))}
                      />
                    </label>
                    <label>
                      Status
                      <input
                        value={createForm.status}
                        onChange={(e) => setCreateForm((s) => ({ ...s, status: e.target.value }))}
                      />
                    </label>
                    <div className="ds-project-modal__actions">
                      <button
                        type="button"
                        className="ds-project-card__open"
                        onClick={() => void createProject()}
                      >
                        Create
                      </button>
                      <button
                        type="button"
                        className="ds-project-modal__cancel"
                        onClick={() => setShowCreateProject(false)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
            </section>
          ) : !selectedProject ? (
            <section className="ds-projects ds-projects--empty" aria-label="Project selection required">
              <p>Select a project from the Projects tab to open tools.</p>
              <button
                type="button"
                className="ds-project-card__open"
                onClick={() => setActiveId('projects')}
              >
                Go to Projects
              </button>
            </section>
          ) : activeId === 'overview' ? (
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
                    <p className="ds-overview-metric__icon" aria-hidden>
                      <i className={metric.icon} />
                    </p>
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
                  className="ds-map-body ds-map-body--globe"
                  role="region"
                  aria-label="3D globe viewer"
                >
                  <GlobeViewer projectId={selectedProject.id} />
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
