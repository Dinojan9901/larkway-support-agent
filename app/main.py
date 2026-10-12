"""FastAPI application: chat over SSE, action confirmation, and the ops dashboard."""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import actions as action_store
from .access import DEFAULT_PRINCIPAL_ID, DEMO_PRINCIPALS, AccessDenied, get_principal
from .agent.loop import AgentUnavailable, Conversation
from .agent.tools import TOOLS
from .config import CHAT_DISABLED_REASON, CHAT_ENABLED, MODEL, PROVIDER, ROOT
from .ingest.documents import load_corpus
from .policy.pack import verify_pack
from .signals.detector import detect_signals, summarise
from .store import dataset_summary, load_dataset

WEB_DIR = ROOT / "app" / "web"

app = FastAPI(
    title="Larkway Support Agent",
    version="1.0.0",
    description="Customer-facing and internal support agent over the Larkway dataset.",
)

# In-memory session store. A real deployment puts this in Redis and derives the
# principal from a verified session cookie rather than a header.
_SESSIONS: dict[str, Conversation] = {}


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


def _principal(user_id: str | None):
    try:
        return get_principal(user_id)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _conversation(session_id: str, user_id: str) -> Conversation:
    principal = _principal(user_id)
    key = f"{session_id}:{user_id}"
    convo = _SESSIONS.get(key)
    if convo is None:
        convo = Conversation(principal)
        _SESSIONS[key] = convo
    return convo


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return {
        "users": [
            {
                "user_id": p.user_id,
                "name": p.display_name,
                "role": p.role.value,
                "account_id": p.account_id,
                "context": p.context,
                "labels": list(p.labels),
            }
            for p in DEMO_PRINCIPALS.values()
        ],
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


@app.post("/api/chat")
def chat(
    payload: dict[str, Any] = Body(...),
) -> StreamingResponse:
    message = (payload.get("message") or "").strip()
    user_id = payload.get("user_id") or DEFAULT_PRINCIPAL_ID
    session_id = payload.get("session_id") or uuid.uuid4().hex
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    convo = _conversation(session_id, user_id)

    def generate():
        yield _sse({"type": "session", "session_id": session_id})
        try:
            for event in convo.run(message):
                yield _sse(event)
        except AgentUnavailable as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # surfaced rather than swallowed
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reset")
def reset(payload: dict[str, Any] = Body(default={})) -> dict[str, str]:
    session_id = payload.get("session_id")
    user_id = payload.get("user_id")
    if session_id and user_id:
        _SESSIONS.pop(f"{session_id}:{user_id}", None)
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# Actions — the human confirmation gate
# ---------------------------------------------------------------------------
@app.get("/api/actions")
def list_actions(user_id: str = Query(...)) -> dict[str, Any]:
    principal = _principal(user_id)
    return {"actions": action_store.list_actions(principal)}


@app.post("/api/actions/{action_id}/confirm")
def confirm_action(action_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The only path that can mark an action confirmed. Requires a real user click."""
    principal = _principal(payload.get("user_id"))
    try:
        confirmed = action_store.confirm(principal, action_id)
        executed = action_store.execute(principal, action_id)
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LookupError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"confirmed": confirmed["action_id"], "action": executed}


@app.post("/api/actions/{action_id}/cancel")
def cancel_action(action_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    principal = _principal(payload.get("user_id"))
    try:
        return {"action": action_store.cancel(principal, action_id)}
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Operations dashboard (proactive issue detection)
# ---------------------------------------------------------------------------
@app.get("/api/signals")
def signals(
    user_id: str = Query(...),
    category: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
) -> dict[str, Any]:
    principal = _principal(user_id)
    try:
        principal.require("read_operations_signals")
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    found = detect_signals(
        categories=[category] if category else None, account_id=account_id
    )
    return {"summary": summarise(found), "signals": [s.as_dict() for s in found]}


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    from .config import HOST, PORT

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
