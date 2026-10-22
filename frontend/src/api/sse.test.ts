import { describe, expect, it } from 'vitest'
import { createSSEParser } from './sse'
import type { ChatEvent } from './types'

function collect() {
  const events: ChatEvent[] = []
  return { events, parser: createSSEParser((e) => events.push(e)) }
}

describe('createSSEParser', () => {
  it('parses complete events', () => {
    const { events, parser } = collect()
    parser.push('data: {"type":"text","text":"Hi"}\n\ndata: {"type":"done"}\n\n')
    expect(events).toEqual([{ type: 'text', text: 'Hi' }, { type: 'done' }])
  })

  it('reassembles an event split across network chunks', () => {
    const { events, parser } = collect()
    parser.push('data: {"type":"te')
    parser.push('xt","text":"split"}\n')
    expect(events).toEqual([])
    parser.push('\n')
    expect(events).toEqual([{ type: 'text', text: 'split' }])
  })

  it('accepts CRLF line endings and flushes a trailing event', () => {
    const { events, parser } = collect()
    parser.push('data: {"type":"session","session_id":"s"}\r\n\r\ndata: {"type":"done"}')
    parser.flush()
    expect(events.map((e) => e.type)).toEqual(['session', 'done'])
  })

  it('skips malformed events instead of throwing', () => {
    const { events, parser } = collect()
    parser.push('data: {not json}\n\n: comment\n\ndata: {"type":"done"}\n\n')
    expect(events).toEqual([{ type: 'done' }])
  })
})
