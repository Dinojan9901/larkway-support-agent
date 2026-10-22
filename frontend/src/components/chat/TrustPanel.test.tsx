import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { TrustPanel as Trust } from '../../api/types'
import { TrustPanel } from './TrustPanel'

const trust: Trust = {
  confidence: 'medium',
  citations: [
    { document: 'Halcyon Foods Enterprise Agreement', section: '2. Shipment cancellation', quote: 'no cancellation fee', authority_tier: 1, status: 'ACTIVE' },
    { document: 'Larkway Support Policy v4', authority_tier: 2, status: 'DEPRECATED' },
  ],
  conflicts: [{ type: 'historical_guidance_contradicted', source: 'CASE-240', claim: 'A fee applied.', resolution: 'Superseded.' }],
  unknowns: [],
  assumptions: ['Business hours are Mon-Fri.'],
  escalation_reasons: ['KI-322 makes BOOKED unreliable.'],
}

describe('TrustPanel', () => {
  it('summarises the evidence and starts collapsed', () => {
    render(<TrustPanel trust={trust} />)
    const toggle = screen.getByRole('button', { name: /medium confidence/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('2 sources · 1 conflict resolved · human review advised')
    expect(screen.queryByText('Sources used')).not.toBeVisible()
  })

  it('expands from the keyboard and shows sources, tiers and conflicts', async () => {
    render(<TrustPanel trust={trust} />)
    await userEvent.tab()
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('button', { name: /confidence/i })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Sources used')).toBeVisible()
    expect(screen.getByText('T1')).toBeInTheDocument()
    expect(screen.getByText('DEPRECATED')).toBeInTheDocument()
    expect(screen.getByText(/Past guidance \(CASE-240\)/)).toBeInTheDocument()
    expect(screen.getByText('KI-322 makes BOOKED unreliable.')).toBeInTheDocument()
  })
})
