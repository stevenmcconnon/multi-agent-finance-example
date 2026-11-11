# finance_agents/

The whole backend package: it answers questions over permissioned documents, and every layer from L2 Data to L6 Orchestration lives in a sub-folder here.

## Files

| File | What it owns |
|---|---|
| `contracts.py` | Typed shapes every layer shares (`Principal`, `ToolSpec`, `Answer`, `TurnEvent`, ...). Nobody redefines them. |
| `interfaces.py` | The ports (Python `Protocol`s): `Authorizer`, `Retriever`, `FactStore`, `Embedder`, `Reranker`, `ModelGateway`, `Tool`. |
| `config.py` | `Settings` read from environment variables (`HR_MODE`, `HR_MODEL_BACKEND`, `HR_AUTHZ_BACKEND`, ...). |
| `db.py` | The one Postgres door: `pool()`, `fetch()`, `execute()`, `run_migrations()`, `ping()`. |

| Folder | Layer | Job |
|---|---|---|
| [`ingest/`](ingest/README.md) | L2 Data | EDGAR filings into Postgres |
| [`gateway/`](gateway/README.md) | L3 Models | the only door to model vendors |
| [`retrieval/`](retrieval/README.md) | L4 Knowledge | permission-filtered hybrid search |
| [`authz/`](authz/README.md) | L5 Harness (authorization half) | Cedar decisions, groups, audit |
| [`harness/`](harness/README.md) | L5 Harness | identity, tools, guarded runner |
| [`agent/`](agent/README.md) | L6 Orchestration | the LangGraph turn |
| [`api/`](api/README.md) | L7 edge | FastAPI routes |
| [`mcp_server/`](mcp_server/README.md) | L5 entry | tools over the Model Context Protocol (MCP) |
| [`observability/`](observability/README.md) | L1/L6 | OpenTelemetry (OTel) traces and metrics |

## How it is used

`api/app.py` verifies the token, then calls `agent.graph.stream_turn(principal, question, thread_id)`. Adapters are chosen by config through `get_gateway()`, `get_retriever()` and `get_authorizer()`.

## Rules that hold here

- A layer imports `contracts.py`, `interfaces.py` and layers below it, never upward.
- Swap an adapter by config, never by editing callers.
- Vendor SDK and Postgres boundaries are checked by [`../../tests/test_architecture.py`](../../tests/test_architecture.py).

## Read next

- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) and [`../../docs/CONTRACTS.md`](../../docs/CONTRACTS.md).
