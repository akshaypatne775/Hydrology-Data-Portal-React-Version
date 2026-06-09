import { useEffect, useMemo, useState } from 'react'
import './Viewer3DSidebar.css'

export type Viewer3DAsset = {
  id: string
  name: string
  url: string
  viewer: 'potree' | 'cesium'
}

type Viewer3DSidebarProps = {
  pointClouds: Viewer3DAsset[]
  models: Viewer3DAsset[]
  selectedAsset: Viewer3DAsset | null
  onSelect: (asset: Viewer3DAsset) => void
  onBack: () => void
}

export default function Viewer3DSidebar({
  pointClouds,
  models,
  selectedAsset,
  onSelect,
  onBack,
}: Viewer3DSidebarProps) {
  const uniquePointClouds = useMemo(() => unique3DAssets(pointClouds), [pointClouds])
  const uniqueModels = useMemo(() => unique3DAssets(models), [models])
  const [activeTab, setActiveTab] = useState<'pointclouds' | 'models'>(
    uniquePointClouds.length > 0 ? 'pointclouds' : 'models',
  )
  const assets = activeTab === 'pointclouds' ? uniquePointClouds : uniqueModels

  useEffect(() => {
    if (activeTab === 'pointclouds' && uniquePointClouds.length === 0 && uniqueModels.length > 0) {
      setActiveTab('models')
    } else if (activeTab === 'models' && uniqueModels.length === 0 && uniquePointClouds.length > 0) {
      setActiveTab('pointclouds')
    }
  }, [activeTab, uniqueModels.length, uniquePointClouds.length])

  return (
    <aside className="viewer-3d-sidebar" aria-label="3D viewer navigation">
      <button type="button" className="viewer-3d-sidebar__back" onClick={onBack}>
        <i className="fas fa-arrow-left" aria-hidden /> Back to Map
      </button>

      <header className="viewer-3d-sidebar__header">
        <span>3D Data Viewer</span>
        <strong>Project Assets</strong>
      </header>

      <div className="viewer-3d-sidebar__tabs" role="tablist" aria-label="3D asset types">
        {uniquePointClouds.length > 0 ? (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'pointclouds'}
            className={activeTab === 'pointclouds' ? 'viewer-3d-sidebar__tab viewer-3d-sidebar__tab--active' : 'viewer-3d-sidebar__tab'}
            onClick={() => setActiveTab('pointclouds')}
          >
            Point Clouds
          </button>
        ) : null}
        {uniqueModels.length > 0 ? (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'models'}
            className={activeTab === 'models' ? 'viewer-3d-sidebar__tab viewer-3d-sidebar__tab--active' : 'viewer-3d-sidebar__tab'}
            onClick={() => setActiveTab('models')}
          >
            3D Models
          </button>
        ) : null}
      </div>

      <div className="viewer-3d-sidebar__list">
        {assets.map((asset) => (
          <button
            key={asset.id}
            type="button"
            className={selectedAsset?.id === asset.id ? 'viewer-3d-sidebar__asset viewer-3d-sidebar__asset--active' : 'viewer-3d-sidebar__asset'}
            onClick={() => onSelect(asset)}
          >
            <i className={asset.viewer === 'potree' ? 'fas fa-cloud' : 'fas fa-cube'} aria-hidden />
            <span>{asset.name}</span>
          </button>
        ))}
      </div>
    </aside>
  )
}

function normalizeAssetKey(asset: Viewer3DAsset): string {
  const specificName = String(asset.name || '').trim().toLowerCase()
  const raw = specificName && specificName !== 'point cloud' && specificName !== '3d model'
    ? `${asset.viewer}:name:${asset.name}`
    : `${asset.viewer}:${asset.url || asset.id || asset.name}`
  try {
    return decodeURIComponent(raw)
      .replace(/\\/g, '/')
      .replace(/^https?:\/\/[^/]+/i, '')
      .replace(/[?#].*$/, '')
      .toLowerCase()
  } catch {
    return raw.replace(/\\/g, '/').replace(/[?#].*$/, '').toLowerCase()
  }
}

function unique3DAssets(assets: Viewer3DAsset[]): Viewer3DAsset[] {
  const unique = new Map<string, Viewer3DAsset>()
  for (const asset of assets) {
    const key = normalizeAssetKey(asset)
    if (!unique.has(key)) unique.set(key, asset)
  }
  return Array.from(unique.values())
}
