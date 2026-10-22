import { createContext, useContext } from 'react'
import type { User } from '../api/types'

export type SignOutReason = 'expired' | null

export interface AuthState {
  user: User | null
  signIn: (username: string, password: string) => Promise<void>
  signOut: (reason?: SignOutReason) => void
  signOutReason: SignOutReason
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside AuthProvider')
  return auth
}
