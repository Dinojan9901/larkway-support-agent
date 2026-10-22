import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/context'
import { Login } from './components/Login'
import { Workspace } from './components/Workspace'

function Gate() {
  const { user } = useAuth()
  // Keyed by user so signing in as someone else starts from a clean slate.
  return user ? <Workspace key={user.username} user={user} /> : <Login />
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
