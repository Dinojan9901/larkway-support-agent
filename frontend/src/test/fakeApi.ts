import { vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { Action, Bootstrap, SignalsResponse, User } from '../api/types'

export const customer: User = {
  username: 'customer',
  name: 'Rohan Iyer (Halcyon Foods)',
  role: 'customer',
  account_id: 'CUS-101',
  labels: ['Enterprise plan', 'Custom agreement'],
}

export const opsUser: User = {
  username: 'ops',
  name: 'Kavya Nair (Support Manager)',
  role: 'ops',
  account_id: null,
  labels: ['All accounts'],
}

export const bootstrap: Bootstrap = {
  dataset: { snapshot_at: '2026-09-19 14:00 Asia/Kolkata', currency: 'INR', accounts: 4, orders: 7, tickets: 8, notes: '' },
  model: 'openai/gpt-oss-120b',
  provider: 'openai_compat',
  chat_enabled: true,
  chat_disabled_reason: null,
  tools: [
    { name: 'evaluate_cancellation', family: 'calculation', label: 'Calculating cancellation', internal_only: false },
    { name: 'detect_operations_signals', family: 'operations', label: 'Scanning', internal_only: true },
  ],
  policy_pack_ok: true,
}

export const action: Action = {
  action_id: 'ACT-1234',
  action_type: 'cancel_order',
  label: 'Cancel order SHP-5001',
  preview: 'Cancel SHP-5001\nNo cancellation fee',
  warnings: [],
  status: 'pending_confirmation',
}

export const signals: SignalsResponse = {
  summary: {
    as_of: '2026-09-19 14:00 IST',
    total: 2,
    by_priority: { critical: 1, high: 1 },
    by_category: { sla: 1, known_issue: 1 },
    accounts_affected: ['CUS-101', 'CUS-104'],
  },
  signals: [
    {
      signal_id: 'SIG-SLA-CASE-301',
      category: 'sla',
      priority: 'critical',
      title: 'CASE-301 (P1) BREACHED — Halcyon Foods',
      detail: 'The P1 target of 10 minutes is breached.',
      evidence: ['CASE-301'],
      accounts: ['CUS-101'],
      recommended_action: { action_type: 'create_escalation', payload: { ticket_id: 'CASE-301' } },
      metrics: { severity: 'P1', overdue_by: '10 min', affected: ['x'] },
    },
    {
      signal_id: 'SIG-KI-KI-314',
      category: 'known_issue',
      priority: 'high',
      title: 'KI-314 explains 2 open tickets',
      detail: 'Bulk upload timeouts.',
      evidence: ['CASE-302', 'CASE-306'],
      accounts: ['CUS-102', 'CUS-104'],
      recommended_action: null,
      metrics: {},
    },
  ],
}

/** An ApiClient whose every method is a mock with a sensible default. */
export function fakeApi(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    login: vi.fn<ApiClient['login']>(async () => ({ access_token: 't', token_type: 'bearer' as const, expires_in: 1800, user: customer })),
    me: vi.fn<ApiClient['me']>(async () => customer),
    bootstrap: vi.fn<ApiClient['bootstrap']>(async () => bootstrap),
    signals: vi.fn<ApiClient['signals']>(async () => signals),
    resetChat: vi.fn<ApiClient['resetChat']>(async () => ({ status: 'reset' })),
    confirmAction: vi.fn<ApiClient['confirmAction']>(async () => ({
      confirmed: action.action_id,
      action: { ...action, status: 'executed' as const, result: { reference: 'REF-42' } },
    })),
    cancelAction: vi.fn<ApiClient['cancelAction']>(async () => ({ action: { ...action, status: 'cancelled' as const } })),
    streamChat: vi.fn<ApiClient['streamChat']>(async () => {}),
    ...overrides,
  }
}
