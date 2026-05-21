import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import { useProjects } from '../hooks/useProjects'
import { logout } from '../services/authService'
import type { Project } from '../services/projectService'
import { getProjectFiles, getProjectJobs } from '../services/datasetService'
import { useWorkspaceContext } from '../context/WorkspaceContext'
import { useModal } from '../context/ModalContext'
import './Dashboard.css'

const MapViewer = lazy(() =>
  import('./MapViewer/MapViewer').then((m) => ({ default: m.MapViewer })),
)
const GlobeViewer = lazy(() => import('./GlobeViewer/GlobeViewer'))
const PotreeViewer = lazy(() => import('./GlobeViewer/PotreeViewer'))
const DatasetsPanel = lazy(() => import('./Datasets/DatasetsPanel'))
const DownloadsPanel = lazy(() => import('./Downloads/DownloadsPanel'))
const ComparePanel = lazy(() => import('./Compare/ComparePanel'))
const AdminDashboard = lazy(() => import('./Admin/AdminDashboard'))

const DROID_CLOUD_LOGO_URL =
  'https://www.droidminingsolutions.com/wp-content/uploads/2026/04/ChatGPT-Image-Apr-25-2026-04_33_45-PM.png'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: 'fa-solid fa-house' },
  { id: 'projects', label: 'Projects', icon: 'fa-solid fa-folder-tree' },
  { id: 'admin', label: 'Admin Control', icon: 'fa-solid fa-shield-halved' },
  { id: 'datasets', label: 'Data Catalog', icon: 'fa-solid fa-database' },
  { id: 'map', label: 'Viewer (2D)', icon: 'fa-solid fa-map' },
  { id: 'globe', label: 'Viewer (3D)', icon: 'fa-solid fa-earth-americas' },
  { id: 'compare', label: 'Compare', icon: 'fa-solid fa-code-compare' },
  { id: 'downloads', label: 'Data Downloads', icon: 'fa-solid fa-download' },
] as const

type DashboardMetric = { label: string; value: string; meta: string; icon: string }

const DASHBOARD_MODULES = [
  {
    id: 'map',
    title: '2D Workspace Viewer',
    icon: 'fa-solid fa-map',
    description:
      'Inspect processed rasters and annotations in a clean 2D workspace viewer.',
    action: 'Open 2D viewer',
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
    title: 'Compare Data Views',
    icon: 'fa-solid fa-code-compare',
    description:
      'Compare outcomes across different model configurations and project versions.',
    action: 'Open compare panel',
  },
  {
    id: 'downloads',
    title: 'Data Download Center',
    icon: 'fa-solid fa-file-arrow-down',
    description:
      'Prepare polished output bundles for review, delivery, and archival.',
    action: 'Open download center',
  },
] as const

function formatDisplayDate(dateValue: string): string {
  const date = new Date(dateValue)
  if (Number.isNaN(date.getTime())) return dateValue
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function initialsFromEmail(email: string): string {
  const local = email.split('@')[0] || 'U'
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || 'U'
}

type DashboardProps = {
  user: { id: number; email: string; role?: string }
  onLogout: () => void
}

export function Dashboard({ user, onLogout }: DashboardProps) {
  const modal = useModal()
  const {
    activeId,
    setActiveId,
    selectedProject,
    setSelectedProject,
    managedUser,
    setManagedUser,
    showCreateProject,
    setShowCreateProject,
    createForm,
    setCreateForm,
    shareCopied,
    setShareCopied,
    activeLayers,
  } = useWorkspaceContext()
  const isAdmin = user.role === 'admin'
  const { projects, loading: projectsLoading, error: projectsError, addProject, renameProject } = useProjects(managedUser?.userId)
  const [createProjectError, setCreateProjectError] = useState<string | null>(null)
  const [renamingProject, setRenamingProject] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [dashboardMetrics, setDashboardMetrics] = useState<DashboardMetric[]>([
    { label: 'Projects', value: '0', meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
    { label: 'Datasets', value: '0', meta: 'In selected project', icon: 'fa-solid fa-database' },
    { label: 'Client Data Hub', value: '0', meta: 'Running server tasks', icon: 'fa-solid fa-gear' },
    { label: 'Reports', value: '0', meta: 'Downloadable files', icon: 'fa-solid fa-file-lines' },
  ])

  const visibleNavItems = useMemo(
    () =>
      (selectedProject ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.id === 'projects' || item.id === 'admin'))
        .filter((item) => item.id !== 'admin' || isAdmin),
    [isAdmin, selectedProject],
  )
  const activePointCloudLayer = useMemo(
    () =>
      activeLayers.find(
        (layer) =>
          layer.projectId === selectedProject?.id &&
          String(layer.layerType).toLowerCase() === 'pointcloud' &&
          layer.url.toLowerCase().endsWith('.html'),
      ),
    [activeLayers, selectedProject?.id],
  )

  const handleShare = useCallback(async () => {
    const url = `${window.location.origin}${window.location.pathname}`
    const flashCopied = () => {
      setShareCopied(true)
      window.setTimeout(() => setShareCopied(false), 2200)
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url)
        flashCopied()
        return
      }
      throw new Error('Clipboard unavailable')
    } catch {
      await modal.prompt('Copy share link', 'Clipboard access is blocked. Copy this link manually.', url)
    }
  }, [modal, setShareCopied])

  const openProject = useCallback(
    (project: Project) => {
      setSelectedProject(project)
      setActiveId('dashboard')
    },
    [setActiveId, setSelectedProject],
  )

  useEffect(() => {
    setSelectedProject((prev) => (prev ? projects.find((p) => p.id === prev.id) ?? null : null))
  }, [projects, setSelectedProject])

  useEffect(() => {
    let cancelled = false
    const loadMetrics = async () => {
      if (!selectedProject?.id) {
        setDashboardMetrics([
          { label: 'Projects', value: String(projects.length), meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
          { label: 'Datasets', value: '-', meta: 'Select a project', icon: 'fa-solid fa-database' },
          { label: 'Client Data Hub', value: '-', meta: 'Select a project', icon: 'fa-solid fa-gear' },
          { label: 'Reports', value: '-', meta: 'Select a project', icon: 'fa-solid fa-file-lines' },
        ])
        return
      }
      try {
        const [jobs, files] = await Promise.all([
          getProjectJobs(selectedProject.id),
          getProjectFiles(selectedProject.id),
        ])
        if (cancelled) return
        const processing = jobs.filter((j) => j.status !== 'Completed' && j.status !== 'Failed').length
        const reports = files.filter((f) => f.kind === 'Reports').length
        setDashboardMetrics([
          { label: 'Projects', value: String(projects.length), meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
          { label: 'Datasets', value: String(files.length), meta: 'In selected project', icon: 'fa-solid fa-database' },
          { label: 'Client Data Hub', value: String(processing), meta: 'Running server tasks', icon: 'fa-solid fa-gear' },
          { label: 'Reports', value: String(reports), meta: 'Downloadable files', icon: 'fa-solid fa-file-lines' },
        ])
      } catch {
        if (!cancelled) {
          setDashboardMetrics([
            { label: 'Projects', value: String(projects.length), meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
            { label: 'Datasets', value: '0', meta: 'Unable to load', icon: 'fa-solid fa-database' },
            { label: 'Client Data Hub', value: '0', meta: 'Unable to load', icon: 'fa-solid fa-gear' },
            { label: 'Reports', value: '0', meta: 'Unable to load', icon: 'fa-solid fa-file-lines' },
          ])
        }
      }
    }
    void loadMetrics()
    return () => {
      cancelled = true
    }
  }, [projects.length, selectedProject?.id])

  const createProject = useCallback(async () => {
    setCreateProjectError(null)
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
    } catch (error) {
      setCreateProjectError(error instanceof Error ? error.message : 'Failed to create project')
    }
  }, [
    addProject,
    createForm,
    setActiveId,
    setCreateForm,
    setSelectedProject,
    setShowCreateProject,
  ])

  const handleLogout = async () => {
    await logout()
    onLogout()
  }

  const handleRenameProject = useCallback(async () => {
    if (!selectedProject) return
    const name = await modal.prompt('Rename project', 'Project name', selectedProject.name)
    if (!name?.trim() || name.trim() === selectedProject.name) return
    setRenamingProject(true)
    try {
      const updated = await renameProject(selectedProject.id, name.trim())
      setSelectedProject(updated)
    } catch (error) {
      await modal.alert('Project rename failed', error instanceof Error ? error.message : 'Project rename failed')
    } finally {
      setRenamingProject(false)
    }
  }, [modal, renameProject, selectedProject, setSelectedProject])

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
          {createProjectError ? (
            <p className="ds-projects__error">{createProjectError}</p>
          ) : null}
        </div>
      </div>
    ),
    [createForm, createProject, createProjectError, setCreateForm, setShowCreateProject],
  )

  return (
    <div className={isSidebarCollapsed ? 'ds-dashboard ds-dashboard--sidebar-collapsed' : 'ds-dashboard'}>
      <aside className="ds-sidebar" aria-label="Droid Cloud navigation">
        <div className="ds-sidebar__brand">
          <div className="ds-sidebar__brand-mark">
            <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="ds-sidebar__logo-img" />
          </div>
          <button
            type="button"
            className="ds-sidebar__collapse"
            onClick={() => setIsSidebarCollapsed((value) => !value)}
            aria-label={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <i className={isSidebarCollapsed ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-left'} aria-hidden />
          </button>
        </div>

        <nav className="ds-sidebar__nav">
          {visibleNavItems.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={
                [
                  'ds-sidebar__link',
                  activeId === item.id ? 'ds-sidebar__link--active' : '',
                  item.id === 'admin' ? 'ds-sidebar__link--admin' : '',
                ].filter(Boolean).join(' ')
              }
              onClick={(e) => {
                e.preventDefault()
                if (item.id === 'projects') {
                  setSelectedProject(null)
                }
                if (item.id === 'admin') {
                  setSelectedProject(null)
                }
                setActiveId(item.id)
              }}
              title={item.label}
            >
              <i className={item.icon} aria-hidden />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>

        <div className="ds-sidebar__footer">Droid Cloud Workspace · v1</div>
      </aside>

      <div className="ds-main">
        <header className="ds-topbar">
          <div className="ds-topbar__brand-logo-wrap">
            <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="ds-topbar__brand-logo" />
          </div>
          <div className="ds-topbar__project">
            <span className="ds-topbar__label">Project</span>
            <h1 className="ds-topbar__name">
              {activeId === 'admin'
                ? 'Admin Control Panel'
                : selectedProject
                  ? selectedProject.name
                  : managedUser
                    ? `Managing ${managedUser.email}`
                    : 'Select Project'}
            </h1>
            {selectedProject ? (
              <button
                type="button"
                className="ds-topbar__edit"
                onClick={() => void handleRenameProject()}
                disabled={renamingProject}
                title="Edit project name"
              >
                <i className="fa-solid fa-pen" aria-hidden />
              </button>
            ) : null}
          </div>

          <div className="ds-topbar__actions">
            <button
              type="button"
              className={
                shareCopied
                  ? 'ds-share ds-share--copied'
                  : 'ds-share'
              }
              onClick={() => void handleShare()}
              title="Copy white-label link to this view"
            >
              <i className="fa-solid fa-link" aria-hidden />
              {shareCopied ? 'Copied' : 'Share'}
            </button>
            <div className={isAdmin ? 'ds-profile ds-profile--admin' : 'ds-profile'} role="group" aria-label="User profile">
              <div className="ds-profile__avatar" aria-hidden>
                {isAdmin ? 'AD' : initialsFromEmail(user.email)}
              </div>
              <div className="ds-profile__meta">
                <span className="ds-profile__name">{user.email}</span>
                <span className="ds-profile__role">{isAdmin ? 'Droid Cloud Admin' : 'Droid Cloud User'}</span>
              </div>
            </div>
            <button type="button" className="ds-share" onClick={() => void handleLogout()}>
              Logout
            </button>
          </div>
        </header>

        <main className="ds-content">
          {activeId === 'admin' && isAdmin ? (
            <Suspense fallback={<div className="ds-panel-loading">Loading admin panel...</div>}>
              <AdminDashboard />
            </Suspense>
          ) : activeId === 'projects' && !selectedProject ? (
            <section className="ds-projects" aria-label="Projects list">
              <header className="ds-projects__header">
                <p className="ds-projects__kicker">
                  <i className="fa-solid fa-folder-tree" aria-hidden /> Project Directory
                </p>
                <h2 className="ds-projects__title">
                  {managedUser ? `Managing ${managedUser.email}` : 'Select a project workspace'}
                </h2>
                {managedUser ? (
                  <button
                    type="button"
                    className="ds-project-card__open"
                    onClick={() => {
                      setManagedUser(null)
                      setSelectedProject(null)
                    }}
                  >
                    Exit God Mode
                  </button>
                ) : null}
                <button
                  type="button"
                  className="ds-project-card__open"
                  onClick={() => setShowCreateProject(true)}
                  disabled={Boolean(managedUser)}
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
                      <i className="fa-regular fa-calendar" aria-hidden /> {formatDisplayDate(project.date)}
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
                    Droid Cloud Workspace
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
                {dashboardMetrics.map((metric) => (
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
                    ? 'WORKSPACE - 2D/3D VIEWER'
                    : activeId === 'globe'
                      ? 'WORKSPACE - 2D/3D VIEWER'
                    : activeId === 'datasets'
                      ? 'Data Catalog'
                    : activeId === 'compare'
                      ? 'Data Comparison'
                      : 'Data Downloads'}
                </h2>
                <span className="ds-map-toolbar__badge">
                  {activeId === 'map'
                    ? 'Leaflet · 2D'
                    : activeId === 'globe'
                      ? activePointCloudLayer?.url
                        ? 'Droid 3D Point Cloud'
                        : '3D Model Viewer'
                    : activeId === 'datasets'
                      ? 'Data Catalog'
                    : activeId === 'compare'
                      ? 'Scenario Compare'
                      : 'Export Center'}
                </span>
              </div>
              {activeId === 'map' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Map viewer"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading map…</div>}>
                    <MapViewer projectId={selectedProject!.id} />
                  </Suspense>
                </div>
              ) : activeId === 'globe' ? (
                <div
                  className="ds-map-body ds-map-body--globe"
                  role="region"
                  aria-label="3D globe viewer"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading 3D globe…</div>}>
                    {activePointCloudLayer?.url ? (
                      <PotreeViewer url={activePointCloudLayer.url} />
                    ) : (
                      <GlobeViewer projectId={selectedProject!.id} />
                    )}
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
              ) : activeId === 'compare' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Project compare panel"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading compare panel...</div>}>
                    <ComparePanel projectId={selectedProject?.id} />
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
                        <i className="fa-solid fa-layer-group" />
                      </div>
                      <h3 className="ds-map-placeholder__title">
                        Workspace panel coming next
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

