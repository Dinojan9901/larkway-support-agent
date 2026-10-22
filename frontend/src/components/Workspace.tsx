import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ApiError } from '../api/client'
import { useApi } from '../api/context'
import type { Bootstrap, User } from '../api/types'
import { useAuth } from '../auth/context'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { ChatView } from './chat/ChatView'
import { useChat } from './chat/useChat'
import { OpsView } from './ops/OpsView'
import { Sidebar } from './Sidebar'

type View = 'chat' | 'ops'

function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark" aria-hidden="true">
        LW
      </div>
      <div>
        <div className="brand-name">Larkway</div>
        <div className="brand-sub">Support Agent</div>
      </div>
    </div>
  )
}

export function Workspace({ user }: { user: User }) {
  const api = useApi()
  const { signOut } = useAuth()
  const chat = useChat()
  const wide = useMediaQuery('(min-width: 1024px)')
  const ops = user.role === 'ops'

  const [boot, setBoot] = useState<Bootstrap | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [view, setView] = useState<View>('chat')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const menuButton = useRef<HTMLButtonElement>(null)
  const drawer = useRef<HTMLElement>(null)
  const tabRefs = useRef<Record<View, HTMLButtonElement | null>>({ chat: null, ops: null })

  const fetchBoot = useCallback(
    () =>
      api.bootstrap().then(
        (data) => ({ data, error: null }),
        (err) => ({ data: null, error: err instanceof ApiError ? err.detail : 'Could not load app details.' }),
      ),
    [api],
  )

  useEffect(() => {
    let live = true
    void fetchBoot().then(({ data, error }) => {
      if (!live) return
      setBoot(data)
      setBootError(error)
    })
    return () => {
      live = false
    }
  }, [fetchBoot])

  const loadBoot = () => {
    setBootError(null)
    void fetchBoot().then(({ data, error }) => {
      setBoot(data)
      setBootError(error)
    })
  }

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false)
    menuButton.current?.focus()
  }, [])

  // Drawer: focus moves in when it opens; Escape closes it.
  useEffect(() => {
    if (!drawerOpen || wide) return
    drawer.current?.querySelector<HTMLElement>('button, [href]')?.focus()
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') closeDrawer()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawerOpen, wide, closeDrawer])

  const views: View[] = ops ? ['chat', 'ops'] : ['chat']
  const labels: Record<View, string> = { chat: 'Chat', ops: 'Operations signals' }

  function onTabKey(e: KeyboardEvent<HTMLButtonElement>) {
    const i = views.indexOf(view)
    const next =
      e.key === 'ArrowRight' ? views[(i + 1) % views.length]
      : e.key === 'ArrowLeft' ? views[(i - 1 + views.length) % views.length]
      : e.key === 'Home' ? views[0]
      : e.key === 'End' ? views[views.length - 1]
      : null
    if (!next) return
    e.preventDefault()
    setView(next)
    tabRefs.current[next]?.focus()
  }

  function applySuggestion(text: string) {
    setView('chat')
    setDraft(text)
    if (!wide) setDrawerOpen(false)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const sidebarHidden = !wide && !drawerOpen

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <h1 className="sr-only">Larkway support agent</h1>

      <aside
        id="sidebar"
        ref={drawer}
        className={`sidebar${drawerOpen ? ' open' : ''}`}
        aria-label="Account, suggestions and tools"
        inert={sidebarHidden}
      >
        <div className="sidebar-top">
          <Brand />
          {!wide && (
            <button type="button" className="icon-btn" onClick={closeDrawer} aria-label="Close menu">
              ✕
            </button>
          )}
        </div>
        <Sidebar
          user={user}
          boot={boot}
          bootError={bootError}
          onRetryBoot={() => void loadBoot()}
          onSuggestion={applySuggestion}
          onReset={() => {
            void chat.reset()
            setDraft('')
            if (!wide) closeDrawer()
          }}
          onSignOut={() => signOut()}
        />
      </aside>

      {drawerOpen && !wide && (
        <button type="button" className="scrim" aria-label="Close menu" tabIndex={-1} onClick={closeDrawer} />
      )}

      <div className="main">
        <header className="topbar">
          {!wide && (
            <button
              ref={menuButton}
              type="button"
              className="icon-btn menu-btn"
              aria-controls="sidebar"
              aria-expanded={drawerOpen}
              onClick={() => setDrawerOpen(true)}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
                <path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <span className="sr-only">Menu</span>
            </button>
          )}
          <div className="tabs" role="tablist" aria-label="Views">
            {views.map((v) => (
              <button
                key={v}
                ref={(el) => {
                  tabRefs.current[v] = el
                }}
                id={`tab-${v}`}
                type="button"
                role="tab"
                className={`tab${view === v ? ' active' : ''}`}
                aria-selected={view === v}
                aria-controls={`panel-${v}`}
                tabIndex={view === v ? 0 : -1}
                onClick={() => setView(v)}
                onKeyDown={onTabKey}
              >
                {labels[v]}
              </button>
            ))}
          </div>
          <span className="spacer" />
          <span className="badge" title={boot ? `${boot.provider} / ${boot.model}` : undefined}>
            {boot ? (boot.chat_enabled ? boot.model : 'chat disabled') : bootError ? 'offline' : '…'}
          </span>
        </header>

        <main id="main-content" className="view-area" tabIndex={-1}>
          {bootError && (
            <div className="banner" role="alert">
              {bootError}{' '}
              <button type="button" className="link-btn" onClick={() => void loadBoot()}>
                Retry
              </button>
            </div>
          )}
          <section
            id="panel-chat"
            role="tabpanel"
            aria-labelledby="tab-chat"
            className="view"
            hidden={view !== 'chat'}
          >
            <ChatView chat={chat} user={user} boot={boot} draft={draft} onDraftChange={setDraft} inputRef={inputRef} />
          </section>
          {ops && (
            <section id="panel-ops" role="tabpanel" aria-labelledby="tab-ops" className="view" hidden={view !== 'ops'}>
              {view === 'ops' && <OpsView />}
            </section>
          )}
        </main>
      </div>
    </div>
  )
}
