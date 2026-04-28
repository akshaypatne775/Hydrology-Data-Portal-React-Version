import { apiRequest, apiRequestJson } from './api'

export type CatchmentStat = { label: string; value: string; unit: string }
export type StreamStat = { label: string; value: string; unit: string }
export type LulcRow = { name: string; pct: number; color: string }

export type HydrologyStatsPayload = {
  catchment_stats: CatchmentStat[]
  stream_stats: StreamStat[]
  lulc_rows: LulcRow[]
}

export async function fetchHydrologyStats(): Promise<HydrologyStatsPayload> {
  return apiRequestJson<HydrologyStatsPayload>('/api/hydrology-stats')
}

export async function runHydrologyEngine(): Promise<void> {
  const response = await apiRequest('/api/run-flood-engine', { method: 'POST' })
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`)
  }
}
