import type { User } from '../api/types'

// sessionStorage, not localStorage: the token dies with the tab, and it is
// short-lived anyway. The server re-checks it on every request.
const KEY = 'larkway.session'

export interface Session {
  token: string
  user: User
  expiresAt: number // epoch ms
}

export function loadSession(now = Date.now()): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const session = JSON.parse(raw) as Session
    if (!session.token || !session.user || session.expiresAt <= now) {
      sessionStorage.removeItem(KEY)
      return null
    }
    return session
  } catch {
    return null
  }
}

export function saveSession(session: Session): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    // storage unavailable (private mode): the session just won't survive a reload
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // ignore
  }
}
