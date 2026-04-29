import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import {
  deleteProjectFile,
  getProjectFiles,
  getProjectJobs,
  invalidateProjectDataCache,
  readDatasetMetadata,
  type ProjectFile,
  type ProjectJob,
} from '../../services/datasetService'
import './DatasetsPanel.css'

const ALLOWED_EXTENSIONS = new Set(['las', 'laz', 'tif', 'tiff'])
type DatasetType = 'Ortho' | 'DTM' | 'DSM' | 'Point Cloud'
type DatasetStatus = 'Raw' | 'Processing' | 'Web-Ready'

type DatasetRow = {
  id: string
  fileName: string
  type: DatasetType
  size: string
  status: DatasetStatus
  relPath?: string
  layerType?: 'cog' | 'pointcloud'
  layerUrl?: string
}

type DatasetsPanelProps = {
  projectId?: string
}

type UploadFormState = {
  name: string
  type: DatasetType
  date: string
  epsg: string
}

function inferDatasetType(fileName: string): DatasetType {
  const lowered = fileName.toLowerCase()
  if (lowered.includes('dtm')) return 'DTM'
  if (lowered.includes('dsm')) return 'DSM'
  if (lowered.includes('ortho') || lowered.endsWith('.tif') || lowered.endsWith('.tiff')) return 'Ortho'
  return 'Point Cloud'
}

function normalizeJobStatus(status: string): DatasetStatus {
  return status === 'Completed' ? 'Web-Ready' : 'Processing'
}

function formatSize(sizeBytes: string): string {
  const n = Number(sizeBytes)
  if (!Number.isFinite(n) || n <= 0) return '--'
  const gb = n / (1024 * 1024 * 1024)
  if (gb >= 1) return `${gb.toFixed(2)} GB`
  const mb = n / (1024 * 1024)
  return `${mb.toFixed(0)} MB`
}

function mapProjectFile(file: ProjectFile): DatasetRow {
  const type = inferDatasetType(file.name)
  const layerType = file.type === 'cog' ? 'cog' : file.type === 'pointcloud' ? 'pointcloud' : undefined
  return {
    id: `file-${file.rel_path}`,
    fileName: file.name,
    type,
    size: formatSize(file.size_bytes),
    status: normalizeJobStatus(file.status),
    relPath: file.rel_path,
    layerType,
    layerUrl: file.layer_url || undefined,
  }
}

export function DatasetsPanel({ projectId }: DatasetsPanelProps) {
  const { tasks, startDatasetUpload, startPointCloudUpload } = useUploadContext()
  const { setActiveId, toggleLayer } = useWorkspaceContext()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [datasets, setDatasets] = useState<DatasetRow[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [loadingRows, setLoadingRows] = useState(false)
  const [uploadForm, setUploadForm] = useState<UploadFormState>({
    name: '',
    type: 'Point Cloud',
    date: new Date().toISOString().slice(0, 10),
    epsg: '',
  })

  const activeTasks = useMemo(() => tasks.filter((task) => task.projectId === projectId), [projectId, tasks])

  useEffect(() => {
    if (!projectId) return
    const cacheKey = `datasets:rows:${projectId}`
    let cancelled = false
    setLoadingRows(true)
    try {
      const raw = window.sessionStorage.getItem(cacheKey)
      if (raw) {
        const cachedRows = JSON.parse(raw) as DatasetRow[]
        setDatasets(cachedRows)
      }
    } catch {
      // ignore cache parse issues
    }
    const load = async () => {
      try {
        const [jobs, files] = await Promise.all([getProjectJobs(projectId), getProjectFiles(projectId)])
        if (cancelled) return
        const fileRows = files.filter((file) => file.kind !== 'Reports').map(mapProjectFile)
        const jobRows: DatasetRow[] = jobs.map((job: ProjectJob) => ({
          id: `job-${job.job_id}`,
          fileName: job.file_name,
          type: inferDatasetType(job.file_name),
          size: '--',
          status: normalizeJobStatus(job.status),
        }))
        const mergedMap = new Map<string, DatasetRow>()
        ;[...fileRows, ...jobRows].forEach((row) => {
          if (!mergedMap.has(row.fileName) || row.layerUrl) mergedMap.set(row.fileName, row)
        })
        const mergedRows = [...mergedMap.values()]
        setDatasets(mergedRows)
        window.sessionStorage.setItem(cacheKey, JSON.stringify(mergedRows))
      } catch {
        setDatasets([])
      } finally {
        if (!cancelled) setLoadingRows(false)
      }
    }
    void load()
    const poll = window.setInterval(() => {
      invalidateProjectDataCache(projectId)
      void load()
    }, 10000)
    return () => {
      cancelled = true
      window.clearInterval(poll)
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId) return
    setDatasets((prev) => {
      const live = activeTasks.map((task) => ({
        id: `live-${task.id}`,
        fileName: task.fileName,
        type: inferDatasetType(task.fileName),
        size: 'Uploading',
        status: task.state === 'success' ? 'Web-Ready' : 'Processing',
      } as DatasetRow))
      const base = prev.filter((row) => !row.id.startsWith('live-'))
      const mergedMap = new Map<string, DatasetRow>()
      ;[...live, ...base].forEach((row) => {
        if (!mergedMap.has(row.fileName) || row.id.startsWith('live-')) mergedMap.set(row.fileName, row)
      })
      return [...mergedMap.values()]
    })
  }, [activeTasks, projectId])

  const prepareFile = useCallback(
    async (file: File) => {
      if (!projectId) return
      const ext = file.name.split('.').pop()?.toLowerCase() || ''
      if (!ALLOWED_EXTENSIONS.has(ext)) return
      const form = new FormData()
      form.append('project_id', projectId)
      form.append('file', file)
      let epsg = ''
      try {
        const meta = await readDatasetMetadata(form)
        epsg = meta.epsg || ''
      } catch {
        epsg = ''
      }
      const defaultName = file.name.replace(/\.[^.]+$/, '')
      setSelectedFile(file)
      setUploadForm({
        name: defaultName,
        type: inferDatasetType(file.name),
        date: new Date().toISOString().slice(0, 10),
        epsg,
      })
    },
    [projectId],
  )

  const onDropFile = useCallback(async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragging(false)
    const droppedFile = event.dataTransfer.files?.[0]
    if (!droppedFile) return
    await prepareFile(droppedFile)
  }, [prepareFile])

  const submitUpload = useCallback(async () => {
    if (!projectId || !selectedFile || !uploadForm.name.trim()) return
    const ext = selectedFile.name.split('.').pop() || 'dat'
    const renamed = new File([selectedFile], `${uploadForm.name.trim()}.${ext}`, {
      type: selectedFile.type,
      lastModified: selectedFile.lastModified,
    })
    if (ext.toLowerCase() === 'tif' || ext.toLowerCase() === 'tiff') {
      await startDatasetUpload(renamed, projectId)
    } else {
      await startPointCloudUpload(renamed, projectId)
    }
    invalidateProjectDataCache(projectId)
    setSelectedFile(null)
  }, [projectId, selectedFile, startDatasetUpload, startPointCloudUpload, uploadForm.name])

  const getActionLabel = useCallback((row: DatasetRow) => {
    if (row.layerType === 'cog') return 'Show Ortho on Map'
    if (row.layerType === 'pointcloud') return 'Show in Globe'
    return 'Delete'
  }, [])

  return (
    <section className="dsp-root">
      <header className="dsp-head">
        <div>
          <h3>Dataset Management</h3>
          <p>Upload only from this panel with metadata and automatic EPSG detection.</p>
        </div>
      </header>

      <div
        className={isDragging ? 'dsp-dropzone dsp-dropzone--dragging' : 'dsp-dropzone'}
        onClick={() => fileInputRef.current?.click()}
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
          if (!event.currentTarget.contains(event.relatedTarget as Node)) setIsDragging(false)
        }}
        onDrop={(event) => {
          void onDropFile(event)
        }}
        role="button"
        tabIndex={0}
        aria-label="Drop LAS, LAZ, or TIF dataset file"
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".las,.laz,.tif,.tiff"
          className="gv-file-input"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void prepareFile(file)
          }}
        />
        <p className="dsp-dropzone__title">Drop or Select .las, .laz, .tif files</p>
        <p className="dsp-dropzone__meta">After select, fill details and start upload</p>
      </div>

      {selectedFile ? (
        <div className="dsp-form">
          <label>
            Name
            <input
              value={uploadForm.name}
              onChange={(e) => setUploadForm((s) => ({ ...s, name: e.target.value }))}
            />
          </label>
          <label>
            Type
            <select
              value={uploadForm.type}
              onChange={(e) => setUploadForm((s) => ({ ...s, type: e.target.value as DatasetType }))}
            >
              <option value="Ortho">Ortho</option>
              <option value="DTM">DTM</option>
              <option value="DSM">DSM</option>
              <option value="Point Cloud">Point Cloud</option>
            </select>
          </label>
          <label>
            Upload Date
            <input
              type="date"
              value={uploadForm.date}
              onChange={(e) => setUploadForm((s) => ({ ...s, date: e.target.value }))}
            />
          </label>
          <label>
            EPSG (auto-read)
            <input
              value={uploadForm.epsg}
              onChange={(e) => setUploadForm((s) => ({ ...s, epsg: e.target.value }))}
              placeholder="EPSG:32644"
            />
          </label>
          <button type="button" className="dsp-action" onClick={() => void submitUpload()}>
            Start Upload
          </button>
        </div>
      ) : null}

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
            {loadingRows && datasets.length === 0 ? (
              <tr>
                <td colSpan={5}>Loading datasets...</td>
              </tr>
            ) : null}
            {datasets.map((row) => (
              <tr key={row.id}>
                <td>{row.fileName}</td>
                <td>{row.type}</td>
                <td>{row.size}</td>
                <td>
                  <span className={row.status === 'Web-Ready' ? 'dsp-badge dsp-badge--ready' : 'dsp-badge dsp-badge--processing'}>
                    {row.status}
                  </span>
                </td>
                <td>
                  {row.layerType && row.layerUrl ? (
                    <button
                      type="button"
                      className="dsp-action"
                      onClick={() => {
                        if (!projectId || !row.layerType || !row.layerUrl) return
                        toggleLayer({
                          id: `${projectId}:${row.fileName}:${row.layerType}`,
                          projectId,
                          name: row.fileName,
                          layerType: row.layerType,
                          url: row.layerUrl,
                        })
                        setActiveId(row.layerType === 'cog' ? 'map' : 'globe')
                      }}
                    >
                      {getActionLabel(row)}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="dsp-action"
                      onClick={async () => {
                        if (!projectId || !row.relPath) return
                        try {
                          await deleteProjectFile(projectId, row.relPath)
                          setDatasets((prev) => prev.filter((item) => item.id !== row.id))
                          invalidateProjectDataCache(projectId)
                        } catch {
                          // keep UI stable on failure
                        }
                      }}
                      disabled={!row.relPath}
                    >
                      Delete
                    </button>
                  )}
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
