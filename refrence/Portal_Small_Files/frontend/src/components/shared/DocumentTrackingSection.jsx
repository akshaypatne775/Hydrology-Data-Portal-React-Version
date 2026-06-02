import { DOCUMENT_DEFS } from '../../utils/documentRegistry'

function DocumentTrackingSection({ formData, setFormData }) {
  return (
    <>
      <div className="field-section">
        <label>Document tracking</label>
        <div className="doc-grid">
          {DOCUMENT_DEFS.map((doc) => (
            <div key={doc.boolKey} className="doc-item">
              <span className="doc-label">{doc.label}</span>
              <button
                type="button"
                className={`doc-switch ${formData[doc.boolKey] ? 'is-on' : ''}`}
                onClick={() =>
                  setFormData((p) => ({
                    ...p,
                    [doc.boolKey]: !p[doc.boolKey],
                    ...(p[doc.boolKey] ? { [doc.fileKey]: null } : {}),
                  }))
                }
              >
                <span className="doc-knob"></span>
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="field-group">
        <label>Document files</label>
        {DOCUMENT_DEFS.filter((doc) => formData[doc.boolKey]).map((doc) => (
          <div key={doc.boolKey} className="upload-box">
            <div className="upload-title">{doc.label}</div>
            <input
              type="file"
              accept={doc.accept}
              capture="environment"
              onChange={(e) =>
                setFormData((p) => ({ ...p, [doc.fileKey]: e.target.files?.[0] || null }))
              }
            />
          </div>
        ))}
      </div>
    </>
  )
}

export default DocumentTrackingSection
