import { ACQUISITION_STAGE_OPTIONS } from './surveyFormConstants'

function SurveyCoreFields({ formData, setFormData, capturedShapesCount, onBoundaryClick, canStartBoundary = true }) {
  return (
    <>
      <div className="field-grid field-grid-2x">
        <div className="field-group">
          <label>Plot / Property ID</label>
          <input
            type="text"
            value={formData.propertyId}
            placeholder="Plot / Property ID"
            onChange={(e) => setFormData((p) => ({ ...p, propertyId: e.target.value }))}
            required
          />
        </div>
        <div className="field-group">
          <label>Owner Name</label>
          <input
            type="text"
            value={formData.ownerName}
            placeholder="Owner Name"
            onChange={(e) => setFormData((p) => ({ ...p, ownerName: e.target.value }))}
            required
          />
        </div>
      </div>

      <div className="field-section">
        <label>Total Plot Area (Sqft)</label>
        <div className="area-row">
          <input
            type="number"
            id="areaSqft"
            min="1"
            step="0.01"
            value={formData.areaSqft}
            onChange={(e) => setFormData((p) => ({ ...p, areaSqft: e.target.value }))}
            required
          />
          <button
            type="button"
            className="btn-map"
            disabled={!canStartBoundary}
            onClick={onBoundaryClick}
            style={!canStartBoundary ? { opacity: 0.6, cursor: 'not-allowed' } : undefined}
          >
            <i className="fas fa-draw-polygon"></i> Add Area Boundary
          </button>
        </div>
        <span className="area-status">
          {capturedShapesCount > 0 ? `${capturedShapesCount} shapes captured` : 'No shapes captured yet.'}
        </span>
      </div>

      <div className="field-grid field-grid-2">
        <div className="field-group">
          <label>Acquisition Stage</label>
          <select
            value={formData.acquisitionStage}
            onChange={(e) => setFormData((p) => ({ ...p, acquisitionStage: e.target.value }))}
          >
            {ACQUISITION_STAGE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <div className="field-group">
          <label>Notice Sent</label>
          <select
            value={formData.noticeSent}
            onChange={(e) => setFormData((p) => ({ ...p, noticeSent: e.target.value }))}
          >
            <option value="No">No</option>
            <option value="Yes">Yes</option>
          </select>
        </div>
      </div>

      <div className="field-group">
        <label>Compensation Distributed (₹)</label>
        <input
          type="number"
          min="0"
          value={formData.moneyDistributed}
          onChange={(e) => setFormData((p) => ({ ...p, moneyDistributed: Number(e.target.value || 0) }))}
        />
      </div>

      <div className="field-grid field-grid-2">
        <div className="field-group">
          <label>Number of Trees</label>
          <input
            type="number"
            min="0"
            value={formData.numberOfTrees}
            onChange={(e) => setFormData((p) => ({ ...p, numberOfTrees: Number(e.target.value || 0) }))}
          />
        </div>
        <div className="field-group">
          <label>Total Distribution</label>
          <input
            type="number"
            min="0"
            value={formData.totalDistribution}
            onChange={(e) => setFormData((p) => ({ ...p, totalDistribution: Number(e.target.value || 0) }))}
          />
        </div>
      </div>
    </>
  )
}

export default SurveyCoreFields
