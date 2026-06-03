import AuthScreen from '../components/Auth/AuthScreen'
import { useAuthContext } from '../context/AuthContext'

export function AuthPage() {
  const { setUser } = useAuthContext()
  return <AuthScreen onAuthenticated={setUser} />
}

export default AuthPage
