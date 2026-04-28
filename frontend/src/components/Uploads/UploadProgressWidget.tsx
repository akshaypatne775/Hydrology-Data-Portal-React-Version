import { useMemo } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import './UploadProgressWidget.css'

export function UploadProgressWidget() {
  const { tasks, dismissTask } = useUploadContext()
  const visibleTasks = useMemo(
    () => tasks.filter((task) => task.state === 'uploading' || task.state === 'processing' || task.state === 'error'),
    [tasks],
  )

  if (visibleTasks.length === 0) return null

  return (
    <aside className="upw-root" aria-live="polite" aria-label="Upload progress">
      <p className="upw-title">Upload Progress</p>
      <div className="upw-list">
        {visibleTasks.map((task) => (
          <div key={task.id} className="upw-item">
            <div className="upw-item__head">
              <span className="upw-item__name">{task.fileName}</span>
              {task.state === 'error' ? (
                <button type="button" className="upw-item__dismiss" onClick={() => dismissTask(task.id)}>
                  Close
                </button>
              ) : null}
            </div>
            <div className="upw-track">
              <div className="upw-fill" style={{ width: `${task.progressPercent}%` }} />
            </div>
            <p className={task.state === 'error' ? 'upw-status upw-status--error' : 'upw-status'}>
              {task.statusText}
            </p>
          </div>
        ))}
      </div>
    </aside>
  )
}

export default UploadProgressWidget
