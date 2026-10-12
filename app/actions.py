"""State-changing actions, behind a two-phase confirmation gate.

Phase 1 — `propose()`: the agent builds a fully-specified action and gets back a
preview plus an action id. Nothing has changed yet.

Phase 2 — `execute()`: refuses unless a *human* confirmed that exact proposal. The
confirmation flag can only be set by `confirm()`, which is reachable solely from the
`POST /api/actions/{id}/confirm` endpoint behind a click in the UI. The model has no
tool that can set it. So "ask before acting" is a property of the system, not an
instruction the model could be talked out of.

Actions are mocked: they persist to `data/runtime/actions.json` and would be swapped
for the ticketing/billing APIs in production.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .access import AccessDenied, Principal, Role
from .config import ACTION_LOG_PATH
from .policy.pack import sop
from .store import account_of, snapshot_now
from .timeutil import fmt

_LOCK = threading.Lock()

# Each action declares what it needs, who may run it, and how to render a preview.
ACTION_TYPES: dict[str, dict[str, Any]] = {
    "create_escalation": {
        "label": "Create escalation",
        "required": ["ticket_id", "severity", "reason"],
        "optional": ["notify_csm"],
        "roles": {Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER, Role.CUSTOMER},
        "risk": "medium",
    },
    "update_ticket": {
        "label": "Update ticket",
        "required": ["ticket_id"],
        "optional": ["status", "severity", "assigned_to", "note"],
        "roles": {Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER},
        "risk": "medium",
    },
    "create_followup_task": {
        "label": "Create follow-up task",
        "required": ["title", "due"],
        "optional": ["ticket_id", "order_id", "account_id", "owner", "details"],
        "roles": {Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER, Role.CUSTOMER},
        "risk": "low",
    },
    "issue_service_credit": {
        "label": "Issue service credit",
        "required": ["order_id", "amount_inr", "reason"],
        "optional": ["ticket_id"],
        "roles": {Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER},
        "risk": "high",
    },
    "cancel_order": {
        "label": "Cancel order",
        "required": ["order_id"],
        "optional": ["fee_inr", "reason"],
        "roles": {Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER, Role.CUSTOMER},
        "risk": "high",
    },
}

# Read from the SOP, not restated, so the action gate and the engine cannot disagree.
MANAGER_APPROVAL_ABOVE_INR = int(sop()["approval"]["manager_approval_above_inr"])


@dataclass
class Action:
    action_id: str
    action_type: str
    label: str
    payload: dict[str, Any]
    account_id: str | None
    proposed_by: str
    proposed_by_role: str
    proposed_at: str
    preview: str
    risk: str
    warnings: list[str] = field(default_factory=list)
    status: str = "pending_confirmation"  # -> confirmed -> executed | cancelled
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    executed_at: str | None = None
    result: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load() -> list[dict]:
    if not ACTION_LOG_PATH.exists():
        return []
    try:
        return json.loads(ACTION_LOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(rows: list[dict]) -> None:
    ACTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTION_LOG_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _find(action_id: str) -> tuple[list[dict], dict | None]:
    rows = _load()
    for row in rows:
        if row["action_id"] == action_id:
            return rows, row
    return rows, None


def _render_preview(action_type: str, payload: dict[str, Any]) -> str:
    p = payload
    if action_type == "create_escalation":
        bits = [f"Escalate {p['ticket_id']} at severity {p['severity']}"]
        bits.append(f"Reason: {p['reason']}")
        if p.get("notify_csm"):
            bits.append(f"Notify CSM: {p['notify_csm']}")
        return "\n".join(bits)
    if action_type == "update_ticket":
        changes = [f"{k} -> {v}" for k, v in p.items() if k != "ticket_id" and v is not None]
        return f"Update {p['ticket_id']}: " + ("; ".join(changes) or "no changes specified")
    if action_type == "create_followup_task":
        return (
            f"Follow-up task: {p['title']}\nDue: {p['due']}"
            + (f"\nOwner: {p['owner']}" if p.get("owner") else "")
            + (f"\nDetails: {p['details']}" if p.get("details") else "")
        )
    if action_type == "issue_service_credit":
        return (
            f"Issue INR {p['amount_inr']:,} service credit against {p['order_id']}\n"
            f"Reason: {p['reason']}"
        )
    if action_type == "cancel_order":
        fee = p.get("fee_inr")
        fee_line = "No cancellation fee" if not fee else f"Cancellation fee: INR {fee:,}"
        return f"Cancel {p['order_id']}\n{fee_line}" + (
            f"\nReason: {p['reason']}" if p.get("reason") else ""
        )
    return json.dumps(p, indent=2)


def credits_issued_this_month(account_id: str, at: datetime | None = None) -> int:
    """Total of already-executed credits in the reference month (for contract caps)."""
    at = at or snapshot_now()
    total = 0
    for row in _load():
        if row["action_type"] != "issue_service_credit" or row["status"] != "executed":
            continue
        if (row.get("account_id") or "").upper() != account_id.upper():
            continue
        executed = row.get("executed_at") or ""
        if executed[:7] == at.strftime("%Y-%m"):
            total += int(row["payload"].get("amount_inr") or 0)
    return total


def propose(principal: Principal, action_type: str, payload: dict[str, Any]) -> Action:
    """Phase 1. Validates, scopes, and stores a pending action. Changes nothing."""
    spec = ACTION_TYPES.get(action_type)
    if spec is None:
        raise ValueError(
            f"Unknown action '{action_type}'. Available: {sorted(ACTION_TYPES)}"
        )
    principal.require("propose_action")
    if principal.role not in spec["roles"]:
        raise AccessDenied(
            f"Role '{principal.role.value}' may not propose '{action_type}'."
        )

    payload = {k: v for k, v in (payload or {}).items() if v is not None}
    missing = [f for f in spec["required"] if f not in payload]
    if missing:
        raise ValueError(f"'{action_type}' requires: {', '.join(missing)}.")
    unknown = set(payload) - set(spec["required"]) - set(spec["optional"])
    if unknown:
        raise ValueError(
            f"'{action_type}' does not accept: {', '.join(sorted(unknown))}. "
            f"Allowed: {sorted(set(spec['required']) | set(spec['optional']))}."
        )

    # Derive the owning account from the referenced record, then authorise it.
    account_id = None
    for key, entity in (("ticket_id", "tickets"), ("order_id", "orders")):
        if payload.get(key):
            account_id = account_of(entity, str(payload[key]))
            if account_id is None:
                raise LookupError(f"{payload[key]} does not exist.")
            principal.assert_owns(entity[:-1].title(), str(payload[key]), account_id)
            break
    if account_id is None and payload.get("account_id"):
        account_id = principal.scope_account(str(payload["account_id"]))
    if account_id is None and not principal.is_internal:
        account_id = principal.account_id

    warnings: list[str] = []
    if action_type == "issue_service_credit":
        amount = int(payload["amount_inr"])
        if amount > MANAGER_APPROVAL_ABOVE_INR and not principal.can("approve_high_value_credit"):
            warnings.append(
                f"INR {amount:,} exceeds the INR {MANAGER_APPROVAL_ABOVE_INR:,} SOP threshold; "
                f"a support manager must approve before this can be executed."
            )
    if action_type == "create_escalation" and str(payload.get("severity", "")).upper() == "P1":
        warnings.append("P1 escalation pages the on-call rota immediately.")

    action = Action(
        action_id=f"ACT-{uuid.uuid4().hex[:8].upper()}",
        action_type=action_type,
        label=spec["label"],
        payload=payload,
        account_id=account_id,
        proposed_by=principal.user_id,
        proposed_by_role=principal.role.value,
        proposed_at=fmt(snapshot_now()) or "",
        preview=_render_preview(action_type, payload),
        risk=spec["risk"],
        warnings=warnings,
    )
    with _LOCK:
        rows = _load()
        rows.append(action.as_dict())
        _save(rows)
    return action


def confirm(principal: Principal, action_id: str) -> dict:
    """Phase 2a. Only reachable from an explicit human click in the UI."""
    with _LOCK:
        rows, row = _find(action_id)
        if row is None:
            raise LookupError(f"No pending action {action_id}.")
        if row["status"] != "pending_confirmation":
            raise ValueError(f"{action_id} is already {row['status']}.")
        if row["proposed_by"] != principal.user_id:
            raise AccessDenied(
                f"{action_id} was prepared for {row['proposed_by']} and cannot be "
                f"confirmed by {principal.user_id}."
            )
        row["status"] = "confirmed"
        row["confirmed_by"] = principal.user_id
        row["confirmed_at"] = fmt(snapshot_now())
        _save(rows)
        return dict(row)


def cancel(principal: Principal, action_id: str) -> dict:
    with _LOCK:
        rows, row = _find(action_id)
        if row is None:
            raise LookupError(f"No pending action {action_id}.")
        if row["proposed_by"] != principal.user_id:
            raise AccessDenied(f"{action_id} does not belong to {principal.user_id}.")
        row["status"] = "cancelled"
        _save(rows)
        return dict(row)


def execute(principal: Principal, action_id: str) -> dict:
    """Phase 2b. Hard-refuses anything a human has not confirmed."""
    with _LOCK:
        rows, row = _find(action_id)
        if row is None:
            raise LookupError(f"No action {action_id}.")
        if row["status"] == "executed":
            return dict(row)  # idempotent
        if row["status"] != "confirmed":
            raise PermissionError(
                f"{action_id} has not been confirmed by the user "
                f"(status: {row['status']}). Ask the user to confirm it in the UI; "
                f"there is no way to bypass this."
            )
        if row["confirmed_by"] != principal.user_id:
            raise AccessDenied(f"{action_id} was confirmed by a different user.")

        # Re-check the manager gate at execution time, not just at proposal time.
        if row["action_type"] == "issue_service_credit":
            amount = int(row["payload"]["amount_inr"])
            if amount > MANAGER_APPROVAL_ABOVE_INR and not principal.can(
                "approve_high_value_credit"
            ):
                raise AccessDenied(
                    f"INR {amount:,} is above the INR {MANAGER_APPROVAL_ABOVE_INR:,} SOP "
                    f"threshold and needs a support manager. Route this to a manager."
                )

        row["status"] = "executed"
        row["executed_at"] = fmt(snapshot_now())
        row["result"] = _mock_effect(row)
        _save(rows)
        return dict(row)


def _mock_effect(row: dict) -> dict[str, Any]:
    """Stand-in for the ticketing / billing integrations."""
    kind = row["action_type"]
    ref = f"{kind.split('_')[0][:3].upper()}-{row['action_id'][-6:]}"
    base = {"reference": ref, "system": "mock", "recorded_at": row["executed_at"]}
    if kind == "create_escalation":
        base["queue"] = "on-call" if row["payload"].get("severity") == "P1" else "tier-2"
    if kind == "issue_service_credit":
        base["credit_note"] = f"CN-{row['action_id'][-6:]}"
    return base


def list_actions(principal: Principal, limit: int = 25) -> list[dict]:
    rows = _load()
    if not principal.is_internal:
        rows = [r for r in rows if r["proposed_by"] == principal.user_id]
    return list(reversed(rows))[:limit]


def get_action(action_id: str) -> dict | None:
    _, row = _find(action_id)
    return dict(row) if row else None
