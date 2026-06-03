import type { ChangeEvent, RefObject } from 'react'
import type { DigitizationMode } from './spatialTypes'

type DigitizationToolbarProps = {
  mode: DigitizationMode
  disabled: boolean
  busy: boolean
  canFinishDraft: boolean
  importInputRef: RefObject<HTMLInputElement | null>
  onModeChange: (mode: DigitizationMode) => void
  onFinishDraft: () => void
  onClearDraft: () => void
  onImportFile: (file: File) => void
}

function toolClass(active: boolean): string {
  return active ? 'digitization-tool digitization-tool--active' : 'digitization-tool'
}

export function DigitizationToolbar({
  mode,
  disabled,
  busy,
  canFinishDraft,
  importInputRef,
  onModeChange,
  onFinishDraft,
  onClearDraft,
  onImportFile,
}: DigitizationToolbarProps) {
  const handleImportChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    if (file) onImportFile(file)
    event.currentTarget.value = ''
  }

  return (
    <div className="digitization-toolbar" aria-label="Digitization and spatial data tools">
      <p className="digitization-toolbar__title">
        <i className="fa-solid fa-draw-polygon" aria-hidden />
        Digitization
      </p>
      <button
        type="button"
        className={toolClass(mode === 'polygon')}
        disabled={disabled || busy}
        onClick={() => onModeChange('polygon')}
        title="Draw project boundary polygons"
      >
        <i className="fa-solid fa-vector-square" aria-hidden />
        Polygon
      </button>
      <button
        type="button"
        className={toolClass(mode === 'polyline')}
        disabled={disabled || busy}
        onClick={() => onModeChange('polyline')}
        title="Draw line features like roads or alignment paths"
      >
        <i className="fa-solid fa-route" aria-hidden />
        Polyline
      </button>
      <button
        type="button"
        className={toolClass(mode === 'marker')}
        disabled={disabled || busy}
        onClick={() => onModeChange('marker')}
        title="Place a point marker"
      >
        <i className="fa-solid fa-location-dot" aria-hidden />
        Marker
      </button>
      <button
        type="button"
        className={toolClass(mode === 'edit')}
        disabled={disabled || busy}
        onClick={() => onModeChange('edit')}
        title="Click a shape, then drag its vertices"
      >
        <i className="fa-solid fa-pen-nib" aria-hidden />
        Edit
      </button>
      <button
        type="button"
        className="digitization-tool"
        disabled={disabled || busy}
        onClick={() => importInputRef.current?.click()}
        title="Import KML, GeoJSON, or zipped shapefile"
      >
        <i className="fa-solid fa-file-import" aria-hidden />
        Import
      </button>
      <input
        ref={importInputRef}
        type="file"
        accept=".kml,.xml,.geojson,.json,.shp,.zip"
        className="digitization-toolbar__input"
        onChange={handleImportChange}
      />
      <button
        type="button"
        className="digitization-tool digitization-tool--finish"
        disabled={disabled || busy || !canFinishDraft}
        onClick={onFinishDraft}
      >
        <i className="fa-solid fa-check" aria-hidden />
        Finish
      </button>
      <button
        type="button"
        className="digitization-tool digitization-tool--ghost"
        disabled={disabled || busy}
        onClick={onClearDraft}
      >
        Clear Draft
      </button>
      {busy ? <span className="digitization-toolbar__status">Saving...</span> : null}
    </div>
  )
}
