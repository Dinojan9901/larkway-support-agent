import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../../api/client'
import { action, fakeApi } from '../../test/fakeApi'
import { renderWith } from '../../test/render'
import { ProposalCard } from './ProposalCard'

describe('ProposalCard — the confirmation gate', () => {
  it('shows the preview and runs nothing until the person clicks', () => {
    const api = fakeApi()
    renderWith(<ProposalCard action={action} />, { api })
    expect(screen.getByRole('heading', { name: 'Cancel order SHP-5001' })).toBeInTheDocument()
    expect(screen.getByText(/No cancellation fee/)).toBeInTheDocument()
    expect(api.confirmAction).not.toHaveBeenCalled()
    expect(api.cancelAction).not.toHaveBeenCalled()
  })

  it('runs the action once on an explicit confirm and reports the reference', async () => {
    const api = fakeApi()
    renderWith(<ProposalCard action={action} />, { api })
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))

    expect(await screen.findByText('Executed — reference REF-42')).toHaveFocus()
    expect(api.confirmAction).toHaveBeenCalledExactlyOnceWith('ACT-1234')
    expect(screen.queryByRole('button', { name: 'Confirm and run' })).not.toBeInTheDocument()
  })

  it('can be confirmed from the keyboard', async () => {
    const api = fakeApi()
    renderWith(<ProposalCard action={action} />, { api })
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Confirm and run' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(api.confirmAction).toHaveBeenCalledOnce())
  })

  it('discards without running anything', async () => {
    const api = fakeApi()
    renderWith(<ProposalCard action={action} />, { api })
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(await screen.findByText('Discarded — nothing was changed.')).toBeInTheDocument()
    expect(api.confirmAction).not.toHaveBeenCalled()
  })

  it('shows why the server blocked the action', async () => {
    const api = fakeApi({
      confirmAction: vi.fn<ApiClient['confirmAction']>(async () => {
        throw new ApiError(403, 'ACT-1234 was prepared for staff-kavya.')
      }),
    })
    renderWith(<ProposalCard action={action} />, { api })
    await userEvent.click(screen.getByRole('button', { name: 'Confirm and run' }))
    expect(await screen.findByText(/Blocked: ACT-1234 was prepared for staff-kavya/)).toBeInTheDocument()
  })
})
