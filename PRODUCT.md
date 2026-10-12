# Product Note

## Two problems, one engine

A support agent for this kind of business has two jobs that look separate but are not.

Trust asks: *is today's answer right, and does the user know how much to
lean on it?* Proactive detection asks: *what is going wrong that nobody has
reported?* Once the system can decide correctly against conflicting sources, running
that same decision across every record turns it into a detector — and the most valuable
thing the detector finds is **answers Larkway already got wrong**.

So the proactive view is not a second feature bolted on. It is the trust engine pointed
backwards.

### Trust and reliability

Four things a support team can actually check:

1. **Rules that cannot drift from the documents.** Every number the engine uses sits in
   YAML beside the verbatim clause it came from, and a verifier re-reads the PDFs to
   confirm the quote is still there. Startup, tests, Docker build. A changed document
   breaks the build instead of quietly changing an answer.
2. **Precedence applied in code, and narrated.** Agreement beats policy beats product
   doc beats history. Answers say which source won *and what the default would have
   been* — "the Enterprise default would have been 30 minutes; your agreement's 15
   minutes governs."
3. **Uncertainty as an outcome, not a hedge.** Missing facts produce
   `undetermined / low confidence / requires human review`, and the trust panel lists
   exactly which facts were missing. The SOP's "do not promise a credit when fault is
   unknown" is implemented, not quoted.
4. **A visible trust panel on every answer** — confidence, every source with its
   authority tier and status, conflicts detected and how they were resolved, unknown
   facts, assumptions, and why human review is advised. A support lead can audit an
   answer without reading the code.

The sharpest case in the dataset: SHP-5001 is TransVelo, `BOOKED`, inside its pickup
window. KI-322 says those webhooks lag 35 minutes, so `BOOKED` may be stale — and the
SOP forbids cancelling something already collected. The system answers "no fee, your
agreement waives it" **and** insists on carrier verification first. The correct answer
is not the confident one.

### Proactive issue detection

Fourteen signals at the snapshot, prioritised, each with evidence and a recommended
action. Two breached P1s (measured against each account's own contract, one of which is
10 minutes rather than the plan's 20), a bulk-upload cluster hitting two customers and
tied to KI-314, an INR 2,000 credit owed that nobody has asked for (and that needs a
manager's sign-off) — and the guidance audit:

- **CASE-240** — Halcyon Foods was quoted an INR 350 cancellation fee their signed
  agreement waives outright. If it was collected, that is a refund.
- **CASE-251** — Brightmoor was told their plan caps at 2,500 rows. The product guide
  says 8,000; the lower number is bug KI-314. That customer has been under-using a
  feature they pay for.

Neither generated a complaint. Both were found by re-running today's rules against past
resolutions. This is the argument for the whole approach: a system that can decide
correctly can also tell you where you decided incorrectly before.

---

## The metric

**Resolution rate without human correction** — the share of conversations that end with
the customer's question answered, where no human afterwards changes the substance of
what the agent said.

Why this one:

- It cannot be gamed from either end. Escalating everything tanks resolution; answering
  confidently and wrongly shows up as correction. A support product has to be judged on
  both halves at once.
- It is **measurable from day one** without a labelling exercise. Every answer already
  carries its decision, citations and confidence; a correction is an agent editing the
  outcome or a customer coming back on the same issue.
- It maps to the thing that actually kills adoption. One confidently wrong answer about
  money costs more trust than ten honest escalations.

Segment it by confidence band. If low-confidence answers are frequently corrected, the
system is under-escalating. If **high**-confidence answers are being corrected, that is
the alarm — it means the trust signal itself is lying, and everything above should stop
until it is fixed.

Supporting metrics I would watch, not optimise: escalation rate by reason (a rising
"required fact unknown" points at a data gap, not a model gap), and time-to-detection
for proactive signals.

---

## What I would build next

Ordered by value per unit of effort.

**1. Close the loop on the guidance audit.** Right now it finds bad past answers. It
should open the refund, draft the correction, and track whether the customer was
actually re-contacted. The finding is worth little until someone acts on it — and this
is the shortest path from "clever demo" to money back in a customer's account.

**2. Write-back to the real systems.** Actions are mocked. The confirmation gate, the
audit trail and the role checks are the hard parts and they are built; what remains is
adapters to the ticketing and billing APIs, with idempotency keys and a reconciliation
job. Until then, a human retypes every confirmed action, which is most of the saving
lost.

**3. Answer-level feedback wired to the metric.** A thumbs-down that captures the
conversation, the tool trace and the trust panel makes every bad answer a reproducible
test case. Cheap to build, and it is what makes the metric above real rather than
aspirational.

**4. Policy-change simulation.** Before publishing SOP v5, run it against the last
90 days of resolved tickets and show what would have changed. Policy authors currently
have no way to see the blast radius of an edit. The engine already supports this —
it takes a rule set and a set of facts.

**5. Ingestion that survives a real document set.** Six clean one-page PDFs flatter the
parser. Real support corpora have tables, scans, appendices and near-duplicate contract
versions. This needs layout-aware extraction, a review step where a human confirms
extracted rules before they go live, and versioned rule history.

**6. Conversation memory and a customer timeline.** The agent is stateless across
sessions. A customer who asked about SHP-5001 yesterday should not re-explain today, and
the agent should see that this is the third time this month.

**7. Retrieval that scales past this corpus.** Hybrid BM25 + embeddings with the
authority re-rank kept on top. Deliberately last: it is the change most likely to be
built first out of habit, and at 27 sections it would buy nothing.

---

## What I deliberately left out

- **A vector database.** 27 sections. Embeddings would have added a dependency, a build
  step and a silent failure mode in exchange for no measurable gain. The `SectionIndex`
  interface is the seam to swap behind when the corpus justifies it.
- **Real authentication.** Six demo principals with a
  role/capability model that a real IdP drops into; the enforcement points do not move.
- **A frontend framework.** One deployable, no build step, full control over the
  streaming tool trace. This is not a React exercise, and a build step is one more thing
  to break in review.
- **Multi-turn conversation persistence.** Sessions are in-memory and single-instance —
  correct for a demo, wrong for production, and a swap rather than a redesign.
- **Automated rule extraction from the PDFs.** I chose a curated policy pack with a
  verifier instead. Regex over prose fails silently; a verified quote fails loudly. When
  the output is money, loud is better.
- **An LLM-as-judge eval.** The evals assert facts and forbidden phrases instead.
  Judging a system whose selling point is determinism with a non-deterministic grader
  seemed like the wrong instinct.
- **Streaming the ops dashboard / websockets.** It is computed at a fixed snapshot; a
  request-response fetch is honest about that.

---

## What I would want a reviewer to push on

Three places where I made a call that could reasonably go the other way:

1. **The curated policy pack.** It is the highest-leverage design decision and the most
   arguable — someone could fairly call it hard-coding. My defence is the verifier: the
   rules are checked against the documents on every build, so they cannot drift. But it
   does mean a new document needs a human to write its rules.
2. **Severity is inferred by rules, not by the model.** Regexes over ticket text are
   crude and will not generalise to a real ticket volume. I chose them because severity
   sets the SLA clock and an unexplainable classification is worse than a crude one. A
   model classifier with the rule set as a validation layer is probably the right end
   state.
3. **The business-hours assumption.** The documents never define them, and the snapshot
   falling on a Saturday makes the choice unusually consequential. I picked Mon–Fri
   09:00–18:00 IST and surfaced it in every answer that depends on it — but if
   the real calendar differs, several SLA answers change.
