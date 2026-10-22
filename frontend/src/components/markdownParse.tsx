import type { ReactNode } from 'react'

// The small subset of markdown the agent writes: paragraphs, dash/bullet lists,
// **bold** and `code`. Everything is rendered as React text nodes, so model output
// can never inject HTML.

export type Block = { kind: 'p'; text: string } | { kind: 'ul'; items: string[] }

const LIST_ITEM = /^\s*[-*•]\s+(.*)$/

export function toBlocks(text: string): Block[] {
  const blocks: Block[] = []
  let para: string[] = []
  let list: string[] | null = null

  const flushPara = () => {
    if (para.length) blocks.push({ kind: 'p', text: para.join('\n') })
    para = []
  }
  const flushList = () => {
    if (list) blocks.push({ kind: 'ul', items: list })
    list = null
  }

  for (const line of text.split('\n')) {
    const item = LIST_ITEM.exec(line)
    if (item) {
      flushPara()
      list ??= []
      list.push(item[1])
    } else if (!line.trim()) {
      flushPara()
      flushList()
    } else {
      flushList()
      para.push(line)
    }
  }
  flushPara()
  flushList()
  return blocks
}

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const pattern = /\*\*(.+?)\*\*|`([^`]+)`/g
  let last = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index))
    out.push(
      match[1] !== undefined ? (
        <strong key={match.index}>{match[1]}</strong>
      ) : (
        <code key={match.index}>{match[2]}</code>
      ),
    )
    last = pattern.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}
