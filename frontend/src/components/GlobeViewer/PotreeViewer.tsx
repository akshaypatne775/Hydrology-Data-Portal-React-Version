import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { logClientError } from '../../services/errorLogService'

export type PotreeToolAction =
  | 'reset-view'
  | 'cross-section'
  | 'lc-sections'
  | 'five-meter-sections'
  | 'slice-line'
  | 'section-box'
  | 'apply-slice'
  | 'clear-slice'
  | 'profile-csv'
  | 'clipped-csv'
  | 'distance'
  | 'area'
  | 'height'
  | 'clear'
  | 'natural-color'
  | 'elevation-color'
  | 'intensity-color'

export type PotreeViewerHandle = {
  runTool: (action: PotreeToolAction) => void
}

type PotreeViewerProps = {
  url: string
  projectId?: string
  datasetId?: string
}

export const PotreeViewer = forwardRef<PotreeViewerHandle, PotreeViewerProps>(function PotreeViewer(
  { url, projectId = '', datasetId = '' },
  ref,
) {
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
        area: 'pointcloud_viewer',
        message: cleanMessage,
        url,
        stack,
        project_id: projectId,
        dataset_id: datasetId,
      })
      if (/dataview|offset is outside|decoder|hierarchy|octree|pointcloud/i.test(cleanMessage)) {
        setViewerError(
          'Point cloud viewer could not read part of the converted data. The issue has been logged; reprocess this point cloud from Data Catalog if it stays blank.',
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

  const runPotreeTool = useCallback((action: PotreeToolAction) => {
    const frame = frameRef.current
    const win = frame?.contentWindow as
      | (Window & {
          droidStartCrossSection?: () => unknown
          droidClearSections?: () => unknown
          droidApplyNaturalColor?: () => unknown
          droidApplyElevationColor?: () => unknown
          droidApplyIntensityColor?: () => unknown
          droidResetView?: () => unknown
          droidStartMeasurement?: (mode: 'distance' | 'area' | 'height') => unknown
          droidStartSliceLine?: () => unknown
          droidStartSectionBox?: () => unknown
          droidApplySlice?: () => unknown
          droidClearSlice?: () => unknown
          droidGenerateFiveMeterSections?: () => unknown
          droidExportProfileCsv?: () => unknown
          droidExportClippedPointsCsv?: () => unknown
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
      if (action === 'reset-view') {
        const button = doc.getElementById('resetViewButton') as HTMLButtonElement | null
        if (button) button.click()
        else win.droidResetView?.()
        setToolMessage('Point cloud view reset.')
        return
      }

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
          setToolMessage('L/C section automation is available only on the latest point cloud viewer template.')
        }
        return
      }

      if (action === 'five-meter-sections') {
        if (typeof win.droidGenerateFiveMeterSections === 'function') {
          win.droidGenerateFiveMeterSections()
          setToolMessage('5m underground cross-section workflow active.')
        } else {
          const interval = doc.getElementById('sectionIntervalInput') as HTMLInputElement | null
          if (interval) interval.value = '5'
          const button = doc.getElementById('alignmentButton') as HTMLButtonElement | null
          button?.click()
          setToolMessage('Set to 5m sections. Draw alignment and generate sections.')
        }
        return
      }

      if (action === 'natural-color') {
        const button = doc.getElementById('naturalColorButton') as HTMLButtonElement | null
        if (button) button.click()
        else win.droidApplyNaturalColor?.()
        setToolMessage('Natural color mode applied.')
        return
      }

      if (action === 'elevation-color') {
        const button = doc.getElementById('elevationColorButton') as HTMLButtonElement | null
        if (button) button.click()
        else win.droidApplyElevationColor?.()
        setToolMessage('Elevation color mode applied.')
        return
      }

      if (action === 'intensity-color') {
        const button = doc.getElementById('intensityColorButton') as HTMLButtonElement | null
        if (button) button.click()
        else win.droidApplyIntensityColor?.()
        setToolMessage('Intensity color mode applied.')
        return
      }

      if (action === 'distance' || action === 'area' || action === 'height') {
        const buttonId = action === 'distance' ? 'distanceButton' : action === 'area' ? 'areaButton' : 'heightButton'
        const button = doc.getElementById(buttonId) as HTMLButtonElement | null
        if (button) button.click()
        else win.droidStartMeasurement?.(action)
        setToolMessage(`${action[0].toUpperCase()}${action.slice(1)} measurement active.`)
        return
      }

      const sliceActions: Record<
        string,
        { buttonId: string; fallback?: () => unknown; message: string }
      > = {
        'slice-line': {
          buttonId: 'sliceLineButton',
          fallback: win.droidStartSliceLine,
          message: 'Slice line mode active. Draw two points to create an editable section box.',
        },
        'section-box': {
          buttonId: 'sectionBoxButton',
          fallback: win.droidStartSectionBox,
          message: 'Manual section box active. Place and edit the clipping box.',
        },
        'apply-slice': {
          buttonId: 'applySliceButton',
          fallback: win.droidApplySlice,
          message: 'Slice clipping applied.',
        },
        'clear-slice': {
          buttonId: 'clearSliceButton',
          fallback: win.droidClearSlice,
          message: 'Slice clipping cleared.',
        },
        'profile-csv': {
          buttonId: 'profileCsvButton',
          fallback: win.droidExportProfileCsv,
          message: 'Profile CSV export requested.',
        },
        'clipped-csv': {
          buttonId: 'clippedCsvButton',
          fallback: win.droidExportClippedPointsCsv,
          message: 'Full clipped-points CSV and LAS export requested.',
        },
      }

      const sliceAction = sliceActions[action]
      if (sliceAction) {
        const button = doc.getElementById(sliceAction.buttonId) as HTMLButtonElement | null
        if (button) button.click()
        else sliceAction.fallback?.()
        setToolMessage(sliceAction.message)
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
  }, [datasetId, projectId, url])

  useImperativeHandle(ref, () => ({ runTool: runPotreeTool }), [runPotreeTool])

  return (
    <div className="potree-viewer-frame-shell">
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
})

export default PotreeViewer
