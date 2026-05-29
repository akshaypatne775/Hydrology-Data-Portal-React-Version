import { useEffect, useState } from 'react'
import { useWorkspaceContext } from '../../context/WorkspaceContext'
import { useModal } from '../../context/ModalContext'
import {
  advancedDeleteAdminUser,
  approveAdminUser,
  assignAdminUserRole,
  deleteAdminUser,
  disapproveAdminUser,
  getAdminUserActivity,
  setAdminUserCatalogAccess,
  type AdminUserActivity,
} from '../../services/adminService'
import './AdminDashboard.css'

function formatLastSeen(value: string): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function humanizePathPart(value: string): string {
  const decoded = (() => {
    try {
      return decodeURIComponent(value)
    } catch {
      return value
    }
  })()
  return decoded
    .replace(/\.(json|png|jpg|jpeg|tif|tiff|kml|geojson|las|laz|zip)$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function endpointSegments(raw: string): string[] {
  const endpoint = raw.replace(/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|LOGOUT)\s+/i, '').split('?')[0] ?? raw
  try {
    return new URL(endpoint, window.location.origin).pathname.split('/').filter(Boolean)
  } catch {
    return endpoint.split('/').filter(Boolean)
  }
}

function formatAccessLabel(raw: string): string {
  if (!raw) return '--'
  if (/LOGOUT|\/api\/auth\/logout/i.test(raw)) return 'Logged out'
  if (/\/api\/auth\/login/i.test(raw)) return 'Logged in'
  if (/\/api\/admin/i.test(raw)) return 'Admin Control'
  if (/\/api\/projects\/[^/]+\/files/i.test(raw)) return 'Project data catalog'

  const segments = endpointSegments(raw)
  const lower = segments.map((part) => part.toLowerCase())
  const typeIndex = lower.findIndex((part) => (
    ['3dmodel', 'pointcloud', 'dtm', 'dem', 'dsm', 'ortho', 'orthomosaic', 'vector', 'cog'].includes(part)
  ))
  const kind = (() => {
    if (lower.includes('3dmodel') || lower.includes('tileset.json')) return '3D Model'
    if (lower.includes('pointcloud')) return 'Point Cloud'
    if (lower.includes('dtm') || lower.includes('dem')) return 'DTM'
    if (lower.includes('dsm')) return 'DSM'
    if (lower.includes('ortho') || lower.includes('orthomosaic')) return 'Ortho'
    if (lower.includes('vector')) return 'Vector'
    return 'Dataset'
  })()
  const namePart = (() => {
    if (typeIndex >= 0) {
      const afterType = segments[typeIndex + 1]
      if (afterType && afterType !== 'tileset.json') return afterType
      const beforeTileset = segments[Math.max(0, lower.indexOf('tileset.json') - 1)]
      if (beforeTileset) return beforeTileset
    }
    const datasetIndex = lower.indexOf('datasets')
    if (datasetIndex >= 0) {
      const candidate = segments[datasetIndex + 2] || segments[datasetIndex + 1]
      if (candidate) return candidate
    }
    return segments.at(-1) ?? ''
  })()
  const name = humanizePathPart(namePart)
  if (!name || ['files', 'bounds', 'tileset'].includes(name.toLowerCase())) return kind
  return `${name} ${kind}`.trim()
}

export default function AdminDashboard() {
  const { setActiveId, setManagedUser, setSelectedProject } = useWorkspaceContext()
  const modal = useModal()
  const [users, setUsers] = useState<AdminUserActivity[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await getAdminUserActivity()
      setUsers(rows)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load user activity')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const rows = await getAdminUserActivity()
        if (!cancelled) setUsers(rows)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load user activity')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const runUserAction = async (action: () => Promise<void>) => {
    try {
      await action()
      await refresh()
    } catch (err) {
      await modal.alert('Admin action failed', err instanceof Error ? err.message : 'Admin action failed')
    }
  }

  return (
    <section className="admin-panel" aria-label="Admin control panel">
      <header className="admin-panel__hero">
        <div>
          <p className="admin-panel__kicker">Admin Control</p>
          <h2>Activity Monitoring System</h2>
          <p>
            Monitor sessions, connected IPs, and workspace access in real time.
          </p>
        </div>
        <div className="admin-panel__summary" aria-label="User activity summary">
          <span>{users.filter((user) => user.status === 'Active').length} Active</span>
          <span>{users.length} Users</span>
        </div>
      </header>

      <div className="admin-panel__table-wrap">
        <table className="admin-panel__table">
          <thead>
            <tr>
              <th>User Name</th>
              <th>Status</th>
              <th>Current IP / Device</th>
              <th>Location</th>
              <th>Total Connected IPs</th>
              <th>Last Accessed Dataset</th>
              <th>Last Seen</th>
              <th>Data Catalog</th>
              <th>Action</th>
              <th>Approval</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {loading && users.length === 0 ? (
              <tr>
                <td colSpan={11}>Loading activity...</td>
              </tr>
            ) : null}
            {error ? (
              <tr>
                <td colSpan={11} className="admin-panel__error">{error}</td>
              </tr>
            ) : null}
            {users.map((user) => (
              <tr key={user.user_id}>
                <td>
                  <strong>{user.email}</strong>
                  <span className={user.role === 'admin' ? 'admin-panel__role admin-panel__role--admin' : 'admin-panel__role'}>
                    {user.role}
                    {user.approval_status === 'pending' ? ` · pending ${user.requested_role || 'user'}` : ''}
                  </span>
                </td>
                <td>
                  <span
                    className={
                      user.status === 'Active'
                        ? 'admin-status admin-status--active'
                        : 'admin-status admin-status--offline'
                    }
                  >
                    <span aria-hidden>{user.status === 'Active' ? '●' : '●'}</span>
                    {user.status}
                  </span>
                </td>
                <td>
                  <strong>{user.current_ip || '--'}</strong>
                  <span className="admin-panel__role">{user.device_label || 'Unknown device'}</span>
                </td>
                <td
                  className={user.location ? 'admin-panel__location' : 'admin-panel__location admin-panel__location--missing'}
                  title={user.location_accuracy_m ? `Accuracy ${user.location_accuracy_m} m` : undefined}
                >
                  {user.location || 'Location required'}
                </td>
                <td>{user.unique_ip_count}</td>
                <td className="admin-panel__endpoint" title={user.last_accessed_data || undefined}>
                  {formatAccessLabel(user.last_accessed_data)}
                </td>
                <td>{formatLastSeen(user.last_seen_at)}</td>
                <td>
                  <button
                    type="button"
                    className={user.can_access_catalog === false ? 'admin-panel__action admin-panel__action--danger' : 'admin-panel__action admin-panel__action--approve'}
                    onClick={() => void runUserAction(() => setAdminUserCatalogAccess(user.user_id, user.can_access_catalog === false))}
                  >
                    {user.can_access_catalog === false ? 'Hidden' : 'Visible'}
                  </button>
                </td>
                <td>
                  <button
                    type="button"
                    className="admin-panel__action"
                    onClick={() => {
                      setManagedUser({ userId: user.user_id, email: user.email })
                      setSelectedProject(null)
                      setActiveId('projects')
                    }}
                  >
                    Manage User Workspace
                  </button>
                </td>
                <td>
                  <div className="admin-panel__actions">
                    {user.approval_status !== 'approved' ? (
                      <>
                        <button
                          type="button"
                          className="admin-panel__action admin-panel__action--approve"
                          onClick={() => void runUserAction(() => approveAdminUser(
                            user.user_id,
                            user.requested_role === 'admin' ? 'admin' : 'user',
                          ))}
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          className="admin-panel__action admin-panel__action--ghost"
                          onClick={() => void runUserAction(() => disapproveAdminUser(user.user_id))}
                        >
                          Disapprove
                        </button>
                      </>
                    ) : null}
                    <button
                      type="button"
                      className="admin-panel__action admin-panel__action--danger"
                      onClick={async () => {
                        const ok = await modal.confirm('Soft delete user', `Delete ${user.email}? Old activity records will remain.`)
                        if (!ok) return
                        void runUserAction(() => deleteAdminUser(user.user_id))
                      }}
                    >
                      Soft Delete
                    </button>
                    <button
                      type="button"
                      className="admin-panel__action admin-panel__action--danger admin-panel__action--advanced"
                      onClick={async () => {
                        const ok = await modal.confirm(
                          'Advanced delete user',
                          `Advanced delete ${user.email}? This removes the user, projects, sessions, activity records, and Project_Data folders.`,
                        )
                        if (!ok) return
                        const typed = await modal.prompt('Confirm advanced delete', 'Type DELETE to confirm advanced delete')
                        if (typed !== 'DELETE') return
                        void runUserAction(() => advancedDeleteAdminUser(user.user_id))
                      }}
                    >
                      Advanced Delete
                    </button>
                  </div>
                </td>
                <td>
                  <div className="admin-panel__actions">
                    <button
                      type="button"
                      className={user.role === 'user' ? 'admin-panel__action admin-panel__action--approve' : 'admin-panel__action admin-panel__action--ghost'}
                      onClick={() => void runUserAction(() => assignAdminUserRole(user.user_id, 'user'))}
                    >
                      User
                    </button>
                    <button
                      type="button"
                      className={user.role === 'admin' ? 'admin-panel__action admin-panel__action--danger' : 'admin-panel__action admin-panel__action--ghost'}
                      onClick={() => void runUserAction(() => assignAdminUserRole(user.user_id, 'admin'))}
                    >
                      Admin
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {!loading && !error && users.length === 0 ? (
              <tr>
                <td colSpan={10}>No users found.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  )
}
