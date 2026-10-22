import type { TrustPanel } from '../../api/types'

export function hasTrustContent(t: TrustPanel | null): t is TrustPanel {
  return Boolean(
    t &&
      (t.citations?.length ||
        t.conflicts?.length ||
        t.unknowns?.length ||
        t.assumptions?.length ||
        t.escalation_reasons?.length),
  )
}
