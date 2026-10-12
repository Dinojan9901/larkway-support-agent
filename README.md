# Larkway Support Agent

**Live demo: [larkway-support-agent.vercel.app](https://larkway-support-agent.vercel.app)** —
pick a customer or staff user from the sidebar and ask away. No sign-up needed.

An AI support agent for **Larkway**, a fictional B2B shipping platform. One codebase
serves two user contexts:

- a **customer-facing agent** that answers entitlement questions about orders,
  cancellations, service credits and support SLAs, and escalates what it should not
  decide alone, and
- an **internal support/operations console** that investigates across accounts and
  surfaces what needs attention before anyone asks.

The source material is deliberately imperfect: a deprecated policy that still reads as
authoritative, customer contracts that override the general rules, and closed tickets
whose recorded answers are simply wrong. The design premise is that **a support agent's
hardest job is not retrieval, it is deciding which source wins** — so that is where most
of the engineering went.

---

## What it does that a thin RAG wrapper does not

| | |
|---|---|
| **The model never does the money maths** | Fees, credits and SLA deadlines are computed by a deterministic policy engine against rules extracted from the documents. Same question, same answer, every time — and unit-testable without an API key. |
| **The rules cannot drift from the documents** | Every rule in the policy pack carries the verbatim clause it came from, and a verifier re-reads the PDFs and asserts each quote is still there. It runs on startup and in the tests. |
| **Retrieval ranks on authority, not just similarity** | Signed agreement > current policy/SOP > product docs > historical tickets. Deprecated documents are excluded from answers and reported separately so a conflict can be *named*. |
| **Access control is in the data layer** | The principal comes from the server session, never from a tool argument. A customer session cannot read another account's rows, columns, or contract — proven by tests, not by prompt instructions. |
| **Confirmation is unforgeable** | The confirm flag can only be set by an HTTP endpoint behind a real button click. The model has no tool that sets it, so "ask before acting" is a property of the system. |
| **It finds problems nobody reported** | The operations sweep flags SLA breaches against each account's own contract, cross-account issues, credits owed that nobody claimed, and **past answers that current policy contradicts**. |
| **Model-agnostic** | Runs on Claude or any OpenAI-compatible endpoint — including free tiers (Groq, Gemini) and a local Ollama. None of the guarantees above live in the model. |

Two things hidden in the data that the system gets right:

- **The snapshot is a Saturday.** A ticket opened at 16:30 on Friday against a
  2-business-hour target has used 90 minutes, not 21 hours — it is *at risk*, not
  breached. A wall-clock calculation gets this wrong.
- **Two customers were previously given wrong answers.** CASE-240 quoted Halcyon Foods
  a fee their contract waives; CASE-251 told Brightmoor their plan caps at 2,500 rows
  when that is really bug KI-314. Both are surfaced automatically.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then add a model key (a free Groq key works)
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

### Choosing a model (free options included)

| Provider | Cost | Settings in `.env` |
|---|---|---|
| **Groq** | Free tier | `PP_PROVIDER=openai_compat`, `PP_BASE_URL=https://api.groq.com/openai/v1`, `PP_API_KEY`, `PP_MODEL=openai/gpt-oss-120b` |
| **Gemini** (Google AI Studio) | Free tier | `PP_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/`, a Gemini key, `PP_MODEL=gemini-3.8-flash` |
| **Ollama** | Free, local | `PP_BASE_URL=http://localhost:11434/v1`, no key |
| **Claude** | Paid API | `PP_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` |

`.env.example` has each block ready to uncomment. Swapping the model changes nothing
that matters for trust: the numbers come from the policy engine, scope comes from the
server session, and confirmation needs a click.

### Without a model

Chat needs one of the providers above. **Everything else still works** — the operations
dashboard, the policy engine, retrieval, the access-control layer, the test suite and
the offline eval harness. The UI says so plainly rather than failing silently.

### Deploying to Vercel

`vercel.json` deploys the FastAPI app as a single Python function; `.vercelignore`
keeps secrets, the virtualenv and source-only files out of the upload.

```bash
vercel deploy --prod
```

Set `PP_PROVIDER`, `PP_BASE_URL`, `PP_MODEL`, `PP_FALLBACK_MODELS` and `PP_API_KEY` as
environment variables in the Vercel project. Conversations and pending actions live in memory, so a cold start
begins a fresh session — fine for a demo; a real deployment would put them in Redis.

---

## Try it

Pick a user from the sidebar; each one sees a genuinely different system.

**As Rohan Iyer (Halcyon Foods — Enterprise, custom contract)**
> Can I cancel SHP-5001 without a cancellation fee? Explain why.

Chains several tools, finds that the SOP would charge INR 350 but the signed agreement
waives it, flags that CASE-240 told this customer the opposite, and warns that KI-322
makes the `BOOKED` status unreliable for TransVelo inside the pickup window — so it asks
for carrier verification before cancelling.

> A pickup was two hours late because of carrier fault. Do I get a service credit? The
> shipment fee was INR 3,600.

**INR 540** — the SOP's 90-minute threshold is met and the credit is the lower of
INR 600 or 15% of the fee.

**As Ananya Das (Brightmoor Textiles — Scale, custom contract)**
> A pickup is two hours late because of carrier fault. Should I get a service credit?

**No** — their contract replaces the SOP's 90-minute threshold with 3 hours. Same facts,
opposite answer. Contract-specific, not policy-generic.

> Show me the orders for Orbitron Medical.

Refused in the data layer.

**As Kavya Nair (Support Manager)**
> What needs my attention right now?

14 signals: two breached P1s, a bulk-upload issue hitting two customers, two customers
given wrong advice, and an INR 2,000 credit owed that nobody has claimed (and that needs
a manager's approval). Also available as the **Operations signals** tab.

---

## Verify the claims

```bash
pytest -q                          # 71 tests, no API key needed
python -m evals.run                # 16 golden scenarios, offline
python -m evals.run --live         # also drives the real agent and checks its prose
python scripts/verify_policy_pack.py
```

The test suite covers the entitlement maths, the access-control boundary, the
confirmation gate, both model backends, and the agent loop itself (driven by a
scripted model, so the loop wiring is verified without a key).

---

## Architecture at a glance

```
Browser (chat + tool trace + trust panel + ops dashboard)
   │  SSE
FastAPI  ──────────────────────────────────────────────────┐
   │                                                        │
Agent loop (Claude or any OpenAI-compatible model)          │ /api/actions/{id}/confirm
   │  principal injected server-side                        │ ← the only path that can
   ▼                                                        │   mark an action confirmed
Tool layer ── access control ── audit                       │
   │                                                        │
   ├── Retrieval ....... BM25 + authority re-rank + conflict detection
   ├── Structured data . row scoping + column redaction
   ├── Policy engine ... deterministic decisions + citations ◄─┐
   ├── Ops detector .... proactive signals ────────────────────┘ same engine
   └── Action store .... two-phase confirm ────────────────────┘
                                                            │
Policy pack (YAML rules + verbatim quotes) ◄── verified against ── Source PDFs
```

Full detail in **[ARCHITECTURE.md](ARCHITECTURE.md)**. Product reasoning, what was left
out, and the success metric in **[PRODUCT.md](PRODUCT.md)**.

---

## Project layout

```
app/
  config.py            tunables, paths, provider selection
  access.py            Principal, roles, capabilities, redaction rules
  store.py             workbook -> scoped, redacted reads
  actions.py           state-changing actions + the confirmation gate
  timeutil.py          business-hours arithmetic (the weekend problem)
  ingest/documents.py  PDF -> authority-tagged sections
  policy/pack.py       policy-as-code loader + quote verifier
  policy/engine.py     cancellation, service credit, SLA, plan capability
  policy/severity.py   P1/P2/P3 classification against the policy's own definitions
  retrieval/index.py   BM25 + authority re-rank
  retrieval/conflicts.py  disagreement detection
  signals/detector.py  proactive operations sweep
  agent/tools.py       the 9 tools + authorisation
  agent/providers.py   model backends (Anthropic, OpenAI-compatible)
  agent/loop.py        agent loop
  agent/prompts.py     system prompts
  main.py              FastAPI app
  web/                 chat UI
api/index.py           Vercel entry point
data/
  source_docs/         the six policy and contract PDFs
  policy_pack/         extracted rules + their source quotes
  larkway_dataset.xlsx accounts, orders, tickets
scripts/
  generate_dataset.py  regenerates the fictional dataset
evals/  tests/
```

---

## The dataset

Everything in `data/` is fictional and generated by `scripts/generate_dataset.py`:
four customer accounts on three plans, seven orders across four carriers, eight
tickets, a current and a deprecated support policy, a cancellation & credit SOP, a
product guide with known issues, and two signed customer agreements. The traps are
deliberate — contracts that override the SOP, a stale shipment status, a weekend
snapshot, and wrong answers in the ticket history.

To change it, edit the generator and run:

```bash
pip install -r requirements-dev.txt
python scripts/generate_dataset.py
python -m app.ingest && python scripts/verify_policy_pack.py
```

---

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `PP_PROVIDER` | `anthropic` | `anthropic` or `openai_compat`. |
| `ANTHROPIC_API_KEY` | — | Claude only. |
| `PP_BASE_URL` / `PP_API_KEY` | — | `openai_compat` only. Ollama needs no key. |
| `PP_MODEL` | `claude-opus-5` / `gemini-3.8-flash` | Default depends on the provider. |
| `PP_FALLBACK_MODELS` | — | `openai_compat` only. Comma-separated models tried when the main one is rate-limited — free tiers cap tokens per model, so this keeps a demo answering. |
| `PP_EFFORT` | `high` | Claude only. `low`…`max`. |
| `PP_MAX_TOKENS` | `8000` | |
| `PP_MAX_AGENT_STEPS` | `10` | Safety stop on runaway tool loops. |
| `PP_REFUSAL_FALLBACK` | `true` | Claude only. Server-side fallback if the model declines; downgrades itself if the beta is not enabled on the key. |

## Assumptions

Stated in full in [ARCHITECTURE.md](ARCHITECTURE.md#assumptions), and surfaced in the UI
wherever an answer depends on one. The two that matter most:

1. **Business hours are Mon–Fri 09:00–18:00 IST.** The documents never define them, but
   "business hours" and "business days" appear throughout the SLA tables.
2. **An unqualified duration runs on the wall clock.** The support policy distinguishes
   "90 minutes" from "3 business hours" in the same table, so the distinction is treated
   as meaningful.
