"""The agent's tool surface.

Nine tools in four families:

  retrieval        search_policy_documents
  structured data  lookup_records
  calculation      evaluate_cancellation, evaluate_service_credit,
                   resolve_support_sla, check_plan_capability
  operations       detect_operations_signals
  state change     propose_action, execute_confirmed_action

Two properties are enforced here rather than in the prompt:

  * **Scope.** Every handler receives the Principal from the server session. Tool
    arguments never carry identity; an `account_id` argument is a filter, and the
    store re-derives what this caller may actually see.
  * **Confirmation.** `execute_confirmed_action` cannot execute anything the human
    has not confirmed through the UI. There is no tool that sets that flag.

Handlers return plain dicts. Anything they raise is turned into an `is_error`
tool_result so the model can recover — a denied lookup is information, not a crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .. import actions as action_store
from ..access import AccessDenied, Principal
from ..policy.engine import (
    evaluate_cancellation,
    evaluate_plan_capability,
    evaluate_service_credit,
    resolve_sla,
)
from ..retrieval.conflicts import detect, historical_contradictions
from ..retrieval.index import get_index
from ..signals.detector import detect_signals, summarise
from ..store import ENTITIES, all_rows, dataset_summary, query, snapshot_now
from ..timeutil import fmt


@dataclass
class ToolContext:
    """Per-turn accumulator that feeds the UI's trust panel."""

    principal: Principal
    citations: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    confidences: list[str] = field(default_factory=list)
    sources_seen: set[str] = field(default_factory=set)

    def note_citation(self, citation: dict[str, Any]) -> None:
        key = (citation.get("document"), citation.get("section"), citation.get("quote"))
        if key not in {(c.get("document"), c.get("section"), c.get("quote")) for c in self.citations}:
            self.citations.append(citation)
        if citation.get("document"):
            self.sources_seen.add(citation["document"])

    def absorb(self, decision: dict[str, Any]) -> None:
        for c in decision.get("citations", []):
            self.note_citation(c)
        for c in decision.get("conflicts", []):
            if c not in self.conflicts:
                self.conflicts.append(c)
        self.unknowns.extend(u for u in decision.get("unknowns", []) if u not in self.unknowns)
        self.assumptions.extend(a for a in decision.get("assumptions", []) if a not in self.assumptions)
        if decision.get("confidence"):
            self.confidences.append(decision["confidence"])
        if decision.get("requires_human_review") and decision.get("human_review_reason"):
            reason = decision["human_review_reason"]
            if reason not in self.escalation_reasons:
                self.escalation_reasons.append(reason)

    @property
    def overall_confidence(self) -> str:
        if not self.confidences:
            return "medium" if not self.citations else "high"
        rank = {"low": 1, "medium": 2, "high": 3}
        worst = min(self.confidences, key=lambda c: rank.get(c, 2))
        return worst

    def trust_panel(self) -> dict[str, Any]:
        return {
            "confidence": self.overall_confidence,
            "citations": self.citations,
            "conflicts": self.conflicts,
            "unknowns": self.unknowns,
            "assumptions": self.assumptions,
            "escalation_reasons": self.escalation_reasons,
            "sources_consulted": sorted(self.sources_seen),
            "pending_actions": self.proposals,
        }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _search_policy_documents(
    ctx: ToolContext,
    query_text: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    hits, stale = get_index().search(
        ctx.principal, query_text, top_k=top_k, doc_types=doc_types,
        include_superseded=include_superseded,
    )
    tickets = all_rows("tickets") if ctx.principal.is_internal else []
    historical = historical_contradictions(query_text, tickets)
    conflicts = detect(hits, stale, historical)
    for c in conflicts:
        if c not in ctx.conflicts:
            ctx.conflicts.append(c)
    for h in hits:
        ctx.note_citation(
            {
                "document": h.section.doc_title,
                "section": h.section.heading,
                "quote": h.section.text[:240],
                "authority_tier": h.section.tier,
                "status": h.section.status,
                "effective": h.section.effective,
            }
        )
    return {
        "query": query_text,
        "results": [h.as_dict() for h in hits],
        "superseded_matches": [
            {
                **h.as_dict(300),
                "warning": (
                    "This document is DEPRECATED and was excluded from the answer. "
                    "Do not quote its figures as current."
                ),
            }
            for h in stale
        ],
        "conflicts": conflicts,
        "authority_rule": (
            "Signed customer agreement > current policy/SOP > current product "
            "documentation > historical tickets (context only)."
        ),
    }


def _lookup_records(
    ctx: ToolContext,
    entity: str,
    filters: dict[str, Any] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    if entity not in ENTITIES:
        raise ValueError(f"entity must be one of {list(ENTITIES)}")
    rows = query(ctx.principal, entity, filters or {}, limit=limit)
    scope = (
        "all accounts"
        if ctx.principal.is_internal
        else f"restricted to {ctx.principal.account_id}"
    )
    return {
        "entity": entity,
        "filters_applied": filters or {},
        "access_scope": scope,
        "redacted_fields": (
            []
            if ctx.principal.can("read_internal_fields")
            else sorted(
                {"accounts": ["csm", "notes", "premium_support"],
                 "orders": ["carrier_fault", "customer_fault", "notes"],
                 "tickets": ["assigned_to", "historical_resolution", "channel"]}[entity]
            )
        ),
        "count": len(rows),
        "rows": rows,
        "reference_time": fmt(snapshot_now()),
    }


def _evaluate_cancellation(ctx: ToolContext, order_id: str) -> dict[str, Any]:
    from ..store import account_of

    account = account_of("orders", order_id)
    if account is None:
        raise LookupError(f"Order {order_id} not found.")
    ctx.principal.assert_owns("Order", order_id, account)
    decision = evaluate_cancellation(order_id).as_dict()
    ctx.absorb(decision)
    return decision


def _evaluate_service_credit(
    ctx: ToolContext,
    order_id: str | None = None,
    delay_hours: float | None = None,
    carrier_fault: bool | None = None,
    customer_fault: bool | None = None,
    shipment_fee_inr: int | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    from ..store import account_of

    if order_id:
        account = account_of("orders", order_id)
        if account is None:
            raise LookupError(f"Order {order_id} not found.")
        ctx.principal.assert_owns("Order", order_id, account)
        resolved_account = account
    else:
        resolved_account = ctx.principal.scope_account(account_id) or ctx.principal.account_id
        if resolved_account is None:
            raise ValueError(
                "Which account is this about? Staff must pass account_id for a "
                "hypothetical, because the applicable terms differ by contract."
            )
    decision = evaluate_service_credit(
        order_id=order_id,
        account_id=resolved_account,
        delay_hours=delay_hours,
        carrier_fault=carrier_fault,
        customer_fault=customer_fault,
        shipment_fee_inr=shipment_fee_inr,
    ).as_dict()
    ctx.absorb(decision)
    return decision


def _resolve_support_sla(
    ctx: ToolContext,
    severity: str | None = None,
    account_id: str | None = None,
    ticket_id: str | None = None,
) -> dict[str, Any]:
    from ..policy.severity import classify_ticket
    from ..store import account_of, get_record_unscoped

    requested = (severity or "").upper().strip() or None
    classification = None
    if ticket_id:
        owner = account_of("tickets", ticket_id)
        if owner is None:
            raise LookupError(f"Ticket {ticket_id} not found.")
        ctx.principal.assert_owns("Ticket", ticket_id, owner)
        account_id = owner
        # Severity sets the SLA clock, so for a real ticket it comes from the
        # classifier against the policy's own definitions — the same call the ops
        # sweep makes — never from the model's guess. Otherwise the chat and the
        # dashboard could disagree about the same ticket.
        classification = classify_ticket(get_record_unscoped("tickets", ticket_id))
        severity = classification.severity
    else:
        account_id = ctx.principal.scope_account(account_id) or ctx.principal.account_id
        if account_id is None:
            raise ValueError("account_id or ticket_id is required.")
        if not requested:
            raise ValueError("severity is required when no ticket_id is given.")
        severity = requested
    decision = resolve_sla(account_id, severity, ticket_id=ticket_id).as_dict()
    if classification is not None:
        decision["severity_classification"] = classification.as_dict()
        if requested and requested != severity:
            decision["severity_override_ignored"] = (
                f"{requested} was requested, but {ticket_id} classifies as {severity} "
                f"under the support policy's definitions: {classification.rationale} "
                f"Measured as {severity}. If you believe the classification is wrong, "
                f"say so and recommend a human review rather than restating it."
            )
    ctx.absorb(decision)
    return decision


def _check_plan_capability(
    ctx: ToolContext, capability: str = "bulk_upload", account_id: str | None = None
) -> dict[str, Any]:
    account_id = ctx.principal.scope_account(account_id) or ctx.principal.account_id
    if account_id is None:
        raise ValueError("account_id is required for staff users.")
    decision = evaluate_plan_capability(account_id, capability).as_dict()
    ctx.absorb(decision)
    return decision


def _detect_operations_signals(
    ctx: ToolContext,
    categories: list[str] | None = None,
    account_id: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    ctx.principal.require("read_operations_signals")
    signals = detect_signals(categories=categories, account_id=account_id)
    for s in signals[:limit]:
        for c in s.citations:
            ctx.note_citation(c)
    return {
        "summary": summarise(signals),
        "signals": [s.as_dict() for s in signals[:limit]],
        "note": (
            "Signals are computed from the same policy engine the chat answers use, so "
            "the dashboard and the chat cannot disagree."
        ),
    }


def for_model(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """The view of a tool result that goes back to the model.

    The UI's tool trace and the trust panel always get the full result. The model
    only needs enough to write the answer, and for the ops sweep the full result is
    ~4k tokens — mostly citation metadata repeated per signal, which the trust panel
    already collects. Sending all of it blows through free-tier per-minute caps and
    costs tokens on every provider, so the sweep is compacted. Nothing a decision
    depends on is dropped: priority, the computed detail, the evidence ids and the
    recommended action all stay.
    """
    if tool_name != "detect_operations_signals" or "signals" not in result:
        return result
    compact = []
    for s in result["signals"]:
        sources = list(dict.fromkeys(
            f"{c.get('document')} ({c.get('section')})" for c in s.get("citations", [])
        ))
        action = s.get("recommended_action") or {}
        compact.append({
            "signal_id": s.get("signal_id"),
            "priority": s.get("priority"),
            "category": s.get("category"),
            "title": s.get("title"),
            "detail": s.get("detail"),
            "evidence": s.get("evidence"),
            "accounts": s.get("accounts"),
            "recommended_action": action.get("action_type"),
            "sources": sources,
        })
    return {
        "summary": result.get("summary"),
        "signals": compact,
        "note": (
            result.get("note", "") + " Full evidence and citations are shown to the user "
            "in the tool trace; recommended actions can be prepared with propose_action."
        ).strip(),
    }


def _propose_action(
    ctx: ToolContext, action_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    action = action_store.propose(ctx.principal, action_type, payload)
    record = action.as_dict()
    ctx.proposals.append(record)
    return {
        **record,
        "next_step": (
            "NOTHING HAS CHANGED YET. The user must press Confirm on this proposal in "
            "the interface. Tell them exactly what will happen and wait. Do not call "
            "execute_confirmed_action until they have confirmed."
        ),
    }


def _execute_confirmed_action(ctx: ToolContext, action_id: str) -> dict[str, Any]:
    ctx.principal.require("propose_action")
    result = action_store.execute(ctx.principal, action_id)
    return {
        **result,
        "note": "Executed against the mock ticketing/billing backend.",
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    family: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    internal_only: bool = False
    label: str = ""

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }


TOOLS: list[Tool] = [
    Tool(
        name="search_policy_documents",
        family="retrieval",
        label="Searching policies, SOPs and agreements",
        description=(
            "Search Larkway's policies, SOPs, product documentation and signed "
            "customer agreements. Results are ranked by authority, not just similarity: "
            "a signed agreement outranks the current policy, which outranks product "
            "documentation. Superseded documents are excluded from results and reported "
            "separately so you can name a conflict instead of quoting stale terms. "
            "Use this for any question about what the rules say."
        ),
        schema={
            "type": "object",
            "properties": {
                "query_text": {
                    "type": "string",
                    "description": "Natural-language description of what you need to find.",
                },
                "top_k": {"type": "integer", "description": "Max results (default 5)."},
                "doc_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["policy", "sop", "product_doc", "agreement"],
                    },
                    "description": "Optional filter by document type.",
                },
                "include_superseded": {
                    "type": "boolean",
                    "description": (
                        "Include DEPRECATED documents in the main results. Only set this "
                        "when the user is explicitly asking what an old policy used to say."
                    ),
                },
            },
            "required": ["query_text"],
        },
        handler=_search_policy_documents,
    ),
    Tool(
        name="lookup_records",
        family="data",
        label="Querying account, order and ticket data",
        description=(
            "Query Larkway's operational data: accounts, orders and tickets. "
            "Results are automatically scoped to what the signed-in user may see, and "
            "internal-only columns are stripped for customer sessions. Filters match "
            "exactly on column values, e.g. {\"status\": \"BOOKED\"} or "
            "{\"carrier\": \"TransVelo\"}."
        ),
        schema={
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": list(ENTITIES)},
                "filters": {
                    "type": "object",
                    "description": (
                        "Column/value pairs. A list of values matches any of them. "
                        "account_id is treated as a filter request and is re-checked "
                        "against the caller's permissions."
                    ),
                    "additionalProperties": True,
                },
                "limit": {"type": "integer", "description": "Max rows (default 25)."},
            },
            "required": ["entity"],
        },
        handler=_lookup_records,
    ),
    Tool(
        name="evaluate_cancellation",
        family="calculation",
        label="Calculating cancellation eligibility and fee",
        description=(
            "Decide whether a specific order can be cancelled and what fee applies. "
            "Applies the SOP's status rules and its free-cancellation window, then lets any "
            "signed agreement override the fee, and flags when a status may be stale "
            "because of a known carrier webhook delay. Returns the full reasoning trace "
            "and citations. Always use this rather than working the fee out yourself."
        ),
        schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=_evaluate_cancellation,
    ),
    Tool(
        name="evaluate_service_credit",
        family="calculation",
        label="Calculating service-credit entitlement",
        description=(
            "Decide whether a failed-pickup service credit is owed, and how much. Pass "
            "order_id to evaluate a real order. For a hypothetical the customer describes "
            "('my pickup was three hours late'), pass delay_hours plus what is known about "
            "fault — the applicable threshold and amount differ by contract, so never "
            "answer this from the SOP alone. Leaving a fault field out is meaningful: the "
            "SOP forbids promising a credit while it is unknown, and the tool will say so."
        ),
        schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Evaluate a real order."},
                "account_id": {
                    "type": "string",
                    "description": "Required for a hypothetical when the caller is staff.",
                },
                "delay_hours": {
                    "type": "number",
                    "description": "Hours past the END of the scheduled pickup window.",
                },
                "carrier_fault": {"type": "boolean"},
                "customer_fault": {"type": "boolean"},
                "shipment_fee_inr": {"type": "integer"},
            },
        },
        handler=_evaluate_service_credit,
    ),
    Tool(
        name="resolve_support_sla",
        family="calculation",
        label="Resolving SLA target and breach status",
        description=(
            "Work out the first-response target for an account at a given severity, and "
            "whether it has been breached. Uses the account's signed agreement when one "
            "exists, otherwise the plan default. Business-hours targets are measured on "
            "the support calendar, not wall-clock time — this matters, and the tool "
            "reports both. Pass ticket_id to measure a real ticket: its severity is then "
            "classified from the ticket against the policy's definitions, so omit "
            "severity. Pass severity only for a hypothetical with no ticket."
        ),
        schema={
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["P1", "P2", "P3"],
                    "description": "Only for a hypothetical. Ignored when ticket_id is given.",
                },
                "account_id": {"type": "string"},
                "ticket_id": {
                    "type": "string",
                    "description": "Measures elapsed time from this ticket's created_at.",
                },
            },
        },
        handler=_resolve_support_sla,
    ),
    Tool(
        name="check_plan_capability",
        family="calculation",
        label="Checking plan entitlements and known issues",
        description=(
            "Check whether a product capability is included on an account's plan and what "
            "its documented limits are, together with any open known issue that changes "
            "what actually works today. Use this for questions about bulk upload."
        ),
        schema={
            "type": "object",
            "properties": {
                "capability": {"type": "string", "enum": ["bulk_upload"]},
                "account_id": {"type": "string"},
            },
        },
        handler=_check_plan_capability,
    ),
    Tool(
        name="detect_operations_signals",
        family="operations",
        label="Scanning support activity for emerging issues",
        description=(
            "Internal only. Sweep all support activity at the dataset snapshot and return "
            "what deserves attention: SLA breaches measured against each account's own "
            "contract, repeat and cross-account issues, tickets explained by a known "
            "issue, overdue pickups, credits owed that nobody has claimed, and past "
            "answers that current policy contradicts."
        ),
        schema={
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "sla", "repeat_issue", "cross_account_impact", "known_issue",
                            "order_anomaly", "unclaimed_entitlement", "incorrect_past_guidance",
                        ],
                    },
                },
                "account_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        handler=_detect_operations_signals,
        internal_only=True,
    ),
    Tool(
        name="propose_action",
        family="action",
        label="Preparing an action for confirmation",
        description=(
            "Prepare a state-changing action and show it to the user for confirmation. "
            "This changes NOTHING — it returns a preview and an action_id. The user must "
            "then press Confirm in the interface. After you call this, explain plainly "
            "what will happen and stop; do not assume confirmation. Actions: "
            "create_escalation (ticket_id, severity, reason), update_ticket (ticket_id + "
            "status/severity/assigned_to/note), create_followup_task (title, due, "
            "optional ticket_id/order_id/owner/details), issue_service_credit (order_id, "
            "amount_inr, reason), cancel_order (order_id, optional fee_inr/reason)."
        ),
        schema={
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": list(action_store.ACTION_TYPES.keys()),
                },
                "payload": {
                    "type": "object",
                    "description": "Fields for this action type. See the tool description.",
                    "additionalProperties": True,
                },
            },
            "required": ["action_type", "payload"],
        },
        handler=_propose_action,
    ),
    Tool(
        name="execute_confirmed_action",
        family="action",
        label="Executing a confirmed action",
        description=(
            "Execute an action the user has already confirmed in the interface. This "
            "fails unless a human pressed Confirm on that exact action_id — you cannot "
            "confirm on the user's behalf, and there is no way around it. Only call this "
            "after the conversation shows the user confirmed."
        ),
        schema={
            "type": "object",
            "properties": {"action_id": {"type": "string"}},
            "required": ["action_id"],
        },
        handler=_execute_confirmed_action,
    ),
]

BY_NAME = {t.name: t for t in TOOLS}


def tools_for(principal: Principal) -> list[Tool]:
    return [t for t in TOOLS if not t.internal_only or principal.is_internal]


def specs_for(principal: Principal) -> list[dict[str, Any]]:
    return [t.spec() for t in tools_for(principal)]


def describe_argument(tool_name: str, args: dict[str, Any]) -> str:
    """Short human-readable subtitle for the UI's tool trace."""
    if tool_name == "search_policy_documents":
        return f"“{args.get('query_text', '')}”"
    if tool_name == "lookup_records":
        filters = args.get("filters") or {}
        bits = ", ".join(f"{k}={v}" for k, v in filters.items()) or "all visible rows"
        return f"{args.get('entity', '')} — {bits}"
    if tool_name in {"evaluate_cancellation"}:
        return str(args.get("order_id", ""))
    if tool_name == "evaluate_service_credit":
        return str(args.get("order_id") or f"{args.get('delay_hours', '?')}h late (hypothetical)")
    if tool_name == "resolve_support_sla":
        return " ".join(str(v) for v in [args.get("severity"), args.get("ticket_id") or args.get("account_id")] if v)
    if tool_name == "check_plan_capability":
        return str(args.get("capability", "bulk_upload"))
    if tool_name == "detect_operations_signals":
        cats = args.get("categories")
        return ", ".join(cats) if cats else "all categories"
    if tool_name == "propose_action":
        return str(args.get("action_type", ""))
    if tool_name == "execute_confirmed_action":
        return str(args.get("action_id", ""))
    return ""


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Execute a tool. Returns (result, is_error)."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": f"Unknown tool '{name}'."}, True
    if tool.internal_only and not ctx.principal.is_internal:
        return {"error": f"'{name}' is available to Larkway staff only."}, True
    try:
        return tool.handler(ctx, **(args or {})), False
    except AccessDenied as exc:
        return {
            "error": "access_denied",
            "message": str(exc),
            "guidance": (
                "Do not retry with a different account. Tell the user plainly that this "
                "record belongs to another account and offer to help with their own."
            ),
        }, True
    except PermissionError as exc:
        return {"error": "not_confirmed", "message": str(exc)}, True
    except (LookupError, ValueError, TypeError) as exc:
        return {"error": type(exc).__name__, "message": str(exc)}, True


def dataset_context() -> dict[str, Any]:
    return dataset_summary()
