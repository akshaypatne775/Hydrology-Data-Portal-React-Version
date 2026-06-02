import ModalShell from './ModalShell'

function DocumentViewerOverlay({
  isOpen,
  url,
  contentType,
  onClose,
  backdropClass = 'owners-modal-backdrop',
  cardClass = 'owners-modal-card',
  cardStyle,
  bodyStyle,
}) {
  return (
    <ModalShell isOpen={isOpen} className={backdropClass} cardClassName={cardClass} cardStyle={cardStyle}>
      <div
        style={{
          background: '#0e3e49',
          color: '#fff',
          padding: '10px 14px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <b>
          <i className="fas fa-file-alt"></i> Document Viewer
        </b>
        <button type="button" className="btn-tool owners-mini-btn owners-remove-btn" onClick={onClose}>
          Close
        </button>
      </div>
      <div
        style={{
          height: 'calc(90vh - 48px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#111827',
          padding: '8px',
          ...bodyStyle,
        }}
      >
        {String(contentType).startsWith('image/') ? (
          <img src={url} alt="Document" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
        ) : (
          <iframe src={url} title="Document viewer" style={{ width: '100%', height: '100%', border: 'none', background: '#fff' }} />
        )}
      </div>
    </ModalShell>
  )
}

export default DocumentViewerOverlay
