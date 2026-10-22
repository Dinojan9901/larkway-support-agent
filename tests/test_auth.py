"""JWT login and role checks, tested at the HTTP layer.

The chat agent is replaced by a scripted conversation, so nothing here calls a model.
"""
from __future__ import annotations

import json

import jwt
import pytest
from fastapi.testclient import TestClient

from app import actions as action_store
from app import auth
from app import main as main_module
from app.main import app

SECRET = "test-secret-" + "x" * 40


def _readme_logins() -> dict[str, str]:
    """The demo logins live in the README for reviewers, and only there. Reading them
    from it also proves the README's credentials actually work."""
    import re
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (?:Customer|Ops) \| `([^`]+)` \| `([^`]+)` \|", readme, re.M)
    assert len(rows) == 2, "README demo-logins table not found"
    return dict(rows)


PASSWORDS = _readme_logins()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV, SECRET)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(action_store, "ACTION_LOG_PATH", tmp_path / "actions.json")
    monkeypatch.setattr(main_module, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
    main_module._SESSIONS.clear()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, username):
    res = client.post(
        "/api/auth/token", data={"username": username, "password": PASSWORDS[username]}
    )
    assert res.status_code == 200, res.text
    return res.json()


def _auth(client, username):
    return {"Authorization": f"Bearer {_login(client, username)['access_token']}"}


# ------------------------------------------------------------------- login
def test_login_returns_a_short_lived_bearer_token(client):
    body = _login(client, "customer")
    assert body["token_type"] == "bearer"
    assert 0 < body["expires_in"] <= 60 * 60
    assert body["user"]["role"] == "customer" and body["user"]["account_id"] == "CUS-101"
    claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == "customer" and claims["exp"] - claims["iat"] == body["expires_in"]


@pytest.mark.parametrize("username,password", [
    ("customer", "wrong-password"),
    ("ops", PASSWORDS["customer"]),
    ("nobody", "anything"),
    ("", ""),
])
def test_bad_credentials_are_refused(client, username, password):
    res = client.post("/api/auth/token", data={"username": username, "password": password})
    assert res.status_code in (401, 422)
    if res.status_code == 401:
        assert res.headers["www-authenticate"] == "Bearer"


def test_only_password_hashes_are_stored():
    for user in auth.DEMO_USERS.values():
        assert user.password_hash.startswith("scrypt$")
        assert PASSWORDS[user.username] not in user.password_hash
        assert auth.verify_password(PASSWORDS[user.username], user.password_hash)


def test_me_identifies_the_caller(client):
    res = client.get("/api/auth/me", headers=_auth(client, "ops"))
    assert res.status_code == 200 and res.json()["role"] == "ops"


# --------------------------------------------------------------- tokens
def test_requests_without_a_token_are_refused(client):
    for method, path in [
        ("get", "/api/auth/me"), ("get", "/api/signals"), ("get", "/api/actions"),
        ("post", "/api/chat"), ("post", "/api/reset"),
        ("post", "/api/actions/ACT-1/confirm"), ("post", "/api/actions/ACT-1/cancel"),
    ]:
        res = client.post(path, json={"message": "hi"}) if method == "post" else client.get(path)
        assert res.status_code == 401, (method, path, res.status_code)


def test_an_expired_token_is_refused(client):
    token = auth.create_access_token(auth.DEMO_USERS["ops"], ttl_seconds=-1)
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401 and "expired" in res.json()["detail"]


def test_a_token_signed_with_another_secret_is_refused(client):
    forged = jwt.encode(
        {"sub": "ops", "pid": "staff-kavya", "exp": 9_999_999_999}, "not-the-secret-" + "y" * 40, "HS256"
    )
    res = client.get("/api/signals", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_a_token_cannot_grant_itself_another_principal(client):
    """Correctly signed, but claims the customer is the manager."""
    token = jwt.encode(
        {"sub": "customer", "pid": "staff-kavya", "exp": 9_999_999_999}, SECRET, "HS256"
    )
    res = client.get("/api/signals", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_on_vercel_login_refuses_to_run_without_a_configured_secret(client, monkeypatch):
    monkeypatch.delenv(auth.SECRET_ENV)
    monkeypatch.setenv("VERCEL", "1")
    res = client.post("/api/auth/token", data={"username": "ops", "password": PASSWORDS["ops"]})
    assert res.status_code == 503 and auth.SECRET_ENV in res.json()["detail"]


# ----------------------------------------------------------------- roles
def test_the_ops_console_needs_the_ops_role(client):
    assert client.get("/api/signals", headers=_auth(client, "customer")).status_code == 403
    res = client.get("/api/signals", headers=_auth(client, "ops"))
    assert res.status_code == 200 and res.json()["summary"]["total"] > 0


def test_a_customer_cannot_confirm_someone_elses_proposal(client):
    ops = action_store.propose(
        auth.get_principal("staff-kavya"), "create_followup_task", {"title": "x", "due": "today"}
    )
    res = client.post(f"/api/actions/{ops.action_id}/confirm", headers=_auth(client, "customer"))
    assert res.status_code == 403
    assert action_store.get_action(ops.action_id)["executed_at"] is None


def test_the_owner_confirms_with_an_explicit_call_and_it_runs_once(client):
    ops = action_store.propose(
        auth.get_principal("staff-kavya"), "create_followup_task", {"title": "x", "due": "today"}
    )
    headers = _auth(client, "ops")
    res = client.post(f"/api/actions/{ops.action_id}/confirm", headers=headers)
    assert res.status_code == 200 and res.json()["action"]["status"] == "executed"
    again = client.post(f"/api/actions/{ops.action_id}/confirm", headers=headers)
    assert again.status_code == 400


def test_bootstrap_is_public_but_exposes_no_users(client):
    body = client.get("/api/bootstrap").json()
    assert "users" not in body
    assert not any(pw in json.dumps(body) for pw in PASSWORDS.values())


# --------------------------------------------- chat + background audit log
class ScriptedConversation:
    """Stands in for the agent: streams a fixed turn, calls no model."""

    def __init__(self, principal):
        self.principal = principal

    def run(self, message):
        yield {"type": "tool_start", "tool": "evaluate_cancellation", "tool_use_id": "t1"}
        yield {"type": "tool_end", "tool": "evaluate_cancellation", "tool_use_id": "t1", "ok": True}
        yield {"type": "text", "text": f"Scripted answer to: {message}"}
        yield {"type": "done", "stop_reason": "end_turn"}


def test_chat_streams_and_a_background_task_logs_the_finished_turn(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "Conversation", ScriptedConversation)
    res = client.post(
        "/api/chat",
        json={"message": "Can I cancel SHP-5001?", "session_id": "s1"},
        headers=_auth(client, "customer"),
    )
    assert res.status_code == 200
    events = [json.loads(l[6:]) for l in res.text.splitlines() if l.startswith("data: ")]
    assert [e["type"] for e in events] == ["session", "tool_start", "tool_end", "text", "done"]

    # TestClient runs background tasks before returning, after the body is sent.
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["user_id"] == "cust-halcyon" and entry["role"] == "customer"
    assert entry["question"] == "Can I cancel SHP-5001?"
    assert entry["tools_used"] == ["evaluate_cancellation"] and entry["outcome"] == "done"
    assert "access_token" not in lines[0] and "Bearer" not in lines[0]


def test_audit_logging_is_a_background_task_not_part_of_the_request(monkeypatch):
    """If the logger were called inline, a slow disk would delay every answer."""
    calls = []
    monkeypatch.setattr(main_module, "Conversation", ScriptedConversation)

    class Recorder:
        def add_task(self, fn, *args, **kwargs):
            calls.append(fn)

    response = main_module.chat(
        Recorder(), {"message": "hi", "session_id": "s2"}, auth.get_principal("cust-halcyon")
    )
    assert calls == [main_module.record_conversation]
    assert not (main_module.AUDIT_LOG_PATH).exists()  # nothing written yet
    assert response.media_type == "text/event-stream"


def test_one_user_cannot_continue_another_users_session(client, monkeypatch):
    monkeypatch.setattr(main_module, "Conversation", ScriptedConversation)
    client.post("/api/chat", json={"message": "a", "session_id": "shared"}, headers=_auth(client, "customer"))
    client.post("/api/chat", json={"message": "b", "session_id": "shared"}, headers=_auth(client, "ops"))
    owners = {c.principal.user_id for c in main_module._SESSIONS.values()}
    assert owners == {"cust-halcyon", "staff-kavya"}
