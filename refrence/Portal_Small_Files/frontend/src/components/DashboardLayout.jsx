import { useCallback, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import area from '@turf/area'
import ExcelJS from 'exceljs'
import AnalyticsPanel from './AnalyticsPanel'
import AssignShapeModal from './AssignShapeModal'
import DrawingModeBar from './DrawingModeBar'
import MapView from './MapView'
import Navbar from './Navbar'
import RecordPasswordConfirmModal from './RecordPasswordConfirmModal'
import ShapeAdminModal from './ShapeAdminModal'
import SurveyFormModal from './SurveyFormModal'
import Toolbar from './Toolbar'
import { buildDocumentUrl, hasDocument } from '../utils/documentUrls'
import { encodeSurveyFileInputs } from '../utils/fileEncoding'
import * as surveyApi from '../services/surveyApi'
import useSurveys from '../hooks/useSurveys'
import { embedPlotMetaInGeoJson } from '../utils/geoJsonPlotMeta'
import { plotForPasswordFromDbId, plotForPasswordFromPropertyId } from '../utils/recordPassword'

const extractPolygons = (node) => {
  let extracted = []
  if (!node) return extracted
  const isClosedRing = (coords) => {
    if (!Array.isArray(coords) || coords.length < 4) return false
    const first = coords[0]
    const last = coords[coords.length - 1]
    if (!Array.isArray(first) || !Array.isArray(last)) return false
    if (first.length < 2 || last.length < 2) return false
    return first[0] === last[0] && first[1] === last[1]
  }

  if (
    node.type === 'Feature' &&
    node.geometry &&
    (node.geometry.type === 'Polygon' || node.geometry.type === 'MultiPolygon')
  ) {
    extracted.push(node)
  } else if (node.type === 'Polygon' || node.type === 'MultiPolygon') {
    extracted.push({
      type: 'Feature',
      properties: {},
      geometry: node,
    })
  } else if (node.type === 'Feature' && node.geometry && node.geometry.type === 'LineString') {
    const coords = node.geometry.coordinates
    if (isClosedRing(coords)) {
      extracted.push({
        type: 'Feature',
        properties: node.properties || {},
        geometry: {
          type: 'Polygon',
          coordinates: [coords],
        },
      })
    }
  } else if (node.type === 'Feature' && node.geometry && node.geometry.type === 'MultiLineString') {
    const lines = node.geometry.coordinates || []
    lines.forEach((line) => {
      if (isClosedRing(line)) {
        extracted.push({
          type: 'Feature',
          properties: node.properties || {},
          geometry: {
            type: 'Polygon',
            coordinates: [line],
          },
        })
      }
    })
  } else if (node.type === 'LineString') {
    const coords = node.coordinates
    if (isClosedRing(coords)) {
      extracted.push({
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'Polygon',
          coordinates: [coords],
        },
      })
    }
  } else if (node.type === 'MultiLineString') {
    const lines = node.coordinates || []
    lines.forEach((line) => {
      if (isClosedRing(line)) {
        extracted.push({
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'Polygon',
            coordinates: [line],
          },
        })
      }
    })
  } else if (Array.isArray(node)) {
    node.forEach((child) => {
      extracted.push(...extractPolygons(child))
    })
  } else if (typeof node === 'object') {
    Object.values(node).forEach((val) => {
      if (typeof val === 'object') {
        extracted.push(...extractPolygons(val))
      }
    })
  }
  return extracted
}

const isLikelyProjectedXY = (coords) => {
  if (!Array.isArray(coords)) return false
  const first = Array.isArray(coords[0]) ? coords[0] : null
  if (!first || first.length < 2) return false
  const x = Number(first[0])
  const y = Number(first[1])
  return Number.isFinite(x) && Number.isFinite(y) && (Math.abs(x) > 180 || Math.abs(y) > 90)
}

const utmToLngLat = (easting, northing, zoneNumber = 44, northernHemisphere = true) => {
  const a = 6378137.0
  const eccSquared = 0.00669438
  const k0 = 0.9996

  const x = easting - 500000.0
  let y = northing
  if (!northernHemisphere) {
    y -= 10000000.0
  }

  const eccPrimeSquared = eccSquared / (1 - eccSquared)
  const m = y / k0
  const mu =
    m /
    (a *
      (1 -
        eccSquared / 4 -
        (3 * eccSquared * eccSquared) / 64 -
        (5 * eccSquared * eccSquared * eccSquared) / 256))

  const e1 = (1 - Math.sqrt(1 - eccSquared)) / (1 + Math.sqrt(1 - eccSquared))
  const j1 = (3 * e1) / 2 - (27 * e1 * e1 * e1) / 32
  const j2 = (21 * e1 * e1) / 16 - (55 * e1 * e1 * e1 * e1) / 32
  const j3 = (151 * e1 * e1 * e1) / 96
  const j4 = (1097 * e1 * e1 * e1 * e1) / 512
  const fp =
    mu +
    j1 * Math.sin(2 * mu) +
    j2 * Math.sin(4 * mu) +
    j3 * Math.sin(6 * mu) +
    j4 * Math.sin(8 * mu)

  const sinFp = Math.sin(fp)
  const cosFp = Math.cos(fp)
  const tanFp = Math.tan(fp)
  const c1 = eccPrimeSquared * cosFp * cosFp
  const t1 = tanFp * tanFp
  const n1 = a / Math.sqrt(1 - eccSquared * sinFp * sinFp)
  const r1 = (a * (1 - eccSquared)) / Math.pow(1 - eccSquared * sinFp * sinFp, 1.5)
  const d = x / (n1 * k0)

  const lat =
    fp -
    ((n1 * tanFp) / r1) *
      (d * d / 2 -
        ((5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * eccPrimeSquared) * Math.pow(d, 4)) / 24 +
        ((61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * eccPrimeSquared - 3 * c1 * c1) *
          Math.pow(d, 6)) /
          720)

  const lonOrigin = (zoneNumber - 1) * 6 - 180 + 3
  const lon =
    ((d -
      ((1 + 2 * t1 + c1) * Math.pow(d, 3)) / 6 +
      ((5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * eccPrimeSquared + 24 * t1 * t1) *
        Math.pow(d, 5)) /
        120) /
      cosFp) *
      (180 / Math.PI) +
    lonOrigin

  return [lon, (lat * 180) / Math.PI]
}

const mercatorToLngLat = ([x, y]) => {
  const lng = (x / 20037508.34) * 180
  const lat =
    (180 / Math.PI) *
    (2 * Math.atan(Math.exp(((y / 20037508.34) * Math.PI))) - Math.PI / 2)
  return [lng, lat]
}

const normalizeGeometryToLngLat = (geometry) => {
  if (!geometry || !geometry.type || !geometry.coordinates) return geometry

  const convertPoint = (pt) => {
    const x = Number(pt?.[0])
    const y = Number(pt?.[1])
    if (!Number.isFinite(x) || !Number.isFinite(y)) return pt

    // UTM-like CAD coordinates (common from survey exports in India)
    if (x >= 100000 && x <= 900000 && y >= 0 && y <= 10000000) {
      return utmToLngLat(x, y, 44, true)
    }

    // Fallback: Web Mercator meters
    return mercatorToLngLat([x, y])
  }

  const convertRing = (ring) => ring.map((pt) => convertPoint(pt))
  const convertPolygon = (poly) => poly.map((ring) => convertRing(ring))

  if (geometry.type === 'Polygon' && isLikelyProjectedXY(geometry.coordinates[0])) {
    return { ...geometry, coordinates: convertPolygon(geometry.coordinates) }
  }
  if (geometry.type === 'MultiPolygon' && isLikelyProjectedXY(geometry.coordinates?.[0]?.[0])) {
    return {
      ...geometry,
      coordinates: geometry.coordinates.map((poly) => convertPolygon(poly)),
    }
  }
  return geometry
}

const calculateAreaSqftFromShape = (shape) => {
  try {
    let geo = shape?.geoJson ?? shape?.geojson ?? null
    if (typeof geo === 'string') {
      geo = JSON.parse(geo)
    }
    if (!geo || !geo.type) return 0
    const feature = geo.type === 'Feature' ? geo : { type: 'Feature', properties: {}, geometry: geo }
    const sqm = area(feature)
    if (!Number.isFinite(sqm) || sqm <= 0) return 0
    return Number((sqm * 10.7639).toFixed(2))
  } catch {
    return 0
  }
}

function DashboardLayout() {
  const {
    surveys,
    shapes,
    loading,
    error,
    refetch,
    saveSurveyData,
    saveShapesForProperty,
    updateShapeAssignment,
    updateSurveyData,
    updateShapeData,
    deleteShapeById,
    deleteSurveyById,
  } = useSurveys()
  const [editingShape, setEditingShape] = useState(null)
  /** @type {null | { variant: 'delete'|'editOpen'|'surveySave', plot: object, formData?: object }} */
  const [passwordGate, setPasswordGate] = useState(null)
  const surveySaveBypassPasswordRef = useRef(false)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isDrawingMode, setIsDrawingMode] = useState(false)
  const [isAddMode, setIsAddMode] = useState(false)
  const [activeFormData, setActiveFormData] = useState(null)
  const [activeDrawType, setActiveDrawType] = useState('')
  const [capturedShapes, setCapturedShapes] = useState([])
  const [selectedUnassignedShape, setSelectedUnassignedShape] = useState(null)
  const [isAssignModalOpen, setIsAssignModalOpen] = useState(false)
  const [lastImportedPropertyIds, setLastImportedPropertyIds] = useState([])
  const [focusImportedNonce, setFocusImportedNonce] = useState(0)
  const [focusAssignedPropertyId, setFocusAssignedPropertyId] = useState('')
  const [focusAssignedNonce, setFocusAssignedNonce] = useState(0)
  const [showUnassignedShapes, setShowUnassignedShapes] = useState(true)
  const [showImportedShapes, setShowImportedShapes] = useState(true)
  const [isMeasureMode, setIsMeasureMode] = useState(false)
  const [measureUnit, setMeasureUnit] = useState('sqft')
  const [measureResetNonce, setMeasureResetNonce] = useState(0)
  const [mapResetNonce, setMapResetNonce] = useState(0)

  const pendingImportedIds = useMemo(
    () =>
      (Array.isArray(shapes) ? shapes : [])
        .map((s) => String(s?.propertyId || ''))
        .filter((id) => id.startsWith('PENDING_')),
    [shapes],
  )

  const effectiveHighlightedIds = useMemo(() => {
    if (lastImportedPropertyIds.length === 0) return pendingImportedIds
    const pendingSet = new Set(pendingImportedIds)
    const filtered = lastImportedPropertyIds.filter((id) => pendingSet.has(id))
    return filtered.length > 0 ? filtered : pendingImportedIds
  }, [lastImportedPropertyIds, pendingImportedIds])

  const statusConfig = useMemo(() => {
    if (loading) return { statusText: 'Loading Data...', statusType: 'loading' }
    if (error) return { statusText: 'Connection Error', statusType: 'error' }
    return { statusText: 'System Online', statusType: 'online' }
  }, [loading, error])

  /** Always read the shape row from current `shapes` by DB id so propertyId never sticks to a stale/new-plot draft. */
  const resolveShapeRow = useCallback(
    (shape) => {
      if (!shape) return null
      const sid = Number(shape.id)
      const list = Array.isArray(shapes) ? shapes : []
      if (Number.isFinite(sid) && sid > 0) {
        const fresh = list.find((s) => Number(s.id) === sid)
        if (fresh) return { ...fresh }
      }
      return { ...shape }
    },
    [shapes],
  )

  const handleShapeAdminEdit = useCallback(
    (shape) => {
      setEditingShape(resolveShapeRow(shape))
    },
    [resolveShapeRow],
  )

  const handleCloseSurveyModal = () => {
    setIsModalOpen(false)
    setCapturedShapes([])
    setActiveFormData(null)
    setIsDrawingMode(false)
    setActiveDrawType('')
    setMapResetNonce((prev) => prev + 1)
  }

  const handleToggleAddMode = () => {
    setIsAddMode((prev) => !prev)
  }

  const handleMapClickForSurvey = (latlng) => {
    setIsAddMode(false)
    setActiveFormData((prev) => ({
      ...(prev || {}),
      lat: latlng.lat,
      lng: latlng.lng,
    }))
    setIsModalOpen(true)
  }

  const applyOpenEditSurvey = (propertyData) => {
    const propertyId = String(propertyData?.propertyId || '').trim()
    const existingShapesForProperty = (Array.isArray(shapes) ? shapes : []).filter(
      (s) => String(s?.propertyId || '').trim() === propertyId,
    )
    const structureSetFromShapes = new Set(
      existingShapesForProperty
        .map((s) => String(s?.structureType || '').trim())
        .filter(Boolean),
    )
    const structureFromSurvey = String(propertyData?.structureType || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const structureTypes = Array.from(new Set([...structureFromSurvey, ...structureSetFromShapes]))
    const originalShapes = existingShapesForProperty.map((s) => ({
      id: Number(s.id),
      structureType: String(s?.structureType || '').trim(),
    }))

    setIsAddMode(false)
    setCapturedShapes([])
    setActiveFormData({
      ...propertyData,
      structureTypes,
      lat: propertyData?.lat,
      lng: propertyData?.lng,
      originalStructureTypes: structureTypes,
      originalShapes,
      structureUpdateMode: 'mark',
    })
    setIsModalOpen(true)
  }

  const handleEditSurvey = (propertyData) => {
    const canonical =
      propertyData?.dbId != null ? plotForPasswordFromDbId(surveys, propertyData.dbId) : null
    const plot =
      canonical && (String(canonical.propertyId || '').trim() || String(canonical.ownerName || '').trim())
        ? canonical
        : {
            propertyId: propertyData?.propertyId,
            ownerName: propertyData?.ownerName,
          }
    if (!String(plot?.propertyId || '').trim() && !String(plot?.ownerName || '').trim()) {
      toast.error('Cannot verify this plot. Wait for data to load or refresh the page.')
      return
    }
    setPasswordGate({
      variant: 'editOpen',
      plot,
      propertyData,
    })
  }

  const handleStartDrawing = (formData) => {
    const selectedStructureTypes = Array.isArray(formData?.structureTypes)
      ? formData.structureTypes.filter(Boolean)
      : []
    if (selectedStructureTypes.length === 0) {
      toast.error('Please select at least one structure before marking boundary.')
      return
    }
    setActiveFormData(formData)
    setIsModalOpen(false)
    setIsDrawingMode(true)
  }

  const handleDoneDrawing = () => {
    const totalArea = capturedShapes.reduce((sum, shape) => sum + (shape.areaSqft || 0), 0)
    setActiveFormData((prev) => ({ ...(prev || {}), areaSqft: totalArea.toFixed(2) }))
    setIsDrawingMode(false)
    setIsModalOpen(true)
  }

  const handleSubmitSurvey = async (formData) => {
    if (formData?.dbId && !surveySaveBypassPasswordRef.current) {
      const canonical = plotForPasswordFromDbId(surveys, formData.dbId)
      if (
        !canonical ||
        (!String(canonical.propertyId || '').trim() && !String(canonical.ownerName || '').trim())
      ) {
        toast.error('Cannot verify this survey. Wait for data to load or refresh the page.')
        return
      }
      setPasswordGate({
        variant: 'surveySave',
        plot: canonical,
        formData,
      })
      return
    }
    surveySaveBypassPasswordRef.current = false

    const totalArea = capturedShapes.reduce((sum, shape) => sum + (shape.areaSqft || 0), 0)
    const {
      aadharFileB64,
      panFileB64,
      bankFileB64,
      ownerVerifFileB64,
      samarpanFileB64,
      surveyFileB64,
      photoB64,
    } = await encodeSurveyFileInputs(formData)

    const lat = Number(formData.lat || 20.5937)
    const lng = Number(formData.lng || 78.9629)
    const propertyId = String(formData?.propertyId || '').trim()
    const existingShapesForProperty = (Array.isArray(shapes) ? shapes : []).filter(
      (s) => String(s?.propertyId || '').trim() === propertyId,
    )
    const originalShapes = Array.isArray(formData?.originalShapes) ? formData.originalShapes : []
    const hasMarkedShapes = capturedShapes.length > 0 || existingShapesForProperty.length > 0
    const selectedStructureTypes = Array.isArray(formData.structureTypes)
      ? formData.structureTypes.filter(Boolean)
      : []
    const originalStructureTypes = Array.isArray(formData.originalStructureTypes)
      ? formData.originalStructureTypes.filter(Boolean)
      : []
    const sortJoin = (arr) => [...arr].sort().join('|')
    const structureChanged = sortJoin(selectedStructureTypes) !== sortJoin(originalStructureTypes)
    const updateMode = formData.structureUpdateMode || 'mark'
    const removedTypes = originalStructureTypes.filter((t) => !selectedStructureTypes.includes(t))
    const addedTypes = selectedStructureTypes.filter((t) => !originalStructureTypes.includes(t))
    const deletingOnly = formData?.dbId && removedTypes.length > 0 && addedTypes.length === 0

    const shouldBulkReclassify =
      Boolean(formData?.dbId) &&
      capturedShapes.length === 0 &&
      existingShapesForProperty.length > 0 &&
      selectedStructureTypes.length === 1 &&
      structureChanged &&
      !deletingOnly &&
      (updateMode === 'replace_existing' || existingShapesForProperty.length === 1)

    if (!formData?.dbId && selectedStructureTypes.length === 0) {
      toast.error('Please select structure classification first.')
      return false
    }
    // New survey: boundary marking required before save.
    if (!formData?.dbId && capturedShapes.length === 0) {
      toast.error('Please mark boundary in Add Area Boundary before saving.')
      return false
    }

    // Restriction (edit mode): structure change requires new capture, unless replacing in place
    // (single polygon + one new type, or "Replace existing marked shapes" with one type).
    if (
      formData?.dbId &&
      structureChanged &&
      !deletingOnly &&
      capturedShapes.length === 0 &&
      !shouldBulkReclassify
    ) {
      toast.error(
        'Structure changed. Mark a new boundary, or select exactly one structure type and use “Replace existing marked shapes”, or with only one polygon pick only the new type to reclassify it.',
      )
      return false
    }
    if (formData?.dbId && updateMode === 'replace_existing' && selectedStructureTypes.length !== 1) {
      toast.error('For replace mode, select exactly one structure type.')
      return false
    }
    // Restriction: no shape => no structure classification can be saved.
    const finalStructureType = hasMarkedShapes ? selectedStructureTypes.join(', ') : ''

    const payload = {
      ...formData,
      structureType: finalStructureType,
      areaSqft: totalArea > 0 ? totalArea : Number(formData.areaSqft || 0),
      coordinates: { lat, lng },
      lat,
      lng,
      state: formData.state || 'Dashboard',
      district: formData.district || 'Dashboard',
      samarpanReceipt: formData.samarpanReceipt ? 1 : 0,
      aadharFileB64: aadharFileB64 || formData.aadharFileB64 || '',
      panFileB64: panFileB64 || formData.panFileB64 || '',
      bankFileB64: bankFileB64 || formData.bankFileB64 || '',
      ownerVerifFileB64: ownerVerifFileB64 || formData.ownerVerifFileB64 || '',
      samarpanFileB64: samarpanFileB64 || formData.samarpanFileB64 || '',
      surveyFileB64: surveyFileB64 || formData.surveyFileB64 || '',
      photoB64: photoB64 || formData.photoB64 || '',
    }

    try {
      // If dbId exists, this is an edit of an existing owner record: update same row.
      // Otherwise create a new survey row.
      if (formData?.dbId) {
        await updateSurveyData({
          ...payload,
          id: Number(formData.dbId),
        })
        await saveShapesForProperty(propertyId, capturedShapes)

        // Build remove list from original edit snapshot to make edit state
        // a true reflection of user checkbox changes.
        const selectedSet = new Set(selectedStructureTypes.map((t) => String(t).trim()))
        const removedTypeSet = new Set(
          originalShapes
            .map((s) => String(s?.structureType || '').trim())
            .filter((t) => t && !selectedSet.has(t)),
        )
        if (removedTypeSet.size > 0 && !shouldBulkReclassify) {
          const targetIds = existingShapesForProperty
            .filter((s) => removedTypeSet.has(String(s?.structureType || '').trim()))
            .map((s) => Number(s.id))
            .filter((id) => Number.isFinite(id) && id > 0)
          for (const sid of targetIds) {
            await deleteShapeById(sid)
          }
          if (targetIds.length > 0) {
            toast.success(`Removed ${targetIds.length} deselected shape(s).`)
          }
        }

        if (shouldBulkReclassify) {
          const newType = selectedStructureTypes[0]
          const needsUpdate = existingShapesForProperty.some(
            (s) => String(s?.structureType || '') !== String(newType),
          )
          if (needsUpdate) {
            for (const s of existingShapesForProperty) {
              await updateShapeData({
                id: Number(s.id),
                propertyId,
                structureType: newType,
              })
            }
            toast.success(`Updated ${existingShapesForProperty.length} marked shape(s) to "${newType}".`)
          }
        }
      } else {
        await saveSurveyData(payload, capturedShapes)
      }
      handleCloseSurveyModal()
      await refetch()
      return true
    } catch (err) {
      toast.error(err?.message || 'Save failed')
      throw err
    }
  }

  const handleDeleteSurveyRecord = (plot) => {
    const id = Number(plot?.dbId)
    if (!Number.isFinite(id) || id <= 0) return
    const canonical = plotForPasswordFromDbId(surveys, plot.dbId)
    if (
      !canonical ||
      (!String(canonical.propertyId || '').trim() && !String(canonical.ownerName || '').trim())
    ) {
      toast.error('Cannot verify this plot for delete. Wait for data to load or refresh the page.')
      return
    }
    setPasswordGate({
      variant: 'delete',
      plot: canonical,
      deletePlot: plot,
    })
  }

  const executeDeleteSurveyAfterConfirmation = async (plot) => {
    const id = Number(plot?.dbId)
    if (!Number.isFinite(id) || id <= 0) {
      toast.error('Invalid survey.')
      throw new Error('Invalid survey')
    }
    try {
      await deleteSurveyById(id)
      toast.success('Survey and boundaries removed.')
      await refetch()
    } catch (err) {
      toast.error(err?.message || 'Delete failed')
      throw err
    }
  }

  const handlePasswordConfirmed = async (gate) => {
    if (!gate?.variant) return true
    switch (gate.variant) {
      case 'delete':
        await executeDeleteSurveyAfterConfirmation(gate.deletePlot)
        return true
      case 'editOpen':
        applyOpenEditSurvey(gate.propertyData)
        return true
      case 'surveySave': {
        surveySaveBypassPasswordRef.current = true
        try {
          const ok = await handleSubmitSurvey(gate.formData)
          if (ok === false) {
            surveySaveBypassPasswordRef.current = false
            return false
          }
        } catch (e) {
          surveySaveBypassPasswordRef.current = false
          throw e
        }
        return true
      }
      default:
        return true
    }
  }

  const handleImportGeoJson = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = async (e) => {
      try {
        const jsonData = JSON.parse(e.target.result)
        console.log('Raw Imported Data:', jsonData)

        const validFeatures = extractPolygons(jsonData)

        if (validFeatures.length === 0) {
          toast.error(
            'Could not find any Polygon data in this file. Please check the console log to verify the file structure.',
          )
          return
        }

        const importedIds = []
        const alreadyExistingIds = []
        const existingGeoMap = new Map()
        ;(Array.isArray(shapes) ? shapes : []).forEach((s) => {
          try {
            const g = typeof s?.geoJson === 'string' ? JSON.parse(s.geoJson) : s?.geoJson
            const key = JSON.stringify(g || {})
            if (key) {
              existingGeoMap.set(key, s?.propertyId || '')
            }
          } catch {
            // ignore malformed stored geometry
          }
        })

        for (const feature of validFeatures) {
          const normalizedGeometry = normalizeGeometryToLngLat(feature.geometry)
          const geometryKey = JSON.stringify(normalizedGeometry || {})
          const existingId = existingGeoMap.get(geometryKey)
          if (existingId) {
            if (String(existingId).startsWith('PENDING_')) {
              alreadyExistingIds.push(existingId)
            }
            continue
          }

          const propertyId = `PENDING_${Date.now()}_${Math.floor(Math.random() * 10000)}`
          const shapePayload = {
            propertyId,
            structureType: 'Unassigned',
            geoJson: embedPlotMetaInGeoJson(normalizedGeometry, propertyId, 'Unassigned'),
            calculatedArea: 0,
          }

          await surveyApi.saveShape(shapePayload)

          existingGeoMap.set(geometryKey, propertyId)
          importedIds.push(propertyId)
        }

        const mergedIds = [...new Set([...importedIds, ...alreadyExistingIds])]
        setLastImportedPropertyIds(mergedIds)
        setShowImportedShapes(true)
        setShowUnassignedShapes(true)
        setFocusImportedNonce((v) => v + 1)

        if (importedIds.length === 0 && alreadyExistingIds.length > 0) {
          toast(
            `No new shapes added. ${alreadyExistingIds.length} matching shapes are already imported as Unassigned and shown on map.`,
          )
        } else {
          toast.success(
            `Successfully imported ${importedIds.length} new shape(s). Already existing: ${alreadyExistingIds.length}.`,
          )
        }
        if (typeof refetch === 'function') refetch()
      } catch (error) {
        console.error('JSON Parse Error:', error)
        toast.error("Invalid JSON file format. Make sure it's a valid JSON or GeoJSON file.")
      }
    }
    reader.readAsText(file)
    event.target.value = ''
  }

  const handleAssignShapeClick = useCallback(
    (shape) => {
      setSelectedUnassignedShape(resolveShapeRow(shape))
      setIsAssignModalOpen(true)
    },
    [resolveShapeRow],
  )

  const handleAssignShape = async (newPropertyId, newStructureType) => {
    if (!selectedUnassignedShape) return
    try {
      const calculatedArea = calculateAreaSqftFromShape(selectedUnassignedShape)
      await updateShapeAssignment(
        selectedUnassignedShape.propertyId,
        newPropertyId,
        newStructureType,
        calculatedArea,
      )
      setIsAssignModalOpen(false)
      setSelectedUnassignedShape(null)
      await refetch()
      setFocusAssignedPropertyId(newPropertyId)
      setFocusAssignedNonce((v) => v + 1)
      toast.success(`Shape assigned to ${newPropertyId} and refreshed.`)
    } catch (err) {
      toast.error(err?.message || 'Shape assignment failed')
    }
  }

  const downloadStyledWorkbook = async (
    filenamePrefix,
    worksheetName,
    columns,
    rows,
    { headerFill = '0E3E49', headerFont = 'FFFFFF', stripeFill = 'F8FBFF' } = {},
  ) => {
    const workbook = new ExcelJS.Workbook()
    workbook.creator = 'Droid Mining Solutions'
    workbook.created = new Date()

    const ws = workbook.addWorksheet(worksheetName)
    ws.columns = columns.map((col) => ({
      header: col.label,
      key: col.key,
      width: Math.max(14, Math.min(42, String(col.label || '').length + 6)),
    }))

    rows.forEach((rowValues) => {
      const rowObj = {}
      columns.forEach((col, idx) => {
        rowObj[col.key] = rowValues[idx]
      })
      ws.addRow(rowObj)
    })

    ws.views = [{ state: 'frozen', ySplit: 1 }]
    const headerRow = ws.getRow(1)
    headerRow.height = 22
    headerRow.eachCell((cell) => {
      cell.font = { name: 'Calibri', size: 11, bold: true, color: { argb: headerFont } }
      cell.fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: headerFill },
      }
      cell.alignment = { vertical: 'middle', horizontal: 'center', wrapText: true }
      cell.border = {
        top: { style: 'thin', color: { argb: 'D9E2EC' } },
        left: { style: 'thin', color: { argb: 'D9E2EC' } },
        bottom: { style: 'thin', color: { argb: 'D9E2EC' } },
        right: { style: 'thin', color: { argb: 'D9E2EC' } },
      }
    })

    ws.eachRow((row, rowNumber) => {
      if (rowNumber === 1) return
      row.height = 20
      row.eachCell((cell, colNumber) => {
        cell.font = { name: 'Calibri', size: 10.5, color: { argb: '1F2937' } }
        cell.alignment = {
          vertical: 'middle',
          horizontal: colNumber === 1 ? 'left' : 'center',
          wrapText: true,
        }
        cell.border = {
          top: { style: 'thin', color: { argb: 'E5E7EB' } },
          left: { style: 'thin', color: { argb: 'E5E7EB' } },
          bottom: { style: 'thin', color: { argb: 'E5E7EB' } },
          right: { style: 'thin', color: { argb: 'E5E7EB' } },
        }
        if (rowNumber % 2 === 0) {
          cell.fill = {
            type: 'pattern',
            pattern: 'solid',
            fgColor: { argb: stripeFill },
          }
        }
      })
    })

    ws.columns.forEach((col) => {
      let maxLen = String(col.header ?? '').length
      col.eachCell?.({ includeEmpty: true }, (cell) => {
        const val = cell.value == null ? '' : String(cell.value)
        maxLen = Math.max(maxLen, val.length)
      })
      col.width = Math.max(14, Math.min(50, maxLen + 3))
    })

    const buffer = await workbook.xlsx.writeBuffer()
    const blob = new Blob([buffer], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const url = URL.createObjectURL(blob)
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}`
    const link = document.createElement('a')
    link.href = url
    link.download = `${filenamePrefix}-${stamp}.xlsx`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  const handleExportDetailedCsv = () => {
    const features = Array.isArray(surveys?.features) ? surveys.features : []
    if (features.length === 0) {
      toast.error('No survey data available to export.')
      return
    }

    const columns = [
      { key: 'propertyId', label: 'Property ID' },
      { key: 'ownerName', label: 'Owner Name' },
      { key: 'structureType', label: 'Structure Type' },
      { key: 'acquisitionStage', label: 'Acquisition Stage' },
      { key: 'noticeSent', label: 'Notice Sent' },
      { key: 'moneyDistributed', label: 'Compensation Distributed' },
      { key: 'areaSqft', label: 'Area Sqft' },
      { key: 'numberOfTrees', label: 'Number of Trees' },
      { key: 'totalDistribution', label: 'Total Distribution' },
      { key: 'samarpanReceipt', label: 'Samarpan Receipt' },
      { key: 'fieldSurveyDone', label: 'Field Survey Done' },
      { key: 'ownerVerification', label: 'Owner Verification' },
      { key: 'aadharCollected', label: 'Aadhar Collected' },
      { key: 'panCollected', label: 'PAN Collected' },
      { key: 'bankDetailsCollected', label: 'Bank Details Collected' },
      { key: 'district', label: 'District' },
      { key: 'state', label: 'State' },
      { key: 'lat', label: 'Latitude' },
      { key: 'lng', label: 'Longitude' },
      { key: 'aadharLink', label: 'Aadhar Link' },
      { key: 'panLink', label: 'PAN Link' },
      { key: 'bankLink', label: 'Bank Link' },
      { key: 'ownerVerificationLink', label: 'Owner Verification Link' },
      { key: 'samarpanLink', label: 'Samarpan Link' },
      { key: 'surveyLink', label: 'Field Survey Link' },
      { key: 'photoLink', label: 'Photo Link' },
    ]

    const rows = features.map((feature) => {
      const p = feature?.properties || {}
      const coords = feature?.geometry?.coordinates || []
      const lat = Number(coords?.[1])
      const lng = Number(coords?.[0])
      const propertyId = String(p.propertyId || '').trim()
      const makeDocUrl = (docType, enabled) => (enabled ? buildDocumentUrl(propertyId, docType) : '')

      return columns.map(({ key }) => {
        if (key === 'lat') return Number.isFinite(lat) ? lat : ''
        if (key === 'lng') return Number.isFinite(lng) ? lng : ''
        if (key === 'aadharLink')
          return makeDocUrl('aadhar', hasDocument(p, { boolKey: 'aadharCollected', b64Key: 'aadharFileB64' }))
        if (key === 'panLink')
          return makeDocUrl('pan', hasDocument(p, { boolKey: 'panCollected', b64Key: 'panFileB64' }))
        if (key === 'bankLink')
          return makeDocUrl('bank', hasDocument(p, { boolKey: 'bankDetailsCollected', b64Key: 'bankFileB64' }))
        if (key === 'ownerVerificationLink') {
          return makeDocUrl(
            'owner_verification',
            hasDocument(p, { boolKey: 'ownerVerification', b64Key: 'ownerVerifFileB64' }),
          )
        }
        if (key === 'samarpanLink')
          return makeDocUrl('samarpan', hasDocument(p, { boolKey: 'samarpanReceipt', b64Key: 'samarpanFileB64' }))
        if (key === 'surveyLink')
          return makeDocUrl('survey', hasDocument(p, { boolKey: 'fieldSurveyDone', b64Key: 'surveyFileB64' }))
        if (key === 'photoLink') return makeDocUrl('photo', hasDocument(p, { b64Key: 'photoB64' }))
        return p[key] ?? ''
      })
    })

    downloadStyledWorkbook('dashboard-surveys', 'Detailed Surveys', columns, rows, {
      headerFill: '0E3E49',
      headerFont: 'FFFFFF',
      stripeFill: 'F4FBFF',
    })
      .then(() => toast.success(`Styled export ready: ${rows.length} survey record(s).`))
      .catch(() => toast.error('Export failed. Please try again.'))
  }

  const handleExportSummaryCsv = () => {
    const features = Array.isArray(surveys?.features) ? surveys.features : []
    if (features.length === 0) {
      toast.error('No survey data available to export.')
      return
    }

    const propBool = (v) => v === true || v === 1 || v === '1'
    const propNum = (v) => {
      const n = Number(v)
      return Number.isFinite(n) ? n : 0
    }

    const normalizeStage = (p = {}) => {
      const stage = p.acquisitionStage || p.legalStatus || ''
      if (stage === 'Legal') return 'Notice 37(2) Distribution'
      if (stage === 'Illegal / Encroached') return 'Dispute'
      return stage || 'Unknown'
    }

    const summaryRows = []
    const push = (section, metric, value) => summaryRows.push([section, metric, value])

    const totalArea = features.reduce((sum, f) => sum + propNum(f?.properties?.areaSqft), 0)
    const totalComp = features.reduce((sum, f) => sum + propNum(f?.properties?.moneyDistributed), 0)
    const disputes = features.reduce(
      (count, f) => count + (normalizeStage(f?.properties || {}) === 'Dispute' ? 1 : 0),
      0,
    )
    const totalTrees = features.reduce((sum, f) => sum + propNum(f?.properties?.numberOfTrees), 0)
    const totalDistribution = features.reduce((sum, f) => sum + propNum(f?.properties?.totalDistribution), 0)
    const samarpanSum = features.reduce((sum, f) => sum + propNum(f?.properties?.samarpanReceipt), 0)
    const ownerVerified = features.reduce(
      (count, f) => count + (propBool(f?.properties?.ownerVerification) ? 1 : 0),
      0,
    )
    const fieldSurveyDone = features.reduce(
      (count, f) => count + (propBool(f?.properties?.fieldSurveyDone) ? 1 : 0),
      0,
    )
    const aadharCollected = features.reduce(
      (count, f) => count + (propBool(f?.properties?.aadharCollected) ? 1 : 0),
      0,
    )
    const panCollected = features.reduce(
      (count, f) => count + (propBool(f?.properties?.panCollected) ? 1 : 0),
      0,
    )
    const bankCollected = features.reduce(
      (count, f) => count + (propBool(f?.properties?.bankDetailsCollected) ? 1 : 0),
      0,
    )

    push('Overall', 'Total Surveys', features.length)
    push('Overall', 'Total Area Sqft', Number(totalArea.toFixed(2)))
    push('Overall', 'Total Compensation', Number(totalComp.toFixed(2)))
    push('Overall', 'Disputes', disputes)

    push('Field & Document KPIs', 'Total Trees', totalTrees)
    push('Field & Document KPIs', 'Total Distribution', totalDistribution)
    push('Field & Document KPIs', 'Samarpan Receipts (Sum)', samarpanSum)
    push('Field & Document KPIs', 'Owner Verified', ownerVerified)
    push('Field & Document KPIs', 'Field Survey Done', fieldSurveyDone)
    push('Field & Document KPIs', 'Aadhar Collected', aadharCollected)
    push('Field & Document KPIs', 'PAN Collected', panCollected)
    push('Field & Document KPIs', 'Bank Details Collected', bankCollected)

    const stageCounts = {}
    const structureCounts = {}
    features.forEach((f) => {
      const p = f?.properties || {}
      const stage = normalizeStage(p)
      stageCounts[stage] = (stageCounts[stage] || 0) + 1
      String(p.structureType || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .forEach((s) => {
          structureCounts[s] = (structureCounts[s] || 0) + 1
        })
    })

    Object.entries(stageCounts).forEach(([stage, count]) =>
      push('Acquisition Pipeline', stage, count),
    )
    Object.entries(structureCounts).forEach(([type, count]) =>
      push('Structure Classification', type, count),
    )

    const columns = [
      { key: 'section', label: 'Section' },
      { key: 'metric', label: 'Metric' },
      { key: 'value', label: 'Value' },
    ]
    downloadStyledWorkbook('dashboard-summary', 'Summary', columns, summaryRows, {
      headerFill: '7A4E00',
      headerFont: 'FFFFFF',
      stripeFill: 'FFF7EA',
    })
      .then(() => toast.success('Styled summary export ready.'))
      .catch(() => toast.error('Summary export failed. Please try again.'))
  }

  return (
    <div className="dashboard-layout-root">
      <Navbar {...statusConfig} />
      <Toolbar
        onAddSurvey={handleToggleAddMode}
        isAddMode={isAddMode}
        isMeasureMode={isMeasureMode}
        measureUnit={measureUnit}
        onToggleMeasureMode={() => setIsMeasureMode((v) => !v)}
        onMeasureUnitChange={setMeasureUnit}
        onClearMeasure={() => setMeasureResetNonce((v) => v + 1)}
        onExportDetailedCsv={handleExportDetailedCsv}
        onExportSummaryCsv={handleExportSummaryCsv}
        onImportGeoJson={handleImportGeoJson}
        onRefreshData={refetch}
        refreshLoading={loading}
        hasImportedShapes={pendingImportedIds.length > 0}
        onShowImportedShapes={() => setFocusImportedNonce((v) => v + 1)}
        showUnassignedShapes={showUnassignedShapes}
        onToggleShowUnassigned={() => setShowUnassignedShapes((v) => !v)}
        showImportedShapes={showImportedShapes}
        onToggleShowImported={() => setShowImportedShapes((v) => !v)}
      />
      <div className="main-container">
        <MapView
          surveys={surveys}
          shapes={shapes}
          mapResetNonce={mapResetNonce}
          isDrawingMode={isDrawingMode}
          activeDrawType={activeDrawType}
          isAddMode={isAddMode}
          onMapClickForSurvey={handleMapClickForSurvey}
          onEditSurvey={handleEditSurvey}
          onAssignShapeClick={handleAssignShapeClick}
          onCapturedShapesChange={setCapturedShapes}
          highlightedImportedIds={effectiveHighlightedIds}
          focusImportedNonce={focusImportedNonce}
          focusAssignedPropertyId={focusAssignedPropertyId}
          focusAssignedNonce={focusAssignedNonce}
          showUnassignedShapes={showUnassignedShapes}
          showImportedShapes={showImportedShapes}
          isMeasureMode={isMeasureMode}
          measureUnit={measureUnit}
          measureResetNonce={measureResetNonce}
          onMeasureCancel={() => setIsMeasureMode(false)}
          onMeasureStartEdit={() => setIsMeasureMode(true)}
          onDeleteSurvey={handleDeleteSurveyRecord}
          onShapeAdminEdit={handleShapeAdminEdit}
          draftPropertyIdForMap={
            isDrawingMode ? String(activeFormData?.propertyId ?? '').trim() : ''
          }
        />
        <AnalyticsPanel surveys={surveys} shapes={shapes} />
      </div>
      <SurveyFormModal
        isOpen={isModalOpen}
        onClose={handleCloseSurveyModal}
        initialData={activeFormData}
        onSubmit={handleSubmitSurvey}
        onStartDrawing={handleStartDrawing}
        capturedShapesCount={capturedShapes.length}
        isEditMode={Boolean(activeFormData?.dbId)}
        existingShapeCount={(Array.isArray(shapes) ? shapes : []).filter(
          (s) =>
            String(s?.propertyId || '').trim() === String(activeFormData?.propertyId || '').trim(),
        ).length}
      />
      <DrawingModeBar
        isVisible={isDrawingMode}
        availableStructures={activeFormData?.structureTypes || []}
        activeDrawType={activeDrawType}
        onDrawTypeChange={setActiveDrawType}
        onDone={handleDoneDrawing}
      />
      <AssignShapeModal
        isOpen={isAssignModalOpen}
        shape={selectedUnassignedShape}
        onClose={() => {
          setIsAssignModalOpen(false)
          setSelectedUnassignedShape(null)
        }}
        onAssign={handleAssignShape}
      />
      <RecordPasswordConfirmModal
        gate={passwordGate}
        onClose={() => setPasswordGate(null)}
        onConfirmed={handlePasswordConfirmed}
      />
      <ShapeAdminModal
        key={editingShape?.id != null ? `shape-${editingShape.id}` : 'shape-closed'}
        isOpen={Boolean(editingShape)}
        shape={editingShape}
        passwordPlot={
          editingShape
            ? plotForPasswordFromPropertyId(
                surveys,
                String(editingShape.propertyId ?? '').trim(),
              )
            : null
        }
        onClose={() => setEditingShape(null)}
        onSave={async (newType) => {
          if (!editingShape) return
          const sid = Number(editingShape.id)
          const pid = String(editingShape.propertyId || '').trim()
          if (!Number.isFinite(sid) || sid <= 0 || !pid) {
            toast.error('Invalid shape.')
            return
          }
          try {
            await updateShapeData({ id: sid, propertyId: pid, structureType: newType })
            await refetch()
            toast.success('Boundary saved. Summary updates automatically.')
            setEditingShape(null)
          } catch (err) {
            toast.error(err?.message || 'Save failed')
          }
        }}
        onDeleteBoundary={async () => {
          if (!editingShape) return
          const sid = Number(editingShape.id)
          if (!Number.isFinite(sid) || sid <= 0) {
            toast.error('Invalid shape.')
            return
          }
          try {
            await deleteShapeById(sid)
            await refetch()
            toast.success('Boundary deleted.')
            setEditingShape(null)
          } catch (err) {
            toast.error(err?.message || 'Delete failed')
          }
        }}
      />
    </div>
  )
}

export default DashboardLayout
