import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { useAuthContext } from '../../context/AuthContext'
import { useModal } from '../../context/ModalContext'
import { API_BASE, toSameOriginBackendUrl } from '../../lib/apiBase'
import { forceDeleteAdminDataset, updateAdminDatasetMetadata } from '../../services/adminService'
import {
  deleteProjectFile,
  getProjectFiles,
  getProjectJobs,
  generateContours,
  invalidateProjectDataCache,
  openManualDatasetFolder,
  readDatasetMetadata,
  syncManualDatasetFolders,
  updateDatasetMetadata,
  type ProjectFile,
  type ProjectJob,
} from '../../services/datasetService'
import './DatasetsPanel.css'

const ALLOWED_EXTENSIONS = new Set(['las', 'laz', 'tif', 'tiff', 'csv', 'zip', 'kml', 'geojson', 'dwg'])
type DatasetType = 'Ortho' | 'DTM' | 'DSM' | 'Point Cloud' | '3D Model' | 'CSV' | 'Vector' | 'CAD'
type DatasetStatus = 'Raw' | 'Processing' | 'Web-Ready'

type DatasetRow = {
  id: string
  fileName: string
  type: DatasetType
  size: string
  status: DatasetStatus
  uploadDate?: string
  filePath?: string
  relPath?: string
  layerType?: 'cog' | 'Ortho' | 'DTM' | 'DSM' | 'pointcloud' | 'PointCloud' | '3DModel' | 'Vector' | 'CAD'
  layerUrl?: string
  datasetId?: string
  month?: string
  datasetType?: string
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
  if (lowered.endsWith('.dwg')) return 'CAD'
  if (lowered.endsWith('.kml') || lowered.endsWith('.geojson')) return 'Vector'
  if (lowered.includes('dtm') || lowered.includes('dem')) return 'DTM'
  if (lowered.includes('dsm')) return 'DSM'
  if (lowered.endsWith('.csv')) return 'CSV'
  if (lowered.endsWith('.zip')) return '3D Model'
  if (lowered.includes('ortho') || lowered.endsWith('.tif') || lowered.endsWith('.tiff')) return 'Ortho'
  return 'Point Cloud'
}

function datasetTypeFromBackend(value?: string): DatasetType | undefined {
  const normalized = (value || '').toLowerCase()
  if (normalized === 'ortho' || normalized === 'orthomosaic') return 'Ortho'
  if (normalized === 'dtm' || normalized === 'dem') return 'DTM'
  if (normalized === 'dsm') return 'DSM'
  if (normalized === 'csv') return 'CSV'
  if (normalized === '3dmodel' || normalized === '3dtiles') return '3D Model'
  if (normalized === 'pointcloud') return 'Point Cloud'
  if (normalized === 'vector') return 'Vector'
  if (normalized === 'cad') return 'CAD'
  return undefined
}

function formatDisplayDate(dateValue?: string): string {
  if (!dateValue) return '--'
  const date = new Date(dateValue)
  if (Number.isNaN(date.getTime())) return dateValue
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function normalizeJobStatus(status: string): DatasetStatus {
  if (status.toLowerCase() === 'web-ready' || status.toUpperCase() === 'WEB-READY') return 'Web-Ready'
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
  const fileType = String(file.type).toLowerCase()
  const type = datasetTypeFromBackend(file.dataset_type) ||
    (file.type === '3DModel' ? '3D Model' : fileType === 'vector' ? 'Vector' : fileType === 'cad' ? 'CAD' : inferDatasetType(file.name))
  const backendLayerType = String(file.layer_type || '')
  const layerType =
    ['Ortho', 'DTM', 'DSM'].includes(backendLayerType) ? backendLayerType as 'Ortho' | 'DTM' | 'DSM' :
    file.type === 'cog' ? (['Ortho', 'DTM', 'DSM'].includes(type) ? type as 'Ortho' | 'DTM' | 'DSM' : 'cog') :
      fileType === 'pointcloud' ? 'pointcloud' :
        file.type === '3DModel' ? '3DModel' :
          fileType === 'vector' ? 'Vector' :
            fileType === 'cad' ? 'CAD' :
          undefined
  const layerUrl =
    layerType === '3DModel'
      ? toSameOriginBackendUrl(file.layer_url) || `${API_BASE}/data/${file.rel_path.replace(/\/$/, '')}/tileset.json`
      : (file.layer_url || '').toLowerCase().endsWith('tileset.json')
        ? `${API_BASE}/data/${file.rel_path.replace(/\/tileset\.json$/i, '').replace(/\/$/, '')}/{z}/{x}/{y}.png`
        : toSameOriginBackendUrl(file.layer_url)
  return {
    id: `file-${file.rel_path}`,
    fileName: file.name,
    type,
    size: formatSize(file.size_bytes),
    status: normalizeJobStatus(file.status),
    uploadDate: formatDisplayDate(file.updated_at),
    filePath: file.file_path || undefined,
    relPath: file.rel_path,
    layerType,
    layerUrl,
    datasetId: file.dataset_id,
    month: file.month,
    datasetType: file.dataset_type,
  }
}

function toBackendDatasetType(type: DatasetType): string {
  if (type === 'Ortho') return 'ortho'
  if (type === 'DTM') return 'dtm'
  if (type === 'DSM') return 'dsm'
  if (type === 'CSV') return 'csv'
  if (type === '3D Model') return '3dmodel'
  if (type === 'Vector') return 'vector'
  if (type === 'CAD') return 'cad'
  return 'pointcloud'
}

export function DatasetsPanel({ projectId }: DatasetsPanelProps) {
  const { tasks, startDatasetUpload, startPointCloudUpload } = useUploadContext()
  const { setActiveId, toggleLayer } = useWorkspaceContext()
  const { isAdmin } = useAuthContext()
  const modal = useModal()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [datasets, setDatasets] = useState<DatasetRow[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [loadingRows, setLoadingRows] = useState(false)
  const [syncingManual, setSyncingManual] = useState(false)
  const [openingManualFolder, setOpeningManualFolder] = useState(false)
  const [detectingEpsg, setDetectingEpsg] = useState(false)
  const [uploadForm, setUploadForm] = useState<UploadFormState>({
    name: '',
    type: 'Point Cloud',
    date: new Date().toISOString().slice(0, 10),
    epsg: '',
  })

  const activeTasks = useMemo(() => tasks.filter((task) => task.projectId === projectId), [projectId, tasks])

  const loadRows = useCallback(async (currentProjectId: string, cacheKey: string, cancelledRef: () => boolean) => {
    try {
      const [jobs, files] = await Promise.all([getProjectJobs(currentProjectId), getProjectFiles(currentProjectId)])
      if (cancelledRef()) return
      const fileRows = files.filter((file) => file.kind !== 'Reports').map(mapProjectFile)
      const fileDatasetIds = new Set(fileRows.map((row) => row.datasetId).filter(Boolean))
      const jobRows: DatasetRow[] = jobs
        .filter((job: ProjectJob) => !fileDatasetIds.has(job.job_id))
        .map((job: ProjectJob) => ({
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
      if (!cancelledRef()) setDatasets([])
    } finally {
      if (!cancelledRef()) setLoadingRows(false)
    }
  }, [])

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
    void loadRows(projectId, cacheKey, () => cancelled)
    const poll = window.setInterval(() => {
      invalidateProjectDataCache(projectId)
      void loadRows(projectId, cacheKey, () => cancelled)
    }, 10000)
    return () => {
      cancelled = true
      window.clearInterval(poll)
    }
  }, [loadRows, projectId])

  useEffect(() => {
    if (!projectId) return
    setDatasets((prev) => {
      const live = activeTasks
        .filter((task) => task.state !== 'success')
        .map((task) => ({
          id: `live-${task.id}`,
          fileName: task.fileName,
          type: datasetTypeFromBackend(task.datasetType) || inferDatasetType(task.fileName),
          size: 'Uploading',
          status: 'Processing',
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
      const defaultName = file.name.replace(/\.[^.]+$/, '')
      setSelectedFile(file)
      setUploadForm({
        name: defaultName,
        type: inferDatasetType(file.name),
        date: new Date().toISOString().slice(0, 10),
        epsg: '',
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
    if (ext.toLowerCase() === 'zip' && uploadForm.type !== '3D Model') {
      await modal.alert('Upload type mismatch', 'ZIP upload is reserved for 3D Model tilesets. Upload DTM, DSM, and Ortho as .tif or .tiff so they open in Viewer (2D).')
      return
    }
    const renamed = new File([selectedFile], `${uploadForm.name.trim()}.${ext}`, {
      type: selectedFile.type,
      lastModified: selectedFile.lastModified,
    })
    if (['tif', 'tiff', 'csv', 'zip', 'kml', 'geojson', 'dwg'].includes(ext.toLowerCase())) {
      await startDatasetUpload(renamed, projectId, {
        datasetType: toBackendDatasetType(uploadForm.type),
        month: uploadForm.date.slice(0, 7),
      })
    } else {
      await startPointCloudUpload(renamed, projectId)
    }
    invalidateProjectDataCache(projectId)
    setSelectedFile(null)
  }, [modal, projectId, selectedFile, startDatasetUpload, startPointCloudUpload, uploadForm.date, uploadForm.name, uploadForm.type])

  const getActionLabel = useCallback((row: DatasetRow) => {
    if (['cog', 'Ortho', 'DTM', 'DSM'].includes(String(row.layerType))) return `Show ${row.type} on Map`
    if (String(row.layerType).toLowerCase() === 'pointcloud') return 'Open Point Cloud'
    if (row.layerType === '3DModel') return 'Show 3D Model'
    if (row.layerType === 'Vector') return 'Show Vector'
    if (row.layerType === 'CAD') return 'CAD Asset'
    return 'Delete'
  }, [])

  const onGenerateContours = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const raw = await modal.prompt('Generate contours', 'Contour interval in meters', '5')
    if (!raw) return
    const interval = Number(raw)
    if (!Number.isFinite(interval) || interval <= 0) {
      await modal.alert('Invalid interval', 'Please enter a valid contour interval.')
      return
    }
    try {
      await generateContours(projectId, { dataset_id: row.datasetId, interval })
      invalidateProjectDataCache(projectId)
      await modal.alert('Contours started', 'Contour generation started. The Vector layer will appear when ready.')
    } catch (error) {
      await modal.alert('Contour generation failed', error instanceof Error ? error.message : 'Contour generation failed')
    }
  }, [modal, projectId])

  const onAdminEditMetadata = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const name = await modal.prompt('Edit metadata', 'Dataset name', row.fileName)
    if (name === null) return
    const date = await modal.prompt('Edit metadata', 'Dataset month/date (YYYY-MM or YYYY-MM-DD)', row.month || '')
    if (date === null) return
    const status = await modal.prompt('Edit metadata', 'Dataset status', row.status)
    if (status === null) return
    try {
      await updateAdminDatasetMetadata(projectId, {
        dataset_id: row.datasetId,
        name: name.trim() || row.fileName,
        date: date.trim(),
        status: status.trim() || row.status,
        dataset_type: row.datasetType || toBackendDatasetType(row.type),
      })
      invalidateProjectDataCache(projectId)
      const cacheKey = `datasets:rows:${projectId}`
      setLoadingRows(true)
      await loadRows(projectId, cacheKey, () => false)
    } catch (error) {
      await modal.alert('Admin metadata update failed', error instanceof Error ? error.message : 'Admin metadata update failed')
    }
  }, [loadRows, modal, projectId])

  const onAdminForceDelete = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const confirmed = await modal.confirm(
      'Force delete dataset',
      `Force delete ${row.fileName}? This removes the selected dataset path from local storage.`,
    )
    if (!confirmed) return
    try {
      await forceDeleteAdminDataset(projectId, row.datasetId)
      invalidateProjectDataCache(projectId)
      setDatasets((prev) => prev.filter((item) => item.id !== row.id))
    } catch (error) {
      await modal.alert('Admin force delete failed', error instanceof Error ? error.message : 'Admin force delete failed')
    }
  }, [modal, projectId])

  const onSyncManual = useCallback(async () => {
    if (!projectId || syncingManual) return
    setSyncingManual(true)
    try {
      const res = await syncManualDatasetFolders(projectId)
      await modal.alert('Manual sync complete', res.message || `Found ${res.new_count} manual datasets`)
      const cacheKey = `datasets:rows:${projectId}`
      setLoadingRows(true)
      await loadRows(projectId, cacheKey, () => false)
    } catch (err) {
      await modal.alert('Manual sync failed', err instanceof Error ? err.message : 'Manual sync failed')
    } finally {
      setSyncingManual(false)
    }
  }, [loadRows, modal, projectId, syncingManual])

  const onOpenManualFolder = useCallback(async () => {
    if (!projectId || openingManualFolder) return
    setOpeningManualFolder(true)
    try {
      const res = await openManualDatasetFolder(projectId)
      await modal.alert('Manual folder', res.message)
    } catch (err) {
      await modal.alert('Cannot open manual folder', err instanceof Error ? err.message : 'Cannot open manual folder')
    } finally {
      setOpeningManualFolder(false)
    }
  }, [modal, openingManualFolder, projectId])

  const onDetectEpsg = useCallback(async () => {
    if (!projectId || !selectedFile || detectingEpsg) return
    setDetectingEpsg(true)
    try {
      const form = new FormData()
      form.append('project_id', projectId)
      form.append('file', selectedFile)
      const meta = await readDatasetMetadata(form)
      setUploadForm((s) => ({ ...s, epsg: meta.epsg || '' }))
      if (!meta.epsg) {
        await modal.alert('EPSG not found', 'EPSG auto detect failed. Please enter it manually if known.')
      }
    } catch {
      await modal.alert('EPSG detect failed', 'EPSG detect failed. Please enter manually.')
    } finally {
      setDetectingEpsg(false)
    }
  }, [detectingEpsg, modal, projectId, selectedFile])

  return (
    <section className="dsp-root">
      <header className="dsp-head">
        <div>
          <h3>Dataset Management</h3>
          <p>Upload only from this panel with metadata and automatic EPSG detection.</p>
        </div>
        <div className="dsp-head__actions">
          <button
            type="button"
            className="dsp-action dsp-action--sync"
            onClick={() => void onOpenManualFolder()}
            disabled={!projectId || openingManualFolder}
          >
            {openingManualFolder ? 'Opening...' : '➕ Add Manually'}
          </button>
          <button
            type="button"
            className="dsp-action dsp-action--sync"
            onClick={() => void onSyncManual()}
            disabled={!projectId || syncingManual}
          >
            {syncingManual ? 'Syncing...' : '🔄 Sync Manual Folders'}
          </button>
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
          accept=".las,.laz,.tif,.tiff,.csv,.zip,.kml,.geojson,.dwg"
          className="gv-file-input"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void prepareFile(file)
          }}
        />
        <p className="dsp-dropzone__title">Drop or Select .las, .laz, .tif, .csv, .zip, .kml, .geojson, .dwg files</p>
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
              <option value="CSV">CSV</option>
              <option value="3D Model">3D Model</option>
              <option value="Point Cloud">Point Cloud</option>
              <option value="Vector">Vector</option>
              <option value="CAD">CAD</option>
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
          <div className="dsp-form__actions">
            <button
              type="button"
              className="dsp-action dsp-action--secondary"
              onClick={() => void onDetectEpsg()}
              disabled={!projectId || !selectedFile || detectingEpsg}
            >
              {detectingEpsg ? 'Detecting EPSG...' : 'Detect EPSG'}
            </button>
            <button type="button" className="dsp-action dsp-action--primary" onClick={() => void submitUpload()}>
              Start Upload
            </button>
          </div>
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
              <th>Upload Date</th>
              <th>Month</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {loadingRows && datasets.length === 0 ? (
              <tr>
                <td colSpan={7}>Loading datasets...</td>
              </tr>
            ) : null}
            {datasets.map((row) => (
              <tr key={row.id}>
                <td>{row.fileName}</td>
                <td>{row.type}</td>
                <td>{row.size}</td>
                <td>{row.uploadDate || '--'}</td>
                <td>
                  {row.datasetId ? (
                    <input
                      className="dsp-month-input"
                      type="month"
                      value={row.month || ''}
                      onChange={async (event) => {
                        if (!projectId || !row.datasetId) return
                        const nextMonth = event.target.value
                        setDatasets((prev) => prev.map((item) => item.id === row.id ? { ...item, month: nextMonth } : item))
                        try {
                          await updateDatasetMetadata(projectId, {
                            dataset_id: row.datasetId,
                            month: nextMonth,
                            dataset_type: row.datasetType || toBackendDatasetType(row.type),
                          })
                        } catch {
                          // keep local UI usable; poll refresh will restore server value if needed
                        }
                      }}
                    />
                  ) : (
                    '--'
                  )}
                </td>
                <td>
                  <span className={row.status === 'Web-Ready' ? 'dsp-badge dsp-badge--ready' : 'dsp-badge dsp-badge--processing'}>
                    {row.status}
                  </span>
                </td>
                <td>
                  <div className="dsp-action-group">
                    {row.layerType && row.layerUrl ? (
                      <button
                        type="button"
                        className="dsp-action"
                        onClick={() => {
                          if (!projectId || !row.layerType || !row.layerUrl) return
                          toggleLayer({
                            id: `${projectId}:${row.fileName}`,
                            projectId,
                            name: row.fileName,
                            layerType: row.layerType,
                            url: row.layerUrl,
                            datasetId: row.datasetId,
                            datasetType: row.datasetType || toBackendDatasetType(row.type),
                            month: row.month,
                          })
                          setActiveId(['cog', 'Ortho', 'DTM', 'DSM', 'Vector'].includes(String(row.layerType)) ? 'map' : 'globe')
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
                    {row.datasetId && ['DTM', 'DSM'].includes(row.type) ? (
                      <button type="button" className="dsp-action" onClick={() => void onGenerateContours(row)}>
                        Contours
                      </button>
                    ) : null}
                    {isAdmin && row.datasetId ? (
                      <button
                        type="button"
                        className="dsp-action dsp-action--secondary"
                        onClick={() => void onAdminEditMetadata(row)}
                      >
                        Edit Metadata
                      </button>
                    ) : null}
                    {isAdmin && row.datasetId ? (
                      <button
                        type="button"
                        className="dsp-action dsp-action--danger"
                        onClick={() => void onAdminForceDelete(row)}
                      >
                        Force Delete
                      </button>
                    ) : null}
                  </div>
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
