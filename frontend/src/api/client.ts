import { createSSEParser } from './sse'
import type {
  Bootstrap,
  ChatEvent,
  ConfirmResponse,
  LoginResponse,
  SignalsResponse,
  User,
  Action,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export interface ApiClientOptions {
  /** Returns the current bearer token, or null when signed out. */
  getToken: () => string | null
  /** Called when an authenticated request is refused (expired or revoked token). */
  onUnauthorized?: () => void
  baseUrl?: string
  fetchImpl?: typeof fetch
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return 'Please fill in every field.'
  } catch {
    // not JSON
  }
  return `Request failed (HTTP ${res.status}).`
}

export function createApiClient(options: ApiClientOptions) {
  const base = options.baseUrl ?? ''
  const doFetch = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args))

  async function send(path: string, init: RequestInit = {}, auth = true): Promise<Response> {
    const headers = new Headers(init.headers)
    const token = auth ? options.getToken() : null
    if (token) headers.set('Authorization', `Bearer ${token}`)
    let res: Response
    try {
      res = await doFetch(base + path, { ...init, headers })
    } catch {
      throw new ApiError(0, 'Could not reach the server. Check your connection and try again.')
    }
    if (!res.ok) {
      const detail = await readDetail(res)
      if (res.status === 401 && token) options.onUnauthorized?.()
      throw new ApiError(res.status, detail)
    }
    return res
  }

  async function json<T>(path: string, init: RequestInit = {}, auth = true): Promise<T> {
    const res = await send(path, init, auth)
    return (await res.json()) as T
  }

  const postJson = (body: unknown): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  return {
    /** OAuth2 password flow: form-encoded credentials, bearer token back. */
    login(username: string, password: string) {
      return json<LoginResponse>(
        '/api/auth/token',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ username, password }).toString(),
        },
        false,
      )
    },

    me: () => json<User>('/api/auth/me'),
    bootstrap: () => json<Bootstrap>('/api/bootstrap', {}, false),
    signals: () => json<SignalsResponse>('/api/signals'),
    resetChat: (sessionId: string) => json<{ status: string }>('/api/reset', postJson({ session_id: sessionId })),

    confirmAction: (id: string) =>
      json<ConfirmResponse>(`/api/actions/${encodeURIComponent(id)}/confirm`, { method: 'POST' }),
    cancelAction: (id: string) =>
      json<{ action: Action }>(`/api/actions/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

    /** Streams one chat turn, calling `onEvent` for each event as it arrives. */
    async streamChat(
      message: string,
      sessionId: string,
      onEvent: (event: ChatEvent) => void,
      signal?: AbortSignal,
    ): Promise<void> {
      const res = await send('/api/chat', { ...postJson({ message, session_id: sessionId }), signal })
      if (!res.body) throw new ApiError(res.status, 'The server returned an empty response.')
      const parser = createSSEParser(onEvent)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        parser.push(decoder.decode(value, { stream: true }))
      }
      parser.push(decoder.decode())
      parser.flush()
    },
  }
}

export type ApiClient = ReturnType<typeof createApiClient>
