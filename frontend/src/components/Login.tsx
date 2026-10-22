import { useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/context'

export function Login() {
  const { signIn, signOutReason } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(username.trim(), password)
      // On success this screen unmounts; nothing more to do here.
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? 'That username and password don’t match.'
          : err instanceof ApiError
            ? err.detail
            : 'Sign-in failed. Try again.',
      )
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            LW
          </div>
          <div>
            <div className="brand-name">Larkway</div>
            <div className="brand-sub">Support Agent</div>
          </div>
        </div>

        <h1>Sign in</h1>
        <p className="login-lead">
          Customers see their own account. The ops role adds every account and the operations console.
        </p>

        {signOutReason === 'expired' && (
          <output className="notice">Your session expired. Sign in again to continue.</output>
        )}

        <form onSubmit={onSubmit} aria-describedby={error ? 'login-error' : undefined}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            aria-invalid={error ? true : undefined}
          />

          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? true : undefined}
          />

          {error && (
            <p id="login-error" className="form-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="btn primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="login-foot">Demo logins for reviewers are listed in the project README.</p>
      </div>
    </main>
  )
}
