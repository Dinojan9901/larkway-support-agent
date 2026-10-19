"""The model backends, driven by fake clients.

Two things are pinned here. The Anthropic backend must survive a key that lacks the
refusal-fallback beta (the request is sent when the stream opens, not when it is
built). And the OpenAI-compatible backend — the free path, via Gemini or Groq —
must round-trip tool calls through the same loop and the same guarantees.
"""
from __future__ import annotations

import json

import anthropic
import httpx2
import pytest
from openai.types.chat import ChatCompletion

from app import actions as action_store
from app.access import get_principal
from app.agent import loop as agent_loop
from app.agent import providers
from app.agent.tools import specs_for
from tests.test_agent_loop import FakeMessage, FakeStream, FakeText


@pytest.fixture(autouse=True)
def clean_action_log(tmp_path, monkeypatch):
    monkeypatch.setattr(action_store, "ACTION_LOG_PATH", tmp_path / "actions.json")
    yield


# ------------------------------------------------------- anthropic: beta fallback
def _bad_request() -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        "unknown beta", response=httpx2.Response(400, request=request), body=None
    )


class RejectingStream(FakeStream):
    def __enter__(self):
        raise _bad_request()


class BetaRejectingClient:
    """Rejects the beta request on open, as the real SDK does; plain requests work."""

    def __init__(self):
        self.beta_calls = 0
        self.plain_calls = 0
        outer = self

        class Beta:
            class messages:
                @staticmethod
                def stream(**kwargs):
                    outer.beta_calls += 1
                    return RejectingStream(None)

        class Messages:
            @staticmethod
            def stream(**kwargs):
                outer.plain_calls += 1
                return FakeStream(FakeMessage([FakeText("hi")], "end_turn"))

        self.beta = Beta
        self.messages = Messages


def test_a_key_without_the_fallback_beta_downgrades_instead_of_failing(monkeypatch):
    client = BetaRejectingClient()
    monkeypatch.setattr(providers, "_anthropic_client", lambda: client)
    convo = agent_loop.Conversation(get_principal("cust-halcyon"))
    convo.backend = providers.AnthropicBackend(convo.system)
    convo.backend.fallback_supported = True

    kinds = [e["type"] for e in convo.run("hello")]
    assert "error" not in kinds and "done" in kinds
    assert convo.backend.fallback_supported is False

    list(convo.run("again"))
    assert client.beta_calls == 1, "the rejected beta should not be retried every turn"
    assert client.plain_calls == 2


# --------------------------------------------------- openai-compatible: free path
def _completion(message: dict, finish_reason: str) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", **message}}],
        }
    )


def _tool_call(call_id: str, name: str, args: dict | str, **extra) -> dict:
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": raw}, **extra}


class FakeOpenAI:
    def __init__(self, script: list[ChatCompletion]):
        self.requests: list[dict] = []
        self._script = script
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        # Snapshot: the backend keeps appending to the same list.
        self.requests.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return self._script.pop(0)


@pytest.fixture
def compat(monkeypatch):
    def install(script, principal_id="staff-meera"):
        client = FakeOpenAI(list(script))
        monkeypatch.setattr(providers, "_openai_client", lambda: client)
        convo = agent_loop.Conversation(get_principal(principal_id))
        convo.backend = providers.OpenAICompatBackend(convo.system)
        return convo, client

    return install


def test_free_form_objects_become_json_strings_for_gemini():
    converted = {
        d["function"]["name"]: (d, fields)
        for d, fields in map(providers.compat_tool, specs_for(get_principal("staff-meera")))
    }
    lookup, fields = converted["lookup_records"]
    assert fields == {"filters"}
    assert lookup["function"]["parameters"]["properties"]["filters"]["type"] == "string"
    assert "additionalProperties" not in json.dumps([d for d, _ in converted.values()])
    assert converted["propose_action"][1] == {"payload"}


def test_compat_turn_decodes_arguments_runs_tools_and_echoes_the_signature(compat):
    convo, client = compat(
        [
            _completion(
                {"content": None, "tool_calls": [
                    _tool_call("c1", "lookup_records",
                               {"entity": "orders", "filters": '{"order_id": "SHP-5001"}'},
                               extra_content={"google": {"thought_signature": "sig"}}),
                    _tool_call("c2", "evaluate_cancellation", {"order_id": "SHP-5001"}),
                ]},
                "tool_calls",
            ),
            _completion({"content": "No fee applies."}, "stop"),
        ]
    )
    events = list(convo.run("Can I cancel SHP-5001 for free?"))

    ends = [e for e in events if e["type"] == "tool_end"]
    assert [e["ok"] for e in ends] == [True, True]
    assert ends[0]["result"]["count"] == 1, "filters string should decode to a real filter"
    assert "".join(e["text"] for e in events if e["type"] == "text") == "No fee applies."
    assert "trust" in [e["type"] for e in events]

    second = client.requests[1]["messages"]
    assert second[0]["role"] == "system"
    assistant = next(m for m in second if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig"
    assert [m["tool_call_id"] for m in second if m["role"] == "tool"] == ["c1", "c2"]


def test_compat_bad_arguments_become_a_tool_error_not_a_crash(compat):
    convo, _ = compat(
        [
            _completion({"content": None, "tool_calls": [
                _tool_call("c1", "lookup_records", '{"entity": "orders", "filters": "not json"}'),
            ]}, "tool_calls"),
            _completion({"content": "Sorry, let me retry."}, "stop"),
        ]
    )
    end = next(e for e in convo.run("orders?") if e["type"] == "tool_end")
    assert end["ok"] is False
    assert end["result"]["error"] == "invalid_arguments"


def test_compat_backend_keeps_the_access_boundary(compat):
    convo, _ = compat(
        [
            _completion({"content": None, "tool_calls": [
                _tool_call("c1", "lookup_records",
                           {"entity": "orders", "filters": '{"account_id": "CUS-104"}'}),
            ]}, "tool_calls"),
            _completion({"content": "I can only see your own account."}, "stop"),
        ],
        principal_id="cust-halcyon",
    )
    end = next(e for e in convo.run("show me Orbitron Medical orders") if e["type"] == "tool_end")
    assert end["ok"] is False
    assert end["result"]["error"] == "access_denied"


def test_compat_content_filter_is_reported_as_a_refusal(compat):
    convo, _ = compat([_completion({"content": None}, "content_filter")])
    events = list(convo.run("hello"))
    assert events[-1]["type"] == "error"
    assert "declined" in events[-1]["message"]


class QuotaSpentOpenAI(FakeOpenAI):
    """The first model's quota is spent; any other model answers."""

    def create(self, **kwargs):
        import openai

        if kwargs["model"] == providers.MODEL:
            self.requests.append({"model": kwargs["model"]})
            request = httpx2.Request("POST", "https://api.example/v1/chat/completions")
            raise openai.RateLimitError(
                "daily token cap reached",
                response=httpx2.Response(429, request=request),
                body=None,
            )
        return super().create(**kwargs)


def test_a_rate_limited_model_falls_back_to_the_next_one(monkeypatch):
    client = QuotaSpentOpenAI([_completion({"content": "Hello from the fallback."}, "stop")])
    monkeypatch.setattr(providers, "_openai_client", lambda: client)
    monkeypatch.setattr(providers, "COMPAT_FALLBACK_MODELS", ["backup-model"])
    convo = agent_loop.Conversation(get_principal("cust-halcyon"))
    convo.backend = providers.OpenAICompatBackend(convo.system)

    events = list(convo.run("hi"))
    assert "error" not in [e["type"] for e in events]
    assert [r["model"] for r in client.requests] == [providers.MODEL, "backup-model"]


def test_without_a_fallback_the_rate_limit_is_reported(monkeypatch):
    client = QuotaSpentOpenAI([])
    monkeypatch.setattr(providers, "_openai_client", lambda: client)
    monkeypatch.setattr(providers, "COMPAT_FALLBACK_MODELS", [])
    convo = agent_loop.Conversation(get_principal("cust-halcyon"))
    convo.backend = providers.OpenAICompatBackend(convo.system)

    events = list(convo.run("hi"))
    assert events[-1]["type"] == "error" and "429" in events[-1]["message"]


class RejectsToolCalls(FakeOpenAI):
    """Groq-style server-side tool-call validation: the first N requests are refused."""

    def __init__(self, script, failures: int, code: str = "tool_use_failed"):
        super().__init__(script)
        self.failures = failures
        self.code = code
        self.attempts = 0

    def create(self, **kwargs):
        import openai

        self.attempts += 1
        if self.attempts <= self.failures:
            request = httpx2.Request("POST", "https://api.example/v1/chat/completions")
            raise openai.BadRequestError(
                "Tool call validation failed: /account_id: expected string, but got null",
                response=httpx2.Response(400, request=request),
                body={"message": "Tool call validation failed", "code": self.code},
            )
        return super().create(**kwargs)


def _compat_convo(monkeypatch, client):
    monkeypatch.setattr(providers, "_openai_client", lambda: client)
    monkeypatch.setattr(providers, "COMPAT_FALLBACK_MODELS", [])
    convo = agent_loop.Conversation(get_principal("staff-kavya"))
    convo.backend = providers.OpenAICompatBackend(convo.system)
    return convo


def test_a_rejected_tool_call_is_retried(monkeypatch):
    """Seen live on Groq: `"account_id": null` failed server-side validation."""
    client = RejectsToolCalls([_completion({"content": "All clear."}, "stop")], failures=1)
    events = list(_compat_convo(monkeypatch, client).run("What needs my attention?"))
    assert "error" not in [e["type"] for e in events]
    assert client.attempts == 2


def test_a_tool_call_that_keeps_failing_validation_gives_up(monkeypatch):
    client = RejectsToolCalls([], failures=99)
    events = list(_compat_convo(monkeypatch, client).run("What needs my attention?"))
    assert events[-1]["type"] == "error" and "400" in events[-1]["message"]
    assert client.attempts == 1 + providers.MALFORMED_TOOL_CALL_RETRIES


def test_other_bad_requests_are_not_retried(monkeypatch):
    client = RejectsToolCalls([], failures=99, code="invalid_api_key")
    events = list(_compat_convo(monkeypatch, client).run("hi"))
    assert events[-1]["type"] == "error"
    assert client.attempts == 1
