import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getCompareDatasets,
  getVolumeCompare,
  refreshCompareCache,
  type CompareDataset,
  type VolumeRow,
} from '../../services/analysisService'
import { updateDatasetMetadata } from '../../services/datasetService'
import './ComparePanel.css'

type ComparePanelProps = {
  projectId?: string
}

const DUMMY_VOLUME_ROWS: VolumeRow[] = [
  { month: '2026-01', label: 'Jan 2026', volume: 12400, cut: 1800, fill: 14200, net: 12400, area: 42000, source: 'csv' },
  { month: '2026-02', label: 'Feb 2026', volume: 18800, cut: 2400, fill: 21200, net: 18800, area: 43800, source: 'csv' },
  { month: '2026-03', label: 'Mar 2026', volume: 26300, cut: 3200, fill: 29500, net: 26300, area: 45100, source: 'csv' },
  { month: '2026-04', label: 'Apr 2026', volume: 33700, cut: 3900, fill: 37600, net: 33700, area: 46900, source: 'csv' },
  { month: '2026-05', label: 'May 2026', volume: 41800, cut: 4700, fill: 46500, net: 41800, area: 48200, source: 'csv' },
  { month: '2026-06', label: 'Jun 2026', volume: 52600, cut: 5300, fill: 57900, net: 52600, area: 50100, source: 'csv' },
]

function fmt(n: number): string {
  if (!Number.isFinite(n)) return '--'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return n.toFixed(2)
}

export default function ComparePanel({ projectId }: ComparePanelProps) {
  const [datasets, setDatasets] = useState<CompareDataset[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [rows, setRows] = useState<VolumeRow[]>([])
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const displayRows = rows.length > 0 ? rows : DUMMY_VOLUME_ROWS
  const displaySource = rows.length > 0 ? source : 'demo'

  const loadDatasets = useCallback(async () => {
    if (!projectId) return
    const data = await getCompareDatasets(projectId)
    setDatasets(data)
    setSelectedIds(new Set(data.filter((d) => d.month || d.dataset_type === 'csv').map((d) => d.dataset_id)))
  }, [projectId])

  useEffect(() => {
    void loadDatasets()
  }, [loadDatasets])

  const selectedArray = useMemo(() => [...selectedIds], [selectedIds])

  const runCompare = useCallback(async () => {
    if (!projectId) return
    setBusy(true)
    setMessage(null)
    try {
      const data = await getVolumeCompare(projectId, selectedArray)
      setRows(data.rows ?? [])
      setSource(data.source)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Volume comparison failed')
    } finally {
      setBusy(false)
    }
  }, [projectId, selectedArray])

  const refresh = useCallback(async () => {
    if (!projectId) return
    setBusy(true)
    try {
      const res = await refreshCompareCache(projectId)
      setMessage(`Cache refreshed. Removed ${res.removed} cached result(s).`)
      await runCompare()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Cache refresh failed')
    } finally {
      setBusy(false)
    }
  }, [projectId, runCompare])

  const maxVolume = Math.max(...displayRows.map((r) => Math.abs(r.net || r.volume || 0)), 1)
  const totals = displayRows.reduce(
    (acc, row) => ({
      cut: acc.cut + (row.cut || 0),
      fill: acc.fill + (row.fill || 0),
      net: acc.net + (row.net || row.volume || 0),
    }),
    { cut: 0, fill: 0, net: 0 },
  )

  return (
    <section className="cmp-root">
      <header className="cmp-head">
        <div>
          <h3>Monthly Volume Compare</h3>
          <p>Assign months, select DTM/DSM/CSV datasets, then calculate mine change volumes.</p>
        </div>
        <div className="cmp-actions">
          <button type="button" onClick={() => void refresh()} disabled={!projectId || busy}>
            Update if data changed
          </button>
          <button type="button" onClick={() => void runCompare()} disabled={!projectId || busy}>
            {busy ? 'Calculating...' : 'Run Compare'}
          </button>
        </div>
      </header>

      {message ? <p className="cmp-message">{message}</p> : null}

      <div className="cmp-layout">
        <div className="cmp-table-wrap">
          <table className="cmp-table">
            <thead>
              <tr>
                <th>Use</th>
                <th>Dataset</th>
                <th>Type</th>
                <th>Month</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.dataset_id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(dataset.dataset_id)}
                      onChange={(event) => {
                        setSelectedIds((prev) => {
                          const next = new Set(prev)
                          if (event.target.checked) next.add(dataset.dataset_id)
                          else next.delete(dataset.dataset_id)
                          return next
                        })
                      }}
                    />
                  </td>
                  <td>{dataset.name}</td>
                  <td>{dataset.dataset_type.toUpperCase()}</td>
                  <td>
                    <input
                      type="month"
                      value={dataset.month || ''}
                      onChange={async (event) => {
                        if (!projectId) return
                        const month = event.target.value
                        setDatasets((prev) => prev.map((d) => d.dataset_id === dataset.dataset_id ? { ...d, month } : d))
                        await updateDatasetMetadata(projectId, {
                          dataset_id: dataset.dataset_id,
                          month,
                          dataset_type: dataset.dataset_type,
                        })
                      }}
                    />
                  </td>
                </tr>
              ))}
              {datasets.length === 0 ? (
                <tr>
                  <td colSpan={4}>No DTM, DSM, or CSV datasets found.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="cmp-results">
          <div className="cmp-summary">
            <article><span>Source</span><strong>{displaySource}</strong></article>
            <article><span>Cut</span><strong>{fmt(totals.cut)} m3</strong></article>
            <article><span>Fill</span><strong>{fmt(totals.fill)} m3</strong></article>
            <article><span>Net</span><strong>{fmt(totals.net)} m3</strong></article>
          </div>
          <div className="cmp-chart" aria-label="Volume comparison chart">
            {displayRows.map((row) => {
              const value = row.net || row.volume || 0
              const height = Math.max(8, (Math.abs(value) / maxVolume) * 180)
              return (
                <div key={`${row.label}-${row.month}`} className="cmp-bar">
                  <div
                    className={value >= 0 ? 'cmp-bar__fill cmp-bar__fill--pos' : 'cmp-bar__fill cmp-bar__fill--neg'}
                    style={{ height }}
                    title={`${row.label}: ${fmt(value)} m3`}
                  />
                  <span>{row.month || row.label}</span>
                </div>
              )
            })}
            {rows.length === 0 ? <p className="cmp-empty">Demo mine-volume trend shown. Run compare to load real CSV/DTM results.</p> : null}
          </div>
        </div>
      </div>
    </section>
  )
}
