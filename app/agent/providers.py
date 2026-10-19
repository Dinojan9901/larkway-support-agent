"""Model backends.

The loop in `loop.py` owns everything that matters for trust — principal
injection, the tool trace, the confirmation stop. A backend only has to do three
things: keep the conversation history in its provider's wire format, run one model
step, and accept a batch of tool results. So swapping the model is a config change,
not a change to any guarantee.

  AnthropicBackend      Claude via the Anthropic SDK; streams text and thinking.
  OpenAICompatBackend   any OpenAI-compatible chat endpoint — Gemini's free tier,
                        Groq, OpenRouter, a local Ollama. One request per step.

`step()` is a generator: it yields UI events (`text`, `thinking`) while the model
runs and *returns* a StepResult, so the loop reads it with `yield from`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Generator

from ..config import (
    ANTHROPIC_API_KEY,
    CHAT_DISABLED_REASON,
    CHAT_ENABLED,
    COMPAT_API_KEY,
    COMPAT_BASE_URL,
    COMPAT_FALLBACK_MODELS,
    EFFORT,
    MAX_TOKENS,
    MODEL,
    PROVIDER,
    REFUSAL_FALLBACK,
    REFUSAL_FALLBACK_BETA,
)

Event = dict[str, Any]


class AgentUnavailable(RuntimeError):
    """Chat is not configured. Everything that does not need a model still works."""


class ModelError(RuntimeError):
    """The model request failed. Surfaced to the user as an error event."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    # Set when the model's arguments could not be decoded; the loop reports it back
    # as a tool error instead of running the tool.
    error: str | None = None


@dataclass
class StepResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    refused: bool = False


def _content_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def make_backend(system: str):
    if not CHAT_ENABLED:
        raise AgentUnavailable(
            f"{CHAT_DISABLED_REASON}, so the chat agent is disabled. The operations "
            "dashboard, the policy engine and the eval harness all still work — see the "
            "README for how to configure a model provider, including free ones."
        )
    if PROVIDER == "openai_compat":
        return OpenAICompatBackend(system)
    return AnthropicBackend(system)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
def _anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


class AnthropicBackend:
    def __init__(self, system: str):
        self.system = system
        self.messages: list[dict[str, Any]] = []
        # Cleared once a request without the fallback beta succeeds where the beta
        # request was rejected, so we stop paying for the failed attempt.
        self.fallback_supported = REFUSAL_FALLBACK

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def _open(self, client, tools: list[dict], with_fallback: bool):
        kwargs = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": self.system,
            "messages": self.messages,
            "tools": tools,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": EFFORT},
        }
        if with_fallback:
            return client.beta.messages.stream(
                **kwargs, betas=[REFUSAL_FALLBACK_BETA], fallbacks="default"
            )
        return client.messages.stream(**kwargs)

    def step(self, tools: list[dict]) -> Generator[Event, None, StepResult]:
        import anthropic

        client = _anthropic_client()
        with_fallback = self.fallback_supported
        while True:
            emitted = False
            try:
                # The stream manager sends the request on __enter__, so a rejected
                # beta surfaces here — not when the manager is built.
                with self._open(client, tools, with_fallback) as stream:
                    for event in stream:
                        if event.type != "content_block_delta":
                            continue
                        if event.delta.type == "text_delta":
                            emitted = True
                            yield {"type": "text", "text": event.delta.text}
                        elif event.delta.type == "thinking_delta":
                            emitted = True
                            yield {"type": "thinking", "text": event.delta.thinking}
                    response = stream.get_final_message()
                if with_fallback != self.fallback_supported:
                    self.fallback_supported = False  # the plain request worked
                break
            except anthropic.BadRequestError as exc:
                if with_fallback and not emitted:
                    with_fallback = False  # retry once without the beta
                    continue
                raise ModelError(f"Model request failed (400): {exc.message}") from exc
            except anthropic.APIStatusError as exc:
                raise ModelError(
                    f"Model request failed ({exc.status_code}): {exc.message}"
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise ModelError(f"Could not reach the Claude API: {exc}") from exc

        if response.stop_reason == "refusal":
            return StepResult(stop_reason="refusal", refused=True)

        self.messages.append({"role": "assistant", "content": response.content})
        calls = [
            ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {})
            for b in response.content
            if b.type == "tool_use"
        ]
        return StepResult(tool_calls=calls, stop_reason=response.stop_reason)

    def add_tool_results(self, outcomes: list[tuple[ToolCall, dict, bool]]) -> None:
        # All results go back in one user message; splitting them teaches the
        # model to stop making parallel calls.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": _content_json(result),
                        **({"is_error": True} if is_error else {}),
                    }
                    for call, result, is_error in outcomes
                ],
            }
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible (Gemini, Groq, OpenRouter, Ollama)
# ---------------------------------------------------------------------------
def _openai_client():
    import openai

    # Ollama ignores the key but the SDK insists on one. Free tiers enforce tight
    # per-minute token caps and answer 429 with a short retry-after, so allow more
    # retries than the SDK's default of 2 before surfacing an error.
    return openai.OpenAI(
        base_url=COMPAT_BASE_URL, api_key=COMPAT_API_KEY or "not-needed", max_retries=6
    )


# Groq validates the model's tool calls against the schema server-side and rejects
# the whole response (400, code "tool_use_failed") when one doesn't fit — e.g.
# `"account_id": null` for an optional string. The generation is random, so asking
# again almost always produces a valid call.
MALFORMED_TOOL_CALL_RETRIES = 2


def _malformed_tool_call(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 400 and (
        getattr(exc, "code", None) == "tool_use_failed" or "tool_use_failed" in str(exc)
    )


def compat_tool(spec: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    """Translate an Anthropic tool spec into an OpenAI function definition.

    Gemini rejects an object parameter that declares no properties, and two of our
    tools take free-form objects (`filters`, `payload`). Those are sent as JSON
    strings instead and decoded again before the tool runs. Returns the definition
    and the names of the fields that were converted.
    """
    schema = spec["input_schema"]
    properties: dict[str, Any] = {}
    json_fields: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        prop = {k: v for k, v in prop.items() if k != "additionalProperties"}
        if prop.get("type") == "object" and not prop.get("properties"):
            description = prop.get("description", "").strip()
            prop = {
                "type": "string",
                "description": f"{description} Encode it as a JSON object string.".strip(),
            }
            json_fields.add(name)
        properties[name] = prop

    function: dict[str, Any] = {"name": spec["name"], "description": spec["description"]}
    if properties:
        parameters = {k: v for k, v in schema.items() if k not in ("properties", "additionalProperties")}
        parameters["properties"] = properties
        function["parameters"] = parameters
    return {"type": "function", "function": function}, json_fields


def decode_arguments(raw: str | None, json_fields: set[str]) -> tuple[dict[str, Any], str | None]:
    """Parse a function call's arguments. Returns (args, error)."""
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        return {}, f"Arguments were not valid JSON ({exc.msg}). Call the tool again."
    if not isinstance(args, dict):
        return {}, "Arguments must be a JSON object. Call the tool again."
    for name in json_fields:
        value = args.get(name)
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip() else {}
            except json.JSONDecodeError:
                return {}, f"`{name}` must be a JSON object string. Call the tool again."
            if not isinstance(value, dict):
                return {}, f"`{name}` must decode to a JSON object. Call the tool again."
            args[name] = value
    return args, None


class OpenAICompatBackend:
    def __init__(self, system: str):
        self.system = system
        self.messages: list[dict[str, Any]] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self, tools: list[dict]) -> Generator[Event, None, StepResult]:
        import openai

        converted = [compat_tool(spec) for spec in tools]
        json_fields = {d["function"]["name"]: fields for d, fields in converted}
        client = _openai_client()
        # Free tiers cap tokens per model, so when one model's quota is spent (429) or
        # a request is over its per-minute size (413), the next model usually still
        # has room. History is plain OpenAI format, so any model can pick it up.
        models = [MODEL, *COMPAT_FALLBACK_MODELS]
        response = None
        for attempt, model in enumerate(models):
            for retry in range(MALFORMED_TOOL_CALL_RETRIES + 1):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        max_tokens=MAX_TOKENS,
                        messages=[{"role": "system", "content": self.system}, *self.messages],
                        tools=[d for d, _ in converted],
                    )
                    break
                except openai.APIStatusError as exc:
                    if _malformed_tool_call(exc) and retry < MALFORMED_TOOL_CALL_RETRIES:
                        continue
                    if exc.status_code in (413, 429) and attempt + 1 < len(models):
                        break
                    raise ModelError(
                        f"Model request failed ({exc.status_code}): {exc.message}"
                    ) from exc
                except openai.APIConnectionError as exc:
                    raise ModelError(
                        f"Could not reach the model endpoint {COMPAT_BASE_URL}: {exc}"
                    ) from exc
            if response is not None:
                break

        if not response.choices:
            raise ModelError("The model returned an empty response. Try rephrasing.")
        choice = response.choices[0]
        message = choice.message
        if choice.finish_reason == "content_filter":
            return StepResult(stop_reason="refusal", refused=True)

        if message.content:
            yield {"type": "text", "text": message.content}

        entry: dict[str, Any] = {"role": "assistant", "content": message.content}
        calls: list[ToolCall] = []
        if message.tool_calls:
            # Dump the calls whole: Gemini attaches a thought signature in an extra
            # field and rejects the next request if it is not echoed back.
            entry["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in message.tool_calls]
            for tc in message.tool_calls:
                args, error = decode_arguments(
                    tc.function.arguments, json_fields.get(tc.function.name, set())
                )
                calls.append(ToolCall(tc.id, tc.function.name, args, error))
        self.messages.append(entry)
        return StepResult(tool_calls=calls, stop_reason=choice.finish_reason or "")

    def add_tool_results(self, outcomes: list[tuple[ToolCall, dict, bool]]) -> None:
        for call, result, _is_error in outcomes:
            self.messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": _content_json(result)}
            )
