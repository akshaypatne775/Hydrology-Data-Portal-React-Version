import { useMemo } from 'react'
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
  canRename?: boolean
  onSelect: (asset: Viewer3DAsset) => void
  onRename?: (asset: Viewer3DAsset) => void
  onBack: () => void
}

function normalizeAssetLabel(value: string): string {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return ''
  return text
    .replace(/\.(las|laz|copc\.laz)$/i, '')
    .replace(/[-_][a-f0-9]{8,}$/i, '')
    .replace(/[^a-z0-9]+/g, '')
}

function assetsMatch(left: Viewer3DAsset, right: Viewer3DAsset | null | undefined): boolean {
  if (!right) return false
  if (left.id && right.id && left.id === right.id) return true
  const leftUrl = normalizeAssetKey(left)
  const rightUrl = normalizeAssetKey(right)
  if (leftUrl && rightUrl && leftUrl === rightUrl) return true
  const leftName = normalizeAssetLabel(left.name)
  const rightName = normalizeAssetLabel(right.name)
  return Boolean(leftName && rightName && leftName === rightName)
}

export default function Viewer3DSidebar({
  pointClouds,
  models,
  selectedAsset,
  canRename = false,
  onSelect,
  onRename,
  onBack,
}: Viewer3DSidebarProps) {
  const uniquePointClouds = useMemo(() => unique3DAssets(pointClouds), [pointClouds])
  const uniqueModels = useMemo(() => unique3DAssets(models), [models])
  const sections = [
    { id: 'pointclouds', label: 'Point Clouds', assets: uniquePointClouds },
    { id: 'models', label: '3D Models', assets: uniqueModels },
  ].filter((section) => section.assets.length > 0)

  return (
    <aside className="viewer-3d-sidebar" aria-label="3D viewer navigation">
      <button type="button" className="viewer-3d-sidebar__back" onClick={onBack}>
        <i className="fas fa-arrow-left" aria-hidden /> Back to Map
      </button>

      <header className="viewer-3d-sidebar__header">
        <span>3D Data Viewer</span>
        <strong>Project Assets</strong>
      </header>

      <div className="viewer-3d-sidebar__list">
        {sections.map((section) => (
          <section key={section.id} className="viewer-3d-sidebar__section">
            <p className="viewer-3d-sidebar__section-title">{section.label}</p>
            {section.assets.map((asset) => (
              <div
                key={asset.id}
                className={assetsMatch(asset, selectedAsset) ? 'viewer-3d-sidebar__asset viewer-3d-sidebar__asset--active' : 'viewer-3d-sidebar__asset'}
              >
                <button type="button" className="viewer-3d-sidebar__asset-main" onClick={() => onSelect(asset)}>
                  <i className={asset.viewer === 'potree' ? 'fas fa-cloud' : 'fas fa-cube'} aria-hidden />
                  <span>{asset.name}</span>
                </button>
                {canRename && onRename ? (
                  <button
                    type="button"
                    className="viewer-3d-sidebar__asset-rename"
                    onClick={() => onRename(asset)}
                    title={`Rename ${asset.name}`}
                  >
                    <i className="fas fa-pen" aria-hidden />
                  </button>
                ) : null}
              </div>
            ))}
          </section>
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
