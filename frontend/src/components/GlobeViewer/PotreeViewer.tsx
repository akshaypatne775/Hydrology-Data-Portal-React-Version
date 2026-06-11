import { useEffect, useRef, useState } from 'react'
import { logClientError } from '../../services/errorLogService'

type PotreeViewerProps = {
  url: string
  projectId?: string
  datasetId?: string
}

export function PotreeViewer({ url, projectId = '', datasetId = '' }: PotreeViewerProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const [viewerError, setViewerError] = useState('')

  useEffect(() => {
    setViewerError('')
  }, [url])

  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return undefined

    const logViewerError = (message: string, stack = '') => {
      const cleanMessage = message.trim()
      if (!cleanMessage) return
      if (/background\.jpg/i.test(cleanMessage)) return
      logClientError({
        area: 'ept_viewer',
        message: cleanMessage,
        url,
        stack,
        project_id: projectId,
        dataset_id: datasetId,
      })
      if (/dataview|offset is outside|decoder|hierarchy|octree|pointcloud/i.test(cleanMessage)) {
        setViewerError(
          'Point cloud viewer could not read part of the EPT data. The issue has been logged; reprocess this point cloud from Data Catalog if it stays blank.',
        )
      }
    }

    const onFrameLoad = () => {
      try {
        const win = frame.contentWindow as (Window & { console?: Pick<Console, 'error'> }) | null
        if (!win) return
        win.addEventListener('error', (event) => {
          logViewerError(
            event.message || 'Point cloud viewer runtime error',
            event.error?.stack || `${event.filename || ''}:${event.lineno || ''}:${event.colno || ''}`,
          )
        })
        win.addEventListener('unhandledrejection', (event) => {
          const reason = event.reason
          logViewerError(
            reason instanceof Error ? reason.message : String(reason || 'Point cloud viewer promise rejection'),
            reason instanceof Error ? reason.stack || '' : '',
          )
        })
        const consoleRef = win.console
        if (consoleRef?.error) {
          const originalConsoleError = consoleRef.error.bind(consoleRef)
          consoleRef.error = (...args: unknown[]) => {
            logViewerError(args.map((arg) => (arg instanceof Error ? arg.message : String(arg))).join(' '))
            originalConsoleError(...args)
          }
        }
      } catch {
        // Same-origin project viewer pages can be instrumented; cross-origin pages cannot.
      }
    }

    frame.addEventListener('load', onFrameLoad)
    return () => frame.removeEventListener('load', onFrameLoad)
  }, [datasetId, projectId, url])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#06171b' }}>
      <iframe
        ref={frameRef}
        src={url}
        loading="eager"
        style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
        title="Droid 3D Point Cloud System"
      />
      {viewerError ? (
        <div className="potree-viewer-error" role="alert">
          <strong>Point cloud viewer issue</strong>
          <span>{viewerError}</span>
        </div>
      ) : null}
    </div>
  )
}

export default PotreeViewer
