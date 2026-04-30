import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react'
import { API_BASE, formatApiNetworkError } from '../lib/apiBase'
import { getDatasetStatus, processDatasetTif } from '../services/datasetService'
import { completeUpload, getPointCloudStatus, uploadChunk } from '../services/pointCloudService'
import { saveWebReadyCogLayer } from '../utils/datasetLayerStorage'
import { readUploadedTilesets, writeUploadedTilesets } from '../utils/pointCloudStorage'

const CHUNK_SIZE_BYTES = 10 * 1024 * 1024

export type UploadTask = {
  id: string
  kind: 'dataset' | 'pointcloud'
  projectId: string
  fileName: string
  progressPercent: number
  statusText: string
  state: 'uploading' | 'processing' | 'success' | 'error'
  resultUrl?: string
  datasetId?: string
}

type UploadContextValue = {
  tasks: UploadTask[]
  startDatasetUpload: (file: File, projectId: string) => Promise<void>
  startPointCloudUpload: (file: File, projectId: string) => Promise<void>
  dismissTask: (taskId: string) => void
}

const UploadContext = createContext<UploadContextValue | null>(null)

function taskId(kind: UploadTask['kind'], projectId: string, fileName: string): string {
  return `${kind}:${projectId}:${fileName}:${Date.now()}`
}

export function UploadProvider({ children }: PropsWithChildren) {
  const [tasks, setTasks] = useState<UploadTask[]>([])

  const upsertTask = useCallback((id: string, patch: Partial<UploadTask>) => {
    setTasks((prev) => prev.map((task) => (task.id === id ? { ...task, ...patch } : task)))
  }, [])

  const createTask = useCallback((task: UploadTask) => {
    setTasks((prev) => [task, ...prev.filter((item) => item.id !== task.id)])
  }, [])

  const dismissTask = useCallback((taskId: string) => {
    setTasks((prev) => prev.filter((task) => task.id !== taskId))
  }, [])

  const startPointCloudUpload = useCallback(
    async (file: File, projectId: string) => {
      const id = taskId('pointcloud', projectId, file.name)
      createTask({
        id,
        kind: 'pointcloud',
        projectId,
        fileName: file.name,
        progressPercent: 0,
        statusText: `Uploading ${file.name}...`,
        state: 'uploading',
      })

      const totalChunks = Math.ceil(file.size / CHUNK_SIZE_BYTES)

      try {
        for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
          const start = chunkIndex * CHUNK_SIZE_BYTES
          const end = Math.min(start + CHUNK_SIZE_BYTES, file.size)
          const chunk = file.slice(start, end)
          const chunkForm = new FormData()
          chunkForm.append('filename', file.name)
          chunkForm.append('project_id', projectId)
          chunkForm.append('chunkIndex', String(chunkIndex))
          chunkForm.append('totalChunks', String(totalChunks))
          chunkForm.append('chunk', chunk, `${file.name}.part.${chunkIndex}`)

          const chunkResponse = await uploadChunk(chunkForm)
          if (!chunkResponse.ok) {
            throw new Error(`Chunk upload failed at part ${chunkIndex + 1}`)
          }
          upsertTask(id, {
            progressPercent: ((chunkIndex + 1) / totalChunks) * 100,
            statusText: `Uploading ${file.name}...`,
          })
        }

        const completeResponse = await completeUpload({
          filename: file.name,
          totalChunks,
          project_id: projectId,
        })
        if (!completeResponse.ok) {
          throw new Error('Failed to complete upload merge step')
        }
        const completeData = (await completeResponse.json()) as {
          project_id?: string
          target_tileset_url?: string
          tileset_url?: string
          tileset_id?: string
        }
        const resolvedProjectId = completeData.project_id || projectId
        const targetUrl =
          completeData.target_tileset_url ||
          (completeData.tileset_url && completeData.tileset_url !== 'PENDING'
            ? completeData.tileset_url
            : `${API_BASE}/data/pointclouds/${encodeURIComponent(resolvedProjectId)}/tileset.json`)

        upsertTask(id, {
          state: 'processing',
          statusText: `Processing ${file.name} on server...`,
          resultUrl: targetUrl,
        })

        const started = Date.now()
        while (Date.now() - started < 2 * 60 * 60 * 1000) {
          const status = await getPointCloudStatus(projectId, completeData.tileset_id)
          if (status?.failed) {
            throw new Error(status.error || 'Point cloud conversion failed.')
          }
          if (status?.ready) {
            const readyUrl = status.tileset_url || targetUrl
            const next = [{ label: file.name, url: readyUrl }, ...readUploadedTilesets(projectId).filter((row) => row.url !== readyUrl)]
            writeUploadedTilesets(projectId, next)
            upsertTask(id, {
              state: 'success',
              progressPercent: 100,
              statusText: `${file.name} is ready.`,
              resultUrl: readyUrl,
            })
            return
          }
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
        }
        throw new Error('Timed out waiting for point cloud conversion.')
      } catch (error) {
        upsertTask(id, {
          state: 'error',
          statusText: formatApiNetworkError(API_BASE, error),
        })
      }
    },
    [createTask, upsertTask],
  )

  const startDatasetUpload = useCallback(
    async (file: File, projectId: string) => {
      const id = taskId('dataset', projectId, file.name)
      createTask({
        id,
        kind: 'dataset',
        projectId,
        fileName: file.name,
        progressPercent: 10,
        statusText: `Uploading ${file.name}...`,
        state: 'uploading',
      })

      try {
        const form = new FormData()
        form.append('project_id', projectId)
        form.append('file', file)
        const created = await processDatasetTif(form)
        upsertTask(id, {
          datasetId: created.dataset_id,
          progressPercent: 45,
          state: 'processing',
          statusText: `Converting ${file.name} to COG...`,
        })

        const start = Date.now()
        while (Date.now() - start < 2 * 60 * 60 * 1000) {
          const status = await getDatasetStatus(projectId, created.dataset_id)
          if (status.status === 'Web-Ready') {
            if (status.cog_tile_url_template) {
              saveWebReadyCogLayer(projectId, created.dataset_id, file.name, status.cog_tile_url_template)
            }
            upsertTask(id, {
              state: 'success',
              progressPercent: 100,
              statusText: `${file.name} is Web-Ready.`,
              resultUrl: status.cog_tile_url_template,
            })
            return
          }
          if (status.status === 'Failed') {
            throw new Error(status.error || 'COG conversion failed.')
          }
          upsertTask(id, {
            state: 'processing',
            progressPercent: 60,
            statusText: `Converting ${file.name} to COG...`,
          })
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
        }
        throw new Error('Timed out waiting for COG conversion.')
      } catch (error) {
        upsertTask(id, {
          state: 'error',
          statusText: formatApiNetworkError(API_BASE, error),
        })
      }
    },
    [createTask, upsertTask],
  )

  const value = useMemo(
    () => ({ tasks, startDatasetUpload, startPointCloudUpload, dismissTask }),
    [dismissTask, startDatasetUpload, startPointCloudUpload, tasks],
  )

  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>
}

export function useUploadContext(): UploadContextValue {
  const ctx = useContext(UploadContext)
  if (!ctx) {
    throw new Error('useUploadContext must be used within UploadProvider')
  }
  return ctx
}
