import { useCallback, useEffect, useState } from 'react'
import {
  createProject,
  listAdminUserProjects,
  listProjects,
  updateProjectName,
  type CreateProjectPayload,
  type Project,
} from '../services/projectService'

export function useProjects(managedUserId?: number) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = managedUserId ? await listAdminUserProjects(managedUserId) : await listProjects()
      setProjects(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
      setProjects([])
    } finally {
      setLoading(false)
    }
  }, [managedUserId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const addProject = useCallback(async (payload: CreateProjectPayload) => {
    const created = await createProject(payload)
    setProjects((prev) => [created, ...prev])
    return created
  }, [])

  const renameProject = useCallback(async (projectId: string, name: string) => {
    const updated = await updateProjectName(projectId, name)
    setProjects((prev) => prev.map((project) => (project.id === projectId ? updated : project)))
    return updated
  }, [])

  return { projects, loading, error, refresh, addProject, renameProject }
}
