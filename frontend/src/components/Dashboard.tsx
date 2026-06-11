import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react'
import { useProjects } from '../hooks/useProjects'
import { logout } from '../services/authService'
import type { Project } from '../services/projectService'
import { getProjectFiles, getProjectJobs } from '../services/datasetService'
import type { ProjectFile, ProjectJob } from '../services/datasetService'
import { useWorkspaceContext } from '../context/WorkspaceContext'
import { useModal } from '../context/ModalContext'
import { toSameOriginBackendUrl } from '../lib/apiBase'
import Viewer3DSidebar from './GlobeViewer/Viewer3DSidebar'
import './Dashboard.css'

const MapViewer = lazy(() =>
  import('./MapViewer/MapViewer').then((m) => ({ default: m.MapViewer })),
)
const GlobeViewer = lazy(() => import('./GlobeViewer/GlobeViewer'))
const PointCloudViewer = lazy(() => import('./GlobeViewer/PointCloudViewer'))
const DatasetsPanel = lazy(() => import('./Datasets/DatasetsPanel'))
const DownloadsPanel = lazy(() => import('./Downloads/DownloadsPanel'))
const ComparePanel = lazy(() => import('./Compare/ComparePanel'))
const AdminDashboard = lazy(() => import('./Admin/AdminDashboard'))

const DROID_CLOUD_LOGO_URL =
  'https://www.droidminingsolutions.com/wp-content/uploads/2026/06/Droid-Cloud-Logo.png'
const WORKSPACE_STATE_KEY = 'droid_workspace_state_v1'

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

type DashboardMetric = { label: string; value: string; meta: string; icon: string; active?: boolean }

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
    id: 'globe',
    title: '3D Workspace Viewer',
    icon: 'fa-solid fa-earth-americas',
    description:
      'Review 3D models and point clouds with project-scoped context for inspection and presentation.',
    action: 'Open 3D viewer',
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

const INACTIVE_JOB_STATUSES = new Set(['completed', 'failed', 'web-ready', 'web ready', 'raw'])

function isProcessingJob(job: ProjectJob): boolean {
  return !INACTIVE_JOB_STATUSES.has(String(job.status || '').trim().toLowerCase())
}

function countProjectDatasets(files: ProjectFile[]): number {
  const datasetKeys = new Set<string>()
  for (const file of files) {
    const kind = String(file.kind || file.type || '').trim().toLowerCase()
    if (kind === 'reports' || kind === 'report') continue
    const key = String(file.dataset_id || file.raw_rel_path || file.cog_rel_path || file.name || file.rel_path || '').trim()
    if (key) datasetKeys.add(key)
  }
  return datasetKeys.size
}

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

type Project3DAsset = {
  id: string
  name: string
  url: string
  viewer: 'potree' | 'cesium'
  dedupeKey?: string
}

function normalize3DAssetToken(value: unknown): string {
  const raw = String(value || '').trim()
  if (!raw) return ''
  let normalized = raw
  try {
    normalized = decodeURIComponent(raw)
  } catch {
    normalized = raw
  }
  normalized = normalized
    .replace(/\\/g, '/')
    .replace(/^https?:\/\/[^/]+/i, '')
    .replace(/[?#].*$/, '')
    .toLowerCase()
    .replace(/\/+/g, '/')
    .replace(/^\/+/, '')
    .replace(/\/+$/, '')
  return normalized
}

function canonical3DAssetKey(viewer: Project3DAsset['viewer'], values: unknown[]): string {
  const tokens = values.map(normalize3DAssetToken).filter(Boolean)
  for (const token of tokens) {
    const parts = token.split('/').filter(Boolean)
    const processedIndex = parts.findIndex((part) => part === 'processed')
    if (processedIndex >= 0 && parts[processedIndex + 1]) {
      return `${viewer}:processed:${parts[processedIndex + 1]}`
    }
    const pointCloudIndex = parts.findIndex((part) => part === 'pointcloud' || part === 'pointclouds' || part === 'droid-ept-viewer')
    if (pointCloudIndex >= 0 && parts[pointCloudIndex + 1]) {
      return `${viewer}:pointcloud:${parts[pointCloudIndex + 1]}`
    }
    const eptIndex = parts.findIndex((part) => part === 'ept.json')
    if (eptIndex > 0) {
      return `${viewer}:ept:${parts[eptIndex - 1]}`
    }
    const tilesetIndex = parts.findIndex((part) => part === 'tileset.json')
    if (tilesetIndex > 0) {
      return `${viewer}:tileset:${parts[tilesetIndex - 1]}`
    }
    const htmlIndex = parts.findIndex((part) => part === 'index.html' || part === 'viewer.html' || part.endsWith('.html'))
    if (htmlIndex > 0) {
      return `${viewer}:html:${parts[htmlIndex - 1]}`
    }
  }
  return `${viewer}:${tokens[0] || 'asset'}`
}

function isRawPointCloudAssetUrl(url: string): boolean {
  return /\.(las|laz)(?:[?#].*)?$/i.test(url.trim())
}

function isConvertedPointCloudAssetUrl(url: string): boolean {
  const normalized = normalize3DAssetToken(url)
  return (
    normalized.includes('/droid-ept-viewer/') ||
    normalized.endsWith('/ept.json') ||
    (
      normalized.includes('/processed/') &&
      normalized.endsWith('/ept.json')
    )
  )
}

function isBetter3DAsset(candidate: Project3DAsset, current: Project3DAsset): boolean {
  const candidateUrl = normalize3DAssetToken(candidate.url)
  const currentUrl = normalize3DAssetToken(current.url)
  if (candidate.viewer === 'potree') {
    const candidateConverted = isConvertedPointCloudAssetUrl(candidate.url)
    const currentConverted = isConvertedPointCloudAssetUrl(current.url)
    if (candidateConverted && !currentConverted) return true
    if (!candidateConverted && currentConverted) return false
    if (!isRawPointCloudAssetUrl(candidate.url) && isRawPointCloudAssetUrl(current.url)) return true
    if (isRawPointCloudAssetUrl(candidate.url) && !isRawPointCloudAssetUrl(current.url)) return false
  }
  if (candidateUrl && !currentUrl) return true
  if (!candidateUrl && currentUrl) return false
  const candidateName = candidate.name.trim()
  const currentName = current.name.trim()
  if (candidateName.length > currentName.length && /point|cloud|3d|model/i.test(candidateName)) return true
  return false
}

function specific3DAssetNameKey(asset: Project3DAsset): string {
  const name = normalize3DAssetToken(asset.name)
  if (!name || name === 'point cloud' || name === '3d model') return ''
  return `${asset.viewer}:name:${name}`
}

function set3DAssetOnce(assets: Map<string, Project3DAsset>, asset: Project3DAsset) {
  const key = specific3DAssetNameKey(asset) || asset.dedupeKey || canonical3DAssetKey(asset.viewer, [asset.url, asset.id, asset.name])
  const existing = assets.get(key)
  if (!existing || isBetter3DAsset(asset, existing)) {
    assets.set(key, asset)
  }
}

function project3DAssetsFromFiles(files: ProjectFile[]): Project3DAsset[] {
  const assets = new Map<string, Project3DAsset>()
  for (const file of files) {
    const rawUrl = String(file.layer_url || file.file_url || '').trim()
    const url = toSameOriginBackendUrl(rawUrl) || rawUrl
    if (!url) continue
    const signature = [
      file.kind,
      file.type,
      file.layer_type,
      file.dataset_type,
      file.name,
      url,
    ].map((value) => String(value || '').toLowerCase()).join(' ')
    const viewer = (
      signature.includes('pointcloud') ||
      signature.includes('point cloud') ||
      signature.includes('/droid-ept-viewer/') ||
      signature.includes('/ept.json')
    )
      ? 'potree'
      : (
          signature.includes('3dmodel') ||
          signature.includes('3d model') ||
          signature.includes('tileset.json') ||
          signature.includes('cesium')
        )
        ? 'cesium'
        : null
    if (!viewer) continue
    if (viewer === 'potree' && isRawPointCloudAssetUrl(url)) continue
    const id = String(file.dataset_id || file.rel_path || url)
    const asset: Project3DAsset = {
      id,
      name: String(file.name || file.dataset_id || (viewer === 'potree' ? 'Point Cloud' : '3D Model')),
      url,
      viewer,
      dedupeKey: canonical3DAssetKey(viewer, [file.dataset_id, file.rel_path, file.raw_rel_path, file.file_path, url, file.name]),
    }
    set3DAssetOnce(assets, asset)
  }
  return Array.from(assets.values())
}

function project3DAssetsFromJobs(jobs: ProjectJob[]): Project3DAsset[] {
  const assets = new Map<string, Project3DAsset>()
  for (const job of jobs) {
    const rawUrl = String(job.result_url || '').trim()
    const url = toSameOriginBackendUrl(rawUrl) || rawUrl
    if (!url) continue
    const signature = [job.kind, job.file_name, url]
      .map((value) => String(value || '').toLowerCase())
      .join(' ')
    const viewer = (
      signature.includes('pointcloud') ||
      signature.includes('point cloud') ||
      signature.includes('/droid-ept-viewer/') ||
      signature.includes('/ept.json')
    )
      ? 'potree'
      : (
          signature.includes('3dmodel') ||
          signature.includes('3d model') ||
          signature.includes('tileset.json') ||
          signature.includes('cesium')
        )
        ? 'cesium'
        : null
    if (!viewer) continue
    if (viewer === 'potree' && isRawPointCloudAssetUrl(url)) continue
    const id = String(job.job_id || url)
    const asset: Project3DAsset = {
      id,
      name: String(job.file_name || (viewer === 'potree' ? 'Point Cloud' : '3D Model')),
      url,
      viewer,
      dedupeKey: canonical3DAssetKey(viewer, [job.result_url, job.file_name, job.job_id]),
    }
    set3DAssetOnce(assets, asset)
  }
  return Array.from(assets.values())
}

function isPointCloudViewerLayer(layer: { layerType?: string; url?: string }): boolean {
  const layerType = String(layer.layerType || '').toLowerCase().replace(/[\s_-]+/g, '')
  const url = String(layer.url || '').toLowerCase()
  return (
    layerType.includes('pointcloud') ||
    url.includes('/droid-ept-viewer/') ||
    url.includes('/ept.json')
  )
}

type DashboardProps = {
  user: { id: number; email: string; role?: string; can_access_catalog?: boolean; can_upload_data?: boolean; hidden_tabs?: string[] }
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
    activeViewerTab,
    setActiveViewerTab,
  } = useWorkspaceContext()
  const isAdmin = user.role === 'admin'
  const canAccessDataCatalog = isAdmin || user.can_access_catalog !== false
  const { projects, loading: projectsLoading, error: projectsError, addProject, renameProject, removeProject } = useProjects(managedUser?.userId, isAdmin && !managedUser)
  const [createProjectError, setCreateProjectError] = useState<string | null>(null)
  const [renamingProject, setRenamingProject] = useState(false)
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [project3DAssets, setProject3DAssets] = useState<Project3DAsset[]>([])
  const [selected3DAsset, setSelected3DAsset] = useState<Project3DAsset | null>(null)
  const [dashboardMetrics, setDashboardMetrics] = useState<DashboardMetric[]>([
    { label: 'Projects', value: '0', meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
    { label: 'Datasets', value: '0', meta: 'In selected project', icon: 'fa-solid fa-database' },
    { label: 'Client Data Hub', value: '0', meta: 'Running server tasks', icon: 'fa-solid fa-gear' },
    { label: 'Reports', value: '0', meta: 'Downloadable files', icon: 'fa-solid fa-file-lines' },
  ])

  useEffect(() => {
    const payload = {
      activeId,
      activeViewerTab,
      selectedProjectId: selectedProject?.id || '',
    }
    window.localStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify(payload))
  }, [activeId, activeViewerTab, selectedProject?.id])

  const hiddenClientTabs = useMemo(() => new Set(isAdmin ? [] : user.hidden_tabs ?? []), [isAdmin, user.hidden_tabs])
  const visibleNavItems = useMemo(
    () =>
      (selectedProject ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.id === 'projects' || item.id === 'admin'))
        .filter((item) => item.id !== 'admin' || isAdmin)
        .filter((item) => item.id !== 'datasets' || canAccessDataCatalog)
        .filter((item) => !hiddenClientTabs.has(item.id)),
    [canAccessDataCatalog, hiddenClientTabs, isAdmin, selectedProject],
  )
  const visibleDashboardModules = useMemo(
    () => DASHBOARD_MODULES.filter((module) => !hiddenClientTabs.has(module.id)),
    [hiddenClientTabs],
  )
  const activePointCloudLayer = useMemo(
    () =>
      activeLayers.find(
        (layer) =>
          layer.projectId === selectedProject?.id &&
          isPointCloudViewerLayer(layer),
      ),
    [activeLayers, selectedProject?.id],
  )
  const active3DLayer = useMemo(
    () =>
      activeLayers.find(
        (layer) =>
          layer.projectId === selectedProject?.id &&
          ['pointcloud', '3dmodel'].includes(String(layer.layerType || '').toLowerCase()) &&
          Boolean(layer.url),
      ),
    [activeLayers, selectedProject?.id],
  )
  const projectPointClouds = useMemo(
    () => project3DAssets.filter((asset) => asset.viewer === 'potree'),
    [project3DAssets],
  )
  const project3DModels = useMemo(
    () => project3DAssets.filter((asset) => asset.viewer === 'cesium'),
    [project3DAssets],
  )
  const selectedPointCloudUrl = selected3DAsset?.viewer === 'potree'
    ? selected3DAsset.url
    : selected3DAsset?.viewer === 'cesium'
      ? ''
      : String(active3DLayer?.layerType || '').toLowerCase() === 'pointcloud'
        ? (active3DLayer?.url && !isRawPointCloudAssetUrl(active3DLayer.url) ? active3DLayer.url : '')
        : ''
  const selectedPointCloudDatasetId = selected3DAsset?.viewer === 'potree'
    ? selected3DAsset.id
    : String(active3DLayer?.layerType || '').toLowerCase() === 'pointcloud'
      ? String(active3DLayer?.datasetId || active3DLayer?.id || '')
      : ''

  useEffect(() => {
    if (active3DLayer) setSelected3DAsset(null)
  }, [active3DLayer?.id, active3DLayer?.url])

  const routedViewerId = useMemo(() => {
    if (activeId !== 'map' && activeId !== 'globe') return activeId
    return activeViewerTab === '3D' ? 'globe' : 'map'
  }, [activeId, activeViewerTab])

  useEffect(() => {
    if ((activeId === 'datasets' && !canAccessDataCatalog) || hiddenClientTabs.has(activeId)) {
      setActiveId(selectedProject ? 'dashboard' : 'projects')
    }
  }, [activeId, canAccessDataCatalog, hiddenClientTabs, selectedProject, setActiveId])

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
    setSelectedProject((prev) => {
      if (prev) return projects.find((p) => p.id === prev.id) ?? null
      try {
        const saved = JSON.parse(window.localStorage.getItem(WORKSPACE_STATE_KEY) || '{}') as { selectedProjectId?: string }
        if (!saved.selectedProjectId) return null
        return projects.find((project) => project.id === saved.selectedProjectId) ?? null
      } catch {
        return null
      }
    })
  }, [projects, setSelectedProject])

  useEffect(() => {
    let cancelled = false
    setSelected3DAsset(null)
    if (!selectedProject?.id) {
      setProject3DAssets([])
      return () => {
        cancelled = true
      }
    }
    void Promise.all([
      getProjectFiles(selectedProject.id, true),
      getProjectJobs(selectedProject.id, true),
    ])
      .then(([files, jobs]) => {
        if (cancelled) return
        const combined = new Map<string, Project3DAsset>()
        for (const asset of [...project3DAssetsFromFiles(files), ...project3DAssetsFromJobs(jobs)]) {
          set3DAssetOnce(combined, asset)
        }
        setProject3DAssets(Array.from(combined.values()))
      })
      .catch(() => {
        if (!cancelled) setProject3DAssets([])
      })
    return () => {
      cancelled = true
    }
  }, [selectedProject?.id])

  useEffect(() => {
    if (routedViewerId !== 'globe') return
    const prefetchUrls = projectPointClouds
      .map((asset) => asset.url)
      .filter((url) => url && !isRawPointCloudAssetUrl(url))
      .slice(0, 2)
    for (const url of prefetchUrls) {
      let hash = 0
      for (let index = 0; index < url.length; index += 1) {
        hash = ((hash << 5) - hash + url.charCodeAt(index)) | 0
      }
      const id = `droid-prefetch-${Math.abs(hash)}`
      if (document.getElementById(id)) continue
      const link = document.createElement('link')
      link.id = id
      link.rel = 'prefetch'
      link.as = 'document'
      link.href = url
      document.head.appendChild(link)
    }
  }, [projectPointClouds, routedViewerId])

  useEffect(() => {
    let cancelled = false
    let refreshTimer: number | undefined
    const loadMetrics = async (forceRefresh = false) => {
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
          getProjectJobs(selectedProject.id, forceRefresh),
          getProjectFiles(selectedProject.id, forceRefresh),
        ])
        if (cancelled) return
        const processing = jobs.filter(isProcessingJob).length
        const datasetCount = countProjectDatasets(files)
        const reports = files.filter((f) => String(f.kind || '').trim().toLowerCase() === 'reports').length
        setDashboardMetrics([
          { label: 'Projects', value: String(projects.length), meta: 'Available workspaces', icon: 'fa-solid fa-folder-tree' },
          { label: 'Datasets', value: String(datasetCount), meta: 'In selected project', icon: 'fa-solid fa-database' },
          { label: 'Client Data Hub', value: String(processing), meta: processing > 0 ? 'Processing active' : 'No active tasks', icon: 'fa-solid fa-gear', active: processing > 0 },
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
    if (selectedProject?.id) {
      refreshTimer = window.setInterval(() => void loadMetrics(true), 10000)
    }
    return () => {
      cancelled = true
      if (refreshTimer) window.clearInterval(refreshTimer)
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

  const handleDeleteProject = useCallback(async (project: Project) => {
    const ok = await modal.confirm(
      'Delete project',
      `Delete "${project.name}"? This removes the project record and its uploaded project files. This action is available only to admins.`,
    )
    if (!ok) return
    const typed = await modal.prompt('Confirm project delete', `Type DELETE to permanently delete "${project.name}".`)
    if (typed !== 'DELETE') return
    try {
      await removeProject(project.id)
      if (selectedProject?.id === project.id) {
        setSelectedProject(null)
        setActiveId('projects')
      }
    } catch (error) {
      await modal.alert('Project delete failed', error instanceof Error ? error.message : 'Project delete failed')
    }
  }, [modal, removeProject, selectedProject?.id, setActiveId, setSelectedProject])

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
    <div
      className={
        routedViewerId === 'globe'
          ? 'ds-dashboard ds-dashboard--3d-mode'
          : isSidebarCollapsed
            ? 'ds-dashboard ds-dashboard--sidebar-collapsed'
            : 'ds-dashboard'
      }
    >
      {routedViewerId === 'globe' ? (
        <Viewer3DSidebar
          pointClouds={projectPointClouds}
          models={project3DModels}
          selectedAsset={selected3DAsset}
          onSelect={setSelected3DAsset}
          onBack={() => {
            setActiveViewerTab('2D')
            setActiveId('map')
          }}
        />
      ) : (
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
                if (item.id === 'map') {
                  setActiveViewerTab('2D')
                }
                if (item.id === 'globe') {
                  setActiveViewerTab('3D')
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
      )}

      <div className="ds-main">
        <header className="ds-topbar">
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
          ) : (activeId === 'projects' || !selectedProject) ? (
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
                    {isAdmin && project.owner_email ? (
                      <p className="ds-project-card__owner">
                        <i className="fa-solid fa-user" aria-hidden />
                        <span>{project.owner_email}</span>
                        {project.owner_user_id ? <small>User ID {project.owner_user_id}</small> : null}
                      </p>
                    ) : null}
                    <div className="ds-project-card__actions">
                      <button
                        type="button"
                        className="ds-project-card__open"
                        onClick={() => openProject(project)}
                      >
                        <i className="fa-solid fa-arrow-up-right-from-square" aria-hidden />
                        Open Project
                      </button>
                      {isAdmin ? (
                        <button
                          type="button"
                          className="ds-project-card__delete"
                          onClick={() => void handleDeleteProject(project)}
                        >
                          <i className="fa-solid fa-trash" aria-hidden />
                          Delete
                        </button>
                      ) : null}
                    </div>
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
                  <article
                    key={metric.label}
                    className={metric.active ? 'ds-overview-metric ds-overview-metric--active' : 'ds-overview-metric'}
                  >
                    <p
                      className={metric.active ? 'ds-overview-metric__icon ds-overview-metric__icon--processing' : 'ds-overview-metric__icon'}
                      aria-hidden
                    >
                      <i className={metric.icon} />
                    </p>
                    <p className="ds-overview-metric__label">{metric.label}</p>
                    <p className="ds-overview-metric__value">{metric.value}</p>
                    <p className="ds-overview-metric__meta">{metric.meta}</p>
                  </article>
                ))}
              </div>

              <div className="ds-module-grid">
                {visibleDashboardModules.map((module) => (
                  <article key={module.id} className="ds-module-card">
                    <div className="ds-module-card__icon" aria-hidden>
                      <i className={module.icon} />
                    </div>
                    <h3 className="ds-module-card__title">{module.title}</h3>
                    <p className="ds-module-card__text">{module.description}</p>
                    <button
                      type="button"
                    className="ds-module-card__action"
                    onClick={() => {
                      if (module.id === 'map') setActiveViewerTab('2D')
                      if (module.id === 'globe') setActiveViewerTab('3D')
                      setActiveId(module.id)
                    }}
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
                routedViewerId === 'map'
                  ? 'ds-map-shell ds-map-shell--viewer ds-map-shell--analysis'
                  : 'ds-map-shell ds-map-shell--viewer'
              }
            >
              <div className="ds-map-toolbar">
                <h2 className="ds-map-toolbar__title">
                  {routedViewerId === 'map'
                    ? 'WORKSPACE - 2D VIEWER'
                    : routedViewerId === 'globe'
                      ? 'WORKSPACE - 3D VIEWER'
                    : activeId === 'datasets'
                      ? 'Data Catalog'
                    : activeId === 'compare'
                      ? 'Data Comparison'
                      : 'Data Downloads'}
                </h2>
                <span className="ds-map-toolbar__badge">
                  {routedViewerId === 'map'
                    ? 'Leaflet - 2D GIS'
                    : routedViewerId === 'globe'
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
              {routedViewerId === 'map' ? (
                <div
                  className="ds-map-body"
                  role="region"
                  aria-label="Map viewer"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading map…</div>}>
                    <MapViewer projectId={selectedProject!.id} />
                  </Suspense>
                </div>
              ) : routedViewerId === 'globe' ? (
                <div
                  className="ds-map-body ds-map-body--globe"
                  role="region"
                  aria-label="3D globe viewer"
                >
                  <Suspense fallback={<div className="ds-panel-loading">Loading 3D globe…</div>}>
                    <>
                      {selectedPointCloudUrl ? (
                      <PointCloudViewer
                        key={selectedPointCloudUrl}
                        url={selectedPointCloudUrl}
                        name={selected3DAsset?.name}
                        projectId={selectedProject!.id}
                        datasetId={selectedPointCloudDatasetId}
                      />
                    ) : (
                      <GlobeViewer key={selected3DAsset?.url || selectedProject!.id} projectId={selectedProject!.id} />
                    )}
                    </>
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

