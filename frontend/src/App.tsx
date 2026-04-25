import { useEffect, useState } from 'react'
import { Dashboard } from './components/Dashboard'
import AuthScreen from './components/Auth/AuthScreen'
import StartupLoader from './components/Brand/StartupLoader'
import { apiJson } from './lib/apiBase'

function App() {
  const [loading, setLoading] = useState(true)
  const [user, setUser] = useState<{ id: number; email: string } | null>(null)

  useEffect(() => {
    let mounted = true
    void apiJson<{ id: number; email: string }>('/api/auth/me')
      .then((me) => {
        if (mounted) setUser(me)
      })
      .catch(() => {
        if (mounted) setUser(null)
      })
      .finally(() => {
        if (mounted) setLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [])

  if (loading) return <StartupLoader />
  if (!user) return <AuthScreen onAuthenticated={setUser} />
  return <Dashboard user={user} onLogout={() => setUser(null)} />
}

export default App
