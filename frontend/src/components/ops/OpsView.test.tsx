import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../../api/client'
import { fakeApi, opsUser, signals } from '../../test/fakeApi'
import { renderWith } from '../../test/render'
import { OpsView } from './OpsView'

describe('OpsView', () => {
  it('shows a loading state, then the summary and the signals', async () => {
    renderWith(<OpsView />, { auth: { user: opsUser } })
    expect(screen.getByText('Loading signals…')).toBeInTheDocument()

    expect(await screen.findByRole('heading', { name: 'CASE-301 (P1) BREACHED — Halcyon Foods' })).toBeInTheDocument()
    const summary = screen.getByRole('list', { name: 'Summary' })
    expect(summary).toHaveTextContent('2signals')
    expect(summary).toHaveTextContent('1critical')
    expect(screen.getByText(/Suggested next step:/).closest('p')).toHaveTextContent('Suggested next step: create escalation — ticket id CASE-301')
    expect(screen.getByText('overdue by: 10 min')).toBeInTheDocument()
  })

  it('explains a 403 instead of offering a retry', async () => {
    const api = fakeApi({
      signals: vi.fn<ApiClient['signals']>(async () => {
        throw new ApiError(403, 'This needs the ops role.')
      }),
    })
    renderWith(<OpsView />, { api })
    expect(await screen.findByRole('alert')).toHaveTextContent('needs the ops role')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('recovers from a failure with Try again', async () => {
    const signalsFn = vi
      .fn<ApiClient['signals']>()
      .mockRejectedValueOnce(new ApiError(500, 'Server error'))
      .mockResolvedValueOnce(signals)
    renderWith(<OpsView />, { api: fakeApi({ signals: signalsFn }) })

    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: /CASE-301/ })).toBeInTheDocument()
    expect(signalsFn).toHaveBeenCalledTimes(2)
  })
})
