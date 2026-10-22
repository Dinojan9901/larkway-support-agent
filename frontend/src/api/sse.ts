import type { ChatEvent } from './types'

/**
 * Incremental parser for the chat endpoint's server-sent events.
 *
 * Network chunks split events anywhere — mid-line, mid-JSON — so text is buffered
 * until a blank line closes an event. Malformed events are skipped rather than
 * thrown: one bad line must not kill a streaming answer.
 */
export function createSSEParser(onEvent: (event: ChatEvent) => void) {
  let buffer = ''

  const dispatch = (block: string) => {
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).replace(/^ /, ''))
      .join('\n')
    if (!data) return
    try {
      const event = JSON.parse(data) as ChatEvent
      if (event && typeof event === 'object' && 'type' in event) onEvent(event)
    } catch {
      // ignore a malformed event
    }
  }

  return {
    push(chunk: string) {
      buffer += chunk.replace(/\r\n/g, '\n')
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''
      blocks.forEach(dispatch)
    },
    flush() {
      if (buffer.trim()) dispatch(buffer)
      buffer = ''
    },
  }
}
