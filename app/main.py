"""FastAPI application: auth, chat over SSE, action confirmation, the ops dashboard,
and the React frontend's static build.

Identity comes from a short-lived JWT (see `auth.py`), never from the request body.
Roles are enforced here and in the data layer below it, not just hidden in the UI:
the operations dashboard needs the ops role, and every read is scoped to the caller.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles

from . import actions as action_store
from .access import AccessDenied, Principal
from .agent.loop import AgentUnavailable, Conversation
from .agent.tools import TOOLS
from .auth import (
    DemoUser,
    authenticate,
    create_access_token,
    current_principal,
    current_user,
    require_ops,
    token_ttl_seconds,
)
from .config import AUDIT_LOG_PATH, CHAT_DISABLED_REASON, CHAT_ENABLED, MODEL, PROVIDER, ROOT
from .ingest.documents import load_corpus
from .policy.pack import verify_pack
from .signals.detector import detect_signals, summarise
from .store import dataset_summary, load_dataset

# The Vite build of frontend/ lands here (on Vercel it is served from the CDN).
PUBLIC_DIR = ROOT / "public"

app = FastAPI(
    title="Larkway Support Agent",
    version="2.0.0",
    description="Customer-facing and internal support agent over the Larkway dataset.",
)

# In-memory session store, keyed by session *and* principal so one user can never
# continue another's conversation. A real deployment puts this in Redis.
_SESSIONS: dict[str, Conversation] = {}
_AUDIT_LOCK = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    load_corpus()            # builds data/index/corpus.json if absent
    load_dataset()
    problems = verify_pack()
    if problems:
        # Loud, not fatal: the operator needs to know the rules drifted from the docs.
        print("\n*** POLICY PACK VERIFICATION FAILED ***")
        for p in problems:
            print("   ", p)
        print("*** Answers may not match the source documents. ***\n")
    else:
        print("Policy pack verified against source PDFs.")
    print(f"Model: {PROVIDER}/{MODEL} | chat {'enabled' if CHAT_ENABLED else f'DISABLED ({CHAT_DISABLED_REASON})'}")


def _conversation(session_id: str, principal: Principal) -> Conversation:
    key = f"{session_id}:{principal.user_id}"
    convo = _SESSIONS.get(key)
    if convo is None:
        convo = Conversation(principal)
        _SESSIONS[key] = convo
    return convo


def _user_view(principal: Principal, username: str) -> dict[str, Any]:
    return {
        "username": username,
        "name": principal.display_name,
        "role": "ops" if principal.is_internal else "customer",
        "account_id": principal.account_id,
        "labels": list(principal.labels),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/api/auth/token")
def login(form: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """OAuth2 password flow: form-encoded username and password in, bearer token out."""
    user = authenticate(form.username, form.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = current_principal(user)
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": token_ttl_seconds(),
        "user": _user_view(principal, user.username),
    }


@app.get("/api/auth/me")
def me(user: DemoUser = Depends(current_user)) -> dict[str, Any]:
    return _user_view(current_principal(user), user.username)


# ---------------------------------------------------------------------------
# Metadata (public: nothing here is account data)
# ---------------------------------------------------------------------------
@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return {
        "dataset": dataset_summary(),
        "model": MODEL,
        "provider": PROVIDER,
        "chat_enabled": CHAT_ENABLED,
        "chat_disabled_reason": None if CHAT_ENABLED else CHAT_DISABLED_REASON,
        "tools": [
            {
                "name": t.name,
                "family": t.family,
                "label": t.label,
                "internal_only": t.internal_only,
            }
            for t in TOOLS
        ],
        "policy_pack_ok": not verify_pack(),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "chat_enabled": CHAT_ENABLED}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def record_conversation(record: dict[str, Any]) -> None:
    """Background task: append one finished chat turn to the audit log.

    Runs after the streamed response has been sent, so a slow disk never holds up
    the answer. Logs who asked what, which tools ran and how it ended — not tokens.
    """
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _AUDIT_LOCK:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


@app.post("/api/chat")
def chat(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    message = (payload.get("message") or "").strip()
    session_id = payload.get("session_id") or uuid.uuid4().hex
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    convo = _conversation(session_id, principal)
    record: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": principal.user_id,
        "role": "ops" if principal.is_internal else "customer",
        "session_id": session_id,
        "question": message,
        "tools_used": [],
        "proposals": 0,
        "outcome": "interrupted",
    }

    def generate():
        started = time.monotonic()
        yield _sse({"type": "session", "session_id": session_id})
        try:
            for event in convo.run(message):
                kind = event.get("type")
                if kind == "tool_start":
                    record["tools_used"].append(event.get("tool"))
                elif kind == "proposal":
                    record["proposals"] += 1
                elif kind in ("done", "error"):
                    record["outcome"] = kind
                yield _sse(event)
        except AgentUnavailable as exc:
            record["outcome"] = "unavailable"
            yield _sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # surfaced rather than swallowed
            record["outcome"] = "error"
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            record["duration_ms"] = int((time.monotonic() - started) * 1000)

    # FastAPI attaches these to the response; Starlette runs them once the stream ends.
    background_tasks.add_task(record_conversation, record)
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reset")
def reset(
    payload: dict[str, Any] = Body(default={}),
    principal: Principal = Depends(current_principal),
) -> dict[str, str]:
    session_id = payload.get("session_id")
    if session_id:
        _SESSIONS.pop(f"{session_id}:{principal.user_id}", None)
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Actions — the human confirmation gate
# ---------------------------------------------------------------------------
@app.get("/api/actions")
def list_actions(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {"actions": action_store.list_actions(principal)}


@app.post("/api/actions/{action_id}/confirm")
def confirm_action(
    action_id: str, principal: Principal = Depends(current_principal)
) -> dict[str, Any]:
    """The only path that can mark an action confirmed. Requires a real user click."""
    try:
        confirmed = action_store.confirm(principal, action_id)
        executed = action_store.execute(principal, action_id)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LookupError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"confirmed": confirmed["action_id"], "action": executed}


@app.post("/api/actions/{action_id}/cancel")
def cancel_action(
    action_id: str, principal: Principal = Depends(current_principal)
) -> dict[str, Any]:
    try:
        return {"action": action_store.cancel(principal, action_id)}
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Operations dashboard (proactive issue detection) — ops role only
# ---------------------------------------------------------------------------
@app.get("/api/signals")
def signals(
    category: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    principal: Principal = Depends(require_ops),
) -> dict[str, Any]:
    found = detect_signals(
        categories=[category] if category else None, account_id=account_id
    )
    return {"summary": summarise(found), "signals": [s.as_dict() for s in found]}


# ---------------------------------------------------------------------------
# React frontend (frontend/ -> `npm run build` -> public/)
# ---------------------------------------------------------------------------
_NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Larkway Support Agent</title></head><body style="font-family:sans-serif;padding:2rem">
<h1>Frontend not built</h1>
<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, then reload.
For development, run <code>npm run dev</code> in <code>frontend/</code> and open the Vite URL.</p>
</body></html>"""

if (PUBLIC_DIR / "index.html").exists():
    # Mounted last, so every /api route above takes precedence.
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="frontend")
else:

    @app.get("/", include_in_schema=False)
    def frontend_not_built() -> HTMLResponse:
        return HTMLResponse(_NOT_BUILT, status_code=503)


if __name__ == "__main__":
    import uvicorn

    from .config import HOST, PORT

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
