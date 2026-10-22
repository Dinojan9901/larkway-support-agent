import { useId, useState } from 'react'
import type { TrustPanel as Trust } from '../../api/types'

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function NoteGroup({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null
  return (
    <div>
      <h4 className="trust-group-title">{title}</h4>
      <ul className="note-list">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/** What an answer was built from: confidence, sources, conflicts, unknowns. */
export function TrustPanel({ trust }: { trust: Trust }) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()

  const bits: string[] = []
  if (trust.citations?.length) bits.push(plural(trust.citations.length, 'source'))
  if (trust.conflicts?.length) bits.push(`${plural(trust.conflicts.length, 'conflict')} resolved`)
  if (trust.unknowns?.length) bits.push(plural(trust.unknowns.length, 'unknown'))
  if (trust.escalation_reasons?.length) bits.push('human review advised')

  return (
    <section className={`trust${open ? ' open' : ''}`} aria-label="How this answer was built">
      <button
        type="button"
        className="trust-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`conf ${trust.confidence}`}>{trust.confidence} confidence</span>
        <span className="trust-summary">{bits.join(' · ')}</span>
        <span className="trust-caret" aria-hidden="true">
          ▶
        </span>
      </button>

      <div id={bodyId} className="trust-body" hidden={!open}>
        {trust.conflicts?.length > 0 && (
          <div>
            <h4 className="trust-group-title">Conflicts detected and resolved</h4>
            {trust.conflicts.map((c, i) => (
              <div className="conflict" key={i}>
                <div className="conflict-type">{(c.type ?? '').replace(/_/g, ' ')}</div>
                {c.winning_source && (
                  <div className="line">
                    <b>Governs:</b> {c.winning_source}
                  </div>
                )}
                {c.losing_source && (
                  <div className="line">
                    <b>Set aside:</b> {c.losing_source}
                  </div>
                )}
                {c.source && (
                  <div className="line">
                    <b>Past guidance ({c.source}):</b> {c.claim}
                  </div>
                )}
                {c.resolution && <div className="line">{c.resolution}</div>}
              </div>
            ))}
          </div>
        )}

        {trust.citations?.length > 0 && (
          <div>
            <h4 className="trust-group-title">Sources used</h4>
            {trust.citations.map((c, i) => (
              <div className="cite" key={i}>
                <div className="cite-head">
                  <span className={`tier t${c.authority_tier}`} title={`Authority tier ${c.authority_tier}`}>
                    T{c.authority_tier}
                  </span>
                  <span className="cite-doc">{c.document}</span>
                  {c.section && <span className="cite-sec">· {c.section}</span>}
                  {c.status && c.status !== 'CURRENT' && c.status !== 'ACTIVE' && (
                    <span className="status-tag deprecated">{c.status}</span>
                  )}
                </div>
                {c.quote && <div className="cite-quote">“{c.quote}”</div>}
              </div>
            ))}
          </div>
        )}

        <NoteGroup title="Unknown facts" items={trust.unknowns} />
        <NoteGroup title="Assumptions" items={trust.assumptions} />
        <NoteGroup title="Why human review is advised" items={trust.escalation_reasons} />
      </div>
    </section>
  )
}
