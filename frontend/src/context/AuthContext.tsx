import {
  createContext,
  useContext,
  useMemo,
  type PropsWithChildren,
} from 'react'
import { useAuthSession } from '../hooks/useAuthSession'
import type { AuthUser } from '../services/authService'

type AuthContextValue = {
  loading: boolean
  user: AuthUser | null
  isAdmin: boolean
  setUser: (user: AuthUser | null) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: PropsWithChildren) {
  const { loading, user, setUser } = useAuthSession()
  const isAdmin = user?.role === 'admin'

  const value = useMemo<AuthContextValue>(
    () => ({ loading, user, isAdmin, setUser }),
    [isAdmin, loading, user, setUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuthContext(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuthContext must be used within AuthProvider')
  }
  return ctx
}
