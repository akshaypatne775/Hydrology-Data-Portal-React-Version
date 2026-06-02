import { DOCUMENT_DEFS, PHOTO_DOC_DEF } from '../../utils/documentRegistry'

function DocumentFileManager({ editing, onChangeFile, onView, onClear, hasStoredFileRef }) {
  return (
    <div style={{ gridColumn: '1 / -1', marginTop: '6px', borderTop: '1px solid #e5e7eb', paddingTop: '10px' }}>
      <div style={{ fontWeight: 700, color: '#0e3e49', marginBottom: '6px' }}>
        Document Files (replace or add)
      </div>
      <div className="owners-form-grid">
        {DOCUMENT_DEFS.map((doc) => (
          <label key={doc.fileKey}>
            {doc.label.replace(' collected', '').replace(' done', '').replace(' receipt', ' File')}{' '}
            {hasStoredFileRef(editing[doc.b64Key]) ? '✅' : '❌'}
            <input
              type="file"
              accept={doc.accept}
              onChange={(e) => onChangeFile(doc.fileKey, e.target.files?.[0] || null)}
            />
            <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
              <button type="button" className="btn-tool owners-mini-btn" onClick={() => onView(editing[doc.b64Key])}>
                View current file
              </button>
              <button
                type="button"
                className="btn-tool owners-mini-btn owners-remove-btn"
                onClick={() => onClear(doc.b64Key, doc.fileKey)}
              >
                Remove existing file
              </button>
            </div>
          </label>
        ))}
        <label style={{ gridColumn: '1 / -1' }}>
          {PHOTO_DOC_DEF.label} {hasStoredFileRef(editing[PHOTO_DOC_DEF.b64Key]) ? '✅' : '❌'}
          <input
            type="file"
            accept={PHOTO_DOC_DEF.accept}
            onChange={(e) => onChangeFile(PHOTO_DOC_DEF.fileKey, e.target.files?.[0] || null)}
          />
          <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
            <button
              type="button"
              className="btn-tool owners-mini-btn"
              onClick={() => onView(editing[PHOTO_DOC_DEF.b64Key])}
            >
              View current file
            </button>
            <button
              type="button"
              className="btn-tool owners-mini-btn owners-remove-btn"
              onClick={() => onClear(PHOTO_DOC_DEF.b64Key, PHOTO_DOC_DEF.fileKey)}
            >
              Remove existing file
            </button>
          </div>
        </label>
      </div>
    </div>
  )
}

export default DocumentFileManager
