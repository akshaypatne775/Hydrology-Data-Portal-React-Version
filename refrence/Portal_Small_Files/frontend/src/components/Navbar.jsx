import { useAuth } from '../contexts/AuthContext'

function Navbar({ statusText = 'System Online', statusType = 'online' }) {
  const { logout } = useAuth()
  const statusIconClass =
    statusType === 'loading' ? 'fas fa-sync fa-spin' : 'fas fa-circle'
  const statusColor =
    statusType === 'error' ? '#dc3545' : statusType === 'loading' ? '#f4a261' : '#28a745'

  return (
    <div className="header">
      <h2>
        <i className="fas fa-satellite"></i> Droid Mining Solutions - Acquisition Hub
      </h2>
      <span id="syncStatus" className="sync-status" style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        <span>
          <i className={statusIconClass} style={{ color: statusColor }}></i> {statusText}
        </span>
        <button
          type="button"
          onClick={logout}
          style={{
            background: 'rgba(255,255,255,0.15)',
            border: '1px solid rgba(255,255,255,0.35)',
            color: '#fff',
            borderRadius: '8px',
            padding: '6px 12px',
            fontWeight: 700,
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          <i className="fas fa-sign-out-alt"></i> Log out
        </button>
      </span>
    </div>
  )
}

export default Navbar
