import { useEffect, useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import {
  normalizeRecordPasswordInput,
  recordPasswordConfirmationExpected,
} from '../utils/recordPassword'
import ModalShell from './shared/overlays/ModalShell'
import './FieldApp.css'

const DEFAULT_TYPES = ['RCC', 'Open Space', 'Shed Tin', 'Kaccha']

function ShapeAdminModal({ isOpen, shape, passwordPlot, onClose, onSave, onDeleteBoundary }) {
  const [structureType, setStructureType] = useState('Open Space')
  const [busy, setBusy] = useState(false)
  const [confirmPassword, setConfirmPassword] = useState('')

  const options = useMemo(() => {
    const set = new Set(DEFAULT_TYPES)
    const cur = String(shape?.structureType || '').trim()
    if (cur) set.add(cur)
    return [...set]
  }, [shape])

  useEffect(() => {
    if (!isOpen || !shape) return
    const cur = String(shape.structureType || '').trim()
    setStructureType(cur || 'Open Space')
    setConfirmPassword('')
  }, [isOpen, shape])

  if (!isOpen || !shape) return null

  const areaVal = Number(shape.area ?? shape.calculatedArea ?? 0)

  const verifyRecordPassword = () => {
    if (!passwordPlot) {
      toast.error('Cannot verify this edit.')
      return false
    }
    const pid = String(passwordPlot.propertyId ?? '').trim()
    const owner = String(passwordPlot.ownerName ?? '').trim()
    if (!pid && !owner) {
      toast.error('Cannot verify: survey record is missing ID and owner.')
      return false
    }
    const expected = recordPasswordConfirmationExpected(passwordPlot)
    if (!expected) {
      toast.error('Cannot verify this edit.')
      return false
    }
    if (normalizeRecordPasswordInput(confirmPassword) !== expected) {
      toast.error('Does not match. Type Property ID and Owner name together with no spaces.')
      return false
    }
    return true
  }

  const run = async (fn) => {
    if (!verifyRecordPassword()) return
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      isOpen={Boolean(isOpen && shape)}
      className="survey-modal-backdrop"
      cardClassName="field-card survey-modal-card"
      cardStyle={{ width: 'min(420px, 94vw)' }}
      onBackdropClick={onClose}
    >
        <div className="field-header">
          <h2>
            <i className="fas fa-draw-polygon"></i> Edit boundary
          </h2>
          <span>Change classification or remove this polygon</span>
        </div>

        <div className="field-section">
          <div className="field-group">
            <label>Property ID</label>
            <input type="text" value={String(shape.propertyId || '')} readOnly style={{ background: '#f4f6f8' }} />
          </div>
          <div className="field-group">
            <label>Calculated area (sq ft)</label>
            <input type="text" value={Number.isFinite(areaVal) ? areaVal.toFixed(2) : '0'} readOnly style={{ background: '#f4f6f8' }} />
          </div>
          <div className="field-group">
            <label>Structure type</label>
            <select value={structureType} onChange={(e) => setStructureType(e.target.value)} disabled={busy}>
              {options.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
          <div className="field-group">
            <label>Verification (Property ID + Owner name, no spaces)</label>
            <input
              type="password"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Required to save or delete"
              disabled={busy}
              style={{ fontFamily: 'ui-monospace, monospace' }}
            />
          </div>
        </div>

        <div className="field-actions">
          <button
            type="button"
            className="btn-save"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await onSave(structureType)
              })
            }
          >
            <i className="fas fa-save"></i> Save to database
          </button>
          <button
            type="button"
            className="btn-edit"
            style={{ background: '#c0392b', color: '#fff' }}
            disabled={busy}
            onClick={() =>
              run(async () => {
                if (!window.confirm('Delete this boundary polygon from the database?')) return
                await onDeleteBoundary()
              })
            }
          >
            <i className="fas fa-trash-alt"></i> Delete boundary
          </button>
          <button type="button" className="btn-close" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
    </ModalShell>
  )
}

export default ShapeAdminModal
