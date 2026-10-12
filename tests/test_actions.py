"""The confirmation gate. The point of these tests is that no path through the
system can execute an action a human has not confirmed."""
import pytest

from app import actions as store
from app.access import AccessDenied, get_principal
from app.agent.tools import ToolContext, run_tool

AGENT = get_principal("staff-meera")
MANAGER = get_principal("staff-kavya")
HALCYON = get_principal("cust-halcyon")


@pytest.fixture(autouse=True)
def clean_action_log(tmp_path, monkeypatch):
    """Isolate each test from the on-disk action log."""
    monkeypatch.setattr(store, "ACTION_LOG_PATH", tmp_path / "actions.json")
    yield


def test_propose_does_not_execute():
    action = store.propose(
        AGENT, "create_escalation",
        {"ticket_id": "CASE-301", "severity": "P1", "reason": "SLA breached"},
    )
    assert action.status == "pending_confirmation"
    assert action.executed_at is None
    assert "CASE-301" in action.preview


def test_execute_without_confirmation_is_refused():
    action = store.propose(
        AGENT, "create_escalation",
        {"ticket_id": "CASE-301", "severity": "P1", "reason": "SLA breached"},
    )
    with pytest.raises(PermissionError):
        store.execute(AGENT, action.action_id)


def test_the_agent_tool_cannot_bypass_the_gate():
    """The model's own execute tool hits the same wall."""
    ctx = ToolContext(AGENT)
    proposed, err = run_tool(
        ctx, "propose_action",
        {"action_type": "create_escalation",
         "payload": {"ticket_id": "CASE-301", "severity": "P1", "reason": "breach"}},
    )
    assert not err
    result, is_error = run_tool(ctx, "execute_confirmed_action", {"action_id": proposed["action_id"]})
    assert is_error
    assert result["error"] == "not_confirmed"


def test_confirm_then_execute_succeeds_and_is_idempotent():
    action = store.propose(
        AGENT, "create_followup_task", {"title": "Chase carrier", "due": "today"},
    )
    store.confirm(AGENT, action.action_id)
    first = store.execute(AGENT, action.action_id)
    assert first["status"] == "executed"
    assert first["result"]["reference"]
    second = store.execute(AGENT, action.action_id)
    assert second["executed_at"] == first["executed_at"]


def test_another_user_cannot_confirm_someone_elses_proposal():
    action = store.propose(
        AGENT, "create_followup_task", {"title": "Chase carrier", "due": "today"},
    )
    with pytest.raises(AccessDenied):
        store.confirm(MANAGER, action.action_id)


def test_high_value_credit_is_blocked_for_an_agent_at_execution_time():
    """Checked again on execute, not only when the proposal was built."""
    action = store.propose(
        AGENT, "issue_service_credit",
        {"order_id": "SHP-6002", "amount_inr": 2500, "reason": "goodwill"},
    )
    assert action.warnings and "manager" in action.warnings[0].lower()
    store.confirm(AGENT, action.action_id)
    with pytest.raises(AccessDenied):
        store.execute(AGENT, action.action_id)


def test_manager_can_execute_a_high_value_credit():
    action = store.propose(
        MANAGER, "issue_service_credit",
        {"order_id": "SHP-6002", "amount_inr": 2500, "reason": "goodwill"},
    )
    store.confirm(MANAGER, action.action_id)
    assert store.execute(MANAGER, action.action_id)["status"] == "executed"


def test_customer_cannot_propose_a_credit_to_themselves():
    with pytest.raises(AccessDenied):
        store.propose(
            HALCYON, "issue_service_credit",
            {"order_id": "SHP-5001", "amount_inr": 500, "reason": "late"},
        )


def test_customer_cannot_propose_an_action_on_another_account():
    with pytest.raises(AccessDenied):
        store.propose(
            HALCYON, "create_escalation",
            {"ticket_id": "CASE-302", "severity": "P2", "reason": "not mine"},
        )


def test_missing_required_fields_are_rejected_with_a_useful_message():
    with pytest.raises(ValueError) as exc:
        store.propose(AGENT, "create_escalation", {"ticket_id": "CASE-301"})
    assert "severity" in str(exc.value) and "reason" in str(exc.value)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        store.propose(
            AGENT, "create_followup_task",
            {"title": "x", "due": "today", "delete_everything": True},
        )


def test_cancelled_proposal_can_never_execute():
    action = store.propose(AGENT, "create_followup_task", {"title": "x", "due": "today"})
    store.cancel(AGENT, action.action_id)
    with pytest.raises(PermissionError):
        store.execute(AGENT, action.action_id)


def test_monthly_credit_cap_counts_only_executed_credits():
    action = store.propose(
        MANAGER, "issue_service_credit",
        {"order_id": "SHP-5001", "amount_inr": 900, "reason": "late"},
    )
    assert store.credits_issued_this_month("CUS-101") == 0   # proposed only
    store.confirm(MANAGER, action.action_id)
    store.execute(MANAGER, action.action_id)
    assert store.credits_issued_this_month("CUS-101") == 900
