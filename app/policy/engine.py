"""Deterministic policy engine.

The model orchestrates; this module decides. Every entitlement question — can this
order be cancelled, is a credit owed, what is the SLA and has it broken — is resolved
here in plain Python against the verified policy pack, and returns a `Decision` that
carries its own reasoning trace, citations, conflicts and confidence.

Why not let the LLM do it: money and SLA promises must be reproducible, testable and
identical on every run. A `Decision` is unit-testable without an API key
(`tests/test_engine.py`), which the model's prose never is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..store import account_plan, get_record_unscoped, snapshot_now
from ..timeutil import Duration, fmt, humanise_minutes, parse_dt
from .pack import Citation, agreement, cite, policy, product, sop

SEVERITIES = ("P1", "P2", "P3")


@dataclass
class Decision:
    """A resolved entitlement question, with everything needed to defend the answer."""

    question: str
    outcome: str
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: str = "high"
    requires_human: bool = False
    human_reason: str | None = None
    suggested_action: dict[str, Any] | None = None

    def add(self, step: str) -> None:
        self.steps.append(step)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "outcome": self.outcome,
            "summary": self.summary,
            "facts": self.facts,
            "reasoning_steps": self.steps,
            "citations": [c.as_dict() for c in self.citations],
            "conflicts": self.conflicts,
            "caveats": self.caveats,
            "unknowns": self.unknowns,
            "assumptions": self.assumptions,
            "confidence": self.confidence,
            "requires_human_review": self.requires_human,
            "human_review_reason": self.human_reason,
            "suggested_action": self.suggested_action,
        }

    def downgrade(self, level: str, reason: str) -> None:
        rank = {"high": 3, "medium": 2, "low": 1}
        if rank[level] < rank[self.confidence]:
            self.confidence = level
        if reason and reason not in self.caveats:
            self.caveats.append(reason)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _duration(spec: dict[str, Any]) -> Duration:
    return Duration(spec["amount"], spec["unit"], spec["clock"])


def _known_issue(kind: str) -> dict[str, Any] | None:
    """Find an open known issue by the shape of problem it describes, not its id, so a
    new incident of the same kind is a product-guide edit rather than a code change."""
    for ki in product()["known_issues"]:
        if ki.get("kind") == kind:
            return ki
    return None


def _ki_citation(ki: dict[str, Any]) -> Citation:
    return cite(product()["doc_id"], ki["section"], ki["quote"])


def _historical_conflicts(account_id: str | None, topic: str, verdict: str) -> list[dict]:
    """Flag closed tickets whose recorded resolution contradicts the current answer.

    The support policy (s1) makes past tickets background only, so they never change
    the verdict — but a customer who was told something different deserves to know,
    and Larkway deserves to find out it mis-advised someone.
    """
    from ..store import all_rows

    out: list[dict] = []
    # What a wrong past answer looks like, derived from today's rules: quoting the
    # SOP fee to a customer whose contract waives it, or quoting the known issue's
    # working limit as if it were the plan limit.
    fee = sop()["cancellation"]["by_status"]["BOOKED"]["fee_after_window_inr"]
    bulk_ki = _known_issue("bulk_upload_degradation") or {}
    rows = bulk_ki.get("threshold_rows")
    markers = {
        "cancellation_fee_waived": ("cancellation", ("fee applied", f"inr {fee}", f"inr {fee:,}", "fee of")),
        "bulk_upload_limit": ("bulk upload", tuple(
            ["only supports"] + ([f"{rows:,} rows", f"{rows} rows"] if rows else [])
        )),
    }
    if verdict not in markers:
        return out
    subject_hint, phrases = markers[verdict]
    for ticket in all_rows("tickets"):
        resolution = (ticket.get("historical_resolution") or "").lower()
        if not resolution:
            continue
        if account_id and (ticket.get("account_id") or "").upper() != account_id.upper():
            continue
        subject = (ticket.get("subject") or "").lower()
        if subject_hint not in subject and subject_hint not in resolution:
            continue
        if any(p in resolution for p in phrases):
            out.append(
                {
                    "type": "historical_guidance_contradicted",
                    "source": f"{ticket['ticket_id']} ({ticket.get('created_at')})",
                    "claim": ticket["historical_resolution"],
                    "resolution": (
                        "Superseded. The support policy (s1) makes past tickets "
                        "background only; the authoritative source above governs."
                    ),
                }
            )
    return out


# ---------------------------------------------------------------------------
# 1. Cancellation
# ---------------------------------------------------------------------------
def evaluate_cancellation(order_id: str, at: datetime | None = None) -> Decision:
    now = at or snapshot_now()
    order = get_record_unscoped("orders", order_id)
    if order is None:
        raise LookupError(f"Order {order_id} not found.")

    account_id = order.get("account_id")
    status = str(order.get("status", "")).upper()
    booked_at = parse_dt(order.get("booked_at"))
    requested_at = parse_dt(order.get("cancellation_requested_at")) or now

    d = Decision(
        question=f"Can {order_id} be cancelled, and is a cancellation fee due?",
        outcome="",
        summary="",
        facts={
            "order_id": order_id,
            "account_id": account_id,
            "status": status,
            "carrier": order.get("carrier"),
            "booked_at": fmt(booked_at),
            "cancellation_requested_at": fmt(parse_dt(order.get("cancellation_requested_at"))),
            "reference_time": fmt(now),
        },
    )

    rules = sop()["cancellation"]
    section = sop()["cancellation"]["section"]
    by_status = rules["by_status"]

    if status not in by_status:
        d.outcome = "unknown_status"
        d.summary = f"Order status '{status}' is not covered by the SOP."
        d.requires_human = True
        d.human_reason = f"The SOP (s1) does not define handling for status '{status}'."
        d.confidence = "low"
        return d

    rule = by_status[status]
    d.add(f"Order {order_id} is in status {status} at {fmt(now)}.")

    # --- terminal statuses ---------------------------------------------------
    if not rule["cancellable"]:
        d.citations.append(cite(sop()["doc_id"], section, rule["quote"]))
        if status == "PICKED_UP":
            d.outcome = "not_cancellable_use_rto"
            d.summary = (
                f"{order_id} has already been picked up, so it cannot be cancelled. "
                f"The return-to-origin workflow is the route to get the parcel back."
            )
            d.add("SOP s1: a PICKED_UP shipment is not cancelled; use return-to-origin.")
            ag = agreement(account_id)
            if ag and ag.get("cancellation", {}).get("picked_up_quote"):
                d.citations.append(
                    cite(ag["doc_id"], ag["cancellation"]["section"],
                         ag["cancellation"]["picked_up_quote"])
                )
                d.add("The customer agreement points to the same return-to-origin process.")
            d.suggested_action = {
                "action_type": "create_followup_task",
                "why": "Return-to-origin is a manual workflow outside this agent.",
                "payload": {
                    "title": f"Start return-to-origin for {order_id}",
                    "due": "next business day",
                    "order_id": order_id,
                },
            }
        else:
            d.outcome = "not_cancellable"
            d.summary = f"{order_id} is {status} and cannot be cancelled."
        return d

    # --- DRAFT ---------------------------------------------------------------
    if status == "DRAFT":
        d.citations.append(cite(sop()["doc_id"], section, rule["quote"]))
        d.outcome = "cancellable_no_fee"
        d.summary = f"{order_id} is a draft and can be cancelled with no fee."
        return d

    # --- BOOKED --------------------------------------------------------------
    minutes_since_booking = (
        int((requested_at - booked_at).total_seconds() // 60) if booked_at else None
    )
    free_window = int(rule["free_window_minutes_from_booking"])
    default_fee = int(rule["fee_after_window_inr"])
    d.facts["minutes_since_booking"] = minutes_since_booking
    d.citations.append(cite(sop()["doc_id"], section, rule["quote"]))

    if minutes_since_booking is None:
        d.unknowns.append(f"booked_at is missing, so the {free_window}-minute free window cannot be checked.")
        d.downgrade("low", "Booking time is missing from the order record.")
        d.requires_human = True
        d.human_reason = "Cannot verify the free-cancellation window without a booking time."
        d.outcome = "undetermined"
        d.summary = f"{order_id} is BOOKED but has no booking timestamp, so the fee cannot be determined."
        return d

    within_free_window = minutes_since_booking <= free_window
    d.add(
        f"Cancellation was requested {humanise_minutes(minutes_since_booking)} after booking "
        f"({'inside' if within_free_window else 'outside'} the SOP's {free_window}-minute free window)."
    )

    # Agreement first — support policy s1 precedence.
    ag = agreement(account_id)
    waived = bool(ag and ag.get("cancellation", {}).get("waives_booked_cancellation_fee"))
    if ag:
        d.citations.append(
            cite(ag["doc_id"], ag["cancellation"]["section"], ag["cancellation"]["quote"])
        )
        if waived:
            d.add(
                f"The signed {ag['title']} waives the BOOKED cancellation fee outright, and "
                f"under the support policy (s1) the agreement outranks the SOP."
            )
        else:
            d.add(
                f"The signed {ag['title']} adds no cancellation-fee waiver, so the SOP default applies."
            )
    else:
        d.add(
            f"No signed agreement is on file for {account_id}, so the SOP default applies."
        )

    fee = 0 if (waived or within_free_window) else default_fee
    d.facts["fee_inr"] = fee
    d.facts["default_fee_inr"] = default_fee
    d.facts["fee_waived_by_agreement"] = waived and not within_free_window

    if fee == 0:
        d.outcome = "cancellable_no_fee"
        reason = (
            f"the request is inside the {free_window}-minute free window"
            if within_free_window
            else f"the {ag['title']} waives it"
        )
        d.summary = f"{order_id} can be cancelled with no cancellation fee — {reason}."
    else:
        d.outcome = "cancellable_with_fee"
        d.summary = (
            f"{order_id} can be cancelled, but an INR {fee:,} cancellation fee applies "
            f"because the request came {humanise_minutes(minutes_since_booking)} after booking."
        )

    # Historical guidance that contradicts this answer.
    if waived:
        d.conflicts.extend(_historical_conflicts(account_id, "cancellation", "cancellation_fee_waived"))

    # Data-reliability caveat: BOOKED can be stale for a carrier with a known
    # webhook delay, and the SOP forbids cancelling something already picked up.
    ki = _known_issue("status_lag")
    window_start = parse_dt(order.get("pickup_window_start"))
    if (
        ki
        and str(order.get("carrier", "")).lower() in {c.lower() for c in ki["affects_carriers"]}
        and window_start
        and now >= window_start
        and not order.get("pickup_actual_at")
    ):
        d.citations.append(_ki_citation(ki))
        d.caveats.append(
            f"{ki['id']}: {order.get('carrier')} pickup webhooks can lag by up to "
            f"{ki['delay_minutes']} minutes, and this order is already inside its pickup "
            f"window. BOOKED may be stale — confirm with the carrier that the parcel has "
            f"not already been collected before cancelling."
        )
        d.downgrade("medium", "")
        d.requires_human = True
        d.human_reason = (
            f"{ki['id']} makes the BOOKED status unreliable for this order; the SOP forbids "
            f"cancelling a shipment that has actually been picked up, so carrier status must "
            f"be verified first."
        )

    d.suggested_action = {
        "action_type": "cancel_order",
        "why": "Cancelling the order is a state change and needs explicit confirmation.",
        "payload": {"order_id": order_id, "fee_inr": fee},
    }
    return d


# ---------------------------------------------------------------------------
# 2. Service credit
# ---------------------------------------------------------------------------
def evaluate_service_credit(
    order_id: str | None = None,
    account_id: str | None = None,
    delay_hours: float | None = None,
    carrier_fault: bool | None = None,
    customer_fault: bool | None = None,
    shipment_fee_inr: int | None = None,
    at: datetime | None = None,
) -> Decision:
    """Evaluate against a real order, or against a hypothetical the user described.

    The hypothetical path exists because customers ask "a pickup is three hours late,
    do I get a credit?" without quoting an order id — and the answer legitimately
    differs by contract.
    """
    now = at or snapshot_now()
    hypothetical = order_id is None

    order: dict[str, Any] | None = None
    if order_id:
        order = get_record_unscoped("orders", order_id)
        if order is None:
            raise LookupError(f"Order {order_id} not found.")
        account_id = order.get("account_id")
        carrier_fault = order.get("carrier_fault") if carrier_fault is None else carrier_fault
        customer_fault = order.get("customer_fault") if customer_fault is None else customer_fault
        shipment_fee_inr = shipment_fee_inr or order.get("shipment_fee_inr")
        window_end = parse_dt(order.get("pickup_window_end"))
        actual = parse_dt(order.get("pickup_actual_at"))
        if window_end:
            reference = actual or now
            delay_hours = max(0.0, (reference - window_end).total_seconds() / 3600)

    d = Decision(
        question=(
            f"Is a failed-pickup service credit owed on {order_id}?"
            if order_id
            else "Is a failed-pickup service credit owed on the described pickup delay?"
        ),
        outcome="",
        summary="",
        facts={
            "order_id": order_id,
            "account_id": account_id,
            "delay_hours_past_window_end": round(delay_hours, 2) if delay_hours is not None else None,
            "carrier_fault": carrier_fault,
            "customer_fault": customer_fault,
            "shipment_fee_inr": shipment_fee_inr,
            "pickup_completed": bool(order and order.get("pickup_actual_at")),
            "reference_time": fmt(now),
            "hypothetical": hypothetical,
        },
    )
    if hypothetical:
        d.assumptions.append(
            "Evaluated from the details stated in the request, not from an order record. "
            "Quote an order id for a decision grounded in Larkway's own data."
        )

    sop_credit = sop()["service_credit"]
    d.citations.append(
        cite(sop()["doc_id"], sop_credit["section"], sop_credit["quote"])
    )

    # --- resolve the applicable terms (agreement first) ----------------------
    ag = agreement(account_id)
    threshold = float(sop_credit["default_threshold_hours_past_window_end"])
    sop_flat = int(sop_credit["amount"]["flat_inr"])
    sop_pct = int(sop_credit["amount"]["percent_of_shipment_fee"])
    sop_threshold = threshold
    amount_rule = f"min(INR {sop_flat:,}, {sop_pct}% of shipment fee)"
    fixed_amount: int | None = None
    monthly_cap: int | None = None

    if ag and ag.get("service_credit"):
        ac = ag["service_credit"]
        d.citations.append(cite(ag["doc_id"], ac["section"], ac["quote"]))
        if ac.get("threshold_hours_past_window_end") is not None:
            threshold = float(ac["threshold_hours_past_window_end"])
        if ac.get("fixed_amount_inr") is not None:
            fixed_amount = int(ac["fixed_amount_inr"])
            amount_rule = f"fixed INR {fixed_amount:,} under the agreement"
        if ac.get("monthly_aggregate_cap_inr") is not None:
            monthly_cap = int(ac["monthly_aggregate_cap_inr"])
        if ac.get("replaces_default_amount_and_threshold"):
            d.add(
                f"The {ag['title']} replaces the SOP's default delay threshold and credit "
                f"amount, and under the support policy (s1) the agreement outranks the SOP."
            )
        else:
            d.add(
                f"The {ag['title']} adds terms on top of the SOP but does not replace the "
                f"delay threshold or the credit amount."
            )
    else:
        d.add(
            f"No signed agreement is on file for {account_id or 'this account'}, so the "
            f"SOP defaults apply."
        )

    d.facts["threshold_hours"] = threshold
    d.facts["amount_rule"] = amount_rule
    d.facts["monthly_cap_inr"] = monthly_cap

    # --- the three SOP conditions -------------------------------------------
    unknown_conditions: list[str] = []
    if delay_hours is None:
        unknown_conditions.append("how late the pickup is relative to the pickup window")
    if carrier_fault is None:
        unknown_conditions.append("whether the carrier is at fault")
    if customer_fault is None:
        unknown_conditions.append("whether there was a customer-caused issue")

    if unknown_conditions:
        unc = sop()["uncertainty"]["no_promise_when_unknown"]
        d.citations.append(cite(sop()["doc_id"], sop()["uncertainty"]["section"], unc["quote"]))
        d.unknowns.extend(unknown_conditions)
        d.outcome = "undetermined"
        d.summary = (
            "I can't confirm a credit yet — the SOP forbids promising one while "
            + ", ".join(unknown_conditions)
            + " is unknown."
        )
        d.confidence = "low"
        d.requires_human = True
        d.human_reason = "Required facts are missing; SOP s3 forbids promising a credit."
        return d

    meets_delay = delay_hours > threshold
    d.add(
        f"Pickup is {delay_hours:.2f}h past the end of the pickup window; the applicable "
        f"threshold is more than {threshold:g}h -> {'met' if meets_delay else 'not met'}."
    )
    d.add(f"Carrier at fault: {bool(carrier_fault)}. Customer-caused issue: {bool(customer_fault)}.")

    if not meets_delay or not carrier_fault or customer_fault:
        reasons = []
        if not meets_delay:
            reasons.append(
                f"the delay of {delay_hours:.2f}h does not exceed the {threshold:g}h threshold"
                + (f" set by the {ag['title']}" if ag and threshold != sop_threshold else " in the SOP")
            )
        if not carrier_fault:
            reasons.append("the carrier is not recorded as being at fault")
        if customer_fault:
            reasons.append("a customer-caused issue is recorded against the order")
        d.outcome = "not_eligible"
        d.summary = "No service credit is due because " + ", and ".join(reasons) + "."
        return d

    # --- amount --------------------------------------------------------------
    if fixed_amount is not None:
        amount = fixed_amount
        d.add(f"The agreement sets a fixed credit of INR {amount:,}.")
    elif shipment_fee_inr:
        pct = int(sop_credit["amount"]["percent_of_shipment_fee"])
        flat = int(sop_credit["amount"]["flat_inr"])
        percent_value = int(round(shipment_fee_inr * pct / 100))
        amount = min(flat, percent_value)
        d.add(
            f"SOP amount = lower of INR {flat:,} and {pct}% of the INR {shipment_fee_inr:,} "
            f"shipment fee (INR {percent_value:,}) = INR {amount:,}."
        )
    else:
        d.unknowns.append(f"the shipment fee, which the SOP's {sop_pct}% comparison needs")
        d.outcome = "eligible_amount_unknown"
        d.summary = (
            "The pickup qualifies for a credit, but the exact amount needs the shipment "
            f"fee: the SOP pays the lower of INR {sop_flat:,} or {sop_pct}% of that fee."
        )
        d.downgrade("medium", "Shipment fee not supplied, so only the range is known.")
        return d

    d.facts["credit_amount_inr"] = amount

    # Contract cap on the month's aggregate.
    if monthly_cap is not None:
        from ..actions import credits_issued_this_month

        already = credits_issued_this_month(account_id, now)
        remaining = max(0, monthly_cap - already)
        d.facts["monthly_credits_already_issued_inr"] = already
        d.facts["monthly_cap_remaining_inr"] = remaining
        d.add(
            f"Agreement caps monthly aggregate credits at INR {monthly_cap:,}; INR {already:,} "
            f"has been issued this month, leaving INR {remaining:,}."
        )
        if amount > remaining:
            d.outcome = "eligible_but_capped"
            d.summary = (
                f"A credit of INR {amount:,} is justified, but only INR {remaining:,} of the "
                f"INR {monthly_cap:,} monthly cap remains for this account."
            )
            d.requires_human = True
            d.human_reason = "The contract's monthly credit cap would be exceeded."
            d.downgrade("medium", "")
            return d

    # Approval threshold.
    approval = sop()["approval"]
    limit = int(approval["manager_approval_above_inr"])
    if amount > limit:
        d.citations.append(cite(sop()["doc_id"], approval["section"], approval["quote"]))
        d.requires_human = True
        d.human_reason = f"INR {amount:,} exceeds the INR {limit:,} manager-approval threshold."
        d.add(f"INR {amount:,} is above the INR {limit:,} SOP threshold, so a manager must approve.")

    d.outcome = "eligible"
    d.summary = f"A service credit of INR {amount:,} is due on this pickup."
    d.suggested_action = {
        "action_type": "issue_service_credit",
        "why": "Issuing a credit is a state change and needs explicit confirmation.",
        "payload": {
            "order_id": order_id,
            "amount_inr": amount,
            "reason": f"Failed pickup, carrier at fault, {delay_hours:.2f}h past window end.",
        },
    } if order_id else None
    return d


# ---------------------------------------------------------------------------
# 3. SLA
# ---------------------------------------------------------------------------
def resolve_sla(
    account_id: str,
    severity: str,
    started_at: datetime | str | None = None,
    ticket_id: str | None = None,
    at: datetime | None = None,
) -> Decision:
    now = at or snapshot_now()
    severity = (severity or "").upper().strip()
    if severity not in SEVERITIES:
        raise ValueError(f"Severity must be one of {SEVERITIES}, got {severity!r}.")

    account_id = (account_id or "").upper()
    plan = account_plan(account_id)
    if plan is None:
        raise LookupError(f"Account {account_id} not found.")

    if ticket_id and started_at is None:
        ticket = get_record_unscoped("tickets", ticket_id)
        if ticket is None:
            raise LookupError(f"Ticket {ticket_id} not found.")
        started_at = ticket.get("created_at")
    start = parse_dt(started_at) if started_at is not None else None

    d = Decision(
        question=f"What is the {severity} first-response target for {account_id}?",
        outcome="",
        summary="",
        facts={
            "account_id": account_id,
            "plan": plan,
            "severity": severity,
            "ticket_id": ticket_id,
            "opened_at": fmt(start),
            "reference_time": fmt(now),
        },
    )

    ag = agreement(account_id)
    source = "plan default"
    if ag and ag.get("first_response_targets", {}).get("replaces_defaults"):
        spec = ag["first_response_targets"]["matrix"][severity]
        target = _duration(spec)
        d.citations.append(
            cite(ag["doc_id"], ag["first_response_targets"]["section"], spec["quote"])
        )
        source = f"{ag['title']} (signed agreement)"
        d.add(
            f"{account_id} has a signed agreement that replaces the standard targets, and "
            f"the support policy (s1) puts the agreement first."
        )
        default_spec = policy()["first_response_targets"]["matrix"][plan][severity]
        default = _duration(default_spec)
        if default.label != target.label:
            d.add(
                f"For reference, the {plan} plan default would have been {default.label}; "
                f"the agreement's {target.label} governs."
            )
        if ag["first_response_targets"].get("no_weekend_coverage"):
            d.citations.append(
                cite(ag["doc_id"], ag["first_response_targets"]["section"],
                     ag["first_response_targets"]["coverage_quote"])
            )
            d.caveats.append(
                "This agreement excludes weekend and after-hours coverage, so the response "
                "clock does not run outside business hours."
            )
    else:
        spec = policy()["first_response_targets"]["matrix"][plan][severity]
        target = _duration(spec)
        frt = policy()["first_response_targets"]
        d.citations.append(cite(policy()["doc_id"], frt["section"], frt["quote"]))
        d.add(
            f"{account_id} is on the {plan} plan with no agreement override for {severity}, "
            f"so the support policy's default table applies."
        )

    d.facts["target"] = target.label
    d.facts["target_source"] = source
    d.facts["clock"] = "24x7" if target.clock == "24x7" else "business hours only"
    if target.clock != "24x7":
        d.assumptions.append(
            "Business hours are taken to be Mon-Fri 09:00-18:00 IST; the documents do not "
            "state them. Weekend elapsed time therefore does not consume the target."
        )

    if start is None:
        d.outcome = "target_only"
        d.summary = f"The {severity} first-response target for {account_id} is {target.label} ({source})."
        return d

    elapsed = target.elapsed_from(start, now)
    budget = target.budget_minutes()
    deadline = target.deadline_from(start)
    breached = elapsed > budget
    remaining = budget - elapsed

    d.facts.update(
        {
            "elapsed_on_target_clock": humanise_minutes(elapsed),
            "elapsed_minutes": elapsed,
            "budget_minutes": budget,
            "due_at": fmt(deadline),
            "breached": breached,
        }
    )
    weekday = now.strftime("%A")
    if target.clock != "24x7":
        wall = int((now - start).total_seconds() // 60)
        d.add(
            f"Opened {fmt(start)}; {humanise_minutes(wall)} of wall-clock time has passed, but "
            f"only {humanise_minutes(elapsed)} of business hours (the snapshot falls on a "
            f"{weekday})."
        )
    else:
        d.add(f"Opened {fmt(start)}; {humanise_minutes(elapsed)} elapsed on a 24x7 clock.")

    if breached:
        over = elapsed - budget
        d.outcome = "breached"
        d.summary = (
            f"The {severity} first-response target of {target.label} for {account_id} is "
            f"BREACHED by {humanise_minutes(over)} (due {fmt(deadline)})."
        )
        esc = policy()["escalation"]["breach_disclosure"]
        d.citations.append(cite(policy()["doc_id"], policy()["escalation"]["section"], esc["quote"]))
        d.requires_human = True
        d.human_reason = "The support policy (s4) requires a breached target to be stated and escalated."
        d.facts["overdue_by"] = humanise_minutes(over)
    else:
        at_risk = remaining <= max(15, budget * 0.25)
        d.outcome = "at_risk" if at_risk else "within_target"
        d.summary = (
            f"The {severity} first-response target of {target.label} for {account_id} is due "
            f"{fmt(deadline)} — {humanise_minutes(remaining)} remaining"
            + (" (at risk)." if at_risk else ".")
        )
        d.facts["remaining"] = humanise_minutes(remaining)

    if severity == "P1":
        p1 = policy()["escalation"]["p1_immediate"]
        d.citations.append(cite(policy()["doc_id"], policy()["escalation"]["section"], p1["quote"]))
        d.add("Support policy s4: P1 incidents are escalated immediately regardless of the clock.")
        d.requires_human = True
        d.human_reason = d.human_reason or "P1 incidents must be escalated immediately."

    if d.requires_human and ticket_id:
        d.suggested_action = {
            "action_type": "create_escalation",
            "why": d.human_reason,
            "payload": {"ticket_id": ticket_id, "severity": severity, "reason": d.summary},
        }
    return d


# ---------------------------------------------------------------------------
# 4. Plan capability lookup (bulk upload etc.)
# ---------------------------------------------------------------------------
def evaluate_plan_capability(account_id: str, capability: str = "bulk_upload") -> Decision:
    account_id = (account_id or "").upper()
    plan = account_plan(account_id)
    if plan is None:
        raise LookupError(f"Account {account_id} not found.")
    caps = product()["plan_capabilities"]
    if capability != "bulk_upload":
        raise ValueError("Only 'bulk_upload' is described in the supplied product guide.")

    bulk = caps["bulk_upload"]
    available = plan in bulk["available_on"]
    d = Decision(
        question=f"Is {capability} available to {account_id}, and with what limits?",
        outcome="available" if available else "not_available",
        summary="",
        facts={
            "account_id": account_id,
            "plan": plan,
            "available": available,
            "documented_max_rows": bulk["max_rows_per_csv"],
        },
    )
    d.citations.append(cite(product()["doc_id"], caps["section"], bulk["quote"]))
    if not available:
        d.citations.append(cite(product()["doc_id"], caps["section"], bulk["excluded_quote"]))
        d.summary = f"Bulk Upload is not included on the {plan} plan."
        return d

    ki = _known_issue("bulk_upload_degradation")
    d.summary = (
        f"Bulk Upload is available on {plan} and the supported limit is "
        f"{bulk['max_rows_per_csv']:,} rows per CSV."
    )
    if ki and plan in ki["affects_plans"]:
        d.citations.append(_ki_citation(ki))
        d.facts["known_issue"] = ki["id"]
        d.facts["known_issue_threshold_rows"] = ki["threshold_rows"]
        d.caveats.append(
            f"{ki['id']} ({ki['state']}): uploads above ~{ki['threshold_rows']:,} rows are "
            f"failing intermittently even though {bulk['max_rows_per_csv']:,} remains the "
            f"supported limit. Workaround: {ki['workaround']}."
        )
        d.add(
            "The supported limit and the currently-working limit are different things right "
            "now; the known issue is a defect, not a plan restriction."
        )
        d.conflicts.extend(_historical_conflicts(account_id, "bulk", "bulk_upload_limit"))
    return d
