# Architecture Note

## The premise

Retrieval over six one-page PDFs is not hard. What is hard is that the six documents
**disagree with each other on purpose**, and that the disagreements are exactly where
the money is: a fee one contract waives, a threshold another contract doubles, a
deprecated SLA table that reads as authoritative, and two past ticket resolutions that
are simply wrong.

So the system is not organised around "find the relevant passage". It is organised
around **deciding which source governs, and being able to prove it afterwards**.

That produces one structural decision from which most of the rest follows:

> The model decides *what to do*. Deterministic code decides *what is true*.

Claude orchestrates — reads the request, chooses tools, chains them, writes the reply.
It never computes a fee, a credit, or an SLA deadline. Those come from a policy engine
that returns a decision with its own reasoning trace and citations, and that can be
unit-tested with no API key. `pytest -q` is the proof: 94 tests pin the numbers this
product is judged on.

---

## Layers

```
                    Browser — chat, live tool trace, trust panel, ops dashboard
                       │ SSE
                    FastAPI
        ┌──────────────┴───────────────────────────────┐
        │                                              │
   Agent loop                                  POST /api/actions/{id}/confirm
   Claude, or Gemini / Groq / Ollama           the ONLY path that can mark an
        │  ← Principal injected server-side    action confirmed (needs a click)
        ▼                                              │
   Tool layer ─── authorisation ─── audit              │
        │                                              │
        ├─ Retrieval ......... BM25 + authority re-rank + conflict detection
        ├─ Structured data ... row scoping + column redaction
        ├─ Policy engine ..... deterministic decisions ──┐  same engine, so the
        ├─ Ops detector ...... proactive sweep ──────────┘  dashboard and the chat
        └─ Action store ...... two-phase confirmation       can never disagree
                       │
   Policy pack (YAML rules + verbatim quotes)
                       │ verified against
   Source PDFs / workbook
```

---

## Agent design

**A manual streaming loop, not the SDK tool runner.** The runner would have been less
code, but three things needed control it does not expose:

1. **Identity is injected, not passed.** Every tool handler receives a `Principal`
   built from the server session. The model cannot supply one, so no amount of
   persuasion in the conversation can widen scope.
2. **The UI needs a live trace.** Each tool call emits `tool_start` / `tool_end` events
   that interleave with the token stream, so the user watches the agent work rather
   than staring at a spinner.
3. **A pending confirmation must end the turn.** When the agent proposes an action, the
   loop lets it write its "here is what I'll do" message and then stops, handing control
   to the human instead of looping onward.

Model configuration: `claude-opus-5`, adaptive thinking with summarised display,
`effort: high`, `max_tokens` 8,000, a 10-step ceiling as a runaway guard. Server-side
refusal fallback is enabled and downgrades itself automatically if the beta is not
enabled on the key, so a missing beta cannot break a deployment.

**The model sits behind a backend, and the backend holds none of the guarantees.**
`agent/providers.py` has two: Claude through the Anthropic SDK (streamed, with
thinking), and any OpenAI-compatible endpoint — which covers Gemini's free tier, Groq,
OpenRouter and a local Ollama. A backend only keeps history in its wire format, runs
one step and accepts tool results; principal injection, the tool trace and the
confirmation stop all stay in the loop. Gemini rejects free-form object parameters,
so the adapter sends `filters` and `payload` as JSON strings and decodes them before
the tool runs, and echoes Gemini's thought signatures back on tool calls. Because the
policy engine owns every number, a smaller free model costs fluency, not correctness.

**The system prompt is deliberately thin on rules the code already enforces.** Access
scoping, the confirmation gate and the arithmetic are guaranteed structurally; repeating
them as instructions would only create the illusion that the prompt is what keeps them
true. The prompt covers what code cannot: which tool to reach for, when to stop and
escalate, and how to talk to the person in front of it.

---

## Tool design

Nine tools in five families. The split follows a rule: **one tool per decision the
agent has to make, not one tool per table.**

| Family | Tool | Why it exists |
|---|---|---|
| Retrieval | `search_policy_documents` | Authority-ranked search across all six documents |
| Data | `lookup_records` | Scoped, redacted reads of accounts / orders / tickets |
| Calculation | `evaluate_cancellation` | Status rules + free window + contract override + stale-status warning |
| Calculation | `evaluate_service_credit` | Threshold, fault, amount, cap, approval — real order or hypothetical |
| Calculation | `resolve_support_sla` | Contract-or-plan target, business-hours elapsed, breach |
| Calculation | `check_plan_capability` | Entitlement vs. what currently works, given open known issues |
| Operations | `detect_operations_signals` | Internal only — the proactive sweep |
| Action | `propose_action` | Builds a confirmable proposal. Changes nothing. |
| Action | `execute_confirmed_action` | Refuses anything a human did not confirm |

Three design choices worth naming:

**Calculators, not table dumps.** It would have been simpler to give the model raw rows
and the SOP text and let it reason. That is precisely the failure mode this domain
punishes — it produces answers that are *usually* right and occasionally quote the
deprecated table. A calculator returns `{outcome, facts, reasoning_steps, citations,
conflicts, unknowns, confidence, requires_human_review}`, so the model's job shrinks to
narrating a decision that has already been made correctly.

**`evaluate_service_credit` accepts a hypothetical.** Customers ask "my pickup was two
hours late, do I get a credit?" without quoting an order id, and the honest answer
differs by contract — Halcyon Foods gets INR 540, Brightmoor Textiles gets nothing. Omitting a fault
field is meaningful, not missing input: the SOP forbids promising a credit while fault
is unknown, and the tool says so instead of guessing.

**Errors are information.** A denied lookup returns a structured tool error *plus
guidance* — "do not retry with a different account; tell the user plainly." The model
recovers gracefully instead of thrashing.

---

## Document handling

`python -m app.ingest` reads the PDFs with `pypdf`. They extract one token per line, so
normalisation rebuilds prose, restores bullets, and splits on numbered headings and
`KI-` blocks. The result is **27 sections across 6 documents**, each carrying:

```
authority tier · status (CURRENT / DEPRECATED / ACTIVE) · effective date ·
visibility (public | account:CUS-10X) · superseded_by · doc type
```

**Retrieval is BM25 plus an authority re-rank**, hand-rolled in about 40 lines rather
than pulled in as a dependency. With a 26-section corpus, an opaque scorer buys nothing
and a transparent one can be defended in a review. Domain synonyms bridge customer
vocabulary to document vocabulary ("refund" → "service credit").

The re-rank is the part that matters:

- Tier weights (agreement 1.35, policy 1.15, product doc 1.0, historical 0.6) let
  authority break near-ties without manufacturing relevance.
- `DEPRECATED` sources are **excluded from the answer set** and returned separately as
  `superseded_matches`, each carrying an explicit warning. They are still searchable
  when someone genuinely asks what the old policy said.
- **Contract-first guarantee**: if any tier-1 section matched at all, the best one is
  promoted into the results. A short, lexically dense policy paragraph must not crowd
  out the clause that actually governs this customer.
- **Access control runs before scoring, never as a penalty.** Another customer's
  agreement is invisible, not down-ranked.

No vector database. The corpus is 27 sections; embeddings would add a dependency, a
build step and a failure mode in exchange for nothing measurable. At a few thousand
documents this flips, and the `SectionIndex` interface is the seam to swap behind.

---

## Structured-data handling

The workbook loads through `openpyxl` into typed rows. **The reference clock is read
from the README sheet**, not hard-coded — `2026-09-19 14:00 Asia/Kolkata`.

That date is a Saturday, which is load-bearing. The support policy expresses targets in
three different units — `20 minutes, 24x7`, `3 business hours`, `2 business days` — and
Brightmoor's agreement explicitly excludes weekend coverage. A ticket opened at 16:30 on
Friday has used 90 business minutes by Saturday afternoon, not 21 hours: a naive
"hours elapsed" calculation reports it as long breached when it is merely at risk. `app/timeutil.py` implements a business-hours calendar, and every SLA answer
reports which clock it used and states the business-hours assumption inline.

**Access control is enforced here, at the store, not above it.** `Principal.scope_account()`
re-derives what the caller may see; an `account_id` tool argument is a filter request,
never a grant. Column redaction strips internal-only fields (`carrier_fault`, `notes`,
`assigned_to`, `historical_resolution`, `csm`) from customer sessions. So a customer
session cannot see that a ticket was assigned to Rohit, and cannot see another
customer's rows even if the model is talked into asking for them — `tests/test_access.py`
asserts exactly that.

---

## Source reliability and conflict handling

Four mechanisms, in the order they fire.

### 1. The policy pack — rules that cannot drift from the documents

The numbers the engine calculates with live in `data/policy_pack/*.yaml`, each beside
the verbatim clause it came from:

```yaml
service_credit:
  section: "2. Failed-pickup service credits"
  quote: >-
    Under the default policy, a customer qualifies for a service credit when pickup
    happens more than 90 minutes after the end of the scheduled pickup window...
  default_threshold_hours_past_window_end: 1.5
  amount: {rule: min_of_flat_and_percent, flat_inr: 600, percent_of_shipment_fee: 15}
```

`verify_pack()` re-reads the PDFs and asserts **every quote is still present verbatim**.
It runs on startup, in the test suite, and in the Docker build. If a document changes,
the build breaks loudly instead of the agent quietly answering from stale rules.

This is the one place I deliberately chose curated structure over automated extraction.
Regex-scraping "INR 350" out of prose is brittle in a way that fails silently; a
reviewable rule with a verified provenance quote fails loudly. That trade is the right
way round when the output is money.

### 2. Precedence, applied in code

Support Policy v3 s1 defines the order, and the engine implements it directly: check the
agreement first, fall through to the SOP, and *say which one won*. Every decision names
the source that governed and, where they differ, what the default would have been —
"the Enterprise plan default would have been 20 minutes; the agreement's 10 minutes
governs."

### 3. Conflict detection at retrieval time

`retrieval/conflicts.py` compares what came back and names disagreements rather than
silently picking one, in three flavours: a superseded document that answered the same
question, an agreement that overrides a policy on a shared topic, and a historical
ticket resolution relevant to the query. Conflicts only fire when two passages share a
**section-level topic** — document-level tags were too coarse and produced noise.

### 4. Uncertainty as a first-class outcome

The SOP says: *"Do not promise a credit when carrier fault, pickup timing, or customer
fault is unknown."* That is implemented, not merely quoted. A missing fact produces
`outcome: undetermined`, `confidence: low`, `requires_human_review: true` — and the
trust panel lists exactly which facts were missing.

Escalation triggers are likewise structural, not vibes: unknown required facts, a
breached target, a P1, a contract cap that would be exceeded, a credit over the INR
1,500 approval threshold, or a data-reliability caveat like KI-322.

### 5. The stale-data caveat

The one I am most pleased with. SHP-5001 is TransVelo, `BOOKED`, and inside its pickup
window. KI-322 says TransVelo pickup webhooks lag up to 35 minutes, so `BOOKED` may be
stale — and the SOP forbids cancelling something already picked up. The engine detects
that combination and asks for carrier verification before cancelling. The correct answer
here is *not* the confident one.

---

## Proactive detection

`signals/detector.py` sweeps the whole support surface at the snapshot and returns 11
prioritised signals, each with evidence and a recommended action. Seven detectors:
SLA pressure (against each account's *own* contract), repeat issues, cross-account
impact, known-issue correlation, order anomalies, unclaimed entitlements, and a
**guidance audit**.

It runs on the same policy engine the chat uses, so the dashboard and a chat answer
cannot contradict each other — and it is exposed both as a dashboard tab and as a tool
the internal agent can call.

The guidance audit is the detector I would keep if I could keep only one. It re-runs
today's rules against past resolutions and finds that Larkway **quoted Halcyon Foods a
fee their contract waives** (CASE-240) and **told Brightmoor their plan caps at 2,500
rows when that is really bug KI-314** (CASE-251). Nobody filed a complaint about either.

---

## Major trade-offs

| Decision | Alternative | Why |
|---|---|---|
| Deterministic engine for all entitlement maths | Let the model reason over raw text | Reproducible, testable, auditable. The model still writes the answer. |
| Curated policy pack + quote verifier | Regex-extract rules from PDFs at runtime | Regex fails silently; a verified quote fails loudly. Correct trade when the output is money. |
| BM25 + authority re-rank | Vector DB / embeddings | 27 sections. Embeddings add dependencies and a failure mode for no measurable gain. Swappable behind `SectionIndex`. |
| Manual agent loop | SDK tool runner | Server-side principal injection, live tool events, clean stop on pending confirmation. |
| Confirmation gated by an HTTP endpoint | Model-side confirmation tool | A model-settable flag is not a gate. A click is. |
| Vanilla JS + FastAPI static | React / Next.js | One deployable, no build step, full control of the SSE trace. Not a UI-framework exercise. |
| In-memory sessions | Redis / Postgres | Correct for a single-instance demo; swap for horizontal scale. |
| Both user contexts, one codebase | Pick one | The interesting behaviours — redaction, scoping, tool visibility — only show up in contrast. |

---

## Assumptions

Each is stated in the code where it applies, and surfaced in the UI whenever an answer
depends on it.

1. **Business hours are Mon–Fri 09:00–18:00 IST.** The documents never define them, but the
   SLA tables lean on "business hours" and "business days" throughout.
2. **An unqualified duration runs on the wall clock.** The support policy writes "90
   minutes" and "3 business hours" in the same table, so the distinction is treated as
   deliberate.
3. **Enterprise P1 is 24x7 by its own words**; other unqualified Enterprise targets are
   elapsed-time.
4. **Authentication and roles are mocked.** Six demo principals —
   four customers, an agent, a manager. In production the `Principal` is built from a
   verified session; nothing else changes.
5. **State-changing actions are mocked** to `data/runtime/actions.json` with a generated
   reference, standing in for the ticketing and billing APIs.
6. **An account with no agreement on file has no custom terms** — the fall-through
   to plan defaults is an answer, not a gap.
7. **Severity is inferred** from the ticket text against the policy's own definitions.
   The classification, its rationale and its confidence are always shown, because it
   determines the SLA clock and deserves to be challengeable.
