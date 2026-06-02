import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useAuth } from '../contexts/AuthContext'
import './LoginPage.css'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { isAuthenticated, login } = useAuth()
  const from = location.state?.from?.pathname || '/dashboard'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (isAuthenticated) {
    return <Navigate to={from} replace />
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      await login(username.trim(), password)
      toast.success('Signed in securely.')
      navigate(from, { replace: true })
    } catch (err) {
      toast.error(err?.message || 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-page__ambient" aria-hidden />
      <div className="login-page__inner">
        <header className="login-page__brand">
          <div className="login-page__logo-mark">
            <i className="fas fa-shield-alt" aria-hidden />
          </div>
          <div className="login-page__brand-text">
            <span className="login-page__brand-name">Droid Survair</span>
            <span className="login-page__brand-tag">Acquisition Hub</span>
          </div>
        </header>

        <div className="login-card">
          <div className="login-card__accent" aria-hidden />
          <h1 className="login-card__title">Secure sign in</h1>
          <p className="login-card__subtitle">
            Enter your credentials to access the dashboard. Sessions are encrypted and time-limited.
          </p>

          <form className="login-form" onSubmit={handleSubmit} noValidate>
            <div className="login-field">
              <label htmlFor="login-username">Username</label>
              <input
                id="login-username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                placeholder="Enter username"
              />
            </div>
            <div className="login-field">
              <label htmlFor="login-password">Password</label>
              <input
                id="login-password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="Enter password"
              />
            </div>
            <button type="submit" className="login-submit" disabled={submitting}>
              {submitting ? (
                <>
                  <i className="fas fa-circle-notch fa-spin login-submit__icon" aria-hidden />
                  Verifying…
                </>
              ) : (
                <>
                  <i className="fas fa-arrow-right-to-bracket login-submit__icon" aria-hidden />
                  Sign in
                </>
              )}
            </button>
          </form>

          <p className="login-card__footer">
            <i className="fas fa-lock login-card__footer-icon" aria-hidden />
            Protected environment. Unauthorized access is prohibited and monitored.
          </p>
        </div>

        <p className="login-page__legal">© Droid Mining Solutions · Authorized use only</p>
      </div>
    </div>
  )
}
