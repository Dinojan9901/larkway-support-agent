import { inline, toBlocks } from './markdownParse'

export function Markdown({ text }: { text: string }) {
  return (
    <>
      {toBlocks(text).map((block, i) =>
        block.kind === 'ul' ? (
          <ul key={i}>
            {block.items.map((item, j) => (
              <li key={j}>{inline(item)}</li>
            ))}
          </ul>
        ) : (
          <p key={i}>{inline(block.text)}</p>
        ),
      )}
    </>
  )
}
