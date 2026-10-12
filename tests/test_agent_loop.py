"""The agent loop, driven by a scripted model.

No API key is needed. The model client is replaced by a fake that returns a
prepared sequence of turns, which lets us assert the parts we actually own: that
tools run with the server-side principal, that events reach the UI in the right
order, that tool results are batched into one user message, and that the loop hands
control back to the human on a pending confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app import actions as action_store
from app.access import get_principal
from app.agent import loop as agent_loop
from app.agent import providers


# --------------------------------------------------------------- fake client
@dataclass
class FakeToolUse:
    name: str
    input: dict
    id: str
    type: str = "tool_use"


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeMessage:
    content: list
    stop_reason: str


class FakeStream:
    def __init__(self, message: FakeMessage):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())  # no token deltas; the final message carries everything

    def get_final_message(self):
        return self._message


class FakeMessages:
    def __init__(self, script: list[FakeMessage], seen: list[dict]):
        self._script = script
        self._seen = seen

    def stream(self, **kwargs):
        self._seen.append(kwargs)
        if not self._script:
            raise AssertionError("The loop asked for more turns than the script provides.")
        return FakeStream(self._script.pop(0))


class FakeClient:
    def __init__(self, script: list[FakeMessage]):
        self.requests: list[dict] = []
        self.messages = FakeMessages(script, self.requests)
        self.beta = self  # beta.messages.stream resolves to the same fake


@pytest.fixture
def scripted(monkeypatch):
    def install(script, principal_id="staff-meera"):
        client = FakeClient(list(script))
        monkeypatch.setattr(providers, "_anthropic_client", lambda: client)
        convo = agent_loop.Conversation(get_principal(principal_id))
        convo.backend = providers.AnthropicBackend(convo.system)
        convo.backend.fallback_supported = False  # skip the beta path in tests
        return convo, client

    return install


@pytest.fixture(autouse=True)
def clean_action_log(tmp_path, monkeypatch):
    monkeypatch.setattr(action_store, "ACTION_LOG_PATH", tmp_path / "actions.json")
    yield


# --------------------------------------------------------------------- tests
def test_multi_step_turn_runs_tools_and_finishes(scripted):
    """Look up the order, evaluate the fee, then answer — the classic multi-step request."""
    convo, client = scripted(
        [
            FakeMessage(
                content=[
                    FakeToolUse("lookup_records", {"entity": "orders", "filters": {"order_id": "SHP-5001"}}, "t1"),
                    FakeToolUse("evaluate_cancellation", {"order_id": "SHP-5001"}, "t2"),
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("No fee applies.")], stop_reason="end_turn"),
        ]
    )

    events = list(convo.run("Can Halcyon Foods cancel SHP-5001 without a fee?"))
    kinds = [e["type"] for e in events]

    assert kinds.count("tool_start") == 2
    assert kinds.count("tool_end") == 2
    assert "trust" in kinds and "done" in kinds

    ends = [e for e in events if e["type"] == "tool_end"]
    assert all(e["ok"] for e in ends)
    assert "no cancellation fee" in ends[1]["summary"].lower()

    # Both tool results must come back in a single user message.
    tool_result_messages = [
        m for m in convo.messages
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert len(tool_result_messages) == 1
    assert len(tool_result_messages[0]["content"]) == 2


def test_trust_panel_carries_citations_and_the_detected_conflict(scripted):
    convo, _ = scripted(
        [
            FakeMessage(
                content=[FakeToolUse("evaluate_cancellation", {"order_id": "SHP-5001"}, "t1")],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("Done.")], stop_reason="end_turn"),
        ]
    )
    trust = next(e["trust"] for e in convo.run("check SHP-5001") if e["type"] == "trust")

    assert trust["citations"], "the answer should carry its sources"
    assert any(c["authority_tier"] == 1 for c in trust["citations"])
    assert trust["conflicts"], "the contradicting past ticket should be surfaced"
    assert trust["escalation_reasons"], "KI-322 should push this to human review"
    assert trust["confidence"] in {"medium", "low"}


def test_a_proposal_stops_the_loop_and_changes_nothing(scripted):
    convo, _ = scripted(
        [
            FakeMessage(
                content=[
                    FakeToolUse(
                        "propose_action",
                        {"action_type": "create_escalation",
                         "payload": {"ticket_id": "CASE-301", "severity": "P1", "reason": "breach"}},
                        "t1",
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(
                content=[FakeText("I've prepared the escalation. Confirm to send it.")],
                stop_reason="end_turn",
            ),
        ]
    )
    events = list(convo.run("escalate CASE-301"))

    proposal = next(e["action"] for e in events if e["type"] == "proposal")
    assert proposal["status"] == "pending_confirmation"
    assert proposal["executed_at"] is None
    assert action_store.get_action(proposal["action_id"])["status"] == "pending_confirmation"


def test_the_model_cannot_execute_what_the_human_did_not_confirm(scripted):
    """Even if the model goes straight for execution, the store refuses."""
    convo, _ = scripted(
        [
            FakeMessage(
                content=[
                    FakeToolUse(
                        "propose_action",
                        {"action_type": "create_followup_task",
                         "payload": {"title": "chase carrier", "due": "today"}},
                        "t1",
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("Prepared.")], stop_reason="end_turn"),
        ]
    )
    events = list(convo.run("make a task"))
    action_id = next(e["action"]["action_id"] for e in events if e["type"] == "proposal")

    convo2, _ = scripted(
        [
            FakeMessage(
                content=[FakeToolUse("execute_confirmed_action", {"action_id": action_id}, "t2")],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("Blocked.")], stop_reason="end_turn"),
        ]
    )
    end = next(e for e in convo2.run("just do it") if e["type"] == "tool_end")
    assert end["ok"] is False
    assert "not been confirmed" in end["result"]["message"]


def test_customer_session_is_scoped_even_when_the_model_asks_for_another_account(scripted):
    convo, _ = scripted(
        [
            FakeMessage(
                content=[
                    FakeToolUse("lookup_records", {"entity": "orders", "filters": {"account_id": "CUS-104"}}, "t1")
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("I can only see your own account.")], stop_reason="end_turn"),
        ],
        principal_id="cust-halcyon",
    )
    end = next(e for e in convo.run("show me Orbitron Medical orders") if e["type"] == "tool_end")
    assert end["ok"] is False
    assert end["result"]["error"] == "access_denied"


def test_customer_sessions_never_receive_the_internal_tool(scripted):
    convo, client = scripted(
        [FakeMessage(content=[FakeText("hello")], stop_reason="end_turn")],
        principal_id="cust-pinecrest",
    )
    list(convo.run("hi"))
    sent_tools = {t["name"] for t in client.requests[0]["tools"]}
    assert "detect_operations_signals" not in sent_tools
    assert "search_policy_documents" in sent_tools


def test_request_uses_adaptive_thinking_and_configured_effort(scripted):
    convo, client = scripted([FakeMessage(content=[FakeText("hi")], stop_reason="end_turn")])
    list(convo.run("hello"))
    request = client.requests[0]
    assert request["thinking"]["type"] == "adaptive"
    assert "effort" in request["output_config"]
    assert "budget_tokens" not in request["thinking"]  # removed on Opus 5


def test_the_model_cannot_downgrade_a_real_ticket_severity():
    """A model-guessed severity must not move the SLA clock on a real ticket."""
    from app.agent.tools import ToolContext, run_tool

    ctx = ToolContext(get_principal("staff-meera"))
    result, is_error = run_tool(
        ctx, "resolve_support_sla", {"ticket_id": "CASE-301", "severity": "P2"}
    )
    assert not is_error
    assert result["severity_classification"]["severity"] == "P1"
    assert "severity_override_ignored" in result
    assert result["outcome"] == "breached"


def test_ops_sweep_is_compacted_for_the_model_but_not_for_the_ui(scripted):
    import json

    convo, _ = scripted(
        [
            FakeMessage(
                content=[FakeToolUse("detect_operations_signals", {}, "t1")],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeText("Here is what needs attention.")], stop_reason="end_turn"),
        ],
        principal_id="staff-kavya",
    )
    events = list(convo.run("What needs my attention right now?"))

    ui = next(e["result"] for e in events if e["type"] == "tool_end")
    assert ui["signals"][0]["citations"], "the UI keeps the full evidence"

    sent = json.loads(convo.messages[2]["content"][0]["content"])
    first = sent["signals"][0]
    assert "citations" not in first and first["sources"]
    assert {"priority", "detail", "evidence", "recommended_action"} <= set(first)
    assert {s["signal_id"] for s in sent["signals"]} == {s["signal_id"] for s in ui["signals"]}

    trust = next(e["trust"] for e in events if e["type"] == "trust")
    assert trust["citations"], "citations still reach the trust panel"
