import { useCallback, useMemo, useState, type DragEvent } from 'react'
import { formatApiNetworkError, getApiBaseUrl } from '../../lib/apiBase'
import './PointCloudUploader.css'

const CHUNK_SIZE_BYTES = 10 * 1024 * 1024

type UploadState = 'idle' | 'uploading' | 'success' | 'error'

type CompleteUploadResponse = {
  status?: string
  message?: string
  tileset_url?: string
}

type PointCloudUploaderProps = {
  onUploadComplete?: (tilesetUrl: string, fileName: string) => void
}

export function PointCloudUploader({ onUploadComplete }: PointCloudUploaderProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [uploadState, setUploadState] = useState<UploadState>('idle')
  const [progressPercent, setProgressPercent] = useState(0)
  const [statusText, setStatusText] = useState('Drag and Drop LAS/LAZ files here')

  const progressLabel = useMemo(
    () => `${Math.max(0, Math.min(100, Math.round(progressPercent)))}%`,
    [progressPercent],
  )

  const apiBase = useMemo(() => getApiBaseUrl(), [])

  const uploadFileInChunks = useCallback(
    async (file: File) => {
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
          chunkForm.append('chunkIndex', String(chunkIndex))
          chunkForm.append('totalChunks', String(totalChunks))
          chunkForm.append('chunk', chunk, `${file.name}.part.${chunkIndex}`)

          const chunkResponse = await fetch(`${apiBase}/api/upload-chunk`, {
            method: 'POST',
            body: chunkForm,
          })

          if (!chunkResponse.ok) {
            throw new Error(`Chunk upload failed at part ${chunkIndex + 1}`)
          }

          const nextPercent = ((chunkIndex + 1) / totalChunks) * 100
          setProgressPercent(nextPercent)
        }

        const projectId = `${file.name.replace(/[^a-zA-Z0-9_-]/g, '-')}-${Date.now()}`
        const completePayload = {
          filename: file.name,
          totalChunks,
          project_id: projectId,
        }
        const completeResponse = await fetch(`${apiBase}/api/complete-upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(completePayload),
        })

        if (!completeResponse.ok) {
          throw new Error('Failed to complete upload merge step')
        }

        const completeData = (await completeResponse.json()) as CompleteUploadResponse
        setUploadState('success')
        setStatusText(
          completeData.tileset_url
            ? `Upload complete. Tileset: ${completeData.tileset_url}`
            : 'Upload complete. Merge request sent successfully.',
        )
        if (completeData.tileset_url) {
          onUploadComplete?.(completeData.tileset_url, file.name)
        }
      } catch (error) {
        setUploadState('error')
        setStatusText(formatApiNetworkError(apiBase, error))
      }
    },
    [apiBase, onUploadComplete],
  )

  const onDropFile = useCallback(
    async (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setIsDragging(false)

      const droppedFile = event.dataTransfer.files?.[0]
      if (!droppedFile) return

      const extension = droppedFile.name.split('.').pop()?.toLowerCase()
      if (extension !== 'las' && extension !== 'laz') {
        setUploadState('error')
        setStatusText('Please upload only LAS/LAZ files.')
        return
      }

      await uploadFileInChunks(droppedFile)
    },
    [uploadFileInChunks],
  )

  return (
    <section className="pcu-root">
      <div
        className={isDragging ? 'pcu-dropzone pcu-dropzone--dragging' : 'pcu-dropzone'}
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
        aria-label="Drop LAS or LAZ point cloud file"
      >
        <p className="pcu-dropzone__title">Drag and Drop LAS/LAZ files here</p>
        <p className="pcu-dropzone__meta">Chunk upload size: 10MB</p>
      </div>

      <div className="pcu-progress-wrap" aria-live="polite">
        <div className="pcu-progress-track">
          <div className="pcu-progress-fill" style={{ width: `${progressPercent}%` }} />
        </div>
        <div className="pcu-progress-meta">
          <span>{progressLabel}</span>
          <span>{statusText}</span>
        </div>
      </div>

      {uploadState === 'uploading' ? <p className="pcu-state">Uploading chunks...</p> : null}
      {uploadState === 'success' ? <p className="pcu-state pcu-state--ok">Upload completed.</p> : null}
      {uploadState === 'error' ? <p className="pcu-state pcu-state--error">Upload failed.</p> : null}
    </section>
  )
}

export default PointCloudUploader
