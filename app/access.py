"""Identity, roles and authorisation.

Design rule for this file: **the model never supplies the identity.** A Principal is
built from the server-side session and passed into every tool by the dispatcher. A
tool argument like `account_id` is treated as a *filter request*, never as a grant —
`Principal.scope_account()` re-derives what the caller is actually allowed to see.

This is what makes the access control real rather than prompt-deep: even if the model
is talked into calling `lookup_records(entity="orders", account_id="CUS-104")`
on behalf of a Halcyon Foods customer, the store returns Halcyon rows or raises.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    CUSTOMER = "customer"
    SUPPORT_AGENT = "support_agent"
    SUPPORT_MANAGER = "support_manager"


class AccessDenied(Exception):
    """Raised by the data/tool layer, surfaced to the model as a tool error."""


# Capabilities are checked by name so that adding a role is a data change, not a
# code change scattered through the tools.
CAPABILITIES: dict[Role, set[str]] = {
    Role.CUSTOMER: {
        "read_own_account",
        "read_public_docs",
        "read_own_agreement",
        "propose_action",
    },
    Role.SUPPORT_AGENT: {
        "read_any_account",
        "read_public_docs",
        "read_any_agreement",
        "read_internal_fields",
        "read_operations_signals",
        "propose_action",
        "execute_action",
    },
    Role.SUPPORT_MANAGER: {
        "read_any_account",
        "read_public_docs",
        "read_any_agreement",
        "read_internal_fields",
        "read_operations_signals",
        "propose_action",
        "execute_action",
        "approve_high_value_credit",
    },
}

# Fields that must never reach a customer, per entity. Enforced on the way out of
# the store (allow-list of rows, deny-list of columns).
INTERNAL_ONLY_FIELDS: dict[str, set[str]] = {
    "accounts": {"csm", "notes", "premium_support"},
    "orders": {"carrier_fault", "customer_fault", "notes"},
    "tickets": {"assigned_to", "historical_resolution", "channel"},
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    role: Role
    account_id: str | None = None  # required for customers, None for staff
    labels: tuple[str, ...] = field(default_factory=tuple)

    # -- capability checks ---------------------------------------------------
    def can(self, capability: str) -> bool:
        return capability in CAPABILITIES[self.role]

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise AccessDenied(
                f"Role '{self.role.value}' is not permitted to '{capability}'."
            )

    @property
    def is_internal(self) -> bool:
        return self.role in (Role.SUPPORT_AGENT, Role.SUPPORT_MANAGER)

    @property
    def context(self) -> str:
        return "internal" if self.is_internal else "customer"

    # -- scoping -------------------------------------------------------------
    def scope_account(self, requested: str | None) -> str | None:
        """Resolve which account this principal may query.

        Customers are pinned to their own account regardless of what was asked for.
        Staff may filter freely; `None` means "all accounts".
        """
        if self.is_internal:
            return requested
        if requested and requested.upper() != (self.account_id or "").upper():
            raise AccessDenied(
                f"You are signed in for {self.account_id}. Records for "
                f"{requested.upper()} belong to a different customer and cannot be shown."
            )
        return self.account_id

    def assert_owns(self, entity: str, record_id: str, account_id: str | None) -> None:
        """Guard for single-record lookups (order/ticket by id)."""
        if self.is_internal:
            return
        if (account_id or "").upper() != (self.account_id or "").upper():
            raise AccessDenied(
                f"{entity} {record_id} does not belong to account {self.account_id}."
            )

    def redact(self, entity: str, row: dict) -> dict:
        if self.can("read_internal_fields"):
            return dict(row)
        hidden = INTERNAL_ONLY_FIELDS.get(entity, set())
        return {k: v for k, v in row.items() if k not in hidden}

    def can_see_document(self, doc_visibility: str) -> bool:
        """`public`, `account:CUS-10X`, or `internal`."""
        if doc_visibility == "public":
            return True
        if doc_visibility == "internal":
            return self.is_internal
        if doc_visibility.startswith("account:"):
            owner = doc_visibility.split(":", 1)[1]
            if self.is_internal:
                return self.can("read_any_agreement")
            return owner.upper() == (self.account_id or "").upper()
        return False


# ---------------------------------------------------------------------------
# Mocked authentication. In production these come from the IdP / session cookie.
# ---------------------------------------------------------------------------
DEMO_PRINCIPALS: dict[str, Principal] = {
    "cust-halcyon": Principal(
        "cust-halcyon", "Rohan Iyer (Halcyon Foods)", Role.CUSTOMER, "CUS-101",
        ("Enterprise plan", "Custom agreement"),
    ),
    "cust-brightmoor": Principal(
        "cust-brightmoor", "Ananya Das (Brightmoor Textiles)", Role.CUSTOMER, "CUS-102",
        ("Scale plan", "Custom agreement"),
    ),
    "cust-pinecrest": Principal(
        "cust-pinecrest", "Farah Khan (Pinecrest Home)", Role.CUSTOMER, "CUS-103",
        ("Starter plan", "No custom agreement"),
    ),
    "cust-orbitron": Principal(
        "cust-orbitron", "Vikram Joshi (Orbitron Medical)", Role.CUSTOMER, "CUS-104",
        ("Enterprise plan", "No custom agreement"),
    ),
    "staff-meera": Principal(
        "staff-meera", "Meera (Support Agent)", Role.SUPPORT_AGENT, None,
        ("All accounts", "Cannot approve credits above the SOP limit"),
    ),
    "staff-kavya": Principal(
        "staff-kavya", "Kavya Nair (Support Manager)", Role.SUPPORT_MANAGER, None,
        ("All accounts", "Can approve high-value credits"),
    ),
}

DEFAULT_PRINCIPAL_ID = "cust-halcyon"


def get_principal(user_id: str | None) -> Principal:
    if not user_id:
        return DEMO_PRINCIPALS[DEFAULT_PRINCIPAL_ID]
    try:
        return DEMO_PRINCIPALS[user_id]
    except KeyError as exc:
        raise AccessDenied(f"Unknown user '{user_id}'.") from exc
