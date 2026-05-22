import { Suspense, lazy, useEffect, useState } from 'react'
import StartupLoader from './components/Brand/StartupLoader'
import LocationGate from './components/LocationGate'
import { AuthProvider, useAuthContext } from './context/AuthContext'
import { ModalProvider } from './context/ModalContext'

const AuthPage = lazy(() => import('./pages/AuthPage'))
const AdminAccessPage = lazy(() => import('./pages/AdminAccessPage'))
const WorkspacePage = lazy(() => import('./pages/WorkspacePage'))

function useMobileUnsupported() {
  const [blocked, setBlocked] = useState(() => (
    typeof window !== 'undefined'
      ? window.matchMedia('(max-width: 767px)').matches
      : false
  ))

  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)')
    const onChange = () => setBlocked(query.matches)
    onChange()
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  return blocked
}

function MobileUnavailable() {
  return (
    <main className="mobile-unavailable" aria-label="Mobile not available">
      <section className="mobile-unavailable__card">
        <div className="mobile-unavailable__mark" aria-hidden>
          <i className="fa-solid fa-display" />
        </div>
        <p className="mobile-unavailable__eyebrow">Droid Cloud Workspace</p>
        <h1>Not available on mobile</h1>
        <p>
          This portal uses professional 2D/3D GIS viewers and large data controls.
          Please open it on a tablet, laptop, desktop, Mac, or 4K workstation.
        </p>
      </section>
    </main>
  )
}

function AppRoot() {
  const { loading, user } = useAuthContext()
  const isAdminRoute = window.location.pathname.replace(/\/+$/, '') === '/admin'
  const mobileUnsupported = useMobileUnsupported()

  if (loading) return <StartupLoader />
  if (mobileUnsupported) return <MobileUnavailable />
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
