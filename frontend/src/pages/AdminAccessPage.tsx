import { useState, type FormEvent } from 'react'
import { requestAdminAccess } from '../services/authService'
import '../components/Auth/AuthScreen.css'

export default function AdminAccessPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setMessage(null)
    try {
      await requestAdminAccess(email, password)
      setMessage('Admin approval request sent. The owner will review it and you will be notified after approval.')
      setEmail('')
      setPassword('')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Admin request failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-root">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-header">
          <p className="auth-kicker">Droid Cloud Admin</p>
          <h1>Request Admin Access</h1>
          <p>Admin access requires owner approval before login is enabled.</p>
        </div>
        <label className="auth-field">
          <span>Email</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" />
        </label>
        <label className="auth-field">
          <span>Password</span>
          <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" />
        </label>
        {message ? <p className="auth-info">{message}</p> : null}
        <button type="submit" className="auth-submit" disabled={submitting}>
          {submitting ? 'Sending...' : 'Send Approval Request'}
        </button>
        <button
          type="button"
          className="auth-tab"
          onClick={() => { window.location.href = '/' }}
        >
          Back to Login
        </button>
      </form>
    </div>
  )
}
