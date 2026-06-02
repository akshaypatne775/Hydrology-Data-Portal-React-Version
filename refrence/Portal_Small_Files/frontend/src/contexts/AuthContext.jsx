import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  clearAccessToken,
  getAccessToken,
  registerUnauthorizedHandler,
  setAccessToken,
} from '../apiConfig'
import { loginRequest } from '../services/authApi'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const navigate = useNavigate()
  const [token, setToken] = useState(() => getAccessToken())

  const logout = useCallback(() => {
    clearAccessToken()
    setToken('')
    navigate('/login', { replace: true })
  }, [navigate])

  useEffect(() => {
    registerUnauthorizedHandler(() => {
      clearAccessToken()
      setToken('')
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
        navigate('/login', { replace: true })
      }
    })
    return () => registerUnauthorizedHandler(null)
  }, [navigate])

  const login = useCallback(async (username, password) => {
    const accessToken = await loginRequest(username, password)
    setAccessToken(accessToken)
    setToken(accessToken)
    return accessToken
  }, [])

  const value = useMemo(
    () => ({
      token,
      isAuthenticated: Boolean(token),
      login,
      logout,
    }),
    [token, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (ctx == null) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
