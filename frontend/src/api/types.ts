// Shapes returned by the FastAPI backend (app/main.py). Kept in one place so a
// backend change shows up as a type error, not a blank screen.

export type Role = 'customer' | 'ops'

export interface User {
  username: string
  name: string
  role: Role
  account_id: string | null
  labels: string[]
}

export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export type ToolFamily = 'retrieval' | 'data' | 'calculation' | 'operations' | 'action'

export interface ToolInfo {
  name: string
  family: ToolFamily
  label: string
  internal_only: boolean
}

export interface DatasetSummary {
  snapshot_at: string
  currency: string
  accounts: number
  orders: number
  tickets: number
  notes: string
}

export interface Bootstrap {
  dataset: DatasetSummary
  model: string
  provider: string
  chat_enabled: boolean
  chat_disabled_reason: string | null
  tools: ToolInfo[]
  policy_pack_ok: boolean
}

export type Confidence = 'high' | 'medium' | 'low'

export interface Citation {
  document: string
  section?: string
  quote?: string
  authority_tier: number
  status?: string
  effective?: string
}

export interface Conflict {
  type?: string
  winning_source?: string
  losing_source?: string
  source?: string
  claim?: string
  resolution?: string
}

export interface TrustPanel {
  confidence: Confidence
  citations: Citation[]
  conflicts: Conflict[]
  unknowns: string[]
  assumptions: string[]
  escalation_reasons: string[]
}

export interface Action {
  action_id: string
  action_type: string
  label: string
  preview: string
  warnings: string[]
  status: 'pending_confirmation' | 'confirmed' | 'executed' | 'cancelled'
  result?: { reference?: string } | null
}

export interface ConfirmResponse {
  confirmed: string
  action: Action
}

export type ChatEvent =
  | { type: 'session'; session_id: string }
  | { type: 'status'; step: number; message: string }
  | { type: 'thinking'; text: string }
  | {
      type: 'tool_start'
      tool: string
      label: string
      family: ToolFamily
      detail: string
      tool_use_id: string
    }
  | { type: 'tool_end'; tool: string; tool_use_id: string; ok: boolean; summary: string }
  | { type: 'text'; text: string }
  | { type: 'proposal'; action: Action }
  | { type: 'trust'; trust: TrustPanel }
  | { type: 'done'; stop_reason?: string }
  | { type: 'error'; message: string }

export type Priority = 'critical' | 'high' | 'medium' | 'low'

export interface Signal {
  signal_id: string
  category: string
  priority: Priority
  title: string
  detail: string
  evidence: string[]
  accounts: string[]
  recommended_action: { action_type: string; payload?: Record<string, unknown> } | null
  metrics: Record<string, unknown>
}

export interface SignalsResponse {
  summary: {
    as_of: string
    total: number
    by_priority: Partial<Record<Priority, number>>
    by_category: Record<string, number>
    accounts_affected: string[]
  }
  signals: Signal[]
}
