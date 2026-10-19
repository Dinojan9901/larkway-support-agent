"""Structured data access — the enforcement point for account scoping.

Nothing above this layer is trusted to filter. Every read goes through a Principal,
rows are filtered to the accounts that principal may see, and internal-only columns
are stripped on the way out. The agent's tools call these functions; they cannot
reach the underlying rows any other way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

from openpyxl import load_workbook

from .access import AccessDenied, Principal
from .config import FALLBACK_SNAPSHOT, WORKBOOK_PATH
from .timeutil import parse_dt

DATE_FIELDS = {
    "orders": ("booked_at", "pickup_window_start", "pickup_window_end",
               "pickup_actual_at", "cancellation_requested_at"),
    "tickets": ("created_at", "last_customer_message_at"),
    "accounts": (),
}
ENTITIES = ("accounts", "orders", "tickets")
KEY_FIELD = {"accounts": "account_id", "orders": "order_id", "tickets": "ticket_id"}


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        text = value.strip()
        return None if text.lower() in {"", "nan", "none", "nat"} else text
    return value


@dataclass
class Dataset:
    snapshot_at: datetime
    snapshot_label: str
    currency: str
    readme: dict[str, str]
    accounts: list[dict]
    orders: list[dict]
    tickets: list[dict]

    def table(self, entity: str) -> list[dict]:
        if entity not in ENTITIES:
            raise ValueError(f"Unknown entity '{entity}'. Expected one of {ENTITIES}.")
        return getattr(self, entity)


@lru_cache(maxsize=1)
def load_dataset() -> Dataset:
    if not WORKBOOK_PATH.exists():
        raise FileNotFoundError(f"Workbook not found at {WORKBOOK_PATH}")
    wb = load_workbook(WORKBOOK_PATH, data_only=True)

    readme: dict[str, str] = {}
    if "README" in wb.sheetnames:
        for row in wb["README"].iter_rows(min_row=2, values_only=True):
            if row and row[0]:
                readme[str(row[0]).strip()] = _clean(row[1]) or ""

    snapshot_label = readme.get("Dataset snapshot", FALLBACK_SNAPSHOT)
    snapshot_at = parse_dt(str(snapshot_label).replace("Asia/Kolkata", "").strip())
    if snapshot_at is None:
        raise RuntimeError(
            f"Could not parse the dataset snapshot time from the README sheet: "
            f"{snapshot_label!r}"
        )

    def sheet(name: str) -> list[dict]:
        if name not in wb.sheetnames:
            return []
        rows = list(wb[name].iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(h).strip() for h in rows[0]]
        out = []
        for raw in rows[1:]:
            if raw is None or all(c is None for c in raw):
                continue
            out.append({h: _clean(v) for h, v in zip(header, raw)})
        return out

    return Dataset(
        snapshot_at=snapshot_at,
        snapshot_label=str(snapshot_label),
        currency=readme.get("Currency", "INR"),
        readme=readme,
        accounts=sheet("accounts"),
        orders=sheet("orders"),
        tickets=sheet("tickets"),
    )


def snapshot_now() -> datetime:
    return load_dataset().snapshot_at


# ---------------------------------------------------------------------------
# Scoped reads
# ---------------------------------------------------------------------------
def _matches(row: dict, filters: dict[str, Any]) -> bool:
    for key, wanted in filters.items():
        if wanted is None:
            continue
        actual = row.get(key)
        if isinstance(wanted, (list, tuple, set)):
            if not any(_eq(actual, w) for w in wanted):
                return False
        elif not _eq(actual, wanted):
            return False
    return True


def _eq(actual: Any, wanted: Any) -> bool:
    if isinstance(actual, str) and isinstance(wanted, str):
        return actual.strip().lower() == wanted.strip().lower()
    if isinstance(actual, bool) or isinstance(wanted, bool):
        return bool(actual) == bool(wanted)
    return actual == wanted


def _account_ids(value: Any) -> list[str]:
    """Normalise an account_id filter (None, one id, or a list of ids) to a list."""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def query(
    principal: Principal,
    entity: str,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
) -> list[dict]:
    """Scoped, redacted table read. The account filter is re-derived, never trusted."""
    data = load_dataset()
    rows = data.table(entity)
    filters = dict(filters or {})

    # The lookup tool tells the model "a list of values matches any of them", so
    # account_id can arrive as one id or several. Every requested id goes through
    # the principal's scope check: one foreign account anywhere in the list denies
    # the whole request, rather than being silently dropped.
    requested = _account_ids(filters.pop("account_id", None))
    allowed = {
        account.upper()
        for account in (principal.scope_account(a) for a in (requested or [None]))
        if account
    }
    if allowed:
        rows = [r for r in rows if str(r.get("account_id", "")).upper() in allowed]

    rows = [r for r in rows if _matches(r, filters)]
    return [principal.redact(entity, r) for r in rows[:limit]]


def get_record(principal: Principal, entity: str, record_id: str) -> dict:
    """Fetch one row by primary key, enforcing ownership for customers."""
    data = load_dataset()
    key = KEY_FIELD[entity]
    wanted = (record_id or "").strip().upper()
    for row in data.table(entity):
        if str(row.get(key, "")).upper() == wanted:
            owner = row.get("account_id") if entity != "accounts" else row.get("account_id")
            principal.assert_owns(entity[:-1].title(), wanted, owner)
            return principal.redact(entity, row)
    raise LookupError(f"No {entity[:-1]} found with {key}={record_id!r}.")


def get_record_unscoped(entity: str, record_id: str) -> dict | None:
    """Internal helper for engine code that has already authorised the caller."""
    key = KEY_FIELD[entity]
    wanted = (record_id or "").strip().upper()
    for row in load_dataset().table(entity):
        if str(row.get(key, "")).upper() == wanted:
            return dict(row)
    return None


def account_of(entity: str, record_id: str) -> str | None:
    row = get_record_unscoped(entity, record_id)
    return row.get("account_id") if row else None


def account_plan(account_id: str | None) -> str | None:
    if not account_id:
        return None
    row = get_record_unscoped("accounts", account_id)
    return row.get("plan") if row else None


def all_rows(entity: str) -> list[dict]:
    """Unscoped read for the operations detector (its tool gates on capability)."""
    return [dict(r) for r in load_dataset().table(entity)]


def require_account_visibility(principal: Principal, account_id: str | None) -> str | None:
    """Raise unless the principal may see this account; returns the account id."""
    if account_id is None:
        return principal.account_id if not principal.is_internal else None
    scoped = principal.scope_account(account_id)
    if scoped is None and not principal.is_internal:
        raise AccessDenied("No account context available for this user.")
    return (scoped or account_id).upper()


def dataset_summary() -> dict[str, Any]:
    d = load_dataset()
    return {
        "snapshot_at": d.snapshot_label,
        "currency": d.currency,
        "accounts": len(d.accounts),
        "orders": len(d.orders),
        "tickets": len(d.tickets),
        "notes": d.readme.get("Important", ""),
    }


def iter_entities() -> Iterable[str]:
    return ENTITIES
