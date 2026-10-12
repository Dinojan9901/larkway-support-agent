"""The agent loop.

A manual loop rather than the SDK tool runner, for three reasons the runner does
not give us:

  * the Principal is injected server-side into every tool call, so identity can
    never arrive as a model-supplied argument;
  * the UI needs a live event per tool call — which tool, on what, what came back —
    and those events have to interleave with the text stream;
  * the turn has to end cleanly on a pending confirmation, handing control back to
    the human instead of looping onward.

The model itself sits behind a backend (`providers.py`): Claude, or any
OpenAI-compatible endpoint such as Gemini's free tier. None of the guarantees above
live in the backend, so switching models cannot weaken them.

Events emitted (consumed by the SSE endpoint and rendered in the UI):
  status | thinking | tool_start | tool_end | text | proposal | trust | done | error
"""
from __future__ import annotations

from typing import Any, Iterator

from ..access import Principal
from ..config import MAX_AGENT_STEPS
from .prompts import build_system_prompt
from .providers import AgentUnavailable, ModelError, make_backend  # noqa: F401
from .tools import ToolContext, describe_argument, for_model, run_tool, specs_for

Event = dict[str, Any]


class Conversation:
    """Holds message history for one browser session and one principal."""

    def __init__(self, principal: Principal):
        self.principal = principal
        self.system = build_system_prompt(principal)
        # Built on the first turn, so a missing key surfaces as a chat error rather
        # than breaking the page.
        self.backend = None

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.backend.messages if self.backend else []

    def run(self, user_text: str) -> Iterator[Event]:
        if self.backend is None:
            self.backend = make_backend(self.system)
        backend = self.backend
        ctx = ToolContext(self.principal)
        tools = specs_for(self.principal)
        backend.add_user(user_text)

        for step in range(MAX_AGENT_STEPS):
            yield {"type": "status", "step": step + 1, "message": "Thinking"}

            try:
                result = yield from backend.step(tools)
            except ModelError as exc:
                yield {"type": "error", "message": str(exc)}
                return

            if result.refused:
                yield {
                    "type": "error",
                    "message": (
                        "The model declined this request. Rephrase it, or escalate to a "
                        "human on the support team."
                    ),
                }
                return

            if not result.tool_calls:
                yield {"type": "trust", "trust": ctx.trust_panel()}
                yield {"type": "done", "stop_reason": result.stop_reason}
                return

            outcomes = []
            awaiting_confirmation = False

            for call in result.tool_calls:
                yield {
                    "type": "tool_start",
                    "tool": call.name,
                    "label": _label(call.name),
                    "family": _family(call.name),
                    "detail": describe_argument(call.name, call.args),
                    "tool_use_id": call.id,
                }
                if call.error:
                    output, is_error = {"error": "invalid_arguments", "message": call.error}, True
                else:
                    output, is_error = run_tool(ctx, call.name, call.args)
                yield {
                    "type": "tool_end",
                    "tool": call.name,
                    "tool_use_id": call.id,
                    "ok": not is_error,
                    "summary": _summarise_result(call.name, output, is_error),
                    "result": output,
                }
                if call.name == "propose_action" and not is_error:
                    awaiting_confirmation = True
                    yield {"type": "proposal", "action": output}
                # The UI gets the full result above; the model gets what it needs.
                outcomes.append((call, output if is_error else for_model(call.name, output), is_error))

            backend.add_tool_results(outcomes)

            if awaiting_confirmation:
                # Let the model write its "here's what I'll do, confirm?" turn, then
                # hand control to the human rather than continuing to act.
                continue

        yield {"type": "trust", "trust": ctx.trust_panel()}
        yield {
            "type": "error",
            "message": (
                f"Stopped after {MAX_AGENT_STEPS} tool steps without finishing. This is a "
                f"safety stop — the request is probably too broad; try narrowing it."
            ),
        }


def _label(tool_name: str) -> str:
    from .tools import BY_NAME

    tool = BY_NAME.get(tool_name)
    return tool.label if tool else tool_name


def _family(tool_name: str) -> str:
    from .tools import BY_NAME

    tool = BY_NAME.get(tool_name)
    return tool.family if tool else "tool"


def _summarise_result(tool_name: str, result: dict[str, Any], is_error: bool) -> str:
    if is_error:
        return result.get("message") or result.get("error") or "failed"
    if tool_name == "search_policy_documents":
        hits = result.get("results", [])
        bits = f"{len(hits)} section(s)"
        if result.get("superseded_matches"):
            bits += f", {len(result['superseded_matches'])} superseded excluded"
        if result.get("conflicts"):
            bits += f", {len(result['conflicts'])} conflict(s) flagged"
        return bits
    if tool_name == "lookup_records":
        return f"{result.get('count', 0)} row(s), {result.get('access_scope', '')}"
    if tool_name in {
        "evaluate_cancellation", "evaluate_service_credit",
        "resolve_support_sla", "check_plan_capability",
    }:
        return result.get("summary") or result.get("outcome", "")
    if tool_name == "detect_operations_signals":
        summary = result.get("summary", {})
        return f"{summary.get('total', 0)} signal(s) across {len(summary.get('accounts_affected', []))} account(s)"
    if tool_name == "propose_action":
        return f"{result.get('action_id')} prepared — awaiting confirmation"
    if tool_name == "execute_confirmed_action":
        return f"{result.get('action_id')} executed"
    return "ok"
