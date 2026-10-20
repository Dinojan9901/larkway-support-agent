"""MCP server: Larkway's agent tools over the Model Context Protocol (stdio).

    python -m app.mcp_server                           # acts as cust-halcyon (default)
    LARKWAY_MCP_USER=staff-meera python -m app.mcp_server

A thin adapter, deliberately. Tools are listed from `tools_for(principal)` and every
call goes through `run_tool(ToolContext(principal), ...)` — the same dispatcher the
chat agent uses — so the guarantees carry over unchanged:

  * **Identity is fixed when the server starts**, from LARKWAY_MCP_USER, never from a
    tool argument. The default is a customer (least privilege: one account, internal
    columns stripped, no operations sweep). Staff access is an explicit opt-in.
  * **Scope is enforced in the data layer** exactly as in the web app; a cross-account
    request comes back as an access_denied tool error.
  * **Numbers come from the policy engine**, through the same tool handlers.
  * **Nothing executes.** `propose_action` records a pending proposal and changes
    nothing; `execute_confirmed_action` is not exposed at all, because confirming
    needs a person's click in the Larkway web app.

Write nothing to stdout: on the stdio transport it is the protocol channel.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .access import DEFAULT_PRINCIPAL_ID, DEMO_PRINCIPALS, AccessDenied, Principal, get_principal
from .agent.tools import Tool, ToolContext, for_model, run_tool, tools_for
from .store import dataset_summary

USER_ENV = "LARKWAY_MCP_USER"

# Executing needs a human's confirmation click in the web app, so it never crosses MCP.
WITHHELD = {"execute_confirmed_action"}
# propose_action writes a pending proposal; everything else only reads and computes.
WRITES = {"propose_action"}


def resolve_principal(user_id: str | None = None) -> Principal:
    """The identity this server acts as: explicit argument, then env var, then default."""
    return get_principal(user_id or os.getenv(USER_ENV) or DEFAULT_PRINCIPAL_ID)


def exposed_tools(principal: Principal) -> list[Tool]:
    return [t for t in tools_for(principal) if t.name not in WITHHELD]


def _mcp_tool(tool: Tool) -> types.Tool:
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.schema,
        annotations=types.ToolAnnotations(
            title=tool.label or None,
            read_only_hint=tool.name not in WRITES,
            destructive_hint=False,
        ),
    )


def call(principal: Principal, name: str, arguments: dict[str, Any] | None) -> tuple[dict, bool]:
    """Run one tool as `principal`. Returns (result, is_error), like `run_tool`."""
    if name in WITHHELD:
        return {
            "error": "not_available_over_mcp",
            "message": (
                "Actions cannot be executed over MCP. propose_action prepares one; a "
                "person confirms it in the Larkway web app."
            ),
        }, True
    result, is_error = run_tool(ToolContext(principal), name, arguments or {})
    # The same compact view the chat agent sends its model (only the ops sweep differs).
    return (result if is_error else for_model(name, result)), is_error


def _instructions(principal: Principal) -> str:
    scope = (
        "all accounts (staff)"
        if principal.is_internal
        else f"account {principal.account_id} only (customer)"
    )
    return (
        f"Larkway support tools, acting as {principal.display_name}: {scope}. "
        f"Data is a fictional snapshot; 'now' is {dataset_summary()['snapshot_at']}. "
        "Fees, credits and SLA deadlines come from the policy engine: quote the tools' "
        "numbers, don't recompute them. propose_action only prepares a change; nothing "
        "is executed over MCP."
    )


def build_server(principal: Principal | None = None) -> Server:
    principal = principal or resolve_principal()
    tools = [_mcp_tool(t) for t in exposed_tools(principal)]

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        # Tool handlers are synchronous and touch the filesystem; keep the loop free.
        result, is_error = await anyio.to_thread.run_sync(
            call, principal, params.name, params.arguments
        )
        text = json.dumps(result, ensure_ascii=False, default=str)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=is_error
        )

    return Server(
        "larkway",
        version="1.0.0",
        instructions=_instructions(principal),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def main() -> None:
    try:
        principal = resolve_principal()
    except AccessDenied as exc:
        sys.exit(f"{exc} Set {USER_ENV} to one of: {', '.join(DEMO_PRINCIPALS)}.")
    server = build_server(principal)

    async def serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(serve)


if __name__ == "__main__":
    main()
