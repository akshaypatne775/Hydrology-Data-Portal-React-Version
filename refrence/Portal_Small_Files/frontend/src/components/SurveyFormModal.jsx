import { useEffect, useState } from 'react'
import StructureTypeSelector from './shared/StructureTypeSelector'
import SurveyCoreFields from './shared/SurveyCoreFields'
import DocumentTrackingSection from './shared/DocumentTrackingSection'
import { PHOTO_DOC_DEF } from '../utils/documentRegistry'
import './FieldApp.css'

const defaultFormData = {
  propertyId: '',
  ownerName: '',
  structureTypes: [],
  areaSqft: '',
  acquisitionStage: 'Notice 37(2) Distribution',
  noticeSent: 'No',
  moneyDistributed: 0,
  numberOfTrees: 0,
  totalDistribution: 0,
  samarpanReceipt: false,
  fieldSurveyDone: false,
  ownerVerification: false,
  aadharCollected: false,
  panCollected: false,
  bankDetailsCollected: false,
  aadharFile: null,
  panFile: null,
  bankFile: null,
  ownerVerifFile: null,
  samarpanFile: null,
  surveyFile: null,
  photoFile: null,
  structureUpdateMode: 'mark', // mark | replace_existing
}

function propBool(v) {
  return v === true || v === 1 || v === '1'
}

const EXTRA_SURVEY_KEYS = [
  'dbId',
  'lat',
  'lng',
  'state',
  'district',
  'photoB64',
  'aadharFileB64',
  'panFileB64',
  'bankFileB64',
  'ownerVerifFileB64',
  'samarpanFileB64',
  'surveyFileB64',
]

function mergeInitialFormData(raw) {
  if (!raw || typeof raw !== 'object') return {}
  const out = {}
  for (const key of Object.keys(defaultFormData)) {
    if (key in raw) out[key] = raw[key]
  }
  for (const key of EXTRA_SURVEY_KEYS) {
    if (key in raw) out[key] = raw[key]
  }

  const structureTypes = Array.isArray(raw.structureTypes)
    ? raw.structureTypes
    : typeof raw.structureType === 'string'
      ? raw.structureType
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
      : defaultFormData.structureTypes

  return {
    ...out,
    structureTypes,
    areaSqft: raw.areaSqft != null && raw.areaSqft !== '' ? String(raw.areaSqft) : '',
    samarpanReceipt: propBool(raw.samarpanReceipt),
    fieldSurveyDone: propBool(raw.fieldSurveyDone),
    ownerVerification: propBool(raw.ownerVerification),
    aadharCollected: propBool(raw.aadharCollected),
    panCollected: propBool(raw.panCollected),
    bankDetailsCollected: propBool(raw.bankDetailsCollected),
    moneyDistributed: Number(raw.moneyDistributed) || 0,
    numberOfTrees: Number(raw.numberOfTrees) || 0,
    totalDistribution: Number(raw.totalDistribution) || 0,
  }
}

function SurveyFormModal({
  isOpen,
  onClose,
  initialData,
  onSubmit,
  onStartDrawing,
  capturedShapesCount = 0,
  isEditMode = false,
  existingShapeCount = 0,
}) {
  const [formData, setFormData] = useState(defaultFormData)
  const canStartBoundary = Array.isArray(formData.structureTypes) && formData.structureTypes.length > 0


  useEffect(() => {
    if (isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFormData({ ...defaultFormData, ...mergeInitialFormData(initialData) })
    }
  }, [isOpen, initialData])

  const toggleStructure = (value) => {
    setFormData((prev) => {
      const found = prev.structureTypes.includes(value)
      return {
        ...prev,
        structureTypes: found
          ? prev.structureTypes.filter((s) => s !== value)
          : [...prev.structureTypes, value],
      }
    })
  }

  if (!isOpen) return null

  return (
    <div id="surveyFormModal" className="survey-modal-backdrop" role="presentation">
      <div className="field-card survey-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="field-header">
          <h2>
            <i className="fas fa-clipboard-list"></i> Add Survey Data
          </h2>
          <span>Fill details, draw boundaries, then save</span>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSubmit(formData)
          }}
        >
          <SurveyCoreFields
            formData={formData}
            setFormData={setFormData}
            capturedShapesCount={capturedShapesCount}
            onBoundaryClick={() => onStartDrawing(formData)}
            canStartBoundary={canStartBoundary}
          />
          <StructureTypeSelector
            selected={formData.structureTypes}
            onToggle={toggleStructure}
            showHint
            editMode={isEditMode}
            structureUpdateMode={formData.structureUpdateMode}
            setStructureUpdateMode={(value) =>
              setFormData((p) => ({ ...p, structureUpdateMode: value }))
            }
            existingShapeCount={existingShapeCount}
          />
          <DocumentTrackingSection formData={formData} setFormData={setFormData} />

          <div className="field-group">
            <label>Site Photo (Optional)</label>
            <div className="upload-box">
              <div className="upload-title">{PHOTO_DOC_DEF.label}</div>
              <input
                type="file"
                accept={PHOTO_DOC_DEF.accept}
                onChange={(e) => setFormData((p) => ({ ...p, photoFile: e.target.files?.[0] || null }))}
              />
            </div>
          </div>

          <div className="field-actions">
            <button type="submit" className="btn-save">
              <i className="fas fa-save"></i> Save Data
            </button>
            <button type="button" className="btn-edit" onClick={() => onStartDrawing(formData)}>
              <i className="fas fa-edit"></i> Edit Shape Boundaries
            </button>
            <button type="button" className="btn-close" onClick={onClose}>
              Close
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default SurveyFormModal
