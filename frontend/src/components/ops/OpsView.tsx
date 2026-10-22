import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { useApi } from '../../api/context'
import type { Signal, SignalsResponse } from '../../api/types'

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; forbidden: boolean }
  | { kind: 'ready'; data: SignalsResponse }

const words = (s: string) => s.replace(/_/g, ' ')

export function SignalCard({ signal }: { signal: Signal }) {
  const metrics = Object.entries(signal.metrics ?? {}).filter(
    ([, v]) => v !== null && v !== undefined && v !== '' && !Array.isArray(v) && typeof v !== 'object',
  )
  const action = signal.recommended_action
  const fields = Object.entries(action?.payload ?? {})
    .map(([k, v]) => `${words(k)} ${String(v)}`)
    .join(', ')

  return (
    <article className={`sig ${signal.priority}`} aria-labelledby={`sig-${signal.signal_id}`}>
      <div className="sig-top">
        <span className={`pri ${signal.priority}`}>{signal.priority}</span>
        <span className="cat">{words(signal.category)}</span>
      </div>
      <h3 id={`sig-${signal.signal_id}`} className="sig-title">
        {signal.title}
      </h3>
      <p className="sig-detail">{signal.detail}</p>
      <ul className="sig-meta" aria-label="Evidence and metrics">
        {signal.evidence.map((e) => (
          <li key={e} className="meta-chip">
            {e}
          </li>
        ))}
        {metrics.map(([k, v]) => (
          <li key={k} className="meta-chip">
            {words(k)}: {String(v)}
          </li>
        ))}
      </ul>
      {action && (
        <p className="sig-rec">
          <b>Suggested next step:</b> {words(action.action_type)}
          {fields ? ` — ${fields}` : ''}
        </p>
      )}
    </article>
  )
}

export function OpsView() {
  const api = useApi()
  const [state, setState] = useState<State>({ kind: 'loading' })

  const fetchSignals = useCallback(
    (): Promise<State> =>
      api.signals().then(
        (data): State => ({ kind: 'ready', data }),
        (err): State => ({
          kind: 'error',
          forbidden: err instanceof ApiError && err.status === 403,
          message: err instanceof ApiError ? err.detail : 'Could not load signals.',
        }),
      ),
    [api],
  )

  useEffect(() => {
    let live = true
    void fetchSignals().then((next) => live && setState(next))
    return () => {
      live = false
    }
  }, [fetchSignals])

  const load = () => {
    setState({ kind: 'loading' })
    void fetchSignals().then(setState)
  }

  return (
    <div className="ops-body">
      <div className="ops-inner">
        <div className="ops-head">
          <h2>What needs attention</h2>
          {state.kind !== 'loading' && !(state.kind === 'error' && state.forbidden) && (
            <button type="button" className="ghost-btn compact" onClick={() => void load()}>
              Refresh
            </button>
          )}
        </div>

        {state.kind === 'loading' && (
          <output className="ops-sub">Loading signals…</output>
        )}

        {state.kind === 'error' && (
          <div className="banner" role="alert">
            {state.forbidden ? 'The operations console needs the ops role.' : `Could not load signals: ${state.message}`}
            {!state.forbidden && (
              <button type="button" className="link-btn" onClick={() => void load()}>
                Try again
              </button>
            )}
          </div>
        )}

        {state.kind === 'ready' && (
          <>
            <p className="ops-sub">
              Computed from the full support surface at {state.data.summary.as_of}. Nobody has to ask — these are found
              by sweeping every ticket, order and past resolution.
            </p>
            <ul className="stat-row" aria-label="Summary">
              <li className="stat">
                <span className="stat-value">{state.data.summary.total}</span>
                <span className="stat-label">signals</span>
              </li>
              <li className="stat critical">
                <span className="stat-value">{state.data.summary.by_priority.critical ?? 0}</span>
                <span className="stat-label">critical</span>
              </li>
              <li className="stat high">
                <span className="stat-value">{state.data.summary.by_priority.high ?? 0}</span>
                <span className="stat-label">high</span>
              </li>
              <li className="stat">
                <span className="stat-value">{state.data.summary.accounts_affected.length}</span>
                <span className="stat-label">accounts affected</span>
              </li>
            </ul>
            <ul className="sig-list">
              {state.data.signals.map((s) => (
                <li key={s.signal_id}>
                  <SignalCard signal={s} />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
