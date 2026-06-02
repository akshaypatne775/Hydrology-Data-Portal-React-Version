import { STRUCTURE_OPTIONS, STRUCTURE_VISUALS } from './surveyFormConstants'

function StructureTypeSelector({
  selected = [],
  onToggle,
  showHint = false,
  editable = true,
  editMode = false,
  structureUpdateMode = 'mark',
  setStructureUpdateMode,
  existingShapeCount = 0,
}) {
  return (
    <div className="field-section">
      <label>Structure Classification (Select all that apply)</label>
      {showHint && (
        <div
          style={{
            fontSize: '0.82rem',
            color: '#0e3e49',
            background: '#f0f7fa',
            border: '1px solid #cde4ea',
            borderRadius: '6px',
            padding: '6px 8px',
            marginBottom: '8px',
          }}
        >
          Select structure first, then use <b>Add Area Boundary</b> to mark shape.
        </div>
      )}
      <div className="structure-grid">
        {STRUCTURE_OPTIONS.map((s) => {
          const picked = selected.includes(s)
          return (
            <button
              key={s}
              type="button"
              className={`structure-card ${picked ? 'is-selected' : ''}`}
              onClick={() => onToggle?.(s)}
              disabled={!editable}
            >
              <span className="structure-icon">{STRUCTURE_VISUALS[s].icon}</span>
              <span className="structure-label">{STRUCTURE_VISUALS[s].label}</span>
              <span className="structure-check">
                <i className={`fas ${picked ? 'fa-check-circle' : 'fa-circle'}`}></i>
              </span>
            </button>
          )
        })}
      </div>
      {editMode && (
        <div style={{ marginTop: '8px' }}>
          <label style={{ display: 'block', fontSize: '0.82rem', fontWeight: 700, marginBottom: '6px' }}>
            Structure update mode
          </label>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', fontSize: '0.82rem' }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <input
                type="radio"
                name="structureUpdateMode"
                value="mark"
                checked={structureUpdateMode === 'mark'}
                onChange={(e) => setStructureUpdateMode?.(e.target.value)}
              />
              Add via boundary marking (requires Add Area Boundary)
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <input
                type="radio"
                name="structureUpdateMode"
                value="replace_existing"
                checked={structureUpdateMode === 'replace_existing'}
                onChange={(e) => setStructureUpdateMode?.(e.target.value)}
                disabled={existingShapeCount <= 0}
              />
              Replace existing marked shapes ({existingShapeCount})
            </label>
          </div>
        </div>
      )}
    </div>
  )
}

export default StructureTypeSelector
