import { useEffect, useRef } from 'react'
import PotreeViewer from './PotreeViewer'
import './PointCloudViewer.css'

type PointCloudViewerProps = {
  url: string
  name?: string
  projectId?: string
  datasetId?: string
}

export default function PointCloudViewer({ url, name = 'Point Cloud', projectId = '', datasetId = '' }: PointCloudViewerProps) {
  const rootRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const suppressRendererChrome = (doc: Document) => {
      if (!doc.getElementById('droid-clean-3d-viewer-style')) {
        const style = doc.createElement('style')
        style.id = 'droid-clean-3d-viewer-style'
        style.textContent = `
          #potree_render_area,
          .potree_render_area,
          .potree_container {
            inset: 0 !important;
            left: 0 !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
          }
          #potree_sidebar_container,
          #potree_branding,
          #potree_map_toggle,
          #potree_map,
          .potree-branding,
          .potree_branding,
          .potree-logo,
          .potree_logo,
          [class*="potree-brand"],
          [class*="potree-logo"],
          [id*="potree-brand"],
          [id*="potree-logo"] {
            display: none !important;
            visibility: hidden !important;
            opacity: 0 !important;
            pointer-events: none !important;
          }
          .potree_profile_graph,
          .profile_window,
          .measurement_label,
          .potree_measurement,
          .potree_volume,
          .volume_tool,
          .annotation,
          .annotation-label,
          .potree_menu_toggle {
            z-index: 99999 !important;
            position: absolute !important;
          }
          @keyframes slowPulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
          }
          .droid-volume-active,
          [title*="Volume"].active,
          [title*="volume"].active,
          .potree_volume_button.active {
            animation: slowPulse 1.8s ease-in-out infinite !important;
          }
        `
        doc.head?.appendChild(style)
      }
      doc.querySelectorAll<HTMLElement>('body *').forEach((element) => {
        const text = element.children.length === 0 ? element.textContent?.trim().toLowerCase() : ''
        const imageSource = element instanceof HTMLImageElement ? element.src.toLowerCase() : ''
        if (text === 'potree' || imageSource.includes('potree')) {
          element.style.setProperty('display', 'none', 'important')
        }
        if (
          text &&
          /(bounding box|position|scale|rotation|classification|attribute|intensity|return number|number of returns|point source|gps-time|rgb|octree|spacing|level)/i.test(text) &&
          !/(cut volume|fill volume|area|perimeter)/i.test(text)
        ) {
          element.style.setProperty('display', 'none', 'important')
        }
      })
    }

    const apply = () => {
      const root = rootRef.current
      if (!root) return
      suppressRendererChrome(document)
      root.querySelectorAll('iframe').forEach((frame) => {
        try {
          if (frame.contentDocument) suppressRendererChrome(frame.contentDocument)
        } catch {
          // Cross-origin frames cannot be styled; project viewer outputs are same-origin.
        }
      })
    }

    apply()
    const timer = window.setInterval(apply, 500)
    return () => window.clearInterval(timer)
  }, [url])

  return (
    <section ref={rootRef} className="point-cloud-viewer" aria-label={`${name} 3D data viewer`}>
      <PotreeViewer key={url} url={url} projectId={projectId} datasetId={datasetId} />
    </section>
  )
}
