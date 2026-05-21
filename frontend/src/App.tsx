import { Suspense, lazy } from 'react'
import StartupLoader from './components/Brand/StartupLoader'
import LocationGate from './components/LocationGate'
import { AuthProvider, useAuthContext } from './context/AuthContext'
import { ModalProvider } from './context/ModalContext'

const AuthPage = lazy(() => import('./pages/AuthPage'))
const AdminAccessPage = lazy(() => import('./pages/AdminAccessPage'))
const WorkspacePage = lazy(() => import('./pages/WorkspacePage'))

function AppRoot() {
  const { loading, user } = useAuthContext()
  const isAdminRoute = window.location.pathname.replace(/\/+$/, '') === '/admin'

  if (loading) return <StartupLoader />
  return (
    <Suspense fallback={<StartupLoader />}>
      {isAdminRoute && !user ? (
        <AdminAccessPage />
      ) : !user ? (
        <AuthPage />
      ) : (
        <LocationGate>
          <WorkspacePage />
        </LocationGate>
      )}
    </Suspense>
  )
}

function App() {
  return (
    <AuthProvider>
      <ModalProvider>
        <AppRoot />
      </ModalProvider>
    </AuthProvider>
  )
}

export default App
