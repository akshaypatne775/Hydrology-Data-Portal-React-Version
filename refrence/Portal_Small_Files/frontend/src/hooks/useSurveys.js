import { startTransition, useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import * as surveyApi from '../services/surveyApi'
import { buildShapePayload, initialSurveyCollection, normalizeSurveysPayload } from '../services/surveyPayloads'

const SURVEY_PAGE_SIZE = 500

function useSurveys() {
  const { isAuthenticated } = useAuth()
  const initialSurveys = initialSurveyCollection
  const [surveys, setSurveys] = useState(initialSurveys)
  const [shapes, setShapes] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const surveysJsonRef = useRef(JSON.stringify(initialSurveys))
  const shapesJsonRef = useRef(JSON.stringify([]))

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const shapesPromise = surveyApi.getShapes()
      const featureList = await surveyApi.getAllSurveyFeaturesMerged({ pageSize: SURVEY_PAGE_SIZE })
      const shapesData = await shapesPromise

      const normalizedSurveys = normalizeSurveysPayload({
        type: 'FeatureCollection',
        features: featureList,
      })
      const normalizedShapes = Array.isArray(shapesData) ? shapesData : []
      const nextSurveysJson = JSON.stringify(normalizedSurveys)
      const nextShapesJson = JSON.stringify(normalizedShapes)

      startTransition(() => {
        if (nextSurveysJson !== surveysJsonRef.current) {
          surveysJsonRef.current = nextSurveysJson
          setSurveys(normalizedSurveys)
        }
      })
      if (nextShapesJson !== shapesJsonRef.current) {
        shapesJsonRef.current = nextShapesJson
        setShapes(normalizedShapes)
      }
    } catch (err) {
      setError(err.message || 'Failed to fetch dashboard data')
    } finally {
      setLoading(false)
    }
  }, [])

  const refetch = useCallback(async () => {
    await fetchData()
  }, [fetchData])

  useEffect(() => {
    if (!isAuthenticated) return
    fetchData()
  }, [isAuthenticated, fetchData])

  const saveShapesForProperty = useCallback(async (propertyId, capturedShapes = []) => {
    if (!propertyId || !Array.isArray(capturedShapes) || capturedShapes.length === 0) return
    for (const shape of capturedShapes) {
      await surveyApi.saveShape(buildShapePayload(shape, propertyId))
    }
  }, [])

  const saveSurveyData = useCallback(async (surveyPayload, capturedShapes = []) => {
    const response = await surveyApi.saveSurvey(surveyPayload)
    await saveShapesForProperty(surveyPayload.propertyId, capturedShapes)
    return response
  }, [saveShapesForProperty])

  const updateShapeAssignment = useCallback(
    async (oldPropertyId, newPropertyId, structureType, calculatedArea = 0) => {
      return surveyApi.updateShapeAssignment({
          oldPropertyId,
          newPropertyId,
          structureType,
          calculatedArea,
        })
    },
    [],
  )

  const updateSurveyData = useCallback(async (payload) => {
    return surveyApi.updateSurvey(payload)
  }, [])

  const updateShapeData = useCallback(async (payload) => {
    return surveyApi.updateShape(payload)
  }, [])

  const deleteShapeById = useCallback(async (id) => {
    return surveyApi.deleteShape(id)
  }, [])

  const deleteSurveyById = useCallback(async (id) => {
    return surveyApi.deleteSurvey(id)
  }, [])

  return {
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
  }
}

export default useSurveys
