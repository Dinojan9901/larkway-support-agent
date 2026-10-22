import { describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from './client'
import type { ChatEvent } from './types'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function client(fetchImpl: typeof fetch, token: string | null = 'tok', onUnauthorized = vi.fn<() => void>()) {
  return { api: createApiClient({ getToken: () => token, onUnauthorized, fetchImpl }), onUnauthorized }
}

describe('api client', () => {
  it('logs in with a form-encoded OAuth2 password request and no bearer header', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => json({ access_token: 'abc', token_type: 'bearer', expires_in: 1800, user: {} }))
    const { api } = client(fetchImpl)
    await api.login('ops', 's3cret & more')

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/auth/token')
    const headers = new Headers(init.headers)
    expect(headers.get('Content-Type')).toBe('application/x-www-form-urlencoded')
    expect(headers.has('Authorization')).toBe(false)
    expect(new URLSearchParams(init.body as string).get('password')).toBe('s3cret & more')
  })

  it('sends the bearer token on authenticated requests', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => json({ summary: {}, signals: [] }))
    const { api } = client(fetchImpl, 'tok-123')
    await api.signals()
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer tok-123')
  })

  it('turns an error response into an ApiError carrying the server detail', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => json({ detail: 'This needs the ops role.' }, 403))
    const { api, onUnauthorized } = client(fetchImpl)
    const err = await api.signals().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 403, detail: 'This needs the ops role.' })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('signals an expired session on 401 so the app can sign out', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => json({ detail: 'Your session has expired.' }, 401))
    const { api, onUnauthorized } = client(fetchImpl)
    await expect(api.me()).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('reports a network failure as an ApiError with status 0', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => {
      throw new TypeError('Failed to fetch')
    })
    const { api } = client(fetchImpl)
    await expect(api.bootstrap()).rejects.toMatchObject({ status: 0 })
  })

  it('streams chat events from the response body in order', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"tool_start","tool":"t","label":"L","family":"data","detail":"","tool_use_id":"1"}\n\n'))
        controller.enqueue(encoder.encode('data: {"type":"text","text":"Hel'))
        controller.enqueue(encoder.encode('lo"}\n\ndata: {"type":"done"}\n\n'))
        controller.close()
      },
    })
    const fetchImpl = vi.fn<typeof fetch>(async () => new Response(body, { status: 200 }))
    const { api } = client(fetchImpl)
    const events: ChatEvent[] = []
    await api.streamChat('hi', 'session-1', (e) => events.push(e))

    expect(events.map((e) => e.type)).toEqual(['tool_start', 'text', 'done'])
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ message: 'hi', session_id: 'session-1' })
  })

  it('rejects a refused chat request before reading any stream', async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => json({ detail: 'Sign in to continue.' }, 401))
    const { api } = client(fetchImpl, null)
    await expect(api.streamChat('hi', 's', () => {})).rejects.toMatchObject({ status: 401 })
  })
})
