"""MCP server over stdio: the registry's tools for the principal in HR_TOKEN, every call through run_tool.

    HR_TOKEN=$(uv run python -m finance_agents.harness.devtoken alice) uv run python -m finance_agents.mcp_server.server

The token is verified by harness.identity (DB-backed groups), so a client never names its own roles; a
tool the principal's roles cannot use is not even listed, and Cedar still decides inside run_tool.
mcp 2.x renamed FastMCP to MCPServer; the tool list is built from each ToolSpec's JSON schema.

Write/money tools (create_ticket) are listed for an admin but answer `ok=False, error="approval_required"`:
run_tool runs them only with `approved=True`, which only the agent's hitl path passes after a human approves
in the UI. There is no approval flow over MCP, by design.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from ..contracts import Principal, ToolCall, ToolSpec
from ..harness import runner
from ..harness.identity import verify_token
from ..harness.registry import ToolRegistry
from ..harness.tools import default_registry

_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}


def _signature(schema: dict[str, Any]) -> inspect.Signature:
    """JSON schema (pydantic model_json_schema) -> a keyword-only signature MCPServer can introspect."""
    required = set(schema.get("required", []))
    params = []
    for name, prop in schema.get("properties", {}).items():
        # shortcut: anyOf/$ref become Any; good enough for the four built-in tools, revisit with nested inputs.
        annotation = _TYPES.get(prop.get("type"), Any)
        default = inspect.Parameter.empty if name in required else prop.get("default")
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation))
    return inspect.Signature(params)


def _bind(server: MCPServer, principal: Principal, spec: ToolSpec, registry: ToolRegistry) -> None:
    async def call(**arguments: Any) -> dict[str, Any]:
        result = await runner.run_tool(principal, ToolCall(tool=spec.name, arguments=arguments), None, registry)
        return result.model_dump(mode="json")

    call.__signature__ = _signature(spec.input_schema)  # type: ignore[attr-defined]
    call.__name__ = spec.name
    server.add_tool(call, name=spec.name, description=f"{spec.description} (risk: {spec.risk.value})")


def build_server(principal: Principal, registry: ToolRegistry | None = None) -> MCPServer:
    registry = registry or default_registry()
    server = MCPServer(
        "multi-agent-finance-example",
        instructions=f"Tools for {principal.user_id}@{principal.org_slug}; every call is policy-checked and audited.",
    )
    for spec in registry.specs_for(principal):
        _bind(server, principal, spec, registry)
    return server


def main() -> None:
    token = os.environ.get("HR_TOKEN")
    if not token:
        raise SystemExit("HR_TOKEN is required (uv run python -m finance_agents.harness.devtoken alice)")
    principal = asyncio.run(verify_token(token))
    build_server(principal).run("stdio")


if __name__ == "__main__":
    main()
