import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ApiClient } from '../../api/client'
import type { ChatEvent } from '../../api/types'
import { action, bootstrap, customer, fakeApi } from '../../test/fakeApi'
import { renderWith } from '../../test/render'
import { ChatView } from './ChatView'
import { applyEvent, emptyAgentTurn, useChat } from './useChat'

function Harness({ draft = '' }: { draft?: string }) {
  const chat = useChat()
  const ref = createRef<HTMLTextAreaElement>()
  return <ChatView chat={chat} user={customer} boot={bootstrap} draft={draft} onDraftChange={() => {}} inputRef={ref} />
}

function StatefulHarness({ initial }: { initial: string }) {
  const chat = useChat()
  const ref = createRef<HTMLTextAreaElement>()
  const [draft, setDraft] = useState(initial)
  return <ChatView chat={chat} user={customer} boot={bootstrap} draft={draft} onDraftChange={setDraft} inputRef={ref} />
}

describe('applyEvent', () => {
  it('builds a turn from streamed events', () => {
    const events: ChatEvent[] = [
      { type: 'tool_start', tool: 'evaluate_cancellation', label: 'Calculating', family: 'calculation', detail: 'SHP-5001', tool_use_id: 't1' },
      { type: 'tool_end', tool: 'evaluate_cancellation', tool_use_id: 't1', ok: true, summary: 'No fee' },
      { type: 'text', text: 'You can ' },
      { type: 'text', text: 'cancel.' },
      { type: 'proposal', action },
      { type: 'done' },
    ]
    const turn = events.reduce(applyEvent, emptyAgentTurn('a'))
    expect(turn.steps).toEqual([expect.objectContaining({ id: 't1', status: 'ok', summary: 'No fee' })])
    expect(turn.text).toBe('You can cancel.')
    expect(turn.proposals).toHaveLength(1)
    expect(turn.finished).toBe(true)
  })

  it('marks a failed tool and records errors', () => {
    let turn = applyEvent(emptyAgentTurn('a'), { type: 'tool_start', tool: 'x', label: 'X', family: 'data', detail: '', tool_use_id: 'q' })
    turn = applyEvent(turn, { type: 'tool_end', tool: 'x', tool_use_id: 'q', ok: false, summary: 'access denied' })
    turn = applyEvent(turn, { type: 'error', message: 'Stopped.' })
    expect(turn.steps[0].status).toBe('failed')
    expect(turn.errors).toEqual(['Stopped.'])
  })
})

describe('ChatView', () => {
  it('greets the customer and scopes the promise to their account', () => {
    renderWith(<Harness />)
    expect(screen.getByRole('heading', { name: 'Hello Rohan' })).toBeInTheDocument()
    expect(screen.getByText(/only ever see CUS-101 data/)).toBeInTheDocument()
  })

  it('streams a turn: tool trace, answer, proposal card and status updates', async () => {
    const streamChat = vi.fn<ApiClient['streamChat']>(async (_m, _s, onEvent) => {
      onEvent({ type: 'tool_start', tool: 'evaluate_cancellation', label: 'Calculating cancellation eligibility and fee', family: 'calculation', detail: 'SHP-5001', tool_use_id: 't1' })
      onEvent({ type: 'tool_end', tool: 'evaluate_cancellation', tool_use_id: 't1', ok: true, summary: 'No fee' })
      onEvent({ type: 'text', text: 'Yes — **no fee**.' })
      onEvent({ type: 'proposal', action })
      onEvent({ type: 'done' })
    })
    const api = fakeApi({ streamChat })
    renderWith(<StatefulHarness initial="Can I cancel SHP-5001?" />, { api })

    await userEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(streamChat).toHaveBeenCalledWith('Can I cancel SHP-5001?', expect.any(String), expect.any(Function), expect.any(AbortSignal))
    expect(await screen.findByText('You said:', { exact: false })).toBeInTheDocument()
    const answer = screen.getByRole('article', { name: 'Assistant answer' })
    expect(within(answer).getByRole('list', { name: 'Tools used' })).toHaveTextContent('Calculating cancellation eligibility and fee')
    expect(within(answer).getByText('no fee').tagName).toBe('STRONG')
    expect(within(answer).getByRole('button', { name: 'Confirm and run' })).toBeInTheDocument()
    expect(api.confirmAction).not.toHaveBeenCalled()
    expect(screen.getByText('Answer ready. An action is waiting for your confirmation.')).toBeInTheDocument()
    expect(screen.getByLabelText('Message')).toHaveValue('')
  })

  it('shows a server error instead of a blank answer', async () => {
    const api = fakeApi({
      streamChat: vi.fn<ApiClient['streamChat']>(async () => {
        throw new ApiError(503, 'Login is not configured on this deployment.')
      }),
    })
    renderWith(<StatefulHarness initial="hello" />, { api })
    await userEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Login is not configured')
  })

  it('does not send an empty message', async () => {
    const api = fakeApi()
    renderWith(<Harness draft="   " />, { api })
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
    await act(async () => {
      screen.getByLabelText('Message').focus()
    })
    await userEvent.keyboard('{Enter}')
    expect(api.streamChat).not.toHaveBeenCalled()
  })

  it('explains when chat is disabled on the deployment', () => {
    const ref = createRef<HTMLTextAreaElement>()
    function Disabled() {
      const chat = useChat()
      return (
        <ChatView
          chat={chat}
          user={customer}
          boot={{ ...bootstrap, chat_enabled: false, chat_disabled_reason: 'PP_BASE_URL is not set' }}
          draft=""
          onDraftChange={() => {}}
          inputRef={ref}
        />
      )
    }
    renderWith(<Disabled />)
    expect(screen.getByText(/PP_BASE_URL is not set, so chat is disabled/)).toBeInTheDocument()
    expect(screen.getByLabelText('Message')).toBeDisabled()
  })
})
