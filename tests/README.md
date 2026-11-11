# tests/

Fast pytest tests for the backend. Most run offline: no network, no AWS, and no database. They also hold the architecture ratchet that keeps layers apart.

## Files

| File | What it proves |
|---|---|
| `conftest.py` | Offline fixtures: audit to a list, idempotency to a dict, fixed chunks. Disables OTel exporters. |
| `test_architecture.py` | Pure source checks: `boto3` only in its doors, agent never imports `psycopg`, every chunk/fact query carries `ACL_PREDICATE`, `/documents` reuses it, deterministic agents never touch the gateway. |
| `test_authz.py` | Cedar decisions (group ACL, user ACL, cross-org, tool roles), fail-closed cases, Verified Permissions mapping, group cache. |
| `test_identity.py` | JWT to `Principal` in local and Cognito mode, and the `HR_MODE=aws` boot guard. |
| `test_harness.py` | `run_tool` doors: Cedar, approval, idempotency, retry, breaker, budget, `specs_for`. |
| `test_gateway.py` | Tool planning for the fake heuristic and Bedrock Converse tool use. |
| `test_retrieval.py` | SQL invariants, RRF order, calculator, relevance floor, facts; two cases need Postgres. |
| `test_ingest.py` | Chunker, HTML parse, facts filter, ACL by form, dead letter after five failures. |
| `test_agent.py` | The whole LangGraph turn with `FakeGateway`, in-memory checkpointer and fixed chunks. |
| `test_api.py` | FastAPI `TestClient` over the real graph; identity and database stubbed. |
| `test_mcp.py` | Spawns the MCP server as a subprocess; skipped without a reachable database. |

## How to run

```sh
uv run pytest tests -q                       # no database needed; DB-backed cases skip themselves
make up && uv run pytest tests -q            # with Postgres on DATABASE_URL, the skipped cases run
uv run pytest tests evals/suites -q          # also the permission-leak and citation suites
```

DB-backed tests skip unless `DATABASE_URL` answers (`test_retrieval.py`, `test_mcp.py`). `make test` runs `uv run pytest`.

## Rules that hold here

- A convention becomes a test: `test_architecture.py` is how layer rules stay true.
- Tests never call AWS; inject a fake client (see `test_authz.py`, `test_gateway.py`).
- New retrieval query: add it to `sql.py` with the predicate, or the architecture test fails.

## Read next

- [`../evals/README.md`](../evals/README.md).
- [`../docs/SECURITY.md`](../docs/SECURITY.md), where each control maps to its test.
