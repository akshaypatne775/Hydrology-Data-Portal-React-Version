import { useMemo, useState } from 'react'
import type { Project } from '../services/projectService'

export type WorkspaceTabId =
  | 'dashboard'
  | 'projects'
  | 'datasets'
  | 'map'
  | 'globe'
  | 'compare'
  | 'downloads'

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
    }),
    [
      activeId,
      selectedProject,
      floodSimulationLevel,
      showCreateProject,
      createForm,
      shareCopied,
    ],
  )
}
