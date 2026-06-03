import { useEffect, useState } from 'react'
import { getCurrentUser, type AuthUser } from '../services/authService'

export function useAuthSession() {
  const [loading, setLoading] = useState(true)
  const [user, setUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    let mounted = true
    void getCurrentUser()
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

  return { loading, user, setUser }
}
