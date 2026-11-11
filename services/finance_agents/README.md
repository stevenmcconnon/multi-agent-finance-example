# finance_agents/

The whole backend package: it answers questions over permissioned documents, and every layer from L2 Data to L6 Orchestration lives in a sub-folder here, with the two L7 entry points (`api/`, `mcp_server/`) that call into them.

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
| [`authz/`](authz/README.md) | L5 Harness | Cedar decisions, groups, audit |
| [`harness/`](harness/README.md) | L5 Harness | identity, tools, guarded runner |
| [`guardrails/`](guardrails/README.md) | L5 Harness | input/output checks, redaction, rate limiting, Bedrock Guardrails |
| [`agent/`](agent/README.md) | L6 Orchestration | the LangGraph turn |
| [`api/`](api/README.md) | L7 entry | FastAPI routes; calls into L5 and L6 |
| [`mcp_server/`](mcp_server/README.md) | L7 entry | tools over the Model Context Protocol (MCP); calls into L5 |
| [`observability/`](observability/README.md) | L1 Platform | OpenTelemetry (OTel) traces and metrics |

## How it is used

`api/app.py` verifies the token, applies the input guardrail, and then calls
`agent.graph.stream_turn(principal, question, thread_id)`. It applies the output guardrail before the final
answer leaves the API. Adapters are chosen by config through `get_gateway()`, `get_retriever()` and
`get_authorizer()`.

## Rules that hold here

- A layer imports `contracts.py`, `interfaces.py` and layers below it, never upward.
- Swap an adapter by config, never by editing callers.
- Vendor SDK and Postgres boundaries are checked by [`../../tests/test_architecture.py`](../../tests/test_architecture.py).

## Read next

- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md), [`../../docs/CONTRACTS.md`](../../docs/CONTRACTS.md), and [`../../docs/GUARDRAILS.md`](../../docs/GUARDRAILS.md).
