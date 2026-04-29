import { useCallback, useMemo, useState } from 'react'
import type { Project } from '../services/projectService'

export type WorkspaceTabId =
  | 'dashboard'
  | 'projects'
  | 'datasets'
  | 'map'
  | 'globe'
  | 'compare'
  | 'downloads'

export type ActiveLayerConfig = {
  id: string
  projectId: string
  name: string
  layerType: 'cog' | 'pointcloud'
  url: string
  rawPath?: string
}

export function useWorkspaceState() {
  const [activeId, setActiveId] = useState<WorkspaceTabId>('projects')
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)
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
      return [layerConfig, ...prev]
    })
  }, [])

  return useMemo(
    () => ({
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
      activeLayers,
      toggleLayer,
    }),
    [
      activeId,
      selectedProject,
      floodSimulationLevel,
      showCreateProject,
      createForm,
      shareCopied,
      activeLayers,
      toggleLayer,
    ],
  )
}
