import { useEffect, useLayoutEffect, useRef, type FormEvent, type KeyboardEvent, type RefObject } from 'react'
import type { Bootstrap, User } from '../../api/types'
import { Markdown } from '../Markdown'
import { ProposalCard } from './ProposalCard'
import { TrustPanel } from './TrustPanel'
import { hasTrustContent } from './trust'
import type { AgentTurn, ChatState } from './useChat'

interface Props {
  chat: ChatState
  user: User
  boot: Bootstrap | null
  draft: string
  onDraftChange: (value: string) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
}

function ToolTrace({ steps }: { steps: AgentTurn['steps'] }) {
  if (!steps.length) return null
  return (
    <ol className="trace" aria-label="Tools used">
      {steps.map((s) => (
        <li key={s.id} className={`trace-item ${s.status}`}>
          <span className={`dot ${s.family}`} aria-hidden="true" />
          <div className="trace-main">
            <div className="trace-label">
              {s.label}
              <span className="sr-only">
                {s.status === 'running' ? ' (running)' : s.status === 'failed' ? ' (failed)' : ' (done)'}
              </span>
            </div>
            {s.detail && <div className="trace-detail">{s.detail}</div>}
            {s.summary && <div className="trace-result">{s.summary}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}

function AgentAnswer({ turn }: { turn: AgentTurn }) {
  const thinking = !turn.finished && !turn.text && turn.steps.length === 0
  return (
    <article className="turn" aria-label="Assistant answer">
      <div className="answer">
        <ToolTrace steps={turn.steps} />
        {thinking && <p className="thinking-line">Thinking…</p>}
        {turn.text && (
          <div className="answer-text">
            <Markdown text={turn.text} />
          </div>
        )}
        {turn.proposals.map((action) => (
          <ProposalCard key={action.action_id} action={action} />
        ))}
        {hasTrustContent(turn.trust) && <TrustPanel trust={turn.trust} />}
        {turn.errors.map((e, i) => (
          <div key={i} className="banner" role="alert">
            {e}
          </div>
        ))}
      </div>
    </article>
  )
}

function EmptyState({ user, boot }: { user: User; boot: Bootstrap | null }) {
  const ops = user.role === 'ops'
  return (
    <div className="empty">
      <h2>{ops ? `Internal support console — ${user.name}` : `Hello ${user.name.split(' ')[0]}`}</h2>
      <p>
        {ops
          ? 'Ask about any account, or start with what needs attention right now.'
          : `Ask about your orders, your agreement, or Larkway's policies. You'll only ever see ${user.account_id} data.`}
      </p>
      <p>Every answer shows the tools it used and the sources it relied on.</p>
      {boot && !boot.chat_enabled && (
        <p className="banner" role="note">
          {boot.chat_disabled_reason ?? 'No model is configured'}, so chat is disabled. The operations console still works.
        </p>
      )}
    </div>
  )
}

export function ChatView({ chat, user, boot, draft, onDraftChange, inputRef }: Props) {
  const transcript = useRef<HTMLElement>(null)
  const chatDisabled = boot !== null && !boot.chat_enabled

  useEffect(() => {
    const el = transcript.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chat.turns])

  // Grow the textarea with its content, up to a cap.
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }, [draft, inputRef])

  function submit(e?: FormEvent) {
    e?.preventDefault()
    if (chat.busy || chatDisabled || !draft.trim()) return
    void chat.send(draft)
    onDraftChange('')
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="chat">
      {/* A scrolling region must be keyboard-focusable so a long answer can be scrolled
          without a mouse (WCAG 2.1.1; axe: scrollable-region-focusable). */}
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
      <section ref={transcript} className="transcript" aria-label="Conversation" tabIndex={0}>
        {chat.turns.length === 0 ? (
          <EmptyState user={user} boot={boot} />
        ) : (
          chat.turns.map((turn) =>
            turn.kind === 'user' ? (
              <div key={turn.id} className="turn user">
                <p className="bubble">
                  <span className="sr-only">You said: </span>
                  {turn.text}
                </p>
              </div>
            ) : (
              <AgentAnswer key={turn.id} turn={turn} />
            ),
          )
        )}
      </section>

      <output className="sr-only" aria-live="polite">
        {chat.status}
      </output>

      <form className="composer" onSubmit={submit}>
        <label htmlFor="composer-input" className="sr-only">
          Message
        </label>
        <textarea
          id="composer-input"
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={chatDisabled ? 'Chat is disabled on this deployment' : 'Ask about an order, a policy, an SLA…'}
          disabled={chatDisabled}
          aria-describedby="composer-hint"
          autoComplete="off"
        />
        <span id="composer-hint" className="sr-only">
          Press Enter to send, Shift and Enter for a new line.
        </span>
        <button
          type="submit"
          className="send-btn"
          disabled={chat.busy || chatDisabled || !draft.trim()}
          aria-label={chat.busy ? 'Sending' : 'Send message'}
        >
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
            <path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z" fill="currentColor" />
          </svg>
        </button>
      </form>
    </div>
  )
}
