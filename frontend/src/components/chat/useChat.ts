import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { useApi } from '../../api/context'
import type { Action, ChatEvent, ToolFamily, TrustPanel } from '../../api/types'

export interface ToolStep {
  id: string
  label: string
  family: ToolFamily
  detail: string
  status: 'running' | 'ok' | 'failed'
  summary?: string
}

export interface UserTurn {
  kind: 'user'
  id: string
  text: string
}

export interface AgentTurn {
  kind: 'agent'
  id: string
  steps: ToolStep[]
  text: string
  proposals: Action[]
  trust: TrustPanel | null
  errors: string[]
  finished: boolean
}

export type Turn = UserTurn | AgentTurn

const newId = () => crypto.randomUUID()

export function emptyAgentTurn(id: string = newId()): AgentTurn {
  return { kind: 'agent', id, steps: [], text: '', proposals: [], trust: null, errors: [], finished: false }
}

/** Fold one streamed event into the answer being built. Pure, so it is unit-tested. */
export function applyEvent(turn: AgentTurn, event: ChatEvent): AgentTurn {
  switch (event.type) {
    case 'tool_start':
      return {
        ...turn,
        steps: [
          ...turn.steps,
          { id: event.tool_use_id, label: event.label, family: event.family, detail: event.detail, status: 'running' },
        ],
      }
    case 'tool_end':
      return {
        ...turn,
        steps: turn.steps.map((s) =>
          s.id === event.tool_use_id ? { ...s, status: event.ok ? 'ok' : 'failed', summary: event.summary } : s,
        ),
      }
    case 'text':
      return { ...turn, text: turn.text + event.text }
    case 'proposal':
      return { ...turn, proposals: [...turn.proposals, event.action] }
    case 'trust':
      return { ...turn, trust: event.trust }
    case 'error':
      return { ...turn, errors: [...turn.errors, event.message], finished: true }
    case 'done':
      return { ...turn, finished: true }
    default:
      return turn
  }
}

export function useChat() {
  const api = useApi()
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const sessionId = useRef(newId())
  const abort = useRef<AbortController | null>(null)

  useEffect(() => () => abort.current?.abort(), [])

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || abort.current) return
      const answerId = newId()
      setTurns((prev) => [...prev, { kind: 'user', id: newId(), text: message }, emptyAgentTurn(answerId)])
      setBusy(true)
      setStatus('Working on your question…')

      const controller = new AbortController()
      abort.current = controller
      const update = (fn: (turn: AgentTurn) => AgentTurn) =>
        setTurns((prev) => prev.map((t) => (t.kind === 'agent' && t.id === answerId ? fn(t) : t)))
      let proposals = 0
      let failed = false

      try {
        await api.streamChat(
          message,
          sessionId.current,
          (event) => {
            if (event.type === 'tool_start') setStatus(`Running: ${event.label}`)
            if (event.type === 'proposal') proposals += 1
            if (event.type === 'error') {
              failed = true
              setStatus(`Error: ${event.message}`)
            }
            update((turn) => applyEvent(turn, event))
          },
          controller.signal,
        )
        update((turn) => ({ ...turn, finished: true }))
        // Keep the confirmation prompt in the final announcement, or it is lost.
        if (!failed) {
          setStatus(
            proposals
              ? 'Answer ready. An action is waiting for your confirmation.'
              : 'Answer ready.',
          )
        }
      } catch (err) {
        if (controller.signal.aborted) return
        const detail = err instanceof ApiError ? err.detail : 'The answer was interrupted.'
        update((turn) => ({ ...turn, errors: [...turn.errors, detail], finished: true }))
        setStatus(`Error: ${detail}`)
      } finally {
        if (abort.current === controller) abort.current = null
        setBusy(false)
      }
    },
    [api],
  )

  const reset = useCallback(async () => {
    abort.current?.abort()
    abort.current = null
    const old = sessionId.current
    sessionId.current = newId()
    setTurns([])
    setBusy(false)
    setStatus('Conversation cleared.')
    try {
      await api.resetChat(old)
    } catch {
      // the server drops idle sessions anyway; a failed reset changes nothing for the user
    }
  }, [api])

  return { turns, busy, status, send, reset }
}

export type ChatState = ReturnType<typeof useChat>
