import { apiRequestJson } from './api'

export type Project = {
  id: string
  name: string
  location: string
  date: string
  status: string
  type: string
}

export type CreateProjectPayload = Omit<Project, 'id'>

export async function listProjects(): Promise<Project[]> {
  const data = await apiRequestJson<{ projects: Project[] }>('/api/projects')
  return data.projects ?? []
}

export async function createProject(payload: CreateProjectPayload): Promise<Project> {
  return apiRequestJson<Project>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
