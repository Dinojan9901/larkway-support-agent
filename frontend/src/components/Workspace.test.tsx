import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { customer, fakeApi, opsUser } from '../test/fakeApi'
import { renderWith } from '../test/render'
import { Workspace } from './Workspace'

describe('Workspace', () => {
  it('gives customers chat only — no operations console', async () => {
    const api = fakeApi()
    renderWith(<Workspace user={customer} />, { api })
    expect(await screen.findByText('openai/gpt-oss-120b')).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Chat'])
    expect(api.signals).not.toHaveBeenCalled()
  })

  it('gives the ops role an operations tab, reachable with the arrow keys', async () => {
    const api = fakeApi()
    renderWith(<Workspace user={opsUser} />, { api, auth: { user: opsUser } })
    const chatTab = screen.getByRole('tab', { name: 'Chat' })
    expect(chatTab).toHaveAttribute('aria-selected', 'true')

    chatTab.focus()
    await userEvent.keyboard('{ArrowRight}')
    const opsTab = screen.getByRole('tab', { name: 'Operations signals' })
    expect(opsTab).toHaveFocus()
    expect(opsTab).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByRole('heading', { name: 'What needs attention' })).toBeInTheDocument()
    expect(api.signals).toHaveBeenCalledOnce()
  })

  it('marks staff-only tools as unavailable to customers', async () => {
    renderWith(<Workspace user={customer} />)
    expect(await screen.findByText('staff only')).toBeInTheDocument()
  })

  it('puts a suggestion into the message box', async () => {
    renderWith(<Workspace user={customer} />)
    await userEvent.click(screen.getByRole('button', { name: /Can I cancel SHP-5001/ }))
    expect(screen.getByLabelText('Message')).toHaveValue('Can I cancel SHP-5001 without a cancellation fee? Explain why.')
  })

  it('signs out', async () => {
    const { auth } = renderWith(<Workspace user={customer} />)
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(auth.signOut).toHaveBeenCalled()
  })
})
