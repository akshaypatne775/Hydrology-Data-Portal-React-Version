import {
  ArcElement,
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
} from 'chart.js'
import { Bar, Doughnut } from 'react-chartjs-2'
import { Link } from 'react-router-dom'

ChartJS.register(ArcElement, BarElement, CategoryScale, LinearScale, Tooltip, Legend)

const ACQUISITION_STAGES = [
  'Notice 37(2) Distribution',
  'Samarpan Received',
  'Field Survey',
  'Owner Verification',
  'Structure Assessment',
  'Compensation Calculation',
  'Payment Processing',
  'Land Possession',
  'Complete (Land Bank)',
  'Dispute',
  'On Hold',
]

function normalizeAcquisitionStage(properties = {}) {
  const stage = properties.acquisitionStage || properties.legalStatus || ''
  if (ACQUISITION_STAGES.includes(stage)) return stage
  if (stage === 'Legal') return 'Notice 37(2) Distribution'
  if (stage === 'Illegal / Encroached') return 'Dispute'
  return 'Notice 37(2) Distribution'
}

function acquisitionStageFillColor(stage) {
  const stageMap = {
    'Samarpan Received': '#8e44ad',
    'Field Survey': '#2980b9',
    'Owner Verification': '#f39c12',
    'Payment Processing': '#e67e22',
    'Land Possession': '#16a085',
    'Complete (Land Bank)': '#2ecc71',
    Dispute: '#e74c3c',
    'On Hold': '#7f8c8d',
  }
  return stageMap[stage] || '#0e3e49'
}

function propBool(v) {
  return v === true || v === 1 || v === '1'
}

function propInt(v, fallback = 0) {
  if (v == null || v === '') return fallback
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function isPendingSurveyPropertyId(pid) {
  const s = String(pid ?? '').trim()
  return s.toUpperCase().startsWith('PENDING_')
}

function AnalyticsPanel({ surveys }) {
  const features = surveys?.features ?? []
  const structuresAcquired = features.length
  /** Total area only for real surveys; imported GeoJSON rows must not use PENDING_ in legal_surveys — exclude if present. */
  const totalArea = features.reduce((sum, feature) => {
    if (isPendingSurveyPropertyId(feature?.properties?.propertyId)) return sum
    const area = Number(feature?.properties?.areaSqft ?? 0)
    return sum + (Number.isFinite(area) ? area : 0)
  }, 0)
  const totalCompensation = features.reduce((sum, feature) => {
    const amount = Number(feature?.properties?.moneyDistributed ?? 0)
    return sum + (Number.isFinite(amount) ? amount : 0)
  }, 0)
  const disputes = features.reduce((count, feature) => {
    const stage = feature?.properties?.acquisitionStage ?? feature?.properties?.legalStatus
    return stage === 'Dispute' ? count + 1 : count
  }, 0)
  const totalTrees = features.reduce(
    (sum, feature) => sum + propInt(feature?.properties?.numberOfTrees, 0),
    0,
  )
  const totalDistribution = features.reduce(
    (sum, feature) => sum + propInt(feature?.properties?.totalDistribution, 0),
    0,
  )
  const samarpanSum = features.reduce(
    (sum, feature) => sum + propInt(feature?.properties?.samarpanReceipt, 0),
    0,
  )
  const ownerVerified = features.reduce(
    (count, feature) => count + (propBool(feature?.properties?.ownerVerification) ? 1 : 0),
    0,
  )
  const fieldSurveyDone = features.reduce(
    (count, feature) => count + (propBool(feature?.properties?.fieldSurveyDone) ? 1 : 0),
    0,
  )
  const aadharCollected = features.reduce(
    (count, feature) => count + (propBool(feature?.properties?.aadharCollected) ? 1 : 0),
    0,
  )
  const panCollected = features.reduce(
    (count, feature) => count + (propBool(feature?.properties?.panCollected) ? 1 : 0),
    0,
  )
  const bankDetailsCollected = features.reduce(
    (count, feature) => count + (propBool(feature?.properties?.bankDetailsCollected) ? 1 : 0),
    0,
  )
  const acquisitionStats = ACQUISITION_STAGES.reduce((acc, stage) => {
    acc[stage] = 0
    return acc
  }, {})
  const structureStats = {}

  features.forEach((feature) => {
    const stage = normalizeAcquisitionStage(feature?.properties || {})
    acquisitionStats[stage] = (acquisitionStats[stage] || 0) + 1

    const types = String(feature?.properties?.structureType || '')
      .split(/,\s*/)
      .map((t) => t.trim())
    types.forEach((type) => {
      if (!type) return
      structureStats[type] = (structureStats[type] || 0) + 1
    })
  })

  let pipelineLabels = ACQUISITION_STAGES.filter((stage) => acquisitionStats[stage] > 0)
  if (pipelineLabels.length === 0 && features.length > 0) {
    pipelineLabels = ACQUISITION_STAGES.slice()
  }
  let pipelineData = pipelineLabels.map((stage) => acquisitionStats[stage] || 0)
  let pipelineColors = pipelineLabels.map((stage) => acquisitionStageFillColor(stage))
  if (pipelineLabels.length === 0) {
    pipelineLabels = ['No surveys']
    pipelineData = [1]
    pipelineColors = ['#7f8c8d']
  }

  return (
    <div className="analytics-panel" style={{ width: '380px', height: '100%', overflowY: 'auto' }}>
      <div className="analytics-actions">
        <Link to="/owners" className="btn-tool analytics-owner-btn">
          <i className="fas fa-users"></i> Owners Page
        </Link>
      </div>
      <div className="kpi-grid">
        <div className="summary-card">
          <p>Structures Acquired</p>
          <h3 id="stat-count">{structuresAcquired}</h3>
        </div>
        <div className="summary-card">
          <p>Total Area</p>
          <h3 id="stat-area">{totalArea.toLocaleString('en-IN')} Sqft</h3>
        </div>
        <div className="summary-card">
          <p>Total Compensation</p>
          <h3 id="stat-money">₹ {totalCompensation.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Disputes</p>
          <h3 id="stat-illegal">{disputes}</h3>
        </div>
      </div>

      <div className="panel-title">Field &amp; document KPIs</div>
      <div className="kpi-grid">
        <div className="summary-card">
          <p>Total Trees</p>
          <h3 id="stat-trees">{totalTrees.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Total distribution (sum)</p>
          <h3 id="stat-total-distribution">{totalDistribution.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Samarpan receipts (sum)</p>
          <h3 id="stat-samarpan-sum">{samarpanSum.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Owners verified</p>
          <h3 id="stat-owner-verified">{ownerVerified.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Field survey done</p>
          <h3 id="stat-field-survey-done">{fieldSurveyDone.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Aadhar collected</p>
          <h3 id="stat-aadhar-done">{aadharCollected.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>PAN collected</p>
          <h3 id="stat-pan-done">{panCollected.toLocaleString('en-IN')}</h3>
        </div>
        <div className="summary-card">
          <p>Bank details collected</p>
          <h3 id="stat-bank-done">{bankDetailsCollected.toLocaleString('en-IN')}</h3>
        </div>
      </div>

      <div className="panel-title">Acquisition pipeline</div>
      <div className="chart-container">
        <Doughnut
          data={{
            labels: pipelineLabels,
            datasets: [{ data: pipelineData, backgroundColor: pipelineColors, borderWidth: 0 }],
          }}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'right', labels: { font: { size: 11, family: 'Montserrat' } } },
            },
          }}
        />
      </div>
      <div className="panel-title">Structure Classification</div>
      <div className="chart-container">
        <Bar
          data={{
            labels: Object.keys(structureStats),
            datasets: [
              {
                label: 'Count',
                data: Object.values(structureStats),
                backgroundColor: '#1a5c6b',
                borderRadius: 4,
              },
            ],
          }}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } },
          }}
        />
      </div>
    </div>
  )
}

export default AnalyticsPanel
