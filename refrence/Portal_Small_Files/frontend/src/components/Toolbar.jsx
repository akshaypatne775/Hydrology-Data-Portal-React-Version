import { useEffect, useRef, useState } from 'react'

function Toolbar({
  onAddSurvey,
  isAddMode = false,
  isMeasureMode = false,
  measureUnit = 'sqft',
  onToggleMeasureMode,
  onMeasureUnitChange,
  onClearMeasure,
  onExportDetailedCsv,
  onExportSummaryCsv,
  onImportGeoJson,
  onRefreshData,
  refreshLoading = false,
  hasImportedShapes = false,
  onShowImportedShapes,
  showUnassignedShapes = false,
  onToggleShowUnassigned,
  showImportedShapes = true,
  onToggleShowImported,
}) {
  const importInputRef = useRef(null)
  const exportMenuRef = useRef(null)
  const [showExportMenu, setShowExportMenu] = useState(false)

  const triggerImportPicker = () => {
    importInputRef.current?.click()
  }

  const handleFileChange = (event) => {
    if (onImportGeoJson) {
      onImportGeoJson(event)
    }
  }

  useEffect(() => {
    const handleDocumentClick = (event) => {
      if (!showExportMenu) return
      if (!exportMenuRef.current?.contains(event.target)) {
        setShowExportMenu(false)
      }
    }
    document.addEventListener('mousedown', handleDocumentClick)
    return () => document.removeEventListener('mousedown', handleDocumentClick)
  }, [showExportMenu])

  return (
    <div className="toolbar">
      <div className="tool-group">
        <button className="btn-tool" type="button" onClick={triggerImportPicker}>
          <i className="fas fa-file-import"></i> Import GeoJSON
        </button>
        <input
          ref={importInputRef}
          type="file"
          id="importFile"
          accept=".geojson,application/geo+json,.json"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        <button
          className="btn-tool"
          id="addSurveyBtn"
          type="button"
          style={{ background: isAddMode ? '#e07a5f' : '#28a745', marginLeft: '10px' }}
          onClick={onAddSurvey}
        >
          <i className={`fas ${isAddMode ? 'fa-times' : 'fa-map-marker-alt'}`}></i>{' '}
          {isAddMode ? 'Cancel Map Click' : 'Add Survey (Map)'}
        </button>
        {typeof onRefreshData === 'function' && (
          <button
            className="btn-tool"
            type="button"
            style={{ background: '#2980b9', marginLeft: '10px' }}
            onClick={() => onRefreshData()}
            disabled={refreshLoading}
            title="Reload surveys and shapes from the server"
          >
            <i className={`fas ${refreshLoading ? 'fa-sync fa-spin' : 'fa-sync-alt'}`}></i>{' '}
            Refresh Data
          </button>
        )}
        {hasImportedShapes && (
          <button
            className="btn-tool"
            type="button"
            style={{ background: '#f1c40f', color: '#222', marginLeft: '10px', fontWeight: 700 }}
            onClick={onShowImportedShapes}
          >
            <i className="fas fa-highlighter"></i> Show Imported Shapes
          </button>
        )}
      </div>

      <div className="tool-group">
        <button
          className="btn-tool"
          type="button"
          style={{ background: isMeasureMode ? '#e07a5f' : '#0e3e49' }}
          onClick={onToggleMeasureMode}
        >
          <i className={`fas ${isMeasureMode ? 'fa-times-circle' : 'fa-ruler-combined'}`}></i>{' '}
          {isMeasureMode ? 'Stop Measure' : 'Measure Area'}
        </button>
        <select value={measureUnit} onChange={(e) => onMeasureUnitChange?.(e.target.value)}>
          <option value="sqm">Sq meter</option>
          <option value="sqkm">Sq kilometer</option>
          <option value="hectare">Hectare</option>
          <option value="acre">Acre</option>
          <option value="sqft">Sq feet</option>
        </select>
        <button
          className="btn-tool"
          type="button"
          style={{ background: '#6b7280' }}
          onClick={onClearMeasure}
        >
          <i className="fas fa-eraser"></i> Clear Measure
        </button>
        <div ref={exportMenuRef} style={{ position: 'relative', marginRight: '10px' }}>
          <button
            className="btn-tool"
            type="button"
            style={{ background: '#1a5c6b' }}
            onClick={() => setShowExportMenu((v) => !v)}
          >
            <i className="fas fa-file-export"></i> Export <i className="fas fa-caret-down"></i>
          </button>
          {showExportMenu && (
            <div
              style={{
                position: 'absolute',
                top: '110%',
                right: 0,
                minWidth: '230px',
                background: '#ffffff',
                border: '1px solid #d7dee7',
                borderRadius: '8px',
                boxShadow: '0 10px 20px rgba(0, 0, 0, 0.15)',
                zIndex: 5000,
                overflow: 'hidden',
              }}
            >
              <button
                type="button"
                style={{
                  width: '100%',
                  textAlign: 'left',
                  border: 'none',
                  background: '#f4fbff',
                  color: '#0e3e49',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  fontFamily: 'Montserrat, sans-serif',
                  fontWeight: 700,
                }}
                onClick={() => {
                  setShowExportMenu(false)
                  onExportDetailedCsv?.()
                }}
              >
                <i className="fas fa-table"></i> Detailed Styled Excel
              </button>
              <button
                type="button"
                style={{
                  width: '100%',
                  textAlign: 'left',
                  border: 'none',
                  borderTop: '1px solid #e6edf3',
                  background: '#fff7ea',
                  color: '#7a4e00',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  fontFamily: 'Montserrat, sans-serif',
                  fontWeight: 700,
                }}
                onClick={() => {
                  setShowExportMenu(false)
                  onExportSummaryCsv?.()
                }}
              >
                <i className="fas fa-chart-pie"></i> Summary Styled Excel
              </button>
            </div>
          )}
        </div>
        <select id="districtFilter" defaultValue="All">
          <option value="All">District Filter (All)</option>
        </select>
        <label
          style={{
            marginLeft: '10px',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '0.85rem',
          }}
        >
          <input type="checkbox" checked={showUnassignedShapes} onChange={onToggleShowUnassigned} />
          Show Unassigned
        </label>
        <label
          style={{
            marginLeft: '10px',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '0.85rem',
          }}
        >
          <input type="checkbox" checked={showImportedShapes} onChange={onToggleShowImported} />
          Show Imported
        </label>
      </div>
    </div>
  )
}

export default Toolbar
