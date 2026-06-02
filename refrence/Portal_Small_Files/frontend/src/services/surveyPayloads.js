import { embedPlotMetaInGeoJson } from '../utils/geoJsonPlotMeta'

export const initialSurveyCollection = { type: 'FeatureCollection', features: [] }

export function normalizeSurveyFeature(feature) {
  const p = feature?.properties || {}
  return {
    ...feature,
    properties: {
      ...p,
      aadharFileB64: p.aadharFileB64 || p.aadhar_file_b64 || '',
      panFileB64: p.panFileB64 || p.pan_file_b64 || '',
      bankFileB64: p.bankFileB64 || p.bank_file_b64 || '',
      ownerVerifFileB64: p.ownerVerifFileB64 || p.owner_verif_file_b64 || '',
      samarpanFileB64: p.samarpanFileB64 || p.samarpan_file_b64 || '',
      surveyFileB64: p.surveyFileB64 || p.survey_file_b64 || '',
    },
  }
}

export function normalizeSurveysPayload(surveysData) {
  const raw = surveysData || initialSurveyCollection
  return {
    ...raw,
    features: (raw.features || []).map(normalizeSurveyFeature),
  }
}

export function buildShapePayload(shape, propertyId) {
  const structureType = shape?.type || shape?.structureType || 'Open Space'
  return {
    propertyId,
    structureType,
    geoJson: embedPlotMetaInGeoJson(shape?.geojson, propertyId, structureType),
    calculatedArea: parseFloat(shape?.area ?? shape?.areaSqft) || 0,
  }
}

export function buildSurveyCreatePayload({ formData, encodedFiles, lat, lng, totalArea, defaults }) {
  return {
    propertyId: formData.propertyId,
    ownerName: formData.ownerName,
    structureType: (formData.structureTypes || []).join(', '),
    acquisitionStage: formData.acquisitionStage,
    noticeSent: formData.noticeSent,
    moneyDistributed: Number(formData.moneyDistributed || 0),
    areaSqft: totalArea > 0 ? totalArea : Number(formData.areaSqft || 0),
    numberOfTrees: Number(formData.numberOfTrees || 0),
    totalDistribution: Number(formData.totalDistribution || 0),
    samarpanReceipt: formData.samarpanReceipt ? 1 : 0,
    fieldSurveyDone: Boolean(formData.fieldSurveyDone),
    ownerVerification: Boolean(formData.ownerVerification),
    aadharCollected: Boolean(formData.aadharCollected),
    panCollected: Boolean(formData.panCollected),
    bankDetailsCollected: Boolean(formData.bankDetailsCollected),
    ...encodedFiles,
    coordinates: { lat, lng },
    lat,
    lng,
    state: defaults?.state || 'Field',
    district: defaults?.district || 'Field',
  }
}

function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function toBoolInt(value) {
  return value === true || value === 1 || value === '1' ? 1 : 0
}

export function buildSurveyUpdatePayloadFromRow(row, patch = {}) {
  const p = row?.properties || {}
  const coords = row?.geometry?.coordinates || []
  const lng = toNumber(coords?.[0], toNumber(p.lng, 0))
  const lat = toNumber(coords?.[1], toNumber(p.lat, 0))
  const next = { ...p, ...patch }

  return {
    id: Number(next.dbId),
    propertyId: String(next.propertyId || '').trim(),
    ownerName: String(next.ownerName || '').trim(),
    structureType: String(next.structureType || ''),
    acquisitionStage: String(next.acquisitionStage || 'Notice 37(2) Distribution'),
    noticeSent: String(next.noticeSent || 'No'),
    moneyDistributed: toNumber(next.moneyDistributed, 0),
    areaSqft: toNumber(next.areaSqft, 0),
    photoB64: String(next.photoB64 || ''),
    lat,
    lng,
    state: String(next.state || 'Dashboard'),
    district: String(next.district || 'Dashboard'),
    totalDistribution: toNumber(next.totalDistribution, 0),
    samarpanReceipt: toBoolInt(next.samarpanReceipt),
    fieldSurveyDone: Boolean(next.fieldSurveyDone),
    ownerVerification: Boolean(next.ownerVerification),
    aadharCollected: Boolean(next.aadharCollected),
    panCollected: Boolean(next.panCollected),
    bankDetailsCollected: Boolean(next.bankDetailsCollected),
    numberOfTrees: toNumber(next.numberOfTrees, 0),
    aadharFileB64: String(next.aadharFileB64 || ''),
    panFileB64: String(next.panFileB64 || ''),
    bankFileB64: String(next.bankFileB64 || ''),
    ownerVerifFileB64: String(next.ownerVerifFileB64 || ''),
    samarpanFileB64: String(next.samarpanFileB64 || ''),
    surveyFileB64: String(next.surveyFileB64 || ''),
  }
}
