import { Suspense, lazy } from 'react'
import StartupLoader from './components/Brand/StartupLoader'
import { AuthProvider, useAuthContext } from './context/AuthContext'

const AuthPage = lazy(() => import('./pages/AuthPage'))
const WorkspacePage = lazy(() => import('./pages/WorkspacePage'))

function AppRoot() {
  const { loading, user } = useAuthContext()

  if (loading) return <StartupLoader />
  return (
    <Suspense fallback={<StartupLoader />}>
      {!user ? <AuthPage /> : <WorkspacePage />}
    </Suspense>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppRoot />
    </AuthProvider>
  )
}

export default App
