function DrawingModeBar({
  isVisible,
  availableStructures,
  activeDrawType,
  onDrawTypeChange,
  onDone,
}) {
  if (!isVisible) return null

  return (
    <div
      id="drawingModeBar"
      style={{
        display: 'block',
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 2200,
        background: '#0e3e49',
        color: '#fff',
        padding: '10px 14px',
        boxShadow: '0 3px 12px rgba(0,0,0,0.35)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <h4 style={{ margin: 0, color: '#fff' }}>
          <i className="fas fa-draw-polygon"></i> Map Drawing Mode
        </h4>
        <select
          id="dashDrawSelect"
          value={activeDrawType}
          onChange={(e) => onDrawTypeChange(e.target.value)}
          style={{ padding: '6px', borderRadius: '4px', border: 'none', minWidth: '220px' }}
        >
          <option value="">Select type & Draw...</option>
          {availableStructures.map((structure) => (
            <option key={structure} value={structure}>
              {structure}
            </option>
          ))}
        </select>
        <button type="button" className="btn-tool" style={{ background: '#f4a261' }} onClick={onDone}>
          <i className="fas fa-check"></i> Done
        </button>
      </div>
    </div>
  )
}

export default DrawingModeBar
