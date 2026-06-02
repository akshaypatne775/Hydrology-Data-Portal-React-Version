import { useEffect, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import {
  normalizeRecordPasswordInput,
  recordPasswordConfirmationExpected,
} from '../utils/recordPassword'
import ModalShell from './shared/overlays/ModalShell'
import './FieldApp.css'

const VARIANT_COPY = {
  delete: {
    title: 'Confirm delete',
    icon: 'fa-trash-alt',
    subtitle: 'Survey records cannot be recovered after deletion',
    intro: (
      <>
        You are about to remove this survey and <strong>all</strong> map boundaries for it. Type{' '}
        <strong>Property ID</strong> and <strong>Owner name</strong> in one line, <strong>with no spaces</strong>{' '}
        (remove every space from both parts).
      </>
    ),
    submitLabel: 'Delete permanently',
    submitClass: '#c0392b',
  },
  editOpen: {
    title: 'Confirm edit access',
    icon: 'fa-edit',
    subtitle: 'Password required to change this survey',
    intro: (
      <>
        To open the editor for this plot, type <strong>Property ID</strong> and <strong>Owner name</strong> together,{' '}
        <strong>with no spaces</strong> anywhere.
      </>
    ),
    submitLabel: 'Continue to editor',
    submitClass: '#0e3e49',
  },
  surveySave: {
    title: 'Confirm save',
    icon: 'fa-save',
    subtitle: 'Changes will be written to the database',
    intro: (
      <>
        To save edits to this survey, type <strong>Property ID</strong> and <strong>Owner name</strong> together,{' '}
        <strong>with no spaces</strong> anywhere.
      </>
    ),
    submitLabel: 'Save changes',
    submitClass: '#0e3e49',
  },
}

/**
 * @param {{ variant: 'delete'|'editOpen'|'surveySave', plot: object, formData?: object }} gate
 */
function RecordPasswordConfirmModal({ gate, onClose, onConfirmed }) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const plot = gate?.plot
  const variant = gate?.variant
  const copy = variant ? VARIANT_COPY[variant] : null

  const expected = useMemo(() => (plot ? recordPasswordConfirmationExpected(plot) : ''), [plot])

  useEffect(() => {
    if (gate) {
      setInput('')
      setBusy(false)
    }
  }, [gate])

  if (!gate || !plot || !copy) return null

  const propertyLabel = String(plot.propertyId ?? '').trim() || '—'
  const ownerLabel = String(plot.ownerName ?? '').trim() || '—'

  const handleSubmit = async (e) => {
    e.preventDefault()
    const pid = String(plot?.propertyId ?? '').trim()
    const owner = String(plot?.ownerName ?? '').trim()
    if (!pid && !owner) {
      toast.error('Cannot verify: this record has no property ID or owner name.')
      return
    }
    if (!expected) {
      toast.error('This record could not be verified. Contact support.')
      return
    }
    const got = normalizeRecordPasswordInput(input)
    if (got !== expected) {
      toast.error('Does not match. Type Property ID and Owner name together with no spaces anywhere.')
      return
    }
    setBusy(true)
    try {
      const result = await onConfirmed(gate)
      if (result !== false) onClose()
    } catch {
      // Parent toasts; keep modal open
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      isOpen={Boolean(gate)}
      className="survey-modal-backdrop"
      cardClassName="field-card survey-modal-card"
      cardStyle={{ width: 'min(440px, 94vw)' }}
      onBackdropClick={busy ? undefined : onClose}
    >
        <div className="field-header">
          <h2>
            <i className={`fas ${copy.icon}`}></i> {copy.title}
          </h2>
          <span>{copy.subtitle}</span>
        </div>

        <div className="field-section">
          <p style={{ margin: '0 0 10px', fontSize: '0.9rem', color: '#374151', lineHeight: 1.45 }}>{copy.intro}</p>
          <div
            style={{
              fontSize: '0.85rem',
              background: '#f0f7fa',
              border: '1px solid #cde4ea',
              borderRadius: '6px',
              padding: '10px 12px',
              marginBottom: '12px',
            }}
          >
            <div>
              <strong>Property ID:</strong> {propertyLabel}
            </div>
            <div style={{ marginTop: '6px' }}>
              <strong>Owner:</strong> {ownerLabel}
            </div>
          </div>
          <form onSubmit={handleSubmit}>
            <div className="field-group">
              <label>Confirmation (ID + name, no spaces)</label>
              <input
                type="password"
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="e.g. PLOT-12RajeshKumar"
                disabled={busy}
                style={{ fontFamily: 'ui-monospace, monospace' }}
              />
            </div>
            <div className="field-actions" style={{ marginTop: '14px' }}>
              <button
                type="submit"
                className="btn-save"
                style={{ background: copy.submitClass }}
                disabled={busy}
              >
                {busy ? 'Please wait…' : copy.submitLabel}
              </button>
              <button type="button" className="btn-close" disabled={busy} onClick={onClose}>
                Cancel
              </button>
            </div>
          </form>
        </div>
    </ModalShell>
  )
}

export default RecordPasswordConfirmModal
