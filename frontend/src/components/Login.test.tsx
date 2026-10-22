import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AuthState } from '../auth/context'
import { renderWith } from '../test/render'
import { Login } from './Login'

describe('Login', () => {
  it('has labelled fields and submits the credentials', async () => {
    const signIn = vi.fn<AuthState['signIn']>(async () => {})
    renderWith(<Login />, { auth: { user: null, signIn } })

    await userEvent.type(screen.getByLabelText('Username'), 'ops ')
    await userEvent.type(screen.getByLabelText('Password'), 'pw')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(signIn).toHaveBeenCalledWith('ops', 'pw')
  })

  it('submits with the Enter key', async () => {
    const signIn = vi.fn<AuthState['signIn']>(async () => {})
    renderWith(<Login />, { auth: { user: null, signIn } })
    await userEvent.type(screen.getByLabelText('Username'), 'customer')
    await userEvent.type(screen.getByLabelText('Password'), 'pw{Enter}')
    expect(signIn).toHaveBeenCalledOnce()
  })

  it('shows a clear error and stays usable when the credentials are wrong', async () => {
    const signIn = vi.fn<AuthState['signIn']>(async () => {
      throw new ApiError(401, 'Incorrect username or password.')
    })
    renderWith(<Login />, { auth: { user: null, signIn } })
    await userEvent.type(screen.getByLabelText('Username'), 'customer')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('don’t match')
    expect(screen.getByLabelText('Password')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled()
  })

  it('disables the button while signing in', async () => {
    let finish: () => void = () => {}
    const signIn = vi.fn<AuthState['signIn']>(() => new Promise<void>((resolve) => (finish = resolve)))
    renderWith(<Login />, { auth: { user: null, signIn } })
    await userEvent.type(screen.getByLabelText('Username'), 'ops')
    await userEvent.type(screen.getByLabelText('Password'), 'pw')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByRole('button', { name: 'Signing in…' })).toBeDisabled()
    finish()
  })

  it('explains an expired session', () => {
    renderWith(<Login />, { auth: { user: null, signOutReason: 'expired' } })
    expect(screen.getByText(/session expired/i)).toBeInTheDocument()
  })
})
