import { useCallback, useMemo, useState, type DragEvent } from 'react'
import { API_BASE, formatApiNetworkError } from '../../lib/apiBase'
import { completeUpload, uploadChunk } from '../../services/pointCloudService'
import { getDatasetStatus, processDatasetTif } from '../../services/datasetService'
import { saveWebReadyCogLayer } from '../../utils/datasetLayerStorage'
import './DatasetsPanel.css'

const CHUNK_SIZE_BYTES = 10 * 1024 * 1024
const ALLOWED_EXTENSIONS = new Set(['las', 'laz', 'tif'])

type UploadState = 'idle' | 'uploading' | 'success' | 'error'
type DatasetType = 'Ortho' | 'DTM' | 'DSM' | 'Point Cloud'
type DatasetStatus = 'Raw' | 'Processing' | 'Web-Ready'

type CompleteUploadResponse = {
  project_id?: string
  target_tileset_url?: string
}

type DatasetRow = {
  id: string
  datasetId?: string
  fileName: string
  type: DatasetType
  size: string
  status: DatasetStatus
  actionLabel: 'View on Map' | 'Delete'
}

type DatasetsPanelProps = {
  projectId?: string
}

const MOCK_DATASETS: DatasetRow[] = [
  {
    id: 'd1',
    fileName: 'nagpur_ortho_2026.tif',
    type: 'Ortho',
    size: '1.42 GB',
    status: 'Web-Ready',
    actionLabel: 'View on Map',
  },
  {
    id: 'd2',
    fileName: 'nagpur_dtm_v2.tif',
    type: 'DTM',
    size: '768 MB',
    status: 'Processing',
    actionLabel: 'View on Map',
  },
  {
    id: 'd3',
    fileName: 'sector-b_scan_raw.laz',
    type: 'Point Cloud',
    size: '3.88 GB',
    status: 'Raw',
    actionLabel: 'Delete',
  },
]

function inferDatasetType(fileName: string): DatasetType {
  const lowered = fileName.toLowerCase()
  if (lowered.includes('dtm')) return 'DTM'
  if (lowered.includes('dsm')) return 'DSM'
  if (lowered.includes('ortho') || lowered.endsWith('.tif')) return 'Ortho'
  return 'Point Cloud'
}

function readableSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB'
  const gb = bytes / (1024 * 1024 * 1024)
  if (gb >= 1) return `${gb.toFixed(2)} GB`
  const mb = bytes / (1024 * 1024)
  return `${mb.toFixed(0)} MB`
}

export function DatasetsPanel({ projectId }: DatasetsPanelProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [uploadState, setUploadState] = useState<UploadState>('idle')
  const [progressPercent, setProgressPercent] = useState(0)
  const [statusText, setStatusText] = useState('Drop .las, .laz, .tif files or click to select')
  const [datasets, setDatasets] = useState<DatasetRow[]>(MOCK_DATASETS)

  const progressLabel = useMemo(
    () => `${Math.max(0, Math.min(100, Math.round(progressPercent)))}%`,
    [progressPercent],
  )

  const addDatasetRow = useCallback((file: File, status: DatasetStatus, datasetId?: string) => {
    setDatasets((prev) => [
      {
        id: `${file.name}-${Date.now()}-${datasetId ?? 'local'}`,
        datasetId,
        fileName: file.name,
        type: inferDatasetType(file.name),
        size: readableSize(file.size),
        status,
        actionLabel: status === 'Raw' ? 'Delete' : 'View on Map',
      },
      ...prev,
    ])
  }, [])

  const updateDatasetStatus = useCallback((datasetId: string, status: DatasetStatus) => {
    setDatasets((prev) =>
      prev.map((row) => (row.datasetId === datasetId ? { ...row, status, actionLabel: status === 'Raw' ? 'Delete' : 'View on Map' } : row)),
    )
  }, [])

  const uploadPointCloudInChunks = useCallback(
    async (file: File) => {
      if (!projectId) {
        throw new Error('Project is required for point cloud upload.')
      }
      setUploadState('uploading')
      setProgressPercent(0)
      setStatusText(`Uploading ${file.name}...`)

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
          setProgressPercent(((chunkIndex + 1) / totalChunks) * 100)
        }

        const completeResponse = await completeUpload({
          filename: file.name,
          totalChunks,
          project_id: projectId,
        })
        if (!completeResponse.ok) {
          throw new Error('Failed to complete upload merge step')
        }

        const completeData = (await completeResponse.json()) as CompleteUploadResponse
        const resolvedProjectId = completeData.project_id || projectId
        const resolvedUrl =
          completeData.target_tileset_url ||
          `${API_BASE}/tiles/pointclouds/${encodeURIComponent(resolvedProjectId)}/tileset.json`

        setUploadState('success')
        setStatusText(`Queued for processing. Tileset target: ${resolvedUrl}`)
        addDatasetRow(file, 'Processing')
      } catch (error) {
        setUploadState('error')
        setStatusText(formatApiNetworkError(API_BASE, error))
      }
    },
    [addDatasetRow, projectId],
  )

  const handleFile = useCallback(
    async (file: File) => {
      if (!projectId) {
        setUploadState('error')
        setStatusText('Please select a project before uploading datasets.')
        return
      }
      const extension = file.name.split('.').pop()?.toLowerCase() || ''
      if (!ALLOWED_EXTENSIONS.has(extension)) {
        setUploadState('error')
        setStatusText('Only .las, .laz, and .tif files are supported.')
        return
      }

      if (extension === 'tif') {
        setUploadState('uploading')
        setProgressPercent(15)
        setStatusText(`Uploading ${file.name}...`)

        const form = new FormData()
        form.append('project_id', projectId)
        form.append('file', file)
        const created = await processDatasetTif(form)
        addDatasetRow(file, 'Processing', created.dataset_id)
        setProgressPercent(45)
        setStatusText(`Converting ${file.name} to COG...`)

        const start = Date.now()
        const timeoutMs = 2 * 60 * 60 * 1000
        while (Date.now() - start < timeoutMs) {
          const status = await getDatasetStatus(projectId, created.dataset_id)
          if (status.status === 'Web-Ready') {
            updateDatasetStatus(created.dataset_id, 'Web-Ready')
            if (status.cog_tile_url_template) {
              saveWebReadyCogLayer(projectId, created.dataset_id, file.name, status.cog_tile_url_template)
            }
            setUploadState('success')
            setProgressPercent(100)
            setStatusText(`${file.name} is Web-Ready via COG tiles.`)
            return
          }
          if (status.status === 'Failed') {
            throw new Error(status.error || 'COG conversion failed.')
          }
          setStatusText(`Converting ${file.name} to COG...`)
          setProgressPercent((p) => Math.min(95, Math.max(45, p + 4)))
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
        }
        throw new Error('Timed out waiting for COG conversion.')
      }

      await uploadPointCloudInChunks(file)
    },
    [addDatasetRow, projectId, updateDatasetStatus, uploadPointCloudInChunks],
  )

  const onDropFile = useCallback(
    async (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setIsDragging(false)
      const droppedFile = event.dataTransfer.files?.[0]
      if (!droppedFile) return
      await handleFile(droppedFile)
    },
    [handleFile],
  )

  return (
    <section className="dsp-root">
      <header className="dsp-head">
        <div>
          <h3>Dataset Management</h3>
          <p>Upload and manage project-ready raster and point-cloud datasets.</p>
        </div>
      </header>

      <div
        className={isDragging ? 'dsp-dropzone dsp-dropzone--dragging' : 'dsp-dropzone'}
        onDragEnter={(event) => {
          event.preventDefault()
          setIsDragging(true)
        }}
        onDragOver={(event) => {
          event.preventDefault()
          setIsDragging(true)
        }}
        onDragLeave={(event) => {
          event.preventDefault()
          if (!event.currentTarget.contains(event.relatedTarget as Node)) {
            setIsDragging(false)
          }
        }}
        onDrop={(event) => {
          void onDropFile(event)
        }}
        role="button"
        tabIndex={0}
        aria-label="Drop LAS, LAZ, or TIF dataset file"
      >
        <p className="dsp-dropzone__title">Drop .las, .laz, .tif files here</p>
        <p className="dsp-dropzone__meta">Point cloud uploads are chunked at 10MB</p>
      </div>

      <div className="dsp-progress" aria-live="polite">
        <div className="dsp-progress__track">
          <div className="dsp-progress__fill" style={{ width: `${progressPercent}%` }} />
        </div>
        <div className="dsp-progress__meta">
          <span>{progressLabel}</span>
          <span>{statusText}</span>
        </div>
      </div>

      <div className="dsp-table-wrap">
        <table className="dsp-table">
          <thead>
            <tr>
              <th>File Name</th>
              <th>Type</th>
              <th>Size</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((row) => (
              <tr key={row.id}>
                <td>{row.fileName}</td>
                <td>{row.type}</td>
                <td>{row.size}</td>
                <td>
                  <span
                    className={
                      row.status === 'Raw'
                        ? 'dsp-badge dsp-badge--raw'
                        : row.status === 'Processing'
                          ? 'dsp-badge dsp-badge--processing'
                          : 'dsp-badge dsp-badge--ready'
                    }
                  >
                    {row.status}
                  </span>
                </td>
                <td>
                  <button type="button" className="dsp-action">
                    {row.actionLabel}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {uploadState === 'error' ? <p className="dsp-state dsp-state--error">Upload failed.</p> : null}
      {uploadState === 'success' ? <p className="dsp-state dsp-state--ok">Dataset updated.</p> : null}
    </section>
  )
}

export default DatasetsPanel
