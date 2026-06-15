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
  const [toolMessage, setToolMessage] = useState('')

  useEffect(() => {
    setViewerError('')
    setToolMessage('')
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

  const runPotreeTool = (action: 'cross-section' | 'lc-sections' | 'clear') => {
    const frame = frameRef.current
    const win = frame?.contentWindow as
      | (Window & {
          droidStartCrossSection?: () => unknown
          droidClearSections?: () => unknown
          viewer?: {
            profileTool?: { startInsertion?: (args?: { name?: string }) => unknown }
            profileWindow?: { show?: () => void }
            profileWindowController?: { setProfile?: (profile: unknown) => void }
            scene?: {
              profiles?: unknown[]
              removeProfile?: (profile: unknown) => void
            }
          }
        })
      | null
    const doc = frame?.contentDocument

    if (!win || !doc) {
      setToolMessage('3D viewer is still loading. Try again in a moment.')
      return
    }

    try {
      if (action === 'cross-section') {
        const button = doc.getElementById('sectionButton') as HTMLButtonElement | null
        if (button) {
          button.click()
          setToolMessage('Cross section mode active. Pick section points, then right-click to finish.')
          return
        }
        if (typeof win.droidStartCrossSection === 'function') {
          const profile = win.droidStartCrossSection()
          if (profile && win.viewer?.profileWindow && win.viewer?.profileWindowController) {
            win.viewer.profileWindow.show?.()
            win.viewer.profileWindowController.setProfile?.(profile)
          }
          setToolMessage('Cross section mode active. Pick section points, then right-click to finish.')
          return
        }
        const profile = win.viewer?.profileTool?.startInsertion?.({ name: 'Cross Section' })
        if (profile && win.viewer?.profileWindow && win.viewer?.profileWindowController) {
          win.viewer.profileWindow.show?.()
          win.viewer.profileWindowController.setProfile?.(profile)
        }
        setToolMessage(profile ? 'Cross section mode active. Pick section points, then right-click to finish.' : 'Cross section tool is not ready yet.')
        return
      }

      if (action === 'lc-sections') {
        const button = doc.getElementById('alignmentButton') as HTMLButtonElement | null
        if (button) {
          button.click()
          setToolMessage('L/C Sections dialog opened inside the point cloud viewer.')
        } else {
          setToolMessage('L/C section automation is available only on the latest EPT viewer template.')
        }
        return
      }

      const clearButton = doc.getElementById('clearButton') as HTMLButtonElement | null
      if (clearButton) {
        clearButton.click()
      } else if (typeof win.droidClearSections === 'function') {
        win.droidClearSections()
      } else {
        const profiles = Array.from(win.viewer?.scene?.profiles || [])
        profiles.forEach((profile) => win.viewer?.scene?.removeProfile?.(profile))
      }
      setToolMessage('Point cloud sections and measurements cleared.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not run the Potree tool.'
      setToolMessage(message)
      logClientError({
        area: 'potree_cross_section',
        message,
        stack: error instanceof Error ? error.stack || '' : '',
        url,
        project_id: projectId,
        dataset_id: datasetId,
      })
    }
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: '#06171b' }}>
      <div className="potree-cross-section-toolbar" aria-label="Point cloud section tools">
        <button type="button" onClick={() => runPotreeTool('cross-section')}>
          <i className="fas fa-vector-square" aria-hidden />
          Cross Section
        </button>
        <button type="button" onClick={() => runPotreeTool('lc-sections')}>
          <i className="fas fa-route" aria-hidden />
          L/C Sections
        </button>
        <button type="button" onClick={() => runPotreeTool('clear')}>
          <i className="fas fa-eraser" aria-hidden />
          Clear
        </button>
      </div>
      <iframe
        ref={frameRef}
        src={url}
        loading="eager"
        style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
        title="Droid 3D Point Cloud System"
      />
      {toolMessage ? (
        <div className="potree-tool-message" role="status">
          {toolMessage}
        </div>
      ) : null}
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
