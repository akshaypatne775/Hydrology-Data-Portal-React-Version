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

  const toggleLayer = useCallback((layerConfig: ActiveLayerConfig) => {
    setActiveLayers((prev) => {
      const exists = prev.some((layer) => layer.id === layerConfig.id)
      if (exists) {
        return prev.filter((layer) => layer.id !== layerConfig.id)
      }
      const is3DLayer = ['3dmodel', 'pointcloud'].includes(String(layerConfig.layerType).toLowerCase())
      const compatible = prev.filter((layer) => {
        const existingIs3D = ['3dmodel', 'pointcloud'].includes(String(layer.layerType).toLowerCase())
        return existingIs3D === is3DLayer
      })
      return [layerConfig, ...compatible]
    })
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
      toggleLayer,
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
      toggleLayer,
    ],
  )
}
