import './StartupLoader.css'

const DROID_CLOUD_LOGO_URL =
  'https://www.droidminingsolutions.com/wp-content/uploads/2026/04/ChatGPT-Image-Apr-25-2026-04_33_45-PM.png'

export function StartupLoader() {
  return (
    <div className="startup-loader" role="status" aria-label="Loading Droid Cloud">
      <div className="startup-loader__ring startup-loader__ring--outer" />
      <div className="startup-loader__ring startup-loader__ring--inner" />
      <div className="startup-loader__glow" />
      <div className="startup-loader__scanline" />
      <img src={DROID_CLOUD_LOGO_URL} alt="Droid Cloud" className="startup-loader__logo" />
      <p className="startup-loader__text">Loading Droid Cloud Workspace...</p>
    </div>
  )
}

export default StartupLoader
