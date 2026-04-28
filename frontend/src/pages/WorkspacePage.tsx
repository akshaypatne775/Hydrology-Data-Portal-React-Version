import { Dashboard } from '../components/Dashboard'
import { WorkspaceProvider } from '../context/WorkspaceContext'
import { useAuthContext } from '../context/AuthContext'

export function WorkspacePage() {
  const { user, setUser } = useAuthContext()
  if (!user) return null
  return (
    <WorkspaceProvider>
      <Dashboard user={user} onLogout={() => setUser(null)} />
    </WorkspaceProvider>
  )
}

export default WorkspacePage
