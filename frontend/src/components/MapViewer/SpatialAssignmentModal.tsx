import { useEffect, useState, type FormEvent } from 'react'
import {
  STRUCTURE_TYPES,
  normalizeStructureType,
  type SpatialFeature,
  type StructureType,
} from './spatialTypes'

type SpatialAssignmentModalProps = {
  feature: SpatialFeature | null
  saving: boolean
  onClose: () => void
  onSave: (payload: {
    plot_id: string
    owner_name: string
    structure_type: StructureType
  }) => void
  onDelete: (feature: SpatialFeature) => void
}

export function SpatialAssignmentModal({
  feature,
  saving,
  onClose,
  onSave,
  onDelete,
}: SpatialAssignmentModalProps) {
  const [plotId, setPlotId] = useState('')
  const [ownerName, setOwnerName] = useState('')
  const [structureType, setStructureType] = useState<StructureType>('Unassigned')

  useEffect(() => {
    if (!feature) return
    setPlotId(feature.plot_id || '')
    setOwnerName(feature.owner_name || '')
    setStructureType(normalizeStructureType(feature.structure_type))
  }, [feature])

  if (!feature) return null

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSave({
      plot_id: plotId.trim(),
      owner_name: ownerName.trim(),
      structure_type: structureType,
    })
  }

  return (
    <div className="spatial-modal" role="dialog" aria-modal="true" aria-label="Assign spatial feature">
      <form className="spatial-modal__card" onSubmit={handleSubmit}>
        <header className="spatial-modal__header">
          <div>
            <p className="spatial-modal__eyebrow">Spatial Assignment</p>
            <h3>
              <i className="fa-solid fa-tags" aria-hidden />
              Shape Metadata
            </h3>
          </div>
          <button
            type="button"
            className="spatial-modal__close"
            onClick={onClose}
            disabled={saving}
            aria-label="Close"
          >
            <i className="fa-solid fa-xmark" aria-hidden />
          </button>
        </header>
        <div className="spatial-modal__body">
          <label>
            Plot ID
            <input
              value={plotId}
              onChange={(event) => setPlotId(event.target.value)}
              placeholder="Enter plot ID"
            />
          </label>
          <label>
            Owner Name
            <input
              value={ownerName}
              onChange={(event) => setOwnerName(event.target.value)}
              placeholder="Enter owner name"
            />
          </label>
          <label>
            Structure Type
            <select
              value={structureType}
              onChange={(event) => setStructureType(normalizeStructureType(event.target.value))}
            >
              {STRUCTURE_TYPES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <p className="spatial-modal__meta">
            Source: {feature.source_type.replace('-', ' ')} - Geometry: {feature.geometry_type}
          </p>
        </div>
        <footer className="spatial-modal__actions">
          <button
            type="button"
            className="spatial-modal__danger"
            onClick={() => onDelete(feature)}
            disabled={saving}
          >
            <i className="fa-solid fa-trash" aria-hidden />
            Delete
          </button>
          <span className="spatial-modal__spacer" />
          <button type="button" className="spatial-modal__ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="spatial-modal__save" disabled={saving}>
            {saving ? 'Saving...' : 'Save Assignment'}
          </button>
        </footer>
      </form>
    </div>
  )
}
