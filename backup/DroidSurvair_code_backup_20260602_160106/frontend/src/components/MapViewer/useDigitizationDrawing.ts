import type { LatLng } from 'leaflet'
import { useCallback, useMemo, useState } from 'react'
import {
  buildLineFeature,
  buildPointFeature,
  buildPolygonFeature,
  type DigitizationMode,
  type GeoJsonFeature,
} from './spatialTypes'

type UseDigitizationDrawingOptions = {
  disabled: boolean
  onDraftComplete: (feature: GeoJsonFeature) => void
}

export function useDigitizationDrawing({
  disabled,
  onDraftComplete,
}: UseDigitizationDrawingOptions) {
  const [mode, setModeState] = useState<DigitizationMode>('idle')
  const [draftPoints, setDraftPoints] = useState<LatLng[]>([])
  const isDrawing = mode === 'polygon' || mode === 'polyline' || mode === 'marker'

  const clearDraft = useCallback(() => {
    setDraftPoints([])
  }, [])

  const setMode = useCallback(
    (nextMode: DigitizationMode) => {
      if (disabled) {
        setModeState('idle')
        clearDraft()
        return
      }
      setModeState((prev) => (prev === nextMode ? 'idle' : nextMode))
      clearDraft()
    },
    [clearDraft, disabled],
  )

  const addDraftPoint = useCallback(
    (point: LatLng) => {
      if (disabled) return
      if (mode === 'marker') {
        onDraftComplete(buildPointFeature(point))
        setModeState('idle')
        clearDraft()
        return
      }
      if (mode === 'polygon' || mode === 'polyline') {
        setDraftPoints((prev) => [...prev, point])
      }
    },
    [clearDraft, disabled, mode, onDraftComplete],
  )

  const canFinishDraft = useMemo(() => {
    if (mode === 'polygon') return draftPoints.length >= 3
    if (mode === 'polyline') return draftPoints.length >= 2
    return false
  }, [draftPoints.length, mode])

  const finishDraft = useCallback(() => {
    if (!canFinishDraft) return
    if (mode === 'polygon') {
      onDraftComplete(buildPolygonFeature(draftPoints))
    }
    if (mode === 'polyline') {
      onDraftComplete(buildLineFeature(draftPoints))
    }
    setModeState('idle')
    clearDraft()
  }, [canFinishDraft, clearDraft, draftPoints, mode, onDraftComplete])

  const cancelDigitization = useCallback(() => {
    setModeState('idle')
    clearDraft()
  }, [clearDraft])

  return {
    mode,
    isDrawing,
    draftPoints,
    canFinishDraft,
    setMode,
    addDraftPoint,
    finishDraft,
    clearDraft,
    cancelDigitization,
  }
}
