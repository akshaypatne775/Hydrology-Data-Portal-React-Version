import { useEffect, useState } from 'react'
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
  const [activeTab, setActiveTab] = useState<'pointclouds' | 'models'>(
    pointClouds.length > 0 ? 'pointclouds' : 'models',
  )
  const assets = activeTab === 'pointclouds' ? pointClouds : models

  useEffect(() => {
    if (activeTab === 'pointclouds' && pointClouds.length === 0 && models.length > 0) {
      setActiveTab('models')
    } else if (activeTab === 'models' && models.length === 0 && pointClouds.length > 0) {
      setActiveTab('pointclouds')
    }
  }, [activeTab, models.length, pointClouds.length])

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
        {pointClouds.length > 0 ? (
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
        {models.length > 0 ? (
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
