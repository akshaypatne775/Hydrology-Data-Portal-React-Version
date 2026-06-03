import { useCallback, useMemo, useState, type DragEvent } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import './PointCloudUploader.css'

type PointCloudUploaderProps = {
  projectId: string
}

export function PointCloudUploader({ projectId }: PointCloudUploaderProps) {
  const [isDragging, setIsDragging] = useState(false)
  const { tasks, startPointCloudUpload } = useUploadContext()

  const activeTask = useMemo(
    () =>
      tasks.find(
        (task) =>
          task.projectId === projectId &&
          task.kind === 'pointcloud' &&
          (task.state === 'uploading' || task.state === 'processing' || task.state === 'error'),
      ),
    [projectId, tasks],
  )

  const progressLabel = useMemo(
    () => `${Math.max(0, Math.min(100, Math.round(activeTask?.progressPercent ?? 0)))}%`,
    [activeTask?.progressPercent],
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
        return
      }

      await startPointCloudUpload(droppedFile, projectId)
    },
    [projectId, startPointCloudUpload],
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
          <div className="pcu-progress-fill" style={{ width: `${activeTask?.progressPercent ?? 0}%` }} />
        </div>
        <div className="pcu-progress-meta">
          <span>{progressLabel}</span>
          <span>{activeTask?.statusText ?? 'Drag and Drop LAS/LAZ files here'}</span>
        </div>
      </div>

      {activeTask?.state === 'uploading' ? <p className="pcu-state">Uploading chunks...</p> : null}
      {activeTask?.state === 'processing' ? <p className="pcu-state">Server processing in background…</p> : null}
      {activeTask?.state === 'error' ? <p className="pcu-state pcu-state--error">Upload failed.</p> : null}
    </section>
  )
}

export default PointCloudUploader
