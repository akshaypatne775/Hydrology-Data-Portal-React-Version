import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useAuth } from '../contexts/AuthContext'
import useSurveys from '../hooks/useSurveys'
import useDocumentViewer from '../hooks/useDocumentViewer'
import { hasStoredFileRef } from '../utils/storedFileRef'
import { fileToBase64 } from '../utils/fileEncoding'
import { buildSurveyUpdatePayloadFromRow } from '../services/surveyPayloads'
import DocumentFileManager from './shared/DocumentFileManager'
import DocumentViewerOverlay from './shared/overlays/DocumentViewerOverlay'

const EARTH_RADIUS_KM = 6371

function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function toBoolInt(value) {
  return value === true || value === 1 || value === '1' ? 1 : 0
}

function haversineKm(lat1, lng1, lat2, lng2) {
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLng = ((lng2 - lng1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) *
      Math.sin(dLng / 2)
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
  return EARTH_RADIUS_KM * c
}

function OwnersPage() {
  const { logout } = useAuth()
  const { surveys, loading, error, refetch, updateSurveyData } = useSurveys()
  const [search, setSearch] = useState('')
  const [districtFilter, setDistrictFilter] = useState('All')
  const [sortMode, setSortMode] = useState('distance')
  const [refPoint, setRefPoint] = useState({ lat: null, lng: null })
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const { viewingDocUrl, viewingDocType, openDocument, closeDocument } = useDocumentViewer()

  const owners = useMemo(() => surveys?.features || [], [surveys])

  const districts = useMemo(() => {
    const set = new Set()
    owners.forEach((f) => {
      const d = String(f?.properties?.district || '').trim()
      if (d) set.add(d)
    })
    return ['All', ...Array.from(set).sort()]
  }, [owners])

  const rows = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    const filtered = owners
      .map((f) => {
        const p = f?.properties || {}
        const coords = f?.geometry?.coordinates || []
        const lng = toNumber(coords?.[0], null)
        const lat = toNumber(coords?.[1], null)
        let distanceKm = null
        if (
          refPoint.lat !== null &&
          refPoint.lng !== null &&
          lat !== null &&
          lng !== null
        ) {
          distanceKm = haversineKm(refPoint.lat, refPoint.lng, lat, lng)
        }
        return { feature: f, p, lat, lng, distanceKm }
      })
      .filter(({ p }) => {
        if (districtFilter !== 'All' && String(p.district || '') !== districtFilter) return false
        if (!keyword) return true
        const id = String(p.propertyId || '').toLowerCase()
        const name = String(p.ownerName || '').toLowerCase()
        return id.includes(keyword) || name.includes(keyword)
      })

    filtered.sort((a, b) => {
      if (sortMode === 'name') {
        return String(a.p.ownerName || '').localeCompare(String(b.p.ownerName || ''))
      }
      if (sortMode === 'id') {
        return String(a.p.propertyId || '').localeCompare(String(b.p.propertyId || ''))
      }
      const da = a.distanceKm ?? Number.POSITIVE_INFINITY
      const db = b.distanceKm ?? Number.POSITIVE_INFINITY
      return da - db
    })
    return filtered
  }, [owners, search, districtFilter, sortMode, refPoint])

  const startEdit = (row) => {
    const p = row?.feature?.properties || {}
    setEditing({
      source: row.feature,
      originalPropertyId: String(p.propertyId || ''),
      authPass: '',
      propertyId: String(p.propertyId || ''),
      ownerName: String(p.ownerName || ''),
      district: String(p.district || ''),
      state: String(p.state || 'Dashboard'),
      acquisitionStage: String(p.acquisitionStage || 'Notice 37(2) Distribution'),
      moneyDistributed: toNumber(p.moneyDistributed, 0),
      areaSqft: toNumber(p.areaSqft, 0),
      numberOfTrees: toNumber(p.numberOfTrees, 0),
      totalDistribution: toNumber(p.totalDistribution, 0),
      structureType: String(p.structureType || ''),
      noticeSent: String(p.noticeSent || 'No'),
      lat: toNumber(row.lat, 0),
      lng: toNumber(row.lng, 0),
      samarpanReceipt: toBoolInt(p.samarpanReceipt),
      fieldSurveyDone: Boolean(p.fieldSurveyDone),
      ownerVerification: Boolean(p.ownerVerification),
      aadharCollected: Boolean(p.aadharCollected),
      panCollected: Boolean(p.panCollected),
      bankDetailsCollected: Boolean(p.bankDetailsCollected),
      aadharFileB64: String(p.aadharFileB64 || ''),
      panFileB64: String(p.panFileB64 || ''),
      bankFileB64: String(p.bankFileB64 || ''),
      ownerVerifFileB64: String(p.ownerVerifFileB64 || ''),
      samarpanFileB64: String(p.samarpanFileB64 || ''),
      surveyFileB64: String(p.surveyFileB64 || ''),
      photoB64: String(p.photoB64 || ''),
      aadharFile: null,
      panFile: null,
      bankFile: null,
      ownerVerifFile: null,
      samarpanFile: null,
      surveyFile: null,
      photoFile: null,
    })
  }

  const saveEdit = async () => {
    if (!editing) return
    if (!editing.propertyId.trim() || !editing.ownerName.trim()) {
      toast.error('Property ID and Owner Name are required.')
      return
    }
    if (!editing.authPass || editing.authPass !== editing.originalPropertyId) {
      toast.error('Password must match current Owner Property ID to allow changes.')
      return
    }
    setSaving(true)
    try {
      const [
        aadharFileB64New,
        panFileB64New,
        bankFileB64New,
        ownerVerifFileB64New,
        samarpanFileB64New,
        surveyFileB64New,
        photoB64New,
      ] = await Promise.all([
        fileToBase64(editing.aadharFile),
        fileToBase64(editing.panFile),
        fileToBase64(editing.bankFile),
        fileToBase64(editing.ownerVerifFile),
        fileToBase64(editing.samarpanFile),
        fileToBase64(editing.surveyFile),
        fileToBase64(editing.photoFile),
      ])

      const payload = buildSurveyUpdatePayloadFromRow(editing.source, {
        propertyId: editing.propertyId,
        ownerName: editing.ownerName,
        district: editing.district,
        state: editing.state,
        acquisitionStage: editing.acquisitionStage,
        moneyDistributed: editing.moneyDistributed,
        areaSqft: editing.areaSqft,
        numberOfTrees: editing.numberOfTrees,
        totalDistribution: editing.totalDistribution,
        structureType: editing.structureType,
        noticeSent: editing.noticeSent,
        lat: editing.lat,
        lng: editing.lng,
        samarpanReceipt: editing.samarpanReceipt,
        fieldSurveyDone: editing.fieldSurveyDone,
        ownerVerification: editing.ownerVerification,
        aadharCollected: editing.aadharCollected,
        panCollected: editing.panCollected,
        bankDetailsCollected: editing.bankDetailsCollected,
        aadharFileB64: aadharFileB64New || editing.aadharFileB64 || '',
        panFileB64: panFileB64New || editing.panFileB64 || '',
        bankFileB64: bankFileB64New || editing.bankFileB64 || '',
        ownerVerifFileB64: ownerVerifFileB64New || editing.ownerVerifFileB64 || '',
        samarpanFileB64: samarpanFileB64New || editing.samarpanFileB64 || '',
        surveyFileB64: surveyFileB64New || editing.surveyFileB64 || '',
        photoB64: photoB64New || editing.photoB64 || '',
      })
      await updateSurveyData(payload)
      await refetch()
      setEditing(null)
      toast.success('Owner data updated successfully.')
    } catch (e) {
      toast.error(e?.message || 'Update failed')
    } finally {
      setSaving(false)
    }
  }

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      toast.error('Geolocation is not supported on this device.')
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setRefPoint({ lat: pos.coords.latitude, lng: pos.coords.longitude })
        toast.success('Distance reference set to your location.')
      },
      () => toast.error('Could not fetch current location.'),
      { enableHighAccuracy: true, timeout: 10000 },
    )
  }

  const clearDocField = (b64Key, fileKey) => {
    setEditing((prev) => ({
      ...prev,
      [b64Key]: '',
      [fileKey]: null,
    }))
    toast.success('Existing file removed from pending changes.')
  }

  return (
    <div className="owners-page">
      <div className="header">
        <h2>
          <i className="fas fa-users"></i> Owners Management
        </h2>
        <span className="sync-status" style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Link to="/dashboard" style={{ color: '#fff', textDecoration: 'none', fontWeight: 700 }}>
            <i className="fas fa-arrow-left"></i> Back to Dashboard
          </Link>
          <button
            type="button"
            className="btn-tool"
            style={{ color: '#fff', borderColor: 'rgba(255,255,255,0.5)' }}
            onClick={logout}
          >
            <i className="fas fa-sign-out-alt"></i> Log out
          </button>
        </span>
      </div>

      <div className="owners-toolbar">
        <input
          className="owners-search"
          type="text"
          placeholder="Search by Property ID or Owner Name..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={districtFilter} onChange={(e) => setDistrictFilter(e.target.value)}>
          {districts.map((d) => (
            <option key={d} value={d}>
              {d === 'All' ? 'All Districts' : d}
            </option>
          ))}
        </select>
        <select value={sortMode} onChange={(e) => setSortMode(e.target.value)}>
          <option value="distance">Sort: Distance (Nearest)</option>
          <option value="name">Sort: Owner Name</option>
          <option value="id">Sort: Property ID</option>
        </select>
        <button className="btn-tool" type="button" onClick={useMyLocation}>
          <i className="fas fa-crosshairs"></i> Use My Location
        </button>
      </div>

      <div className="owners-table-wrap">
        {loading ? (
          <div className="owners-placeholder">Loading owners data...</div>
        ) : error ? (
          <div className="owners-placeholder owners-error">Connection error: {error}</div>
        ) : rows.length === 0 ? (
          <div className="owners-placeholder">No owners found for current filters.</div>
        ) : (
          <table className="owners-table">
            <thead>
              <tr>
                <th>Property ID</th>
                <th>Owner Name</th>
                <th>District</th>
                <th>Distance (km)</th>
                <th>Area (Sqft)</th>
                <th>Stage</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const p = row.p || {}
                return (
                  <tr key={`${p.dbId}-${p.propertyId}`}>
                    <td>{p.propertyId || '-'}</td>
                    <td>{p.ownerName || '-'}</td>
                    <td>{p.district || '-'}</td>
                    <td>{row.distanceKm == null ? '-' : row.distanceKm.toFixed(2)}</td>
                    <td>{toNumber(p.areaSqft, 0).toLocaleString('en-IN')}</td>
                    <td>{p.acquisitionStage || '-'}</td>
                    <td>
                      <button className="btn-tool owners-edit-btn" type="button" onClick={() => startEdit(row)}>
                        <i className="fas fa-pen"></i> Edit
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {editing && (
        <div className="owners-modal-backdrop" role="presentation">
          <div className="owners-modal-card">
            <h3 style={{ marginTop: 0, color: '#0e3e49' }}>
              <i className="fas fa-user-edit"></i> Edit Owner
            </h3>
            <div className="owners-form-grid">
              <label style={{ gridColumn: '1 / -1' }}>
                Security Password (enter current Property ID)
                <input
                  type="password"
                  value={editing.authPass}
                  placeholder={`Current ID: ${editing.originalPropertyId}`}
                  onChange={(e) => setEditing((p) => ({ ...p, authPass: e.target.value }))}
                />
              </label>
              <label>
                Property ID
                <input
                  value={editing.propertyId}
                  onChange={(e) => setEditing((p) => ({ ...p, propertyId: e.target.value }))}
                />
              </label>
              <label>
                Owner Name
                <input
                  value={editing.ownerName}
                  onChange={(e) => setEditing((p) => ({ ...p, ownerName: e.target.value }))}
                />
              </label>
              <label>
                District
                <input
                  value={editing.district}
                  onChange={(e) => setEditing((p) => ({ ...p, district: e.target.value }))}
                />
              </label>
              <label>
                State
                <input
                  value={editing.state}
                  onChange={(e) => setEditing((p) => ({ ...p, state: e.target.value }))}
                />
              </label>
              <label>
                Stage
                <input
                  value={editing.acquisitionStage}
                  onChange={(e) => setEditing((p) => ({ ...p, acquisitionStage: e.target.value }))}
                />
              </label>
              <label>
                Notice Sent
                <select
                  value={editing.noticeSent}
                  onChange={(e) => setEditing((p) => ({ ...p, noticeSent: e.target.value }))}
                >
                  <option value="No">No</option>
                  <option value="Yes">Yes</option>
                </select>
              </label>
              <label>
                Structure Type
                <input
                  value={editing.structureType}
                  onChange={(e) => setEditing((p) => ({ ...p, structureType: e.target.value }))}
                />
              </label>
              <label>
                Compensation
                <input
                  type="number"
                  value={editing.moneyDistributed}
                  onChange={(e) => setEditing((p) => ({ ...p, moneyDistributed: toNumber(e.target.value, 0) }))}
                />
              </label>
              <label>
                Area Sqft
                <input
                  type="number"
                  value={editing.areaSqft}
                  onChange={(e) => setEditing((p) => ({ ...p, areaSqft: toNumber(e.target.value, 0) }))}
                />
              </label>
              <label>
                Trees
                <input
                  type="number"
                  value={editing.numberOfTrees}
                  onChange={(e) => setEditing((p) => ({ ...p, numberOfTrees: toNumber(e.target.value, 0) }))}
                />
              </label>
              <label>
                Total Distribution
                <input
                  type="number"
                  value={editing.totalDistribution}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, totalDistribution: toNumber(e.target.value, 0) }))
                  }
                />
              </label>
              <label>
                Latitude
                <input
                  type="number"
                  step="0.000001"
                  value={editing.lat}
                  onChange={(e) => setEditing((p) => ({ ...p, lat: toNumber(e.target.value, 0) }))}
                />
              </label>
              <label>
                Longitude
                <input
                  type="number"
                  step="0.000001"
                  value={editing.lng}
                  onChange={(e) => setEditing((p) => ({ ...p, lng: toNumber(e.target.value, 0) }))}
                />
              </label>
              <label>
                Samarpan Receipt
                <select
                  value={editing.samarpanReceipt}
                  onChange={(e) => setEditing((p) => ({ ...p, samarpanReceipt: toNumber(e.target.value, 0) }))}
                >
                  <option value={0}>No</option>
                  <option value={1}>Yes</option>
                </select>
              </label>
              <label>
                Field Survey Done
                <select
                  value={editing.fieldSurveyDone ? '1' : '0'}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, fieldSurveyDone: String(e.target.value) === '1' }))
                  }
                >
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </label>
              <label>
                Owner Verification
                <select
                  value={editing.ownerVerification ? '1' : '0'}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, ownerVerification: String(e.target.value) === '1' }))
                  }
                >
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </label>
              <label>
                Aadhar Collected
                <select
                  value={editing.aadharCollected ? '1' : '0'}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, aadharCollected: String(e.target.value) === '1' }))
                  }
                >
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </label>
              <label>
                PAN Collected
                <select
                  value={editing.panCollected ? '1' : '0'}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, panCollected: String(e.target.value) === '1' }))
                  }
                >
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </label>
              <label>
                Bank Details Collected
                <select
                  value={editing.bankDetailsCollected ? '1' : '0'}
                  onChange={(e) =>
                    setEditing((p) => ({ ...p, bankDetailsCollected: String(e.target.value) === '1' }))
                  }
                >
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </label>
              <DocumentFileManager
                editing={editing}
                onChangeFile={(fileKey, file) => setEditing((p) => ({ ...p, [fileKey]: file }))}
                onView={openDocument}
                onClear={clearDocField}
                hasStoredFileRef={hasStoredFileRef}
              />
            </div>
            <div className="owners-modal-actions">
              <button className="btn-tool" type="button" onClick={saveEdit} disabled={saving}>
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                className="btn-tool"
                type="button"
                style={{ background: '#6b7280' }}
                onClick={() => setEditing(null)}
                disabled={saving}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      <DocumentViewerOverlay
        isOpen={Boolean(viewingDocUrl)}
        url={viewingDocUrl}
        contentType={viewingDocType}
        onClose={closeDocument}
        cardStyle={{ width: 'min(1100px, 96vw)', height: '90vh', padding: 0 }}
      />
    </div>
  )
}

export default OwnersPage
