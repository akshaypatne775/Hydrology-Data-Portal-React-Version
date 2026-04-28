import { Suspense, lazy, useCallback, useEffect, useMemo } from 'react'
import { useProjects } from '../hooks/useProjects'
import { logout } from '../services/authService'
import type { Project } from '../services/projectService'
import { useWorkspaceContext } from '../context/WorkspaceContext'
import './Dashboard.css'

const HydrologyStats = lazy(() =>
  import('./HydrologyStats/HydrologyStats').then((m) => ({ default: m.HydrologyStats })),
)
const MapViewer = lazy(() =>
  import('./MapViewer/MapViewer').then((m) => ({ default: m.MapViewer })),
)
const GlobeViewer = lazy(() => import('./GlobeViewer/GlobeViewer'))
const DatasetsPanel = lazy(() => import('./Datasets/DatasetsPanel'))
const DownloadsPanel = lazy(() => import('./Downloads/DownloadsPanel'))

const DROID_CLOUD_LOGO_URL =
  'https://www.droidminingsolutions.com/wp-content/uploads/2026/04/ChatGPT-Image-Apr-25-2026-04_33_45-PM.png'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: 'fa-solid fa-house' },
  { id: 'projects', label: 'Projects', icon: 'fa-solid fa-folder-tree' },
  { id: 'datasets', label: 'Datasets', icon: 'fa-solid fa-database' },
  { id: 'map', label: 'Map View', icon: 'fa-solid fa-map' },
  { id: 'globe', label: 'Globe View', icon: 'fa-solid fa-earth-americas' },
  { id: 'compare', label: 'Compare', icon: 'fa-solid fa-code-compare' },
  { id: 'downloads', label: 'Downloads', icon: 'fa-solid fa-download' },
] as const

const DASHBOARD_METRICS = [
  { label: 'Active Modeling Jobs', value: '08', meta: 'Across 3 basins', icon: 'fa-solid fa-wave-square' },
  { label: 'Validated Media Files', value: '214', meta: 'Images and field clips', icon: 'fa-solid fa-photo-film' },
  { label: 'Open Engineering Issues', value: '11', meta: 'Needs review this week', icon: 'fa-solid fa-triangle-exclamation' },
  { label: 'Release Packages', value: '06', meta: 'Ready for stakeholder export', icon: 'fa-solid fa-box-archive' },
]

const DASHBOARD_MODULES = [
  {
    id: 'map',
    title: 'Hydrology Analysis & Modeling',
    icon: 'fa-solid fa-droplet',
    description:
      'Run rainfall scenarios, inspect return-period behavior, and align outputs with map overlays.',
    action: 'Open analysis workspace',
  },
  {
    id: 'datasets',
    title: 'Project Datasets',
    icon: 'fa-solid fa-database',
    description:
      'Access project datasets and prepare structured inputs for map and model workflows.',
    action: 'Open datasets panel',
  },
  {
    id: 'compare',
    title: 'Compare Scenarios',
    icon: 'fa-solid fa-code-compare',
    description:
      'Compare outcomes across different model configurations and project versions.',
    action: 'Open compare panel',
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
  const {
    activeId,
    setActiveId,
    selectedProject,
    setSelectedProject,
    floodSimulationLevel,
    setFloodSimulationLevel,
    showCreateProject,
    setShowCreateProject,
    createForm,
    setCreateForm,
    shareCopied,
    setShareCopied,
  } = useWorkspaceContext()
  const { projects, loading: projectsLoading, error: projectsError, addProject } = useProjects()

  const visibleNavItems = useMemo(
    () =>
      selectedProject ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.id === 'projects'),
    [selectedProject],
  )

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

  const openProject = useCallback(
    (project: Project) => {
      setSelectedProject(project)
      setActiveId('dashboard')
    },
    [setActiveId, setSelectedProject],
  )

  useEffect(() => {
    setSelectedProject((prev) => (prev ? projects.find((p) => p.id === prev.id) ?? null : null))
  }, [projects])

  const createProject = async () => {
    try {
      const project = await addProject(createForm)
      setSelectedProject(project)
      setShowCreateProject(false)
      setActiveId('dashboard')
      setCreateForm({
        name: '',
        location: '',
        date: '',
        status: 'Active',
        type: 'Drone Survey',
      })
    } catch {}
  }

  const handleLogout = async () => {
    await logout()
    onLogout()
  }

  const createProjectForm = useMemo(
    () => (
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
    ),
    [createForm, createProject, setCreateForm, setShowCreateProject],
  )

  return (
    <div className="ds-dashboard">
      <aside className="ds-sidebar" aria-label="Droid Cloud navigation">
        <div className="ds-sidebar__brand">
          <div className="ds-sidebar__brand-mark">
            <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="ds-sidebar__logo-img" />
          </div>
        </div>

        <nav className="ds-sidebar__nav">
          {visibleNavItems.map((item) => (
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
              {selectedProject ? selectedProject.name : 'Select Project'}
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
                      onClick={() => openProject(project)}
                    >
                      <i className="fa-solid fa-arrow-up-right-from-square" aria-hidden />
                      Open Project
                    </button>
                  </article>
                ))}
              </div>
              {showCreateProject ? createProjectForm : null}
            </section>
          ) : activeId === 'dashboard' ? (
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
                activeId === 'map'
                  ? 'ds-map-shell ds-map-shell--viewer ds-map-shell--analysis'
                  : 'ds-map-shell ds-map-shell--viewer'
              }
            >
              <div className="ds-map-toolbar">
                <h2 className="ds-map-toolbar__title">
                  {activeId === 'map'
                    ? 'Map View · Hydrology Analysis'
                    : activeId === 'globe'
                      ? '3D Globe Workspace'
                    : activeId === 'datasets'
                      ? 'Project Datasets'
                    : activeId === 'compare'
                      ? 'Model Comparison'
                      : 'Downloads'}
                </h2>
                <span className="ds-map-toolbar__badge">
                  {activeId === 'map'
                    ? 'Stats · Map'
                    : activeId === 'globe'
                      ? 'CesiumJS · 3D'
                    : activeId === 'datasets'
                      ? 'Data Catalog'
                    : activeId === 'compare'
                      ? 'Scenario Compare'
                      : 'Export Center'}
                </span>
              </div>
              {activeId === 'map' ? (
                <div className="ds-analysis-split">
                  <Suspense fallback={<div className="ds-panel-loading">Loading analytics…</div>}>
                    <HydrologyStats
                      floodSimulationLevel={floodSimulationLevel}
                      onFloodSimulationChange={setFloodSimulationLevel}
                    />
                  </Suspense>
                  <div
                    className="ds-map-body"
                    role="region"
                    aria-label="Map viewer"
                  >
                    <Suspense fallback={<div className="ds-panel-loading">Loading map…</div>}>
                      <MapViewer
                        floodSimulationLevel={floodSimulationLevel}
                        projectId={selectedProject!.id}
                      />
                    </Suspense>
                  </div>
                </div>
              ) : activeId === 'globe' ? (
                <div
                  className="ds-map-body ds-map-body--globe"
                  role="region"
                  aria-label="3D globe viewer"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading 3D globe…</div>}>
                    <GlobeViewer projectId={selectedProject!.id} />
                  </Suspense>
                </div>
              ) : activeId === 'datasets' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Project datasets panel"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading datasets…</div>}>
                    <DatasetsPanel projectId={selectedProject?.id} />
                  </Suspense>
                </div>
              ) : activeId === 'downloads' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Project downloads panel"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading downloads…</div>}>
                    <DownloadsPanel projectId={selectedProject?.id} />
                  </Suspense>
                </div>
              ) : (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Workspace panel"
                >
                  <div className="ds-map-placeholder">
                    <div className="ds-map-placeholder__inner">
                      <div className="ds-map-placeholder__icon" aria-hidden>
                        <i
                          className={
                            activeId === 'compare'
                                ? 'fa-solid fa-code-compare'
                                : 'fa-solid fa-download'
                          }
                        />
                      </div>
                      <h3 className="ds-map-placeholder__title">
                        {activeId === 'compare'
                            ? 'Compare panel coming next'
                            : 'Downloads panel coming next'}
                      </h3>
                      <p className="ds-map-placeholder__text">
                        This workspace is ready and scoped to the selected project.
                      </p>
                    </div>
                  </div>
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
