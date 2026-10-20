"""The MCP server is a thin adapter over the agent's tool layer.

Driven in-process through the SDK's own client, so the protocol round trip is real
while staying fast and key-free. What's pinned: which tools are exposed to whom, that
a tool returns exactly what the chat agent's path returns, that scope still holds,
and that nothing can be executed over MCP.
"""
from __future__ import annotations

import json

import anyio
import pytest
from mcp.client import Client

from app import actions as action_store
from app import mcp_server
from app.access import DEFAULT_PRINCIPAL_ID, AccessDenied, get_principal
from app.agent.tools import ToolContext, for_model, run_tool

CUSTOMER_TOOLS = {
    "search_policy_documents", "lookup_records", "evaluate_cancellation",
    "evaluate_service_credit", "resolve_support_sla", "check_plan_capability",
    "propose_action",
}


@pytest.fixture(autouse=True)
def clean_action_log(tmp_path, monkeypatch):
    monkeypatch.setattr(action_store, "ACTION_LOG_PATH", tmp_path / "actions.json")
    yield


def _session(user_id: str | None, work):
    """Run `work(client)` against a server acting as `user_id` (None = default)."""
    server = mcp_server.build_server(mcp_server.resolve_principal(user_id))

    async def run():
        async with Client(server) as client:
            return await work(client)

    return anyio.run(run)


def _call(user_id, name, arguments):
    async def work(client):
        result = await client.call_tool(name, arguments)
        return json.loads(result.content[0].text), result.is_error

    return _session(user_id, work)


def _tool_names(user_id):
    async def work(client):
        return {t.name for t in (await client.list_tools()).tools}

    return _session(user_id, work)


# ------------------------------------------------------------------ identity
def test_default_identity_is_the_least_privileged_customer(monkeypatch):
    monkeypatch.delenv(mcp_server.USER_ENV, raising=False)
    principal = mcp_server.resolve_principal()
    assert principal.user_id == DEFAULT_PRINCIPAL_ID
    assert not principal.is_internal


def test_identity_comes_from_the_environment_and_must_be_known(monkeypatch):
    monkeypatch.setenv(mcp_server.USER_ENV, "staff-meera")
    assert mcp_server.resolve_principal().is_internal
    monkeypatch.setenv(mcp_server.USER_ENV, "someone-else")
    with pytest.raises(AccessDenied):
        mcp_server.resolve_principal()


# ---------------------------------------------------------------- tool list
def test_customer_sees_the_customer_tools_and_nothing_else():
    assert _tool_names(None) == CUSTOMER_TOOLS


def test_staff_also_get_the_operations_sweep_but_never_execute():
    names = _tool_names("staff-meera")
    assert names == CUSTOMER_TOOLS | {"detect_operations_signals"}
    assert "execute_confirmed_action" not in names


# ------------------------------------------------- same answers as the agent
def test_a_read_tool_returns_exactly_what_the_agent_path_returns():
    principal = get_principal(DEFAULT_PRINCIPAL_ID)
    expected, expected_error = run_tool(
        ToolContext(principal), "evaluate_cancellation", {"order_id": "SHP-5001"}
    )
    got, is_error = _call(None, "evaluate_cancellation", {"order_id": "SHP-5001"})

    assert is_error is expected_error is False
    assert got == json.loads(json.dumps(expected, default=str))
    assert got["facts"]["fee_inr"] == 0  # the policy engine's number, not recomputed


def test_the_ops_sweep_matches_the_view_the_agent_gives_its_model():
    principal = get_principal("staff-kavya")
    full, _ = run_tool(ToolContext(principal), "detect_operations_signals", {})
    got, is_error = _call("staff-kavya", "detect_operations_signals", {})
    assert not is_error
    assert got == json.loads(json.dumps(for_model("detect_operations_signals", full), default=str))


# ------------------------------------------------------------------- scope
def test_a_cross_account_request_is_denied():
    got, is_error = _call(None, "lookup_records", {
        "entity": "orders", "filters": {"account_id": "CUS-104"},
    })
    assert is_error and got["error"] == "access_denied"


def test_another_accounts_order_cannot_be_evaluated():
    got, is_error = _call(None, "evaluate_cancellation", {"order_id": "SHP-8001"})
    assert is_error and got["error"] == "access_denied"


def test_customers_cannot_reach_the_operations_sweep_even_by_name():
    got, is_error = _call(None, "detect_operations_signals", {})
    assert is_error and "staff only" in got["error"]


# ----------------------------------------------------------------- actions
def test_propose_action_only_proposes():
    got, is_error = _call("staff-meera", "propose_action", {
        "action_type": "create_escalation",
        "payload": {"ticket_id": "CASE-301", "severity": "P1", "reason": "SLA breached"},
    })
    assert not is_error
    assert got["status"] == "pending_confirmation" and got["executed_at"] is None
    assert action_store.get_action(got["action_id"])["status"] == "pending_confirmation"


def test_execute_is_refused_over_mcp_even_when_called_by_name():
    proposal, _ = _call("staff-meera", "propose_action", {
        "action_type": "create_followup_task", "payload": {"title": "x", "due": "today"},
    })
    got, is_error = _call("staff-meera", "execute_confirmed_action", {
        "action_id": proposal["action_id"],
    })
    assert is_error and got["error"] == "not_available_over_mcp"
    assert action_store.get_action(proposal["action_id"])["executed_at"] is None
