import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react'
import { API_BASE, formatApiNetworkError } from '../lib/apiBase'
import { ApiError } from '../services/api'
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
  datasetType?: string
  progressPercent: number
  statusText: string
  state: 'uploading' | 'processing' | 'success' | 'error'
  stage?: string
  etaText?: string
  startedAt?: number
  resultUrl?: string
  datasetId?: string
}

type UploadContextValue = {
  tasks: UploadTask[]
  startDatasetUpload: (file: File, projectId: string, metadata?: { datasetType?: string; month?: string }) => Promise<void>
  startPointCloudUpload: (file: File, projectId: string) => Promise<void>
  dismissTask: (taskId: string) => void
}

const UploadContext = createContext<UploadContextValue | null>(null)

function taskId(kind: UploadTask['kind'], projectId: string, fileName: string): string {
  return `${kind}:${projectId}:${fileName}:${Date.now()}`
}

function formatEta(seconds?: string | number): string {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n <= 0) return 'Almost done'
  if (n < 60) return `${Math.max(1, Math.round(n))} sec left`
  const minutes = Math.ceil(n / 60)
  return `${minutes} min left`
}

function estimateEtaFromProgress(startedAt: number | undefined, progressPercent: number): string {
  if (!startedAt || progressPercent <= 5 || progressPercent >= 99) return ''
  const elapsed = (Date.now() - startedAt) / 1000
  const remaining = Math.max(0, (elapsed / progressPercent) * (100 - progressPercent))
  return formatEta(remaining)
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
        stage: 'Uploading file',
        startedAt: Date.now(),
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
    async (file: File, projectId: string, metadata?: { datasetType?: string; month?: string }) => {
      const id = taskId('dataset', projectId, file.name)
      createTask({
        id,
        kind: 'dataset',
        projectId,
        fileName: file.name,
        datasetType: metadata?.datasetType,
        progressPercent: 10,
        statusText: `Uploading ${file.name}...`,
        state: 'uploading',
        stage: 'Uploading file',
        etaText: file.size > 0 ? 'Estimating...' : '',
        startedAt: Date.now(),
      })

      try {
        const lowerFileName = file.name.toLowerCase()
        const isCsv = lowerFileName.endsWith('.csv')
        const isPdf = lowerFileName.endsWith('.pdf')
        const isZip = lowerFileName.endsWith('.zip')
        const is3DModel = (metadata?.datasetType || '').toLowerCase() === '3dmodel'
        const form = new FormData()
        form.append('project_id', projectId)
        form.append('file', file)
        if (metadata?.datasetType) form.append('dataset_type', metadata.datasetType)
        if (metadata?.month) form.append('month', metadata.month)
        const created = await processDatasetTif(form)
        upsertTask(id, {
          datasetId: created.dataset_id,
          progressPercent: 45,
          state: 'processing',
          stage: isPdf
            ? 'Preparing report'
            : isCsv
            ? 'Preparing comparison data'
            : isZip && is3DModel
              ? 'Extracting 3D tiles'
              : 'Starting raster tiler',
          etaText: 'Estimating...',
          statusText: isPdf
            ? `Preparing ${file.name} report...`
            : isCsv
            ? `Preparing ${file.name} for compare...`
            : isZip && is3DModel
              ? `Extracting ${file.name} as 3D model...`
              : `Converting ${file.name} to COG...`,
        })
        if (isCsv || isPdf) {
          upsertTask(id, {
            state: 'success',
            progressPercent: 100,
            statusText: isPdf ? `${file.name} report is ready.` : `${file.name} is ready for comparison.`,
            resultUrl: created.cog_tile_url_template,
          })
          return
        }

        const start = Date.now()
        while (Date.now() - start < 2 * 60 * 60 * 1000) {
          let status
          try {
            status = await getDatasetStatus(projectId, created.dataset_id)
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) {
              const elapsedProgress = Math.min(55, 45 + Math.floor((Date.now() - start) / 3000))
              upsertTask(id, {
                state: 'processing',
                progressPercent: elapsedProgress,
                stage: 'Waiting for processor',
                etaText: 'Estimating...',
                statusText: `Waiting for processor - ${file.name}`,
              })
              await new Promise((resolve) => window.setTimeout(resolve, 1500))
              continue
            }
            throw error
          }
          const serverProgress = Number(status.progress_percent)
          const nextProgress = Number.isFinite(serverProgress)
            ? Math.max(45, Math.min(99, serverProgress))
            : Math.min(95, 60 + Math.floor((Date.now() - start) / 4000))
          const stage = status.stage || (isZip && is3DModel ? 'Extracting 3D tiles' : 'Converting raster tiles')
          if (status.status === 'Web-Ready') {
            if (!(isZip && is3DModel) && status.cog_tile_url_template) {
              saveWebReadyCogLayer(projectId, created.dataset_id, file.name, status.cog_tile_url_template)
            }
            upsertTask(id, {
              state: 'success',
              progressPercent: 100,
              stage: 'Web-ready',
              etaText: 'Done',
              statusText: isZip && is3DModel ? `${file.name} 3D model is ready.` : `${file.name} is Web-Ready.`,
              resultUrl: status.cog_tile_url_template,
            })
            return
          }
          if (status.status === 'Failed') {
            throw new Error(status.error || 'COG conversion failed.')
          }
          upsertTask(id, {
            state: 'processing',
            progressPercent: nextProgress,
            stage,
            etaText: status.eta_seconds !== undefined && status.eta_seconds !== ''
              ? formatEta(status.eta_seconds)
              : estimateEtaFromProgress(start, nextProgress),
            statusText: `${stage} - ${file.name}`,
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
