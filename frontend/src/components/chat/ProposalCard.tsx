import { useEffect, useId, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { useApi } from '../../api/context'
import type { Action } from '../../api/types'

type Phase =
  | { kind: 'pending' }
  | { kind: 'running' }
  | { kind: 'done'; reference: string }
  | { kind: 'blocked'; detail: string }
  | { kind: 'discarded' }

/**
 * The confirmation gate, client side. Nothing runs until the person clicks
 * "Confirm and run" — and the server refuses execution without that same
 * confirm call, so this is a convenience over the guarantee, not the guarantee.
 */
export function ProposalCard({ action }: { action: Action }) {
  const api = useApi()
  const titleId = useId()
  const [phase, setPhase] = useState<Phase>({ kind: 'pending' })
  const outcome = useRef<HTMLOutputElement>(null)
  const settled = phase.kind === 'done' || phase.kind === 'blocked' || phase.kind === 'discarded'

  // Move focus to the result so keyboard and screen-reader users hear what happened.
  useEffect(() => {
    if (settled) outcome.current?.focus()
  }, [settled])

  async function confirm() {
    setPhase({ kind: 'running' })
    try {
      const res = await api.confirmAction(action.action_id)
      setPhase({ kind: 'done', reference: res.action.result?.reference ?? action.action_id })
    } catch (err) {
      setPhase({ kind: 'blocked', detail: err instanceof ApiError ? err.detail : 'The action could not run.' })
    }
  }

  async function discard() {
    setPhase({ kind: 'running' })
    try {
      await api.cancelAction(action.action_id)
      setPhase({ kind: 'discarded' })
    } catch (err) {
      setPhase({ kind: 'blocked', detail: err instanceof ApiError ? err.detail : 'Could not discard the action.' })
    }
  }

  const message =
    phase.kind === 'done'
      ? `Executed — reference ${phase.reference}`
      : phase.kind === 'blocked'
        ? `Blocked: ${phase.detail}`
        : phase.kind === 'discarded'
          ? 'Discarded — nothing was changed.'
          : ''

  return (
    <section className={`proposal${settled ? ' settled' : ''}`} aria-labelledby={titleId}>
      <div className="proposal-head">
        {!settled && <span className="proposal-tag">Needs your confirmation</span>}
        <h3 id={titleId} className="proposal-title">
          {action.label}
        </h3>
      </div>
      <pre>{action.preview}</pre>
      {action.warnings?.length > 0 && (
        <ul className="warn-list">
          {action.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {!settled && (
        <div className="proposal-actions">
          <button type="button" className="btn confirm" onClick={confirm} disabled={phase.kind === 'running'}>
            {phase.kind === 'running' ? 'Working…' : 'Confirm and run'}
          </button>
          <button type="button" className="btn cancel" onClick={discard} disabled={phase.kind === 'running'}>
            Discard
          </button>
        </div>
      )}
      <output
        ref={outcome}
        tabIndex={-1}
        className={`outcome ${phase.kind === 'done' ? 'done' : 'void'}`}
        hidden={!settled}
      >
        {message}
      </output>
    </section>
  )
}
