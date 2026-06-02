import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'

const STRUCTURE_OPTIONS = ['RCC', 'Shed Tin', 'Open Space', 'Kaccha']

function AssignShapeModal({ isOpen, shape, onClose, onAssign }) {
  const [propertyId, setPropertyId] = useState('')
  const [structureType, setStructureType] = useState('Open Space')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!isOpen) return
    setPropertyId('')
    setStructureType('Open Space')
  }, [isOpen, shape])

  if (!isOpen || !shape) return null

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!propertyId.trim()) {
      toast.error('Please enter Property ID')
      return
    }
    setSaving(true)
    try {
      await onAssign?.(propertyId.trim(), structureType)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100000,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '12px',
        fontFamily: 'Montserrat, sans-serif',
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          width: 'min(480px, 100%)',
          background: '#fff',
          borderRadius: '10px',
          overflow: 'hidden',
          boxShadow: '0 10px 30px rgba(0,0,0,0.3)',
        }}
      >
        <div style={{ background: '#0e3e49', color: '#fff', padding: '12px 16px', fontWeight: 700 }}>
          Assign Imported Shape
        </div>
        <div style={{ padding: '14px 16px', display: 'grid', gap: '10px' }}>
          <label style={{ fontSize: '0.92rem' }}>
            Property ID
            <input
              value={propertyId}
              onChange={(e) => setPropertyId(e.target.value)}
              placeholder="Enter plot/property ID"
              style={{ width: '100%', marginTop: '6px', padding: '9px', border: '1px solid #ddd' }}
            />
          </label>
          <label style={{ fontSize: '0.92rem' }}>
            Structure Type
            <select
              value={structureType}
              onChange={(e) => setStructureType(e.target.value)}
              style={{ width: '100%', marginTop: '6px', padding: '9px', border: '1px solid #ddd' }}
            >
              {STRUCTURE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', padding: '0 16px 14px' }}>
          <button type="button" className="btn-tool" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn-tool"
            disabled={saving}
            style={{ background: '#0e3e49', color: '#fff' }}
          >
            {saving ? 'Saving...' : 'Assign Shape'}
          </button>
        </div>
      </form>
    </div>
  )
}

export default AssignShapeModal
