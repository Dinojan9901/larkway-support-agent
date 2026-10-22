import type { Role } from './api/types'

export const SUGGESTIONS: Record<Role, string[]> = {
  customer: [
    'Can I cancel SHP-5001 without a cancellation fee? Explain why.',
    'A pickup was two hours late because of carrier fault. Should I get a service credit?',
    "What's my first-response time if we hit a complete outage?",
    'Show me the orders on my account.',
    "What's the row limit on bulk upload, and why do my 3,000-row files time out?",
  ],
  ops: [
    'What needs my attention right now?',
    'Can Halcyon Foods cancel SHP-5001 free of charge? Check for past guidance that conflicts.',
    'Which open tickets have breached SLA, and against which contract?',
    'SHP-6002 missed its pickup — work out what Brightmoor is owed and prepare the credit.',
    'Have we given any customer incorrect advice?',
  ],
}
