import { useEffect, useState } from 'react'
import {
  fetchHydrologyStats,
  runHydrologyEngine,
  type CatchmentStat,
  type LulcRow,
  type StreamStat,
} from '../services/hydrologyService'

export function useHydrologyStats() {
  const [catchmentStats, setCatchmentStats] = useState<CatchmentStat[]>([])
  const [streamStats, setStreamStats] = useState<StreamStat[]>([])
  const [lulcRows, setLulcRows] = useState<LulcRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isSimulating, setIsSimulating] = useState(false)
  const [simulationDone, setSimulationDone] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchHydrologyStats()
        if (cancelled) return
        setCatchmentStats(data.catchment_stats ?? [])
        setStreamStats(data.stream_stats ?? [])
        setLulcRows(data.lulc_rows ?? [])
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load project stats')
          setCatchmentStats([])
          setStreamStats([])
          setLulcRows([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const runEngine = async () => {
    if (isSimulating || simulationDone) return
    setIsSimulating(true)
    try {
      await runHydrologyEngine()
      setSimulationDone(true)
    } finally {
      setIsSimulating(false)
    }
  }

  return {
    catchmentStats,
    streamStats,
    lulcRows,
    loading,
    error,
    isSimulating,
    simulationDone,
    runEngine,
  }
}
