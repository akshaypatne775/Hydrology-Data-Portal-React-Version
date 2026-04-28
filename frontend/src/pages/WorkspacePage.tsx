import { Dashboard } from '../components/Dashboard'
import UploadProgressWidget from '../components/Uploads/UploadProgressWidget'
import { UploadProvider } from '../context/UploadContext'
import { WorkspaceProvider } from '../context/WorkspaceContext'
import { useAuthContext } from '../context/AuthContext'

export function WorkspacePage() {
  const { user, setUser } = useAuthContext()
  if (!user) return null
  return (
    <UploadProvider>
      <WorkspaceProvider>
        <Dashboard user={user} onLogout={() => setUser(null)} />
        <UploadProgressWidget />
      </WorkspaceProvider>
    </UploadProvider>
  )
}

export default WorkspacePage
