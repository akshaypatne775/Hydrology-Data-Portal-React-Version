import { useMemo } from 'react'
import { useUploadContext } from '../../context/UploadContext'
import './UploadProgressWidget.css'

const PIPELINE_STEPS = ['Upload', 'Merge', 'COPC', 'Ready'] as const

function pipelineStepIndex(stage?: string, state?: string): number {
  if (state === 'success') return 3
  const text = String(stage || '').toLowerCase()
  if (text.includes('merg')) return 1
  if (text.includes('copc') || text.includes('converting') || text.includes('pdal')) return 2
  if (text.includes('upload')) return 0
  if (state === 'processing') return 2
  return 0
}

export function UploadProgressWidget() {
  const { tasks, dismissTask } = useUploadContext()
  const visibleTasks = useMemo(
    () => tasks.filter((task) => (
      task.state === 'uploading'
      || task.state === 'processing'
      || task.state === 'success'
      || task.state === 'error'
    )),
    [tasks],
  )

  if (visibleTasks.length === 0) return null

  return (
    <aside className="upw-root" aria-live="polite" aria-label="Upload and conversion progress">
      <p className="upw-title">Upload &amp; Conversion</p>
      <div className="upw-list">
        {visibleTasks.map((task) => {
          const isPointCloud = task.kind === 'pointcloud'
          const activeStep = pipelineStepIndex(task.stage, task.state)
          return (
            <div
              key={task.id}
              className={[
                'upw-item',
                task.state === 'success' ? 'upw-item--success' : '',
                task.state === 'error' ? 'upw-item--error' : '',
              ].filter(Boolean).join(' ')}
            >
              <div className="upw-item__head">
                <span className="upw-item__name">{task.fileName}</span>
                <span className="upw-item__percent">{Math.round(task.progressPercent)}%</span>
                {task.state === 'error' || task.state === 'success' ? (
                  <button type="button" className="upw-item__dismiss" onClick={() => dismissTask(task.id)}>
                    Close
                  </button>
                ) : null}
              </div>

              {isPointCloud ? (
                <div className="upw-pipeline" aria-hidden>
                  {PIPELINE_STEPS.map((label, index) => (
                    <span
                      key={label}
                      className={[
                        'upw-pipeline__step',
                        index < activeStep ? 'upw-pipeline__step--done' : '',
                        index === activeStep ? 'upw-pipeline__step--active' : '',
                      ].filter(Boolean).join(' ')}
                    >
                      {label}
                    </span>
                  ))}
                </div>
              ) : null}

              <div className="upw-track">
                <div
                  className={[
                    'upw-fill',
                    task.state === 'success' ? 'upw-fill--success' : '',
                    task.state === 'error' ? 'upw-fill--error' : '',
                  ].filter(Boolean).join(' ')}
                  style={{ width: `${Math.max(task.state === 'processing' ? 4 : 0, task.progressPercent)}%` }}
                />
              </div>

              {task.stage ? (
                <p className="upw-stage">{task.stage}</p>
              ) : null}

              <p className={task.state === 'error' ? 'upw-status upw-status--error' : 'upw-status'}>
                {task.statusText}
              </p>

              {task.etaText && task.state !== 'success' && task.state !== 'error' ? (
                <p className="upw-eta">{task.etaText}</p>
              ) : null}
            </div>
          )
        })}
      </div>
    </aside>
  )
}

export default UploadProgressWidget
