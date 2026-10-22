import type { Bootstrap, User } from '../api/types'
import { SUGGESTIONS } from '../suggestions'

interface Props {
  user: User
  boot: Bootstrap | null
  bootError: string | null
  onRetryBoot: () => void
  onSuggestion: (text: string) => void
  onReset: () => void
  onSignOut: () => void
}

export function Sidebar({ user, boot, bootError, onRetryBoot, onSuggestion, onReset, onSignOut }: Props) {
  const ops = user.role === 'ops'
  return (
    <>
      <div className="panel">
        <h2 className="panel-title">Signed in as</h2>
        <div className="user-card">
          <div className="user-name">{user.name}</div>
          <div className="user-role">{ops ? 'Support manager · ops role' : 'Customer'}</div>
          <div className="chips">
            {user.account_id && <span className="chip">{user.account_id}</span>}
            {ops && <span className="chip internal">all accounts</span>}
            {user.labels.map((l) => (
              <span key={l} className="chip">
                {l}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="panel">
        <h2 className="panel-title">Try asking</h2>
        <ul className="suggestions">
          {SUGGESTIONS[user.role].map((text) => (
            <li key={text}>
              <button type="button" className="suggestion" onClick={() => onSuggestion(text)}>
                {text}
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="panel grow">
        <h2 className="panel-title">Agent tools</h2>
        {bootError ? (
          <p className="dataset-note">
            Couldn't load app details.{' '}
            <button type="button" className="link-btn" onClick={onRetryBoot}>
              Retry
            </button>
          </p>
        ) : (
          <ul className="tool-list">
            {(boot?.tools ?? []).map((t) => {
              const available = !t.internal_only || ops
              return (
                <li key={t.name} className={`tool-row${available ? '' : ' dim'}`} title={available ? t.label : 'Staff only'}>
                  <span className={`dot ${t.family}`} aria-hidden="true" />
                  <span>{t.name}</span>
                  {!available && <span className="staff-tag">staff only</span>}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="panel">
        {boot && (
          <p className="dataset-note">
            Snapshot {boot.dataset.snapshot_at} · {boot.dataset.accounts} accounts · {boot.dataset.orders} orders ·{' '}
            {boot.dataset.tickets} tickets · {boot.policy_pack_ok ? 'policy pack verified' : 'POLICY PACK MISMATCH'}
          </p>
        )}
        <button type="button" className="ghost-btn" onClick={onReset}>
          Clear conversation
        </button>
        <button type="button" className="ghost-btn" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </>
  )
}
