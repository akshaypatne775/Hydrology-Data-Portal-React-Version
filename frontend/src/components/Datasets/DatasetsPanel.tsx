import { useCallback, useEffect, useMemo, useState, type DragEvent } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import { getProjectJobs, type ProjectJob } from '../../services/datasetService'
import './DatasetsPanel.css'
const ALLOWED_EXTENSIONS = new Set(['las', 'laz', 'tif'])
type DatasetType = 'Ortho' | 'DTM' | 'DSM' | 'Point Cloud'
type DatasetStatus = 'Raw' | 'Processing' | 'Web-Ready'

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

function normalizeJobStatus(status: string): DatasetStatus {
  return status === 'Completed' ? 'Web-Ready' : 'Processing'
}

export function DatasetsPanel({ projectId }: DatasetsPanelProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [datasets, setDatasets] = useState<DatasetRow[]>(MOCK_DATASETS)
  const { tasks, startDatasetUpload, startPointCloudUpload } = useUploadContext()

  const activeTasks = useMemo(
    () => tasks.filter((task) => task.projectId === projectId),
    [projectId, tasks],
  )

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    const loadJobs = async () => {
      try {
        const jobs = await getProjectJobs(projectId)
        if (cancelled) return
        const mapped: DatasetRow[] = jobs.map((job: ProjectJob) => ({
          id: `job-${job.job_id}`,
          datasetId: job.job_id,
          fileName: job.file_name,
          type: inferDatasetType(job.file_name),
          size: 'Server Job',
          status: normalizeJobStatus(job.status),
          actionLabel: job.status === 'Completed' ? 'View on Map' : 'Delete',
        }))
        setDatasets((prev) => {
          const keepMocks = prev.filter((row) => !row.id.startsWith('job-'))
          const merged = [...mapped, ...keepMocks]
          return merged.filter(
            (row, index, arr) => arr.findIndex((item) => item.fileName === row.fileName) === index,
          )
        })
      } catch {
        // keep current state
      }
    }
    void loadJobs()
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId) return
    setDatasets((prev) => {
      const liveRows = activeTasks.map((task) => ({
        id: `live-${task.id}`,
        datasetId: task.datasetId,
        fileName: task.fileName,
        type: inferDatasetType(task.fileName),
        size: 'Uploading',
        status: (task.state === 'success' ? 'Web-Ready' : 'Processing') as DatasetStatus,
        actionLabel: (task.state === 'success' ? 'View on Map' : 'Delete') as 'View on Map' | 'Delete',
      }))
      const base = prev.filter((row) => !row.id.startsWith('live-'))
      return [...liveRows, ...base].filter(
        (row, index, arr) => arr.findIndex((item) => item.fileName === row.fileName) === index,
      )
    })
  }, [activeTasks, projectId])

  const handleFile = useCallback(
    async (file: File) => {
      if (!projectId) {
        return
      }
      const extension = file.name.split('.').pop()?.toLowerCase() || ''
      if (!ALLOWED_EXTENSIONS.has(extension)) {
        return
      }

      if (extension === 'tif') {
        await startDatasetUpload(file, projectId)
        return
      }

      await startPointCloudUpload(file, projectId)
    },
    [projectId, startDatasetUpload, startPointCloudUpload],
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

      {activeTasks[0] ? (
        <div className="dsp-progress" aria-live="polite">
          <div className="dsp-progress__track">
            <div className="dsp-progress__fill" style={{ width: `${activeTasks[0].progressPercent}%` }} />
          </div>
          <div className="dsp-progress__meta">
            <span>{`${Math.round(activeTasks[0].progressPercent)}%`}</span>
            <span>{activeTasks[0].statusText}</span>
          </div>
        </div>
      ) : null}

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
    </section>
  )
}

export default DatasetsPanel
