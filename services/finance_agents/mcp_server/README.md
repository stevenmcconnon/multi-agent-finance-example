# mcp_server/

An L5 entry point: the harness tools offered over the Model Context Protocol (MCP) on standard input and output, so an outside client can use them.

## Files

| File | What it owns |
|---|---|
| `server.py` | Builds one MCP tool per `ToolSpec` the caller's roles may use; every call goes to `run_tool`. |
| `__init__.py` | Package marker. |

## How it is used

```sh
HR_TOKEN=$(uv run python -m finance_agents.harness.devtoken alice) uv run python -m finance_agents.mcp_server.server
```

The token is verified by `harness/identity.py`, so a client never names its own roles.

## Rules that hold here

- Every call goes through `harness.runner.run_tool`; the server has no second path to a tool.
- A tool the principal's roles cannot use is not listed, and Cedar still decides inside `run_tool`.
- Write and money tools (`create_ticket`) are listed for an admin but answer `approval_required`: only the agent's human-approval path passes `approved=True`. Tested by `test_carol_write_tool_over_mcp_needs_approval` and `test_bob_is_restricted` in `tests/test_mcp.py` (skipped without a database).

## Read next

- [`../harness/README.md`](../harness/README.md).
