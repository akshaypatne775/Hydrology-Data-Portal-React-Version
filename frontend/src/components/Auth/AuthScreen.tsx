import { useMemo, useState } from 'react'
import { getCurrentUser, login, signup, type AuthUser } from '../../services/authService'
import './AuthScreen.css'

type AuthMode = 'login' | 'signup'

type AuthScreenProps = {
  onAuthenticated: (user: AuthUser) => void
}

export function AuthScreen({ onAuthenticated }: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const title = useMemo(
    () => (mode === 'login' ? 'Welcome to Droid Cloud' : 'Create Droid Cloud account'),
    [mode],
  )

  const submit = async () => {
    setError(null)
    setSubmitting(true)
    try {
      const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/signup'
      if (endpoint.includes('login')) {
        await login(email, password)
      } else {
        await signup(email, password)
      }
      const me = await getCurrentUser()
      onAuthenticated(me)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Authentication failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-root">
      <div className="auth-card">
        <div className="auth-header">
          <p className="auth-kicker">Droid Cloud</p>
          <h1>{title}</h1>
          <p>Login to access your projects and tools.</p>
        </div>
        <div className="auth-tabs">
          <button
            type="button"
            className={mode === 'login' ? 'auth-tab auth-tab--active' : 'auth-tab'}
            onClick={() => setMode('login')}
          >
            Login
          </button>
          <button
            type="button"
            className={mode === 'signup' ? 'auth-tab auth-tab--active' : 'auth-tab'}
            onClick={() => setMode('signup')}
          >
            Sign Up
          </button>
        </div>
        <label className="auth-field">
          <span>Email</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" />
        </label>
        <label className="auth-field">
          <span>Password</span>
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" />
        </label>
        {error ? <p className="auth-error">{error}</p> : null}
        <button type="button" className="auth-submit" onClick={() => void submit()} disabled={submitting}>
          {submitting ? 'Please wait...' : mode === 'login' ? 'Login' : 'Create Account'}
        </button>
      </div>
    </div>
  )
}

export default AuthScreen
