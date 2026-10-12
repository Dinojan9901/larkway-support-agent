"""Proactive issue detection.

A reactive chatbot only helps once someone asks. This module sweeps the whole
support surface at the dataset snapshot and answers "what deserves attention right
now", without anyone having to ask a question.

Every signal is computed deterministically from the same policy engine the chat
agent uses, so the dashboard and the chat answer can never disagree. Each signal
carries its evidence (the record ids it is built from) and a recommended next action,
which the agent can turn into a confirmable proposal.

Detectors:
  1. SLA breach / at-risk, using each account's own contractual target.
  2. Repeat issues — the same product problem hit more than once.
  3. Known-issue correlation against the product guide.
  4. Cross-account impact — one defect touching several customers.
  5. Operational anomalies in orders (overdue pickups, cancellations in limbo).
  6. Unclaimed entitlements — credits owed that nobody has asked for.
  7. Guidance audit — past answers that contradict current authoritative sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..policy.engine import evaluate_service_credit, resolve_sla
from ..policy.pack import cite, product, sop
from ..policy.severity import classify_ticket
from ..store import all_rows, snapshot_now
from ..timeutil import fmt, humanise_minutes, parse_dt

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Signal:
    signal_id: str
    category: str
    priority: str
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    recommended_action: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "category": self.category,
            "priority": self.priority,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "accounts": self.accounts,
            "recommended_action": self.recommended_action,
            "citations": self.citations,
            "metrics": self.metrics,
        }


def _account_names() -> dict[str, str]:
    return {a["account_id"]: a.get("account_name", a["account_id"]) for a in all_rows("accounts")}


def _open_tickets() -> list[dict]:
    return [t for t in all_rows("tickets") if str(t.get("status", "")).lower() == "open"]


def _normalise_subject(text: str) -> str:
    """Collapse a subject to its issue shape so near-duplicates cluster together."""
    lowered = (text or "").lower()
    lowered = re.sub(r"\d[\d,]*", "#", lowered)          # 4,200-row -> #-row
    lowered = re.sub(r"[^a-z# ]+", " ", lowered)
    stop = {"the", "a", "an", "for", "is", "are", "on", "of", "to", "after", "with", "in"}
    return " ".join(w for w in lowered.split() if w not in stop)


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())}


# ---------------------------------------------------------------------------
# 1. SLA pressure
# ---------------------------------------------------------------------------
def _sla_signals(now: datetime) -> list[Signal]:
    out: list[Signal] = []
    names = _account_names()
    for ticket in _open_tickets():
        call = classify_ticket(ticket)
        try:
            sla = resolve_sla(
                ticket["account_id"], call.severity, ticket_id=ticket["ticket_id"], at=now
            )
        except (LookupError, ValueError):
            continue
        if sla.outcome not in {"breached", "at_risk"} and call.severity != "P1":
            continue

        breached = sla.outcome == "breached"
        priority = "critical" if (breached and call.severity == "P1") else (
            "high" if breached or call.severity == "P1" else "medium"
        )
        out.append(
            Signal(
                signal_id=f"SIG-SLA-{ticket['ticket_id']}",
                category="sla",
                priority=priority,
                title=(
                    f"{ticket['ticket_id']} ({call.severity}) "
                    f"{'BREACHED' if breached else 'at risk'} — {names.get(ticket['account_id'], '')}"
                ),
                detail=f"{sla.summary} {call.rationale}",
                evidence=[ticket["ticket_id"]],
                accounts=[ticket["account_id"]],
                citations=[c.as_dict() for c in sla.citations],
                metrics={
                    "severity": call.severity,
                    "severity_confidence": call.confidence,
                    "target": sla.facts.get("target"),
                    "target_source": sla.facts.get("target_source"),
                    "clock": sla.facts.get("clock"),
                    "due_at": sla.facts.get("due_at"),
                    "overdue_by": sla.facts.get("overdue_by"),
                    "remaining": sla.facts.get("remaining"),
                    "opened_at": ticket.get("created_at"),
                },
                recommended_action={
                    "action_type": "create_escalation",
                    "payload": {
                        "ticket_id": ticket["ticket_id"],
                        "severity": call.severity,
                        "reason": sla.summary,
                    },
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# 2 & 4. Repeat issues and cross-account impact
# ---------------------------------------------------------------------------
def _cluster_signals(now: datetime) -> list[Signal]:
    tickets = all_rows("tickets")
    names = _account_names()
    clusters: dict[str, list[dict]] = {}
    for ticket in tickets:
        key = _normalise_subject(ticket.get("subject", ""))
        best = None
        for existing in clusters:
            a, b = set(key.split()), set(existing.split())
            if a and b and len(a & b) / len(a | b) >= 0.5:
                best = existing
                break
        clusters.setdefault(best or key, []).append(ticket)

    out: list[Signal] = []
    for key, group in clusters.items():
        if len(group) < 2:
            continue
        accounts = sorted({t["account_id"] for t in group})
        open_count = sum(1 for t in group if str(t.get("status", "")).lower() == "open")
        multi = len(accounts) > 1
        out.append(
            Signal(
                signal_id=f"SIG-CLUSTER-{abs(hash(key)) % 10000:04d}",
                category="cross_account_impact" if multi else "repeat_issue",
                priority="high" if (multi and open_count) else "medium",
                title=(
                    f"{len(group)} tickets on the same issue"
                    + (f" across {len(accounts)} accounts" if multi else
                       f" from {names.get(accounts[0], accounts[0])}")
                    + f": \"{group[0].get('subject')}\""
                ),
                detail=(
                    f"{open_count} of {len(group)} are still open. Recurrence across "
                    f"separate reports usually means a product defect rather than a "
                    f"per-customer configuration problem."
                    if not multi else
                    f"{open_count} of {len(group)} are still open and more than one customer "
                    f"is affected, which points at a platform-wide defect."
                ),
                evidence=[t["ticket_id"] for t in group],
                accounts=accounts,
                metrics={
                    "ticket_count": len(group),
                    "open_count": open_count,
                    "accounts_affected": len(accounts),
                    "first_seen": min((t.get("created_at") or "") for t in group),
                    "last_seen": max((t.get("created_at") or "") for t in group),
                },
                recommended_action={
                    "action_type": "create_followup_task",
                    "payload": {
                        "title": f"Investigate repeat issue: {group[0].get('subject')}",
                        "due": "next business day",
                        "details": f"Tickets: {', '.join(t['ticket_id'] for t in group)}",
                    },
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# 3. Known-issue correlation
# ---------------------------------------------------------------------------
def _known_issue_signals(now: datetime) -> list[Signal]:
    out: list[Signal] = []
    names = _account_names()
    for ki in product()["known_issues"]:
        matched = []
        for ticket in _open_tickets():
            haystack = f"{ticket.get('subject', '')} {ticket.get('description', '')}".lower()
            if any(term.lower() in haystack for term in ki["match_any"]):
                matched.append(ticket)
        if not matched:
            continue
        accounts = sorted({t["account_id"] for t in matched})
        out.append(
            Signal(
                signal_id=f"SIG-KI-{ki['id']}",
                category="known_issue",
                priority="high" if len(accounts) > 1 else "medium",
                title=(
                    f"{ki['id']} ({ki['state']}) explains {len(matched)} open ticket(s): "
                    f"{ki['title']}"
                ),
                detail=(
                    f"{ki['quote']} "
                    + (f"Workaround: {ki['workaround']}. " if ki.get("workaround") else "")
                    + (f"Caution: {ki['caution']}" if ki.get("caution") else "")
                ),
                evidence=[t["ticket_id"] for t in matched],
                accounts=accounts,
                citations=[cite(product()["doc_id"], ki["section"], ki["quote"]).as_dict()],
                metrics={
                    "known_issue": ki["id"],
                    "state": ki["state"],
                    "opened": ki["opened"],
                    "accounts_affected": len(accounts),
                    "affected_customers": [names.get(a, a) for a in accounts],
                },
                recommended_action={
                    "action_type": "update_ticket",
                    "payload": {
                        "ticket_id": matched[0]["ticket_id"],
                        "note": f"Linked to {ki['id']} ({ki['state']}).",
                    },
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# 5 & 6. Order anomalies and unclaimed entitlements
# ---------------------------------------------------------------------------
def _order_signals(now: datetime) -> list[Signal]:
    out: list[Signal] = []
    names = _account_names()
    for order in all_rows("orders"):
        status = str(order.get("status", "")).upper()
        window_end = parse_dt(order.get("pickup_window_end"))
        picked = parse_dt(order.get("pickup_actual_at"))
        account = order.get("account_id")

        # Overdue pickup that nobody has closed out.
        if status == "BOOKED" and window_end and not picked and now > window_end:
            overdue = int((now - window_end).total_seconds() // 60)
            credit = None
            try:
                credit = evaluate_service_credit(order_id=order["order_id"], at=now)
            except LookupError:
                pass

            entitled = credit is not None and credit.outcome == "eligible"
            out.append(
                Signal(
                    signal_id=f"SIG-PICKUP-{order['order_id']}",
                    category="unclaimed_entitlement" if entitled else "order_anomaly",
                    priority="high" if entitled else "medium",
                    title=(
                        f"{order['order_id']} pickup is {humanise_minutes(overdue)} overdue"
                        + (
                            f" — INR {credit.facts.get('credit_amount_inr'):,} credit is owed and "
                            f"has not been requested"
                            if entitled else ""
                        )
                    ),
                    detail=(
                        (credit.summary + " ") if credit else ""
                    )
                    + (
                        "The customer has not raised a ticket for this. Getting to them first "
                        "is cheaper than an escalation later."
                        if entitled
                        else "Carrier fault is not established, so no credit can be promised yet."
                    ),
                    evidence=[order["order_id"]],
                    accounts=[account],
                    citations=[c.as_dict() for c in (credit.citations if credit else [])],
                    metrics={
                        "carrier": order.get("carrier"),
                        "overdue_by": humanise_minutes(overdue),
                        "window_end": fmt(window_end),
                        "carrier_fault": order.get("carrier_fault"),
                        "credit_amount_inr": credit.facts.get("credit_amount_inr") if credit else None,
                        "customer": names.get(account, account),
                    },
                    recommended_action=(credit.suggested_action if entitled else {
                        "action_type": "create_followup_task",
                        "payload": {
                            "title": f"Chase carrier on overdue pickup for {order['order_id']}",
                            "due": "today",
                            "order_id": order["order_id"],
                        },
                    }),
                )
            )

        # Cancellation requested but the order is still sitting in BOOKED.
        requested = parse_dt(order.get("cancellation_requested_at"))
        if status == "BOOKED" and requested and requested <= now:
            waiting = int((now - requested).total_seconds() // 60)
            if waiting >= 0:
                out.append(
                    Signal(
                        signal_id=f"SIG-CANCEL-{order['order_id']}",
                        category="order_anomaly",
                        priority="medium",
                        title=(
                            f"{order['order_id']} has an unactioned cancellation request "
                            f"({humanise_minutes(waiting)} old)"
                        ),
                        detail=(
                            f"{names.get(account, account)} asked to cancel at {fmt(requested)} "
                            f"and the order is still BOOKED. Every minute it stays open risks "
                            f"the parcel being collected, after which cancellation is off the "
                            f"table and return-to-origin is the only route."
                        ),
                        evidence=[order["order_id"]],
                        accounts=[account],
                        metrics={
                            "requested_at": fmt(requested),
                            "waiting": humanise_minutes(waiting),
                            "carrier": order.get("carrier"),
                        },
                        recommended_action={
                            "action_type": "cancel_order",
                            "payload": {"order_id": order["order_id"]},
                        },
                    )
                )
    return out


# ---------------------------------------------------------------------------
# 7. Guidance audit — the trust engine, applied retrospectively
# ---------------------------------------------------------------------------
def _guidance_audit(now: datetime) -> list[Signal]:
    """Find past answers that the current authoritative sources contradict.

    This is the reverse of the chat agent's job: instead of getting today's answer
    right, it asks which answers Larkway already got wrong, so the team can go
    back to those customers.
    """
    from ..policy.engine import evaluate_cancellation, evaluate_plan_capability

    out: list[Signal] = []
    names = _account_names()
    fee = sop()["cancellation"]["by_status"]["BOOKED"]["fee_after_window_inr"]
    bulk_ki = next(
        (ki for ki in product()["known_issues"] if ki.get("kind") == "bulk_upload_degradation"),
        None,
    )
    for ticket in all_rows("tickets"):
        resolution = ticket.get("historical_resolution")
        if not resolution:
            continue
        account = ticket["account_id"]
        text = f"{ticket.get('subject', '')} {ticket.get('description', '')} {resolution}".lower()
        finding: tuple[str, str, list[dict]] | None = None

        if "cancel" in text:
            # Re-run today's rules against the account the advice was given to.
            orders = [
                o for o in all_rows("orders")
                if o["account_id"] == account and str(o.get("status", "")).upper() == "BOOKED"
            ]
            if orders:
                decision = evaluate_cancellation(orders[0]["order_id"], at=now)
                waived = decision.facts.get("fee_waived_by_agreement")
                charged = re.search(
                    rf"inr\s?{fee}|inr\s?{fee:,}|fee applied|fee of", resolution.lower()
                )
                if waived and charged:
                    finding = (
                        "A cancellation fee was quoted to this customer, but their signed "
                        "agreement waives the BOOKED cancellation fee outright.",
                        f"The customer may have been charged INR {fee:,} they did not owe. Worth a "
                        "billing check and, if it was collected, a refund.",
                        [c.as_dict() for c in decision.citations if c.tier == 1],
                    )

        if finding is None and ("bulk upload" in text or "csv" in text):
            decision = evaluate_plan_capability(account)
            documented = decision.facts.get("documented_max_rows")
            quoted = re.search(r"(\d[\d,]*)\s*rows?", resolution.lower())
            if documented and quoted:
                quoted_rows = int(quoted.group(1).replace(",", ""))
                ki_id = bulk_ki["id"] if bulk_ki else "an open known issue"
                if quoted_rows < documented and "only supports" in resolution.lower():
                    finding = (
                        f"The customer was told their plan only supports {quoted_rows:,} rows. "
                        f"The product guide documents {documented:,} rows; the lower number is "
                        f"the symptom of known issue {ki_id}, not a plan limit.",
                        "The customer is under-using a feature they are entitled to. Correct the "
                        f"record and point them at the {ki_id} workaround instead.",
                        [c.as_dict() for c in decision.citations],
                    )

        if finding:
            title, action_detail, citations = finding
            out.append(
                Signal(
                    signal_id=f"SIG-GUIDANCE-{ticket['ticket_id']}",
                    category="incorrect_past_guidance",
                    priority="high",
                    title=f"{ticket['ticket_id']}: past answer contradicts current policy",
                    detail=f"{title} {action_detail} Recorded resolution: \"{resolution}\"",
                    evidence=[ticket["ticket_id"]],
                    accounts=[account],
                    citations=citations,
                    metrics={
                        "customer": names.get(account, account),
                        "answered_on": ticket.get("created_at"),
                        "recorded_resolution": resolution,
                    },
                    recommended_action={
                        "action_type": "create_followup_task",
                        "payload": {
                            "title": f"Re-contact {names.get(account, account)} about {ticket['ticket_id']}",
                            "due": "next business day",
                            "ticket_id": ticket["ticket_id"],
                            "details": action_detail,
                        },
                    },
                )
            )
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def detect_signals(
    at: datetime | None = None,
    categories: list[str] | None = None,
    account_id: str | None = None,
) -> list[Signal]:
    now = at or snapshot_now()
    signals: list[Signal] = []
    for fn in (_sla_signals, _cluster_signals, _known_issue_signals, _order_signals, _guidance_audit):
        signals.extend(fn(now))

    if categories:
        wanted = {c.lower() for c in categories}
        signals = [s for s in signals if s.category in wanted]
    if account_id:
        signals = [s for s in signals if account_id.upper() in {a.upper() for a in s.accounts}]

    signals.sort(key=lambda s: (PRIORITY_ORDER.get(s.priority, 9), s.signal_id))
    return signals


def summarise(signals: list[Signal]) -> dict[str, Any]:
    by_priority: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for s in signals:
        by_priority[s.priority] = by_priority.get(s.priority, 0) + 1
        by_category[s.category] = by_category.get(s.category, 0) + 1
    return {
        "as_of": fmt(snapshot_now()),
        "total": len(signals),
        "by_priority": by_priority,
        "by_category": by_category,
        "accounts_affected": sorted({a for s in signals for a in s.accounts}),
    }
