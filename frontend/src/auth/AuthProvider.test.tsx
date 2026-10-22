import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import { useApi } from '../api/context'
import { customer, fakeApi } from '../test/fakeApi'
import { AuthProvider } from './AuthProvider'
import { useAuth } from './context'

function Probe() {
  const { user, signIn, signOutReason } = useAuth()
  const api = useApi()
  return (
    <div>
      <p>user: {user?.username ?? 'none'}</p>
      <p>reason: {signOutReason ?? 'none'}</p>
      <button type="button" onClick={() => void signIn('customer', 'pw')}>
        sign in
      </button>
      <button type="button" onClick={() => void api.me()}>
        call api
      </button>
    </div>
  )
}

function setup() {
  let tokenGetter: () => string | null = () => null
  let unauthorized: () => void = () => {}
  const api = fakeApi({
    login: vi.fn<ApiClient['login']>(async () => ({ access_token: 'tok-1', token_type: 'bearer' as const, expires_in: 1800, user: customer })),
  })
  const factory = (getToken: () => string | null, onUnauthorized: () => void): ApiClient => {
    tokenGetter = getToken
    unauthorized = onUnauthorized
    return api
  }
  render(
    <AuthProvider apiFactory={factory}>
      <Probe />
    </AuthProvider>,
  )
  return { api, token: () => tokenGetter(), expire: () => unauthorized() }
}

describe('AuthProvider', () => {
  it('signs in, hands the token to the API client and keeps it for this tab only', async () => {
    const { token } = setup()
    expect(token()).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'sign in' }))
    expect(screen.getByText('user: customer')).toBeInTheDocument()
    expect(token()).toBe('tok-1')
    expect(JSON.parse(sessionStorage.getItem('larkway.session')!).token).toBe('tok-1')
    expect(localStorage.getItem('larkway.session')).toBeNull()
  })

  it('signs out and explains why when the server rejects the token', async () => {
    const { token, expire } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'sign in' }))
    act(() => expire())
    expect(screen.getByText('user: none')).toBeInTheDocument()
    expect(screen.getByText('reason: expired')).toBeInTheDocument()
    expect(token()).toBeNull()
    expect(sessionStorage.getItem('larkway.session')).toBeNull()
  })

  it('ignores a stored session that has already expired', () => {
    sessionStorage.setItem(
      'larkway.session',
      JSON.stringify({ token: 'old', user: customer, expiresAt: Date.now() - 1000 }),
    )
    setup()
    expect(screen.getByText('user: none')).toBeInTheDocument()
  })
})
