import { useCallback, useEffect, useState } from 'react'
import {
  createProject,
  listProjects,
  type CreateProjectPayload,
  type Project,
} from '../services/projectService'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await listProjects()
      setProjects(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load projects')
      setProjects([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const addProject = useCallback(async (payload: CreateProjectPayload) => {
    const created = await createProject(payload)
    setProjects((prev) => [created, ...prev])
    return created
  }, [])

  return { projects, loading, error, refresh, addProject }
}
