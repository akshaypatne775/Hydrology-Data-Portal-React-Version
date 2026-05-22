import { useCallback, useMemo, useState } from 'react'
import type { Project } from '../services/projectService'

export type WorkspaceTabId =
  | 'dashboard'
  | 'projects'
  | 'admin'
  | 'datasets'
  | 'map'
  | 'globe'
  | 'compare'
  | 'downloads'

export type ActiveViewerTab = '2D' | '3D'

export type ActiveLayerConfig = {
  id: string
  projectId: string
  name: string
  layerType: 'cog' | 'Ortho' | 'DTM' | 'DSM' | 'pointcloud' | 'PointCloud' | '3DModel' | 'Vector' | 'CAD'
  url: string
  rawPath?: string
  datasetId?: string
  datasetType?: string
  month?: string
  processedSize?: string
  uploadDate?: string
  height_offset?: number | string
}

export function useWorkspaceState() {
  const [activeId, setActiveId] = useState<WorkspaceTabId>('projects')
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)
  const [managedUser, setManagedUser] = useState<{ userId: number; email: string } | null>(null)
  const [floodSimulationLevel, setFloodSimulationLevel] = useState(0)
  const [showCreateProject, setShowCreateProject] = useState(false)
  const [createForm, setCreateForm] = useState({
    name: '',
    location: '',
    date: '',
    status: 'Active',
    type: 'Drone Survey',
  })
  const [shareCopied, setShareCopied] = useState(false)
  const [activeLayers, setActiveLayers] = useState<ActiveLayerConfig[]>([])
  const [activeViewerTab, setActiveViewerTab] = useState<ActiveViewerTab>('2D')

  const upsertLayer = useCallback((layerConfig: ActiveLayerConfig) => {
    setActiveLayers((prev) => {
      const existing = prev.find((layer) => layer.id === layerConfig.id)
      if (
        existing &&
        existing.url === layerConfig.url &&
        existing.layerType === layerConfig.layerType &&
        existing.datasetId === layerConfig.datasetId &&
        existing.datasetType === layerConfig.datasetType &&
        existing.height_offset === layerConfig.height_offset
      ) {
        return prev
      }
      return [
        layerConfig,
        ...prev.filter((layer) => layer.id !== layerConfig.id),
      ]
    })
  }, [])

  const toggleLayer = useCallback((layerConfig: ActiveLayerConfig) => {
    setActiveLayers((prev) => {
      const exists = prev.some((layer) => layer.id === layerConfig.id)
      if (exists) {
        return prev.filter((layer) => layer.id !== layerConfig.id)
      }
      return [layerConfig, ...prev]
    })
  }, [])

  const removeLayer = useCallback((layerId: string) => {
    setActiveLayers((prev) => prev.filter((layer) => layer.id !== layerId))
  }, [])

  return useMemo(
    () => ({
      activeId,
      setActiveId,
      selectedProject,
      setSelectedProject,
      managedUser,
      setManagedUser,
      floodSimulationLevel,
      setFloodSimulationLevel,
      showCreateProject,
      setShowCreateProject,
      createForm,
      setCreateForm,
      shareCopied,
      setShareCopied,
      activeLayers,
      activeViewerTab,
      setActiveViewerTab,
      upsertLayer,
      toggleLayer,
      removeLayer,
    }),
    [
      activeId,
      selectedProject,
      managedUser,
      floodSimulationLevel,
      showCreateProject,
      createForm,
      shareCopied,
      activeLayers,
      activeViewerTab,
      upsertLayer,
      toggleLayer,
      removeLayer,
    ],
  )
}
