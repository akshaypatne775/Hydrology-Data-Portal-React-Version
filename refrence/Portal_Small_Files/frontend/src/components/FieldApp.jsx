import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import FieldMapModal from './FieldMapModal'
import StructureTypeSelector from './shared/StructureTypeSelector'
import SurveyCoreFields from './shared/SurveyCoreFields'
import DocumentTrackingSection from './shared/DocumentTrackingSection'
import { useAuth } from '../contexts/AuthContext'
import { PHOTO_DOC_DEF } from '../utils/documentRegistry'
import { encodeSurveyFileInputs } from '../utils/fileEncoding'
import * as surveyApi from '../services/surveyApi'
import {
  enqueueOfflineSurveyJob,
  getPendingOfflineCount,
  syncPendingOfflineJobs,
} from '../services/offlineSurveyQueue'
import { buildShapePayload, buildSurveyCreatePayload } from '../services/surveyPayloads'
import './FieldApp.css'

function isLikelyNetworkError(err) {
  if (!err) return false
  if (err.name === 'TypeError') return true
  const msg = String(err.message || '').toLowerCase()
  return (
    msg.includes('failed to fetch') ||
    msg.includes('network error') ||
    msg.includes('load failed') ||
    msg.includes('networkerror') ||
    msg.includes('network request failed')
  )
}

const initialState = {
  propertyId: '',
  ownerName: '',
  structureTypes: [],
  acquisitionStage: 'Notice 37(2) Distribution',
  noticeSent: 'No',
  moneyDistributed: 0,
  areaSqft: '',
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
}

function FieldApp() {
  const { logout } = useAuth()
  const [formData, setFormData] = useState(initialState)
  const [isMapOpen, setIsMapOpen] = useState(false)
  const [capturedShapes, setCapturedShapes] = useState([])
  const [coordinates, setCoordinates] = useState(null)
  const [pendingOfflineCount, setPendingOfflineCount] = useState(0)
  const [syncingOffline, setSyncingOffline] = useState(false)
  const [isOnline, setIsOnline] = useState(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  )

  const refreshPendingCount = useCallback(async () => {
    try {
      const n = await getPendingOfflineCount()
      setPendingOfflineCount(n)
    } catch {
      setPendingOfflineCount(0)
    }
  }, [])

  useEffect(() => {
    refreshPendingCount()
    const onOnline = () => {
      setIsOnline(true)
      refreshPendingCount()
    }
    const onOffline = () => setIsOnline(false)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [refreshPendingCount])

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

  const buildSurveyJob = async (lat, lng) => {
    const totalArea = capturedShapes.reduce((sum, s) => sum + (s.areaSqft || 0), 0)
    const encodedFiles = await encodeSurveyFileInputs(formData)
    const surveyPayload = buildSurveyCreatePayload({
      formData,
      encodedFiles,
      lat,
      lng,
      totalArea,
      defaults: { state: 'Field', district: 'Field' },
    })
    const shapePayloads = capturedShapes.map((shape) => buildShapePayload(shape, formData.propertyId))
    return { surveyPayload, shapePayloads }
  }

  const pushJobToServer = async (surveyPayload, shapePayloads) => {
    await surveyApi.saveSurvey(surveyPayload)
    for (const p of shapePayloads) {
      await surveyApi.saveShape(p)
    }
  }

  const queueJobLocally = async (lat, lng) => {
    const { surveyPayload, shapePayloads } = await buildSurveyJob(lat, lng)
    await enqueueOfflineSurveyJob({ surveyPayload, shapePayloads })
  }

  const handleSyncOffline = async () => {
    if (!navigator.onLine) {
      toast.error('You are offline. Connect to the internet, then sync.')
      return
    }
    if (pendingOfflineCount === 0) {
      toast('Nothing to sync.')
      return
    }
    setSyncingOffline(true)
    try {
      const { synced, failed, errors } = await syncPendingOfflineJobs(surveyApi)
      await refreshPendingCount()
      if (synced > 0) {
        toast.success(`Synced ${synced} offline survey job${synced === 1 ? '' : 's'}.`)
      }
      if (failed > 0) {
        toast.error(errors[0] || `Could not sync ${failed} job(s). Try again.`)
      }
    } catch (e) {
      toast.error(e?.message || 'Sync failed.')
    } finally {
      setSyncingOffline(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!formData.photoFile) {
      toast.error('Please select a site photograph.')
      return
    }
    if (!coordinates && !navigator.geolocation) {
      toast.error('GPS unavailable. Please capture location from map modal.')
      return
    }

    try {
      let lat
      let lng
      if (coordinates) {
        lat = coordinates.lat
        lng = coordinates.lng
      } else {
        try {
          const position = await new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(resolve, reject, {
              enableHighAccuracy: true,
              timeout: 20000,
              maximumAge: 0,
            })
          })
          lat = position.coords.latitude
          lng = position.coords.longitude
        } catch (geoErr) {
          const code = geoErr?.code
          if (code === 1) {
            toast.error('Location access denied. Please select location from the map manually.')
          } else if (code === 2) {
            toast.error('GPS position unavailable. Please set your location on the map.')
          } else if (code === 3) {
            toast.error('Location request timed out. Please try again or pick a point on the map.')
          } else {
            toast.error('Could not read your location. Please select location from the map manually.')
          }
          return
        }
      }

      const finishLocalSave = async () => {
        setFormData(initialState)
        setCapturedShapes([])
        setCoordinates(null)
        await refreshPendingCount()
        toast.success('Saved on this device. Tap “Sync Offline Data” when you have internet.')
      }

      if (!navigator.onLine) {
        await queueJobLocally(lat, lng)
        await finishLocalSave()
        return
      }

      try {
        const { surveyPayload, shapePayloads } = await buildSurveyJob(lat, lng)
        await pushJobToServer(surveyPayload, shapePayloads)
      } catch (err) {
        if (isLikelyNetworkError(err)) {
          await queueJobLocally(lat, lng)
          await finishLocalSave()
          return
        }
        throw err
      }

      setFormData(initialState)
      setCapturedShapes([])
      setCoordinates(null)
      toast.success('Survey data saved successfully.')
    } catch (err) {
      toast.error(err?.message || 'Failed to save survey. Please try again.')
    }
  }

  return (
    <div className="field-page">
      <div className="field-top-nav">
        <Link to="/dashboard" className="field-top-nav-link">
          <i className="fas fa-th-large"></i> Dashboard
        </Link>
        <button
          type="button"
          className="field-top-nav-logout"
          onClick={logout}
        >
          <i className="fas fa-sign-out-alt"></i> Log out
        </button>
      </div>
      <div className="field-card">
        <div className="field-header">
          <h2>
            <i className="fas fa-clipboard-list"></i> Add Survey Data
          </h2>
          <div className="field-header-meta">
            <span>Fill details, draw boundaries, then save</span>
            {!isOnline && <span className="field-offline-badge">Offline</span>}
            {pendingOfflineCount > 0 && (
              <span className="field-pending-badge">{pendingOfflineCount} queued</span>
            )}
          </div>
        </div>

        <div className="field-offline-row">
          <button
            type="button"
            className="btn-sync-offline"
            disabled={syncingOffline || pendingOfflineCount === 0}
            onClick={handleSyncOffline}
          >
            <i className="fas fa-cloud-upload-alt"></i>{' '}
            {syncingOffline ? 'Syncing…' : 'Sync Offline Data'}
            {pendingOfflineCount > 0 ? ` (${pendingOfflineCount})` : ''}
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <input type="hidden" value={coordinates?.lat ?? ''} readOnly />
          <input type="hidden" value={coordinates?.lng ?? ''} readOnly />

          <SurveyCoreFields
            formData={formData}
            setFormData={setFormData}
            capturedShapesCount={capturedShapes.length}
            onBoundaryClick={() => setIsMapOpen(true)}
            canStartBoundary
          />
          <StructureTypeSelector selected={formData.structureTypes} onToggle={toggleStructure} />
          <DocumentTrackingSection formData={formData} setFormData={setFormData} />

          <div className="field-group">
            <label>Site Photo (Optional)</label>
            <div className="upload-box">
              <div className="upload-title">{PHOTO_DOC_DEF.label}</div>
              <input
                type="file"
                accept={PHOTO_DOC_DEF.accept}
                onChange={(e) => setFormData((p) => ({ ...p, photoFile: e.target.files?.[0] || null }))}
                required
              />
            </div>
          </div>

          <div className="field-actions">
            <button type="submit" className="btn-save">
              <i className="fas fa-save"></i> Save Data
            </button>
            <button type="button" className="btn-edit" onClick={() => setIsMapOpen(true)}>
              <i className="fas fa-edit"></i> Edit Shape Boundaries
            </button>
            <button
              type="button"
              className="btn-close"
              onClick={() => {
                setFormData(initialState)
                setCapturedShapes([])
                setCoordinates(null)
              }}
            >
              Close
            </button>
          </div>
        </form>
      </div>
      <FieldMapModal
        isOpen={isMapOpen}
        selectedStructures={formData.structureTypes}
        onClose={() => setIsMapOpen(false)}
        onDone={({ coordinates: point, capturedShapes: shapes }) => {
          if (point) setCoordinates(point)
          setCapturedShapes(shapes || [])
          setFormData((prev) => ({
            ...prev,
            areaSqft: (shapes || []).reduce((sum, s) => sum + (s.areaSqft || 0), 0).toFixed(2),
          }))
          setIsMapOpen(false)
        }}
      />
    </div>
  )
}

export default FieldApp
