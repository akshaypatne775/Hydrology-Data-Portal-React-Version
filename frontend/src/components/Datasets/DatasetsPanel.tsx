import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { useAuthContext } from '../../context/AuthContext'
import { useModal } from '../../context/ModalContext'
import { API_BASE, toSameOriginBackendUrl } from '../../lib/apiBase'
import { forceDeleteAdminDataset, updateAdminDatasetMetadata } from '../../services/adminService'
import { logClientError } from '../../services/errorLogService'
import { getPointCloudStatus } from '../../services/pointCloudService'
import {
  deleteProjectFile,
  exportDatasetGrid,
  getProjectFiles,
  getProjectJobs,
  generateContours,
  invalidateProjectDataCache,
  openManualDatasetFolder,
  syncManualDatasetFolders,
  type ProjectFile,
  type ProjectJob,
} from '../../services/datasetService'
import './DatasetsPanel.css'

const ALLOWED_EXTENSIONS = new Set(['las', 'laz', 'tif', 'tiff', 'csv', 'zip', 'kml', 'geojson', 'dwg', 'pdf'])
type DatasetType = 'Ortho' | 'DTM' | 'DSM' | 'Point Cloud' | '3D Model' | 'CSV' | 'Vector' | 'CAD' | 'Reports'
type DatasetStatus = 'Raw' | 'Processing' | 'Web-Ready'

type DatasetRow = {
  id: string
  fileName: string
  type: DatasetType
  size: string
  status: DatasetStatus
  stage?: string
  progressPercent?: number
  etaText?: string
  uploadDate?: string
  uploadDateRaw?: string
  filePath?: string
  relPath?: string
  layerType?: 'cog' | 'Ortho' | 'DTM' | 'DSM' | 'pointcloud' | 'PointCloud' | '3DModel' | 'Vector' | 'CAD' | 'Reports'
  layerUrl?: string
  downloadUrl?: string
  datasetId?: string
  month?: string
  datasetType?: string
  processedSize?: string
  height_offset?: number | string
  cogPath?: string
  cogRelPath?: string
  rescaleMin?: number | string
  rescaleMax?: number | string
  boundsWgs84?: [number, number, number, number]
  sourceCrs?: string
  detectedEpsg?: string
  manualEpsg?: string
  appliedEpsg?: string
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
  if (lowered.endsWith('.pdf')) return 'Reports'
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
  if (normalized === 'reports' || normalized === 'report' || normalized === 'pdf') return 'Reports'
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

function normalizeProgressPercent(value?: string | number): number | undefined {
  const n = Number(value)
  if (!Number.isFinite(n)) return undefined
  return Math.max(0, Math.min(100, n))
}

function formatEtaLabel(value?: string | number): string | undefined {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return undefined
  if (n < 60) return `${Math.max(1, Math.round(n))} sec left`
  return `${Math.ceil(n / 60)} min left`
}

function formatSize(sizeBytes: string): string {
  const n = Number(sizeBytes)
  if (!Number.isFinite(n) || n <= 0) return '--'
  const gb = n / (1024 * 1024 * 1024)
  if (gb >= 1) return `${gb.toFixed(2)} GB`
  const mb = n / (1024 * 1024)
  return `${mb.toFixed(0)} MB`
}

const GEOTIFF_PROBE_BYTES = 16 * 1024 * 1024

function normalizeEpsgCode(value: number): string {
  if (!Number.isFinite(value) || value <= 0 || value === 32767) return ''
  return value >= 1000 && value <= 999999 ? `EPSG:${value}` : ''
}

function findEpsgInGeoText(text: string): string {
  const projectedPatterns = [
    /PROJ(?:CRS|CS)\s*\[[\s\S]{0,8000}(?:AUTHORITY|ID)\s*\[\s*["']EPSG["']\s*,\s*["']?(\d{4,6})["']?\s*\]/i,
    /ProjectedCSTypeGeoKey[\s\S]{0,200}?(?:EPSG[:\s"']+)?(\d{4,6})/i,
  ]
  for (const pattern of projectedPatterns) {
    const match = text.match(pattern)
    if (match?.[1]) {
      const epsg = normalizeEpsgCode(Number(match[1]))
      if (epsg) return epsg
    }
  }

  const patterns = [
    /(?:AUTHORITY|ID)\s*\[\s*["']EPSG["']\s*,\s*["']?(\d{4,6})["']?\s*\]/gi,
    /EPSG(?::|["'\s,]+)(\d{4,6})/gi,
  ]
  const candidates: string[] = []
  const noisyGeodeticCodes = new Set(['EPSG:4326', 'EPSG:6326', 'EPSG:7030', 'EPSG:8901', 'EPSG:9001', 'EPSG:9122'])
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    pattern.lastIndex = 0
    while ((match = pattern.exec(text)) !== null) {
      const epsg = normalizeEpsgCode(Number(match[1]))
      if (epsg) candidates.push(epsg)
    }
  }
  return candidates.find((epsg) => /^EPSG:32[67]\d{2}$/.test(epsg)) ||
    candidates.find((epsg) => !noisyGeodeticCodes.has(epsg)) ||
    candidates[0] ||
    ''
}

async function detectGeoTiffEpsgLocally(file: File): Promise<string> {
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  if (!['tif', 'tiff'].includes(ext)) return ''
  const buffer = await file.slice(0, Math.min(file.size, GEOTIFF_PROBE_BYTES)).arrayBuffer()
  const view = new DataView(buffer)
  if (view.byteLength < 8) return ''
  const byteOrder = String.fromCharCode(view.getUint8(0), view.getUint8(1))
  const littleEndian = byteOrder === 'II'
  if (!littleEndian && byteOrder !== 'MM') return ''

  const magic = view.getUint16(2, littleEndian)
  const isClassicTiff = magic === 42
  const isBigTiff = magic === 43 && view.byteLength >= 16
  if (!isClassicTiff && !isBigTiff) return ''

  const typeSize: Record<number, number> = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 12: 8, 16: 8 }
  const readU64 = (offset: number) => {
    if (offset + 8 > view.byteLength) return 0
    const value = view.getBigUint64(offset, littleEndian)
    return value > BigInt(Number.MAX_SAFE_INTEGER) ? 0 : Number(value)
  }
  const getOffsetValue = (entryOffset: number, type: number, count: number, inlineBytes: number) => {
    const bytes = (typeSize[type] || 0) * count
    if (!bytes) return 0
    if (bytes <= inlineBytes) return entryOffset + (isBigTiff ? 12 : 8)
    return isBigTiff ? readU64(entryOffset + 12) : view.getUint32(entryOffset + 8, littleEndian)
  }

  let ifdOffset = isBigTiff ? readU64(8) : view.getUint32(4, littleEndian)
  const maxIfdWalk = 8
  let geographicEpsg = ''
  const asciiFallbackValues: string[] = []
  for (let ifdIndex = 0; ifdIndex < maxIfdWalk && ifdOffset > 0 && ifdOffset < view.byteLength; ifdIndex += 1) {
    const rawEntryCount = isBigTiff ? readU64(ifdOffset) : view.getUint16(ifdOffset, littleEndian)
    const entriesStart = ifdOffset + (isBigTiff ? 8 : 2)
    const entrySize = isBigTiff ? 20 : 12
    const inlineBytes = isBigTiff ? 8 : 4
    const entryCount = Math.min(rawEntryCount, Math.floor((view.byteLength - entriesStart) / entrySize))
    let geoKeys: number[] = []
    const asciiValues: string[] = []

    for (let index = 0; index < entryCount; index += 1) {
      const entryOffset = entriesStart + index * entrySize
      if (entryOffset + entrySize > view.byteLength) break
      const tag = view.getUint16(entryOffset, littleEndian)
      const type = view.getUint16(entryOffset + 2, littleEndian)
      const count = isBigTiff ? readU64(entryOffset + 4) : view.getUint32(entryOffset + 4, littleEndian)
      const valueOffset = getOffsetValue(entryOffset, type, count, inlineBytes)
      if (!valueOffset || valueOffset >= view.byteLength) continue

      if (tag === 34735 && type === 3 && count >= 4 && valueOffset + count * 2 <= view.byteLength) {
        geoKeys = Array.from({ length: count }, (_, shortIndex) => view.getUint16(valueOffset + shortIndex * 2, littleEndian))
      }

      if (tag === 34737 && type === 2 && count > 0 && valueOffset + count <= view.byteLength) {
        const bytes = new Uint8Array(buffer, valueOffset, count)
        asciiValues.push(new TextDecoder('utf-8', { fatal: false }).decode(bytes))
      }
    }

    if (geoKeys.length >= 4) {
      const keyCount = geoKeys[3] || 0
      for (let keyIndex = 0; keyIndex < keyCount; keyIndex += 1) {
        const keyOffset = 4 + keyIndex * 4
        const keyId = geoKeys[keyOffset]
        const tiffTagLocation = geoKeys[keyOffset + 1]
        const keyValue = geoKeys[keyOffset + 3]
        if (tiffTagLocation === 0 && keyId === 3072) {
          const epsg = normalizeEpsgCode(keyValue)
          if (epsg) return epsg
        }
        if (tiffTagLocation === 0 && keyId === 2048 && !geographicEpsg) {
          geographicEpsg = normalizeEpsgCode(keyValue)
        }
      }
    }

    const epsgFromAscii = findEpsgInGeoText(asciiValues.join('\n'))
    if (epsgFromAscii && epsgFromAscii !== 'EPSG:4326') return epsgFromAscii
    asciiFallbackValues.push(...asciiValues)

    const nextIfdOffsetLocation = entriesStart + entryCount * entrySize
    if (nextIfdOffsetLocation + (isBigTiff ? 8 : 4) > view.byteLength) break
    ifdOffset = isBigTiff ? readU64(nextIfdOffsetLocation) : view.getUint32(nextIfdOffsetLocation, littleEndian)
  }
  const decodedProbe = new TextDecoder('utf-8', { fatal: false }).decode(new Uint8Array(buffer))
  const epsgFromText = findEpsgInGeoText(`${asciiFallbackValues.join('\n')}\n${decodedProbe}`)
  if (epsgFromText && epsgFromText !== 'EPSG:4326') return epsgFromText
  return geographicEpsg || epsgFromText || ''
}

function displayProjectFileSize(file: ProjectFile): string {
  if (file.processed_size && file.processed_size.trim()) return file.processed_size
  return formatSize(file.size_bytes)
}

function mapProjectFile(file: ProjectFile): DatasetRow {
  const fileType = String(file.type).toLowerCase()
  const type = datasetTypeFromBackend(file.dataset_type) ||
    (file.kind === 'Reports' || fileType === 'pdf' ? 'Reports' :
    (file.type === '3DModel' ? '3D Model' : fileType === 'vector' ? 'Vector' : fileType === 'cad' ? 'CAD' : inferDatasetType(file.name))
    )
  const backendLayerType = String(file.layer_type || '')
  const layerType =
    ['Ortho', 'DTM', 'DSM'].includes(backendLayerType) ? backendLayerType as 'Ortho' | 'DTM' | 'DSM' :
    file.type === 'cog' ? (['Ortho', 'DTM', 'DSM'].includes(type) ? type as 'Ortho' | 'DTM' | 'DSM' : 'cog') :
      fileType === 'pointcloud' ? 'pointcloud' :
        file.type === '3DModel' ? '3DModel' :
          fileType === 'vector' ? 'Vector' :
            fileType === 'cad' ? 'CAD' :
              type === 'Reports' ? 'Reports' :
          undefined
  const preferredViewerUrl = file.viewer_url || file.layer_url || file.file_url
  const rawLayerUrl =
    layerType === 'Reports'
      ? toSameOriginBackendUrl(file.file_url)
      : layerType === '3DModel'
      ? toSameOriginBackendUrl(preferredViewerUrl) || `${API_BASE}/data/${file.rel_path.replace(/\/$/, '')}/tileset.json`
      : (preferredViewerUrl || '').toLowerCase().endsWith('tileset.json')
        ? `${API_BASE}/data/${file.rel_path.replace(/\/tileset\.json$/i, '').replace(/\/$/, '')}/{z}/{x}/{y}.png`
        : toSameOriginBackendUrl(preferredViewerUrl)
  const layerUrl = layerType === 'pointcloud' && rawLayerUrl && isRawPointCloudUrl(rawLayerUrl) ? '' : rawLayerUrl
  const parseBounds = (value?: string): [number, number, number, number] | undefined => {
    if (!value) return undefined
    try {
      const parsed = JSON.parse(value) as unknown
      if (!Array.isArray(parsed) || parsed.length !== 4) return undefined
      const bounds = parsed.map((item) => Number(item))
      if (bounds.every(Number.isFinite)) return bounds as [number, number, number, number]
    } catch {
      return undefined
    }
    return undefined
  }
  return {
    id: file.dataset_id ? `dataset-${file.dataset_id}` : `file-${file.rel_path}`,
    fileName: file.name,
    type,
    size: displayProjectFileSize(file),
    status: normalizeJobStatus(file.status),
    stage: file.stage || undefined,
    progressPercent: normalizeProgressPercent(file.progress_percent),
    etaText: formatEtaLabel(file.eta_seconds),
    uploadDate: formatDisplayDate(file.upload_date || file.updated_at),
    uploadDateRaw: file.upload_date || file.updated_at,
    filePath: file.file_path || undefined,
    relPath: file.rel_path,
    layerType,
    layerUrl,
    downloadUrl: toSameOriginBackendUrl(file.download_url) || toSameOriginBackendUrl(file.file_url),
    datasetId: file.dataset_id,
    month: file.month,
    datasetType: file.dataset_type,
    processedSize: file.processed_size,
    height_offset: file.height_offset,
    cogPath: file.cog_path,
    cogRelPath: file.cog_rel_path,
    rescaleMin: file.rescale_min,
    rescaleMax: file.rescale_max,
    boundsWgs84: parseBounds(file.bounds_wgs84),
    sourceCrs: file.source_crs,
    detectedEpsg: file.detected_epsg,
    manualEpsg: file.manual_epsg,
    appliedEpsg: file.applied_epsg,
  }
}

function epsgLabel(row: DatasetRow): string {
  return row.detectedEpsg || row.appliedEpsg || row.manualEpsg || row.sourceCrs || ''
}

function toBackendDatasetType(type: DatasetType): string {
  if (type === 'Ortho') return 'ortho'
  if (type === 'DTM') return 'dtm'
  if (type === 'DSM') return 'dsm'
  if (type === 'CSV') return 'csv'
  if (type === '3D Model') return '3dmodel'
  if (type === 'Vector') return 'vector'
  if (type === 'CAD') return 'cad'
  if (type === 'Reports') return 'reports'
  return 'pointcloud'
}

function isTwoDLayer(layerType?: DatasetRow['layerType']): boolean {
  return ['cog', 'Ortho', 'DTM', 'DSM', 'Vector'].includes(String(layerType))
}

function isPointCloudLayer(layerType?: DatasetRow['layerType']): boolean {
  return String(layerType).toLowerCase() === 'pointcloud'
}

function isPointCloudRow(row: Pick<DatasetRow, 'layerType' | 'type' | 'fileName'>): boolean {
  return isPointCloudLayer(row.layerType) || row.type === 'Point Cloud' || /\.(las|laz)$/i.test(row.fileName)
}

function datasetMergeKey(
  row: Pick<DatasetRow, 'datasetId' | 'fileName' | 'layerType' | 'type' | 'layerUrl' | 'relPath' | 'cogRelPath' | 'filePath'>,
): string {
  const stableId = row.datasetId || row.relPath || row.cogRelPath || row.layerUrl || row.filePath || row.fileName
  if (isPointCloudRow(row)) {
    if (row.datasetId) return `pointcloud:id:${row.datasetId}`
    const canonicalName = normalizedPointCloudName(row.fileName || String(stableId))
    return `pointcloud:${canonicalName || String(stableId).toLowerCase()}`
  }
  return String(stableId)
}

function isSameDatasetRow(left: DatasetRow, right: DatasetRow): boolean {
  const leftKeys = new Set([
    left.id,
    left.datasetId,
    left.relPath,
    left.cogRelPath,
    left.layerUrl,
    left.filePath,
  ].filter(Boolean))
  return [
    right.id,
    right.datasetId,
    right.relPath,
    right.cogRelPath,
    right.layerUrl,
    right.filePath,
  ].some((key) => Boolean(key) && leftKeys.has(key))
}

function isRawPointCloudUrl(url: string): boolean {
  const normalized = url.trim().toLowerCase().split(/[?#]/, 1)[0] || ''
  return /\.(las|laz)$/i.test(normalized) && !normalized.endsWith('.copc.laz')
}

function isPointCloudViewerUrl(url: string): boolean {
  const normalized = url.trim().toLowerCase().split(/[?#]/, 1)[0] || ''
  return normalized.includes('/droid-ept-viewer/') || normalized.endsWith('/ept.json') || normalized.endsWith('.copc.laz')
}

function eptApiUrlToViewerUrl(eptUrl: string, projectId: string, datasetId: string, displayName: string): string {
  const normalizedEptUrl = toSameOriginBackendUrl(eptUrl) || eptUrl
  const params = new URLSearchParams({
    ept: normalizedEptUrl,
    project: projectId,
    dataset: datasetId || normalizedPointCloudName(displayName),
    name: displayName,
  })
  return `/droid-ept-viewer/index.html?${params.toString()}`
}

function copcApiUrlToViewerUrl(copcUrl: string, projectId: string, datasetId: string, displayName: string): string {
  const normalizedCopcUrl = toSameOriginBackendUrl(copcUrl) || copcUrl
  const params = new URLSearchParams({
    copc: normalizedCopcUrl,
    project: projectId,
    dataset: datasetId || normalizedPointCloudName(displayName),
    name: displayName,
  })
  return `/droid-ept-viewer/index.html?${params.toString()}`
}

function normalizePointCloudViewerUrl(url: string | undefined, projectId: string, datasetId: string, displayName: string): string {
  const sameOriginUrl = toSameOriginBackendUrl(url) || ''
  if (!sameOriginUrl || isRawPointCloudUrl(sameOriginUrl)) return ''
  if (sameOriginUrl.toLowerCase().split(/[?#]/, 1)[0]?.endsWith('.copc.laz')) {
    return copcApiUrlToViewerUrl(sameOriginUrl, projectId, datasetId, displayName)
  }
  if (sameOriginUrl.toLowerCase().split(/[?#]/, 1)[0]?.endsWith('/ept.json')) {
    return eptApiUrlToViewerUrl(sameOriginUrl, projectId, datasetId, displayName)
  }
  return isPointCloudViewerUrl(sameOriginUrl) ? sameOriginUrl : ''
}

function normalizedPointCloudName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/\\/g, '/')
    .split('/')
    .pop()!
    .replace(/\.(copc\.laz|las|laz|json)$/i, '')
    .replace(/^(ept|copc|pointcloud|point-cloud|pc)[_\-\s]+/i, '')
    .replace(/[_\-\s]+(ept|copc|pointcloud|point-cloud|pc)$/i, '')
    .replace(/[-_][a-f0-9]{8,}$/i, '')
    .replace(/[^a-z0-9]+/g, '')
}

function buildActiveLayer(projectId: string, row: DatasetRow) {
  if (!row.layerType || !row.layerUrl) return null
  if (isPointCloudLayer(row.layerType) && isRawPointCloudUrl(row.layerUrl)) return null
  if (row.layerType === 'Reports') return null
  return {
    id: `${projectId}:${row.datasetId || row.fileName}`,
    projectId,
    name: row.fileName,
    layerType: row.layerType,
    url: row.layerUrl,
    datasetId: row.datasetId,
    datasetType: row.datasetType || toBackendDatasetType(row.type),
    month: row.month,
    processedSize: row.processedSize || row.size,
    uploadDate: row.uploadDateRaw,
    height_offset: row.height_offset,
    cogPath: row.cogPath,
    cogRelPath: row.cogRelPath,
    rescaleMin: row.rescaleMin,
    rescaleMax: row.rescaleMax,
    boundsWgs84: row.boundsWgs84,
  }
}

export function DatasetsPanel({ projectId }: DatasetsPanelProps) {
  const { tasks, startDatasetUpload, startPointCloudUpload } = useUploadContext()
  const { setActiveId, setActiveViewerTab, upsertLayer, removeLayer } = useWorkspaceContext()
  const { isAdmin, user } = useAuthContext()
  const canUploadData = isAdmin || user?.can_upload_data === true
  const modal = useModal()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [datasets, setDatasets] = useState<DatasetRow[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [loadingRows, setLoadingRows] = useState(false)
  const [syncingManual, setSyncingManual] = useState(false)
  const [openingManualFolder, setOpeningManualFolder] = useState(false)
  const [detectingEpsg, setDetectingEpsg] = useState(false)
  const [epsgStatus, setEpsgStatus] = useState('')
  const [reportViewer, setReportViewer] = useState<{ name: string; url: string; downloadUrl: string } | null>(null)
  const [generatingContours, setGeneratingContours] = useState<Record<string, boolean>>({})
  const [exportingGrid, setExportingGrid] = useState<Record<string, string>>({})
  const [exportToast, setExportToast] = useState<{ title: string; body: string } | null>(null)
  const [uploadForm, setUploadForm] = useState<UploadFormState>({
    name: '',
    type: 'Point Cloud',
    date: new Date().toISOString().slice(0, 10),
    epsg: '',
  })

  const activeTasks = useMemo(
    () => tasks.filter((task) => task.projectId === projectId && task.state !== 'success'),
    [projectId, tasks],
  )
  const primaryTask = activeTasks.find((task) => task.state === 'uploading' || task.state === 'processing') || activeTasks[0]

  const loadRows = useCallback(async (currentProjectId: string, cacheKey: string, cancelledRef: () => boolean) => {
    try {
      const [jobs, files] = await Promise.all([getProjectJobs(currentProjectId), getProjectFiles(currentProjectId)])
      if (cancelledRef()) return
      const fileRows = files
        .filter((file) => file.kind !== 'Raw Survey Data' && file.kind !== 'Generated Grid Export')
        .map(mapProjectFile)
      const fileDatasetIds = new Set(fileRows.map((row) => row.datasetId).filter(Boolean))
      const jobRows: DatasetRow[] = jobs
        .filter((job: ProjectJob) => !fileDatasetIds.has(job.job_id))
        .map((job: ProjectJob) => ({
          id: `job-${job.job_id}`,
          fileName: job.file_name,
          type: inferDatasetType(job.file_name),
          size: '--',
          status: normalizeJobStatus(job.status),
          stage: job.stage || undefined,
          progressPercent: normalizeProgressPercent(job.progress_percent),
          etaText: formatEtaLabel(job.eta_seconds),
          datasetId: job.job_id,
        }))
      const mergedMap = new Map<string, DatasetRow>()
      ;[...fileRows, ...jobRows].forEach((row) => {
        const key = datasetMergeKey(row)
        const previous = mergedMap.get(key)
        if (!previous || (row.status === 'Web-Ready' && previous.status !== 'Web-Ready') || row.layerUrl) {
          mergedMap.set(key, row)
        }
      })
      const mergedRows = [...mergedMap.values()].sort((a, b) => {
        const aProcessing = a.status === 'Processing' ? 0 : 1
        const bProcessing = b.status === 'Processing' ? 0 : 1
        if (aProcessing !== bProcessing) return aProcessing - bProcessing
        return String(b.uploadDateRaw || b.fileName).localeCompare(String(a.uploadDateRaw || a.fileName))
      })
      setDatasets(mergedRows)
      mergedRows
        .filter((row) => row.status === 'Web-Ready')
        .forEach((row) => {
          const layer = buildActiveLayer(currentProjectId, row)
          if (layer) upsertLayer(layer)
        })
      window.sessionStorage.setItem(cacheKey, JSON.stringify(mergedRows))
    } catch {
      if (!cancelledRef()) setDatasets([])
    } finally {
      if (!cancelledRef()) setLoadingRows(false)
    }
  }, [upsertLayer])

  useEffect(() => {
    if (!projectId) return
    const cacheKey = `datasets:rows:v3:${projectId}`
    let cancelled = false
    setLoadingRows(true)
    try {
      const raw = window.sessionStorage.getItem(cacheKey)
      if (raw) {
        const cachedRows = JSON.parse(raw) as DatasetRow[]
        setDatasets(cachedRows.filter((row) => !isPointCloudRow(row) || Boolean(row.layerUrl)))
      }
    } catch {
      // ignore cache parse issues
    }
    void loadRows(projectId, cacheKey, () => cancelled)
    const poll = window.setInterval(() => {
      invalidateProjectDataCache(projectId)
      void loadRows(projectId, cacheKey, () => cancelled)
    }, activeTasks.length ? 3000 : 10000)
    return () => {
      cancelled = true
      window.clearInterval(poll)
    }
  }, [activeTasks.length, loadRows, projectId])

  useEffect(() => {
    if (!projectId) return
    setDatasets((prev) => {
      const live = activeTasks
        .filter((task) => task.state !== 'success')
        .map((task) => ({
          id: task.datasetId ? `dataset-${task.datasetId}` : `live-${task.id}`,
          fileName: task.fileName,
          type: datasetTypeFromBackend(task.datasetType) || inferDatasetType(task.fileName),
          size: task.state === 'uploading' ? 'Uploading' : '--',
          status: 'Processing',
          stage: task.stage || task.statusText,
          progressPercent: task.progressPercent,
          etaText: task.etaText,
          datasetId: task.datasetId,
        } as DatasetRow))
      const readyPointCloudNames = new Set(
        prev
          .filter((row) => isPointCloudLayer(row.layerType) && row.layerUrl && isPointCloudViewerUrl(row.layerUrl))
          .map((row) => normalizedPointCloudName(row.fileName)),
      )
      const filteredLive = live.filter((row) => !readyPointCloudNames.has(normalizedPointCloudName(row.fileName)))
      const filteredLiveKeys = new Set(filteredLive.map((row) => datasetMergeKey(row)))
      const base = prev.filter((row) => !row.id.startsWith('live-') && !filteredLiveKeys.has(datasetMergeKey(row)))
      const mergedMap = new Map<string, DatasetRow>()
      ;[...filteredLive, ...base].forEach((row) => {
        const key = datasetMergeKey(row)
        const previous = mergedMap.get(key)
        if (
          !previous ||
          (row.status === 'Web-Ready' && previous.status !== 'Web-Ready') ||
          (row.layerUrl && !previous.layerUrl) ||
          (row.id.startsWith('live-') && previous.status !== 'Web-Ready')
        ) {
          mergedMap.set(key, row)
        }
      })
      return [...mergedMap.values()].sort((a, b) => {
        const aProcessing = a.status === 'Processing' ? 0 : 1
        const bProcessing = b.status === 'Processing' ? 0 : 1
        if (aProcessing !== bProcessing) return aProcessing - bProcessing
        return String(b.uploadDateRaw || b.fileName).localeCompare(String(a.uploadDateRaw || a.fileName))
      })
    })
  }, [activeTasks, projectId])

  useEffect(() => {
    if (!exportToast) return
    const timer = window.setTimeout(() => setExportToast(null), 5000)
    return () => window.clearTimeout(timer)
  }, [exportToast])

  const prepareFile = useCallback(
    async (file: File) => {
      if (!projectId || !canUploadData) return
      const ext = file.name.split('.').pop()?.toLowerCase() || ''
      if (!ALLOWED_EXTENSIONS.has(ext)) return
      const defaultName = file.name.replace(/\.[^.]+$/, '')
      setSelectedFile(file)
      setEpsgStatus(['tif', 'tiff'].includes(ext) ? 'Not detected, enter manually' : '')
      setUploadForm({
        name: defaultName,
        type: inferDatasetType(file.name),
        date: new Date().toISOString().slice(0, 10),
        epsg: '',
      })
    },
    [canUploadData, projectId],
  )

  const onDropFile = useCallback(async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragging(false)
    if (!canUploadData) return
    const droppedFile = event.dataTransfer.files?.[0]
    if (!droppedFile) return
    await prepareFile(droppedFile)
  }, [canUploadData, prepareFile])

  const submitUpload = useCallback(async () => {
    if (!projectId || !selectedFile || !uploadForm.name.trim()) return
    if (!canUploadData) {
      await modal.alert('Upload access required', 'Your upload access is currently off. Please ask the admin to enable User Upload for your account.')
      return
    }
    const ext = selectedFile.name.split('.').pop() || 'dat'
    if (ext.toLowerCase() === 'zip' && uploadForm.type !== '3D Model') {
      await modal.alert('Upload type mismatch', 'ZIP upload is reserved for 3D Model tilesets. Upload DTM, DSM, and Ortho as .tif or .tiff so they open in Viewer (2D).')
      return
    }
    const renamed = new File([selectedFile], `${uploadForm.name.trim()}.${ext}`, {
      type: selectedFile.type,
      lastModified: selectedFile.lastModified,
    })
    if (['tif', 'tiff', 'csv', 'zip', 'kml', 'geojson', 'dwg', 'pdf'].includes(ext.toLowerCase())) {
      await startDatasetUpload(renamed, projectId, {
        datasetType: toBackendDatasetType(uploadForm.type),
        month: uploadForm.date.slice(0, 7),
        epsg: uploadForm.epsg.trim(),
      })
    } else {
      await startPointCloudUpload(renamed, projectId)
    }
    invalidateProjectDataCache(projectId)
    setSelectedFile(null)
  }, [canUploadData, modal, projectId, selectedFile, startDatasetUpload, startPointCloudUpload, uploadForm.date, uploadForm.epsg, uploadForm.name, uploadForm.type])

  const getActionLabel = useCallback((row: DatasetRow) => {
    if (['cog', 'Ortho', 'DTM', 'DSM'].includes(String(row.layerType))) return `Show ${row.type} on Map`
    if (String(row.layerType).toLowerCase() === 'pointcloud') return 'Open Point Cloud'
    if (row.layerType === '3DModel') return 'Show 3D Model'
    if (row.layerType === 'Vector') return 'Show Vector'
    if (row.layerType === 'CAD') return 'CAD Asset'
    if (row.layerType === 'Reports') return 'View Report'
    return 'Delete'
  }, [])

  const onGenerateContours = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const contourKey = row.datasetId
    const raw = await modal.prompt('Generate contours', 'Contour interval in meters', '2')
    if (!raw) return
    const interval = Number(raw)
    if (!Number.isFinite(interval) || interval <= 0) {
      await modal.alert('Invalid interval', 'Please enter a valid contour interval.')
      return
    }
    setGeneratingContours((prev) => ({ ...prev, [contourKey]: true }))
    try {
      await generateContours(projectId, { dataset_id: row.datasetId, interval })
      invalidateProjectDataCache(projectId)
      const cacheKey = `datasets:rows:v2:${projectId}`
      setLoadingRows(true)
      await loadRows(projectId, cacheKey, () => false)
      await modal.alert('Contours started', 'Contour generation started. The Vector layer will appear when ready.')
    } catch (error) {
      await modal.alert('Contour generation failed', error instanceof Error ? error.message : 'Contour generation failed')
    } finally {
      setGeneratingContours((prev) => ({ ...prev, [contourKey]: false }))
    }
  }, [loadRows, modal, projectId])

  const onExportGrid = useCallback(async (row: DatasetRow, format: 'csv' | 'dxf') => {
    if (!projectId || !row.datasetId) return
    const raw = await modal.prompt(`Export ${format.toUpperCase()} grid`, 'Grid interval in meters', '2')
    if (raw === null) return
    const interval = Number(raw)
    if (!Number.isFinite(interval) || interval <= 0) {
      await modal.alert('Invalid interval', 'Please enter a valid grid interval greater than zero.')
      return
    }
    const key = `${row.datasetId}:${format}`
    setExportingGrid((prev) => ({ ...prev, [key]: format }))
    try {
      const filename = await exportDatasetGrid(projectId, row.datasetId, { format, interval, fileName: row.fileName })
      window.sessionStorage.removeItem(`datasets:rows:v2:${projectId}`)
      setExportToast({
        title: 'Grid export ready',
        body: `${filename} added to Data Downloads.`,
      })
    } catch (error) {
      await modal.alert('Grid export failed', error instanceof Error ? error.message : 'Grid export failed')
    } finally {
      setExportingGrid((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [modal, projectId])

  const onAdminEditMetadata = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const name = await modal.prompt('Edit metadata', 'Dataset name', row.fileName)
    if (name === null) return
    const date = await modal.prompt('Edit metadata', 'Upload date (YYYY-MM-DD)', row.uploadDateRaw?.slice(0, 10) || '')
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
      const cacheKey = `datasets:rows:v2:${projectId}`
      setLoadingRows(true)
      await loadRows(projectId, cacheKey, () => false)
    } catch (error) {
      await modal.alert('Admin metadata update failed', error instanceof Error ? error.message : 'Admin metadata update failed')
    }
  }, [loadRows, modal, projectId])

  const onAdminEditDate = useCallback(async (row: DatasetRow) => {
    if (!projectId || !row.datasetId) return
    const current = row.uploadDateRaw && /^\d{4}-\d{2}-\d{2}/.test(row.uploadDateRaw)
      ? row.uploadDateRaw.slice(0, 10)
      : new Date().toISOString().slice(0, 10)
    const nextDate = await modal.prompt('Edit upload date', 'Upload date (YYYY-MM-DD)', current)
    if (nextDate === null) return
    const cleanDate = nextDate.trim()
    if (!/^\d{4}-\d{2}-\d{2}$/.test(cleanDate)) {
      await modal.alert('Invalid date', 'Please enter date in YYYY-MM-DD format.')
      return
    }
    try {
      await updateAdminDatasetMetadata(projectId, {
        dataset_id: row.datasetId,
        date: cleanDate,
        dataset_type: row.datasetType || toBackendDatasetType(row.type),
      })
      invalidateProjectDataCache(projectId)
      setDatasets((prev) => prev.map((item) => (
        item.id === row.id
          ? { ...item, uploadDateRaw: cleanDate, uploadDate: formatDisplayDate(cleanDate) }
          : item
      )))
    } catch (error) {
      await modal.alert('Date update failed', error instanceof Error ? error.message : 'Date update failed')
    }
  }, [modal, projectId])

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
      const layer = buildActiveLayer(projectId, row)
      if (layer) removeLayer(layer.id)
      window.sessionStorage.removeItem(`datasets:rows:v2:${projectId}`)
      setDatasets((prev) => prev.filter((item) => !isSameDatasetRow(item, row)))
      void loadRows(projectId, `datasets:rows:v2:${projectId}`, () => false)
    } catch (error) {
      await modal.alert('Admin force delete failed', error instanceof Error ? error.message : 'Admin force delete failed')
    }
  }, [loadRows, modal, projectId, removeLayer])

  const onSyncManual = useCallback(async () => {
    if (!projectId || syncingManual) return
    setSyncingManual(true)
    try {
      const res = await syncManualDatasetFolders(projectId)
      await modal.alert('Manual sync complete', res.message || `Found ${res.new_count} manual datasets`)
      const cacheKey = `datasets:rows:v2:${projectId}`
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
    setEpsgStatus('Detecting EPSG...')
    try {
      const epsg = await detectGeoTiffEpsgLocally(selectedFile)
      if (epsg === 'EPSG:4326' && ['Ortho', 'DTM', 'DSM'].includes(uploadForm.type)) {
        setUploadForm((s) => ({ ...s, epsg: '' }))
        setEpsgStatus('Projected EPSG not found, enter manually')
        return
      }
      setUploadForm((s) => ({ ...s, epsg }))
      if (!epsg) {
        setEpsgStatus('Not detected, enter manually')
      } else {
        setEpsgStatus(`Detected ${epsg}`)
      }
    } catch {
      setEpsgStatus('Detect failed, enter manually')
    } finally {
      setDetectingEpsg(false)
    }
  }, [detectingEpsg, projectId, selectedFile, uploadForm.type])

  return (
    <>
    {reportViewer ? (
      <div className="dsp-report-modal" role="dialog" aria-modal="true" aria-label={`Report preview ${reportViewer.name}`}>
        <div className="dsp-report-modal__shell">
          <div className="dsp-report-modal__bar">
            <div>
              <span>PDF Report</span>
              <strong>{reportViewer.name}</strong>
            </div>
            <div className="dsp-report-modal__actions">
              <a href={reportViewer.downloadUrl} download={reportViewer.name}>
                <i className="fa-solid fa-download" aria-hidden />
                Download
              </a>
              <button type="button" onClick={() => setReportViewer(null)} aria-label="Close report viewer">
                <i className="fa-solid fa-xmark" aria-hidden />
              </button>
            </div>
          </div>
          <object
            data={`${reportViewer.url}#toolbar=0&navpanes=0&scrollbar=0`}
            type="application/pdf"
            width="100%"
            height="100%"
          >
            <p>Unable to display PDF.</p>
          </object>
        </div>
      </div>
    ) : null}
    {exportToast ? (
      <div className="dsp-toast" role="status" aria-live="polite">
        <i className="fa-solid fa-circle-check" aria-hidden />
        <div>
          <strong>{exportToast.title}</strong>
          <span>{exportToast.body}</span>
        </div>
      </div>
    ) : null}
    <section className="dsp-root">
      <header className="dsp-head">
        <div>
          <h3>Dataset Management</h3>
          <p>{canUploadData ? 'Upload from this panel with metadata and automatic EPSG detection.' : 'View and download project datasets from this panel.'}</p>
        </div>
        {isAdmin ? (
        <div className="dsp-head__actions">
          <button
            type="button"
            className="dsp-action dsp-action--sync"
            onClick={() => void onOpenManualFolder()}
            disabled={!projectId || openingManualFolder}
          >
            {openingManualFolder ? 'Opening...' : '+ Add Manually'}
          </button>
          <button
            type="button"
            className="dsp-action dsp-action--sync"
            onClick={() => void onSyncManual()}
            disabled={!projectId || syncingManual}
          >
            {syncingManual ? 'Syncing...' : 'Sync Manual Folders'}
          </button>
        </div>
        ) : null}
      </header>

      {canUploadData ? (
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
        aria-label="Drop survey, GIS, model, or report file"
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".las,.laz,.tif,.tiff,.csv,.zip,.kml,.geojson,.dwg,.pdf"
          className="gv-file-input"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void prepareFile(file)
          }}
        />
        <p className="dsp-dropzone__title">Drop or Select .las, .laz, .tif, .csv, .zip, .kml, .geojson, .dwg, .pdf files</p>
        <p className="dsp-dropzone__meta">After select, fill details and start upload</p>
      </div>
      ) : (
        <div className="dsp-readonly-note">
          Upload is currently blocked for your account. Ask the admin to enable User Upload when you need to add data.
        </div>
      )}

      {canUploadData && selectedFile ? (
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
              <option value="Reports">Reports</option>
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
              onChange={(e) => {
                const next = e.target.value
                setUploadForm((s) => ({ ...s, epsg: next }))
                setEpsgStatus(next.trim() ? 'Manual EPSG will be used if source CRS is missing' : 'Not detected, enter manually')
              }}
              placeholder="EPSG:32644"
            />
            {epsgStatus ? <span className="dsp-form__hint">{epsgStatus}</span> : null}
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

      {canUploadData && primaryTask ? (
        <div className="dsp-progress" aria-live="polite">
          <div className="dsp-progress__summary">
            <strong>{primaryTask.fileName}</strong>
            <span>{primaryTask.stage || primaryTask.statusText}</span>
            <em>{primaryTask.etaText || 'Estimating time...'}</em>
          </div>
          <div className="dsp-progress__barline">
            <span>{`${Math.round(primaryTask.progressPercent)}%`}</span>
            <div className="dsp-progress__track">
              <div className="dsp-progress__fill" style={{ width: `${primaryTask.progressPercent}%` }} />
            </div>
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
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {loadingRows && datasets.length === 0 ? (
              <tr>
                <td colSpan={6}>Loading datasets...</td>
              </tr>
            ) : null}
            {datasets.map((row) => (
              <tr key={row.id}>
                <td>{row.fileName}</td>
                <td>
                  <span>{row.type}</span>
                  {epsgLabel(row) ? (
                    <span className="dsp-epsg-chip" title={`Detected coordinate system: ${epsgLabel(row)}`}>
                      {epsgLabel(row)}
                    </span>
                  ) : ['Ortho', 'DTM', 'DSM'].includes(row.type) ? (
                    <span className="dsp-epsg-chip dsp-epsg-chip--missing" title="Coordinate system not detected">
                      EPSG missing
                    </span>
                  ) : null}
                </td>
                <td>{row.size}</td>
                <td>
                  <span>{row.uploadDate || '--'}</span>
                  {isAdmin && row.datasetId ? (
                    <button
                      type="button"
                      className="dsp-date-edit"
                      onClick={() => void onAdminEditDate(row)}
                      title="Edit upload date"
                      aria-label={`Edit upload date for ${row.fileName}`}
                    >
                      <i className="fa-solid fa-pen" aria-hidden />
                    </button>
                  ) : null}
                </td>
                <td>
                  <span className={row.status === 'Web-Ready' ? 'dsp-badge dsp-badge--ready' : 'dsp-badge dsp-badge--processing'}>
                    {row.status}
                  </span>
                  {row.status === 'Processing' ? (
                    <div className="dsp-row-progress">
                      <span>{row.stage || 'Processing'}</span>
                      <em>{row.progressPercent !== undefined ? `${Math.round(row.progressPercent)}%` : ''}{row.etaText ? ` - ${row.etaText}` : ''}</em>
                    </div>
                  ) : null}
                </td>
                <td>
                  <div className="dsp-action-group">
                    {row.status !== 'Web-Ready' ? (
                      <button type="button" className="dsp-action" disabled>
                        {row.stage || 'Processing...'}
                      </button>
                    ) : row.layerType && (row.layerUrl || (isPointCloudLayer(row.layerType) && row.datasetId)) ? (
                      <button
                        type="button"
                        className="dsp-action"
                        onClick={async () => {
                          if (!projectId || !row.layerType) return
                          if (row.layerType === 'Reports') {
                            if (!row.layerUrl) return
                            setReportViewer({
                              name: row.fileName,
                              url: row.layerUrl,
                              downloadUrl: row.downloadUrl || row.layerUrl,
                            })
                            return
                          }
                          let layer = buildActiveLayer(projectId, row)
                          if (isPointCloudLayer(row.layerType)) {
                            try {
                              const directUrl = normalizePointCloudViewerUrl(
                                row.layerUrl,
                                projectId,
                                row.datasetId || row.fileName,
                                row.fileName,
                              )
                              if (directUrl) {
                                layer = buildActiveLayer(projectId, {
                                  ...row,
                                  layerType: 'pointcloud',
                                  layerUrl: directUrl,
                                  datasetType: 'pointcloud',
                                })
                              } else {
                                let status: Awaited<ReturnType<typeof getPointCloudStatus>> | null = null
                                const lookupCandidates = Array.from(new Set([
                                  row.datasetId,
                                  row.fileName,
                                  normalizedPointCloudName(row.fileName),
                                ].map((value) => String(value || '').trim()).filter(Boolean)))
                                for (const lookup of lookupCandidates) {
                                  const candidateStatus = await getPointCloudStatus(projectId, lookup)
                                  if (candidateStatus?.ready && (candidateStatus.tileset_url || candidateStatus.copc_url || candidateStatus.ept_url)) {
                                    status = candidateStatus
                                    break
                                  }
                                  if (!status || candidateStatus?.failed) status = candidateStatus
                                }
                                const resolvedUrl = normalizePointCloudViewerUrl(
                                  status?.tileset_url,
                                  projectId,
                                  row.datasetId || row.fileName,
                                  row.fileName,
                                )
                                const statusEptViewerUrl =
                                  status?.ready && status?.ept_url
                                    ? normalizePointCloudViewerUrl(
                                      status.ept_url,
                                      projectId,
                                      row.datasetId || row.fileName,
                                      row.fileName,
                                    )
                                    : ''
                                const statusCopcViewerUrl =
                                  status?.ready && status?.copc_url
                                    ? normalizePointCloudViewerUrl(
                                      status.copc_url,
                                      projectId,
                                      row.datasetId || row.fileName,
                                      row.fileName,
                                    )
                                    : ''
                                let readyUrl =
                                  status?.ready && resolvedUrl
                                    ? resolvedUrl
                                    : statusCopcViewerUrl || statusEptViewerUrl
                                      ? statusCopcViewerUrl || statusEptViewerUrl
                                      : ''
                                const sameNameReadyRow = datasets.find((candidate) => {
                                  const candidateUrl = candidate.layerUrl
                                  if (!candidateUrl) return false
                                  return (
                                    candidate !== row &&
                                    isPointCloudRow(candidate) &&
                                    isPointCloudViewerUrl(candidateUrl) &&
                                    normalizedPointCloudName(candidate.fileName) === normalizedPointCloudName(row.fileName)
                                  )
                                })
                                const sameNameReadyUrl = sameNameReadyRow?.layerUrl || ''
                                if (!readyUrl && sameNameReadyUrl) {
                                  readyUrl = normalizePointCloudViewerUrl(
                                    sameNameReadyUrl,
                                    projectId,
                                    sameNameReadyRow?.datasetId || row.datasetId || row.fileName,
                                    sameNameReadyRow?.fileName || row.fileName,
                                  )
                                }
                                if (!readyUrl || isRawPointCloudUrl(readyUrl) || !isPointCloudViewerUrl(readyUrl)) {
                                  await modal.alert(
                                    status?.failed ? 'Point cloud conversion failed' : 'Point cloud is still preparing',
                                    status?.failed
                                      ? 'This point cloud did not finish cleanly. Please reprocess/upload it again; the detailed error is saved in the portal error log.'
                                      : 'This LAS/LAZ file is uploaded, but the point cloud viewer asset is not ready yet. Keep it processing, then open it again from Data Catalog.',
                                  )
                                  return
                                }
                                layer = buildActiveLayer(projectId, {
                                  ...(sameNameReadyRow || row),
                                  layerType: 'pointcloud',
                                  layerUrl: readyUrl,
                                  datasetType: 'pointcloud',
                                })
                              }
                            } catch (error) {
                              logClientError({
                                area: 'data_catalog_pointcloud_open',
                                message: error instanceof Error ? error.message : String(error || 'Point cloud open failed'),
                                project_id: projectId,
                                dataset_id: row.datasetId || row.fileName,
                                extra: { fileName: row.fileName },
                              })
                              await modal.alert('Point cloud open failed', 'Could not open the processed 3D viewer. The error has been logged.')
                              return
                            }
                          }
                          if (!layer) return
                          upsertLayer(layer)
                          if (isTwoDLayer(row.layerType)) {
                            setActiveViewerTab('2D')
                            setActiveId('map')
                          } else {
                            setActiveViewerTab('3D')
                            setActiveId('globe')
                          }
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
                            const layer = buildActiveLayer(projectId, row)
                            if (layer) removeLayer(layer.id)
                            window.sessionStorage.removeItem(`datasets:rows:v2:${projectId}`)
                            setDatasets((prev) => prev.filter((item) => !isSameDatasetRow(item, row)))
                            void loadRows(projectId, `datasets:rows:v2:${projectId}`, () => false)
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
                      <button
                        type="button"
                        className="dsp-action"
                        onClick={() => void onGenerateContours(row)}
                        disabled={Boolean(generatingContours[row.datasetId])}
                      >
                        {generatingContours[row.datasetId] ? '⏳ Generating...' : '🗺️ Generate Contours'}
                      </button>
                    ) : null}
                    {row.datasetId && ['DTM', 'DSM'].includes(row.type) ? (
                      <button
                        type="button"
                        className="dsp-action dsp-action--secondary"
                        onClick={() => void onExportGrid(row, 'csv')}
                        disabled={Boolean(exportingGrid[`${row.datasetId}:csv`])}
                      >
                        {exportingGrid[`${row.datasetId}:csv`] ? 'Exporting CSV...' : 'Export CSV'}
                      </button>
                    ) : null}
                    {row.datasetId && ['DTM', 'DSM'].includes(row.type) ? (
                      <button
                        type="button"
                        className="dsp-action dsp-action--secondary"
                        onClick={() => void onExportGrid(row, 'dxf')}
                        disabled={Boolean(exportingGrid[`${row.datasetId}:dxf`])}
                      >
                        {exportingGrid[`${row.datasetId}:dxf`] ? 'Exporting DXF...' : 'Export DXF'}
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
    </>
  )
}

export default DatasetsPanel
