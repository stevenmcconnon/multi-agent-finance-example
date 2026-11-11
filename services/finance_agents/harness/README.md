# harness/

The L5 Harness: who the caller is, which tools exist, and the one guarded door every tool call passes through.

## Files

| File | What it owns |
|---|---|
| `identity.py` | JSON Web Token (JWT) to `Principal`. Local mode (HS256) or Cognito mode (JSON Web Key Set, JWKS). Raises `InvalidToken` on any failure. |
| `devtoken.py` | `mint()` dev tokens for the seeded users alice, bob, carol; backs `POST /auth/dev-token`. |
| `registry.py` | `ToolRegistry`; `specs_for(principal)` hides tools the caller's roles cannot use. |
| `runner.py` | `run_tool`: the one door. Cedar check, approval, idempotency, timeout, retry, circuit breaker, audit. |
| `budget.py` | `TurnBudget`: tokens, tool calls and a wall-clock deadline per turn. |
| `tools/search_documents.py` | Retrieval as a read tool. |
| `tools/facts_sql.py` | Structured numbers from the fact store (read). |
| `tools/calculator.py` | Safe arithmetic (read). |
| `tools/create_ticket.py` | A write tool; needs admin and a human approval. |
| `tools/__init__.py` | `default_registry()` wires the four tools. |

## How it is used

`api/app.py` calls `identity.verify_token`. `agent/team.py` (Researcher) and `mcp_server/server.py` call `runner.run_tool(...)` with tools from `default_registry()`.

## Rules that hold here

- The model never calls a provider or a table directly; every effect goes through `run_tool`. Tested in `tests/test_harness.py` (`test_bob_cannot_call_facts_sql`, `test_write_tool_needs_explicit_approval`).
- Idempotency reserves its key before running, so concurrent calls run the tool once (`test_concurrent_same_key_runs_the_tool_once`).
- Retries are for transient errors only; the breaker opens after five failures (`test_breaker_opens_after_five_failures`).
- Token groups are a hint; the database decides roles. `HR_MODE=aws` refuses local JWT mode (`tests/test_identity.py`).
- A hidden tool is UX only; Cedar inside `run_tool` is the enforcement (`test_specs_for_hides_tools_by_role`).

## Read next

- [`../authz/README.md`](../authz/README.md), the Cedar decision `run_tool` asks for.
- [`../../docs/TOUR.md`](../../../docs/TOUR.md) stop 6, the tool door.
