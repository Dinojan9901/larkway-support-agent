"""Access control lives in the data/tool layer, so it is tested there — not by
asking a model nicely."""
import pytest

from app.access import AccessDenied, get_principal
from app.agent.tools import ToolContext, run_tool, specs_for
from app.retrieval.index import get_index
from app.store import get_record, query

HALCYON = get_principal("cust-halcyon")
BRIGHTMOOR = get_principal("cust-brightmoor")
AGENT = get_principal("staff-meera")
MANAGER = get_principal("staff-kavya")


# ------------------------------------------------------------------ row scope
def test_customer_sees_only_their_own_orders():
    ids = {o["order_id"] for o in query(HALCYON, "orders")}
    assert ids == {"SHP-5001", "SHP-5002"}


def test_staff_see_every_order():
    assert len(query(AGENT, "orders")) == 7


def test_customer_cannot_widen_scope_via_a_filter_argument():
    """The model could be talked into passing another account_id; the store re-derives."""
    with pytest.raises(AccessDenied):
        query(HALCYON, "orders", {"account_id": "CUS-104"})


def test_customer_cannot_fetch_another_accounts_record_by_id():
    with pytest.raises(AccessDenied):
        get_record(HALCYON, "orders", "SHP-8001")


def test_customer_can_fetch_their_own_record():
    assert get_record(HALCYON, "orders", "SHP-5001")["order_id"] == "SHP-5001"


# --------------------------------------------------------------- column scope
def test_internal_only_columns_are_stripped_for_customers():
    ticket = query(HALCYON, "tickets")[0]
    for hidden in ("assigned_to", "historical_resolution", "channel"):
        assert hidden not in ticket
    order = query(HALCYON, "orders")[0]
    for hidden in ("carrier_fault", "customer_fault", "notes"):
        assert hidden not in order


def test_staff_see_internal_columns():
    ticket = query(AGENT, "tickets")[0]
    assert "assigned_to" in ticket and "historical_resolution" in ticket


# ------------------------------------------------------------------ documents
def test_a_customer_cannot_retrieve_another_customers_agreement():
    hits, _ = get_index().search(BRIGHTMOOR, "Halcyon cancellation terms 10 minutes", top_k=8)
    assert all(h.section.account_id in (None, "CUS-102") for h in hits)
    assert not any("Halcyon" in h.section.doc_title for h in hits)


def test_a_customer_can_retrieve_their_own_agreement():
    hits, _ = get_index().search(HALCYON, "cancellation fee booked shipment", top_k=6)
    assert any(h.section.account_id == "CUS-101" for h in hits)


def test_staff_can_retrieve_any_agreement():
    hits, _ = get_index().search(AGENT, "Halcyon cancellation no fee", top_k=6)
    assert any(h.section.account_id == "CUS-101" for h in hits)


def test_deprecated_policy_is_excluded_from_the_answer_set():
    hits, stale = get_index().search(AGENT, "enterprise P1 response target hours", top_k=8)
    assert all(h.section.status != "DEPRECATED" for h in hits)
    assert any(s.section.doc_id == "support_policy_v4" for s in stale)


# ---------------------------------------------------------------- tool surface
def test_operations_tool_is_hidden_from_customers_and_refused_if_called():
    assert "detect_operations_signals" not in {t["name"] for t in specs_for(HALCYON)}
    assert "detect_operations_signals" in {t["name"] for t in specs_for(AGENT)}

    ctx = ToolContext(HALCYON)
    result, is_error = run_tool(ctx, "detect_operations_signals", {})
    assert is_error and "staff only" in result["error"]


def test_denied_lookup_comes_back_as_a_recoverable_tool_error():
    ctx = ToolContext(HALCYON)
    result, is_error = run_tool(ctx, "lookup_records", {"entity": "orders", "filters": {"account_id": "CUS-102"}})
    assert is_error
    assert result["error"] == "access_denied"
    assert "guidance" in result  # tells the model not to retry with another account


def test_customer_cannot_evaluate_another_accounts_order():
    ctx = ToolContext(HALCYON)
    result, is_error = run_tool(ctx, "evaluate_cancellation", {"order_id": "SHP-6001"})
    assert is_error and result["error"] == "access_denied"


def test_manager_has_the_approval_capability_and_the_agent_does_not():
    assert MANAGER.can("approve_high_value_credit")
    assert not AGENT.can("approve_high_value_credit")
