import { useCallback, useEffect, useState } from 'react'
import {
  createProject,
  deleteAdminProject,
  listAdminProjects,
  listAdminUserProjects,
  listProjects,
  updateProjectName,
  type CreateProjectPayload,
  type Project,
} from '../services/projectService'

export function useProjects(managedUserId?: number, includeAllAdminProjects = false) {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = managedUserId
        ? await listAdminUserProjects(managedUserId)
        : includeAllAdminProjects
          ? await listAdminProjects()
          : await listProjects()
      setProjects(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
      setProjects([])
    } finally {
      setLoading(false)
    }
  }, [includeAllAdminProjects, managedUserId])

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

  const removeProject = useCallback(async (projectId: string) => {
    await deleteAdminProject(projectId)
    setProjects((prev) => prev.filter((project) => project.id !== projectId))
  }, [])

  return { projects, loading, error, refresh, addProject, renameProject, removeProject }
}
