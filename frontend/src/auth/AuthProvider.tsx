import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { createApiClient, type ApiClient } from '../api/client'
import { ApiContext } from '../api/context'
import { AuthContext, type SignOutReason } from './context'
import { clearSession, loadSession, saveSession, type Session } from './session'

interface Props {
  children: ReactNode
  /** Tests inject a fake client; the app builds a real one. */
  apiFactory?: (getToken: () => string | null, onUnauthorized: () => void) => ApiClient
}

/** Holds the bearer token outside React state: the API client reads it per request. */
function createTokenStore(initial: string | null) {
  let token = initial
  return { get: () => token, set: (next: string | null) => void (token = next) }
}

export function AuthProvider({ children, apiFactory }: Props) {
  const [session, setSession] = useState<Session | null>(() => loadSession())
  const [signOutReason, setSignOutReason] = useState<SignOutReason>(null)
  const [tokens] = useState(() => createTokenStore(loadSession()?.token ?? null))

  const signOut = useCallback(
    (reason: SignOutReason = null) => {
      tokens.set(null)
      clearSession()
      setSession(null)
      setSignOutReason(reason)
    },
    [tokens],
  )

  const api = useMemo(() => {
    const onUnauthorized = () => signOut('expired')
    return apiFactory
      ? apiFactory(tokens.get, onUnauthorized)
      : createApiClient({ getToken: tokens.get, onUnauthorized })
  }, [apiFactory, signOut, tokens])

  const signIn = useCallback(
    async (username: string, password: string) => {
      const res = await api.login(username, password)
      const next: Session = {
        token: res.access_token,
        user: res.user,
        expiresAt: Date.now() + res.expires_in * 1000,
      }
      tokens.set(next.token)
      saveSession(next)
      setSignOutReason(null)
      setSession(next)
    },
    [api, tokens],
  )

  // Sign out the moment the token expires, rather than on the next failed request.
  useEffect(() => {
    if (!session) return
    const timer = window.setTimeout(() => signOut('expired'), Math.max(0, session.expiresAt - Date.now()))
    return () => window.clearTimeout(timer)
  }, [session, signOut])

  const value = useMemo(
    () => ({ user: session?.user ?? null, signIn, signOut, signOutReason }),
    [session, signIn, signOut, signOutReason],
  )

  return (
    <ApiContext.Provider value={api}>
      <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
    </ApiContext.Provider>
  )
}
