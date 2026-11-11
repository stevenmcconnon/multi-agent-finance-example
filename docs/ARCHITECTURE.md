# Architecture

harness-rag is seven layers with one direction of dependency: a layer imports the typed contracts in
`services/harness_rag/contracts.py`, the ports in `interfaces.py`, and the layers below it. Nothing imports
upward. The README's stack diagram is the picture; this page is the text.

## Layers

### L1 Cloud / Platform (`infra/`)

Terraform modules under `infra/terraform/modules`: `network` (VPC, public subnets, security groups, VPC
endpoints for Bedrock, Secrets Manager, ECR, CloudWatch Logs, S3), `database` (RDS Postgres 16 with
pgvector, master password in Secrets Manager), `cache` (ElastiCache Valkey), `identity` (Cognito user pool,
groups, demo users, PKCE client), `policy` (Verified Permissions store, schema and the policies from
`db/policies`), `compute` (ECS cluster, ALB, task definitions and services for `api`, `ingest-worker`,
`temporal`, `phoenix`, autoscaling on CPU, an ADOT sidecar), `web` (S3 bucket behind CloudFront for the
SPA), `observability` (ADOT log group, optional Managed Grafana), `schedule` (EventBridge Scheduler
running the ingest CLI as an ECS task). `envs/dev` wires them. `infra/otel` holds the collector config.

    In production: private subnets, NAT or egress-only endpoints, remote Terraform state with locking.
    In this demo: public subnets without NAT and local state, because a clone must `init` with no
    pre-existing resource and the NAT gateway alone would be a fifth of the monthly cost.

### L2 Data / Pipelines (`ingest/`)

`edgar.py` (fair-access client, 10 requests/second, `User-Agent` from `EDGAR_USER_AGENT`) ->
`parse.py` (HTML to sections to ~700-word chunks with 80-word sentence-aligned overlap) ->
`facts.py` (XBRL `companyfacts` filtered to the filing's accession) -> `writer.py` (documents,
`document_acl`, chunks with embeddings, facts, in one transaction; unchanged content hash writes nothing).
`acl.py` maps a form to reader groups. `jobs.py` tracks each `(org, source, source_id, version)` in
`ingest_jobs` with a sha256 idempotency key; the fifth failure sets `status='dead'`. `workflow.py` makes it
durable on Temporal: `DiscoverWorkflow` starts one `IngestFilingWorkflow` per filing whose workflow id *is*
the idempotency key, so a second start of the same version is refused by Temporal itself. There is no SQS
and no queue module ([ADR 0003](adr/0003-temporal-owns-ingestion-durability.md)). EDGAR primary documents are
HTML, so there is no PDF parser.

    In production: PDF via PyMuPDF or Textract, for sources that ship PDFs.
    In this demo: HTML only, because EDGAR primary documents are HTML.

### L3 Models (`gateway/`, plus `retrieval/embed.py` and `retrieval/rerank.py`)

The only modules that speak to a model vendor (`tests/test_architecture.py::test_boto3_only_behind_the_doors`
enforces the list). `ModelGateway.complete(role, ...)` picks a model per `ModelRole`: generate (Claude
Sonnet 4.5), verify and understand (Claude Haiku 4.5), through `bedrock-runtime converse`. `plan` is a fourth
call: Converse with `toolConfig` (native tool use) on the generate model, over the tool specs the harness
offers; if that call fails, the gateway falls back to the deterministic heuristic. `FakeGateway`
answers deterministically from the numbered evidence lines in the prompt and plans with `plan_heuristic`; it is what tests and the $0
quickstart run. Embedders produce 512-dimension vectors on both backends ([ADR 0005](adr/0005-512-dim-embeddings.md));
rerankers are Cohere Rerank 3.5 or a local BGE cross-encoder, with `none` as a degraded mode.

### L4 Knowledge / Retrieval (`retrieval/` and `authz/`)

`retrieval/sql.py` owns the one ACL predicate and the three candidate queries that embed it (dense cosine
over the HNSW index, sparse `websearch_to_tsquery` over the generated `tsvector`, facts by entity and
concept). `hybrid.py` runs dense and sparse with the same parameters, fuses with reciprocal rank fusion
(k = 60), reranks to `rerank_top_n`, drops chunks the reranker scores below its own floor (`Reranker.min_score`: 0.0 for the local BGE logits, 0.15 for Cohere Rerank's 0..1 relevance; `HR_RERANK_MIN_SCORE` overrides it)
(authorized but irrelevant evidence counts as absent; [ADR 0009](adr/0009-rerank-relevance-floor.md)), and
writes one `retrieval` audit row. The dense query is pinned to the stored `embedding_model` and runs with
`hnsw.iterative_scan = relaxed_order`, so a reader permitted few chunks still gets `k` rows past the filter. `facts.py` reads structured
numbers so the numeric path never asks a model for a figure; `calculator.py` is an AST-walked evaluator.

`authz/` is the policy half: `cedar_local.py` and `verified_permissions.py` implement `Authorizer.decide`
over the same `entities.py` mapping, fail closed on any error, and audit every decision. `group_cache.py`
caches principal resolution in Redis with a TTL and resolves from Postgres on a miss or a Redis failure.
`audit.py` is the single writer of `audit_events`.

### L5 AI Harness (`harness/`)

`identity.py` turns a JWT into a `Principal`: signature, `exp` (required) and, for Cognito, issuer, audience
and `token_use` are verified; the user is `cognito:username` and the org the one `org:<slug>` group (locally
`sub` and `org`). Groups and roles are then *resolved from the database*; the token's claims are a hint that
never wins. `check_config()` refuses to boot `HR_MODE=aws` on local JWT settings. `registry.py` lists
tools with their `ToolSpec` (JSON schema, risk level `read | write | money`, required roles, timeout,
idempotency). `runner.run_tool` is the one door for a tool call from the agent or MCP: Cedar
`tool:<name>` on the caller's Org; then the risk gate (a `write` or `money` tool returns `approval_required`
unless the caller passes `approved=True`, which only the graph does, after a human approved); then an
idempotency reservation (`INSERT ... ON CONFLICT DO NOTHING RETURNING`; the key is
`org:tool:<thread>:<tool>:<sha256(args)[:16]>`, the graph supplying the last three parts); then a timeout and
Tenacity retries on transient errors inside a per-tool pybreaker circuit breaker; then an audit row. MCP therefore
cannot run a write tool. `budget.py` charges tokens,
tool calls and wall clock per turn and raises `BudgetExceeded`.

### L6 Orchestration (`agent/graph.py`)

One LangGraph `StateGraph` per turn: `understand -> policy -> plan -> act -> generate -> verify -> answer |
abstain`. `plan` asks the model for tool calls through Bedrock Converse native tool use and shows it only
`registry.specs_for(principal)`; `act` runs each chosen call through `harness.runner.run_tool` (Cedar,
idempotency, retries, breaker, audit) and, before a write or money call runs, parks the thread with `interrupt()` inside `act` itself (there is no
separate `hitl` node) until `approve()` resumes it with a `Command`; the node re-runs with the decision. The model's evidence is built only from what those calls
returned; with none, the model is not called and the turn abstains. A draft the second model finds
ungrounded is regenerated once with the unsupported claims named, then abstained. The checkpointer is
`AsyncPostgresSaver` (tables created on startup) or an injected `MemorySaver`; `replay` lists a thread's
checkpoints; `_assert_owner` keeps a thread with the principal who started it.

**Two orchestrators, two grains.** LangGraph orchestrates a turn: seconds long, a checkpoint per node,
replayable, resumable after a human decision. Temporal orchestrates ingestion: minutes to hours per batch,
activities retried with backoff, a dead letter per filing. Neither is used for the other's job; a turn never
waits on Temporal and an ingest never runs inside a graph node.

**Deterministic by default.** Identity resolution, the Cedar decision, the SQL ACL filter, reciprocal rank
fusion, the facts table and the calculator, turn budgets, breakers and idempotency are all deterministic.
Only the four model calls (understand, plan, generate, verify) are not, and each one sits between a
deterministic gate before it (policy, `specs_for`, the ACL-filtered evidence) and after it (the Cedar check
in `run_tool`, the verifier's JSON parsed into `Verification`, the citation map). A non-deterministic step
can therefore change *which authorized evidence* ranks first and *how the answer reads*, never *who sees
what* or *what runs*.

### L7 Experience (`apps/web`, `api/`, `mcp_server/`)

`api/app.py` is FastAPI: bearer auth on every route but `/healthz`, `/metrics` and the local-only
`/auth/dev-token`; `/healthz` answers 503 when Postgres or Redis is down, and at startup `check_config()` and
(in `aws` mode) the Postgres checkpointer fail fast, while in local mode the process stays up degraded; `/chat` and `/chat/{thread_id}/approve` stream `TurnEvent`s as SSE; `/threads/{id}/replay`
and `/documents` (which reuses the ACL predicate). `apps/web` is a Vite + React + TypeScript SPA with React Router,
Tailwind and Zod validation of every payload: sign-in, chat with the event timeline and the approval card,
replay, and the documents page that proves item-level permissions by listing what the signed-in identity may
read. In local mode a persona switcher in the header and "Re-ask as <persona>" chips run the same question
as another seeded user in a second column; that is the demo's mechanism for showing permissions.

    In production: one person, one Cognito sign-in; no persona switch.
    In this demo: switch among alice, bob and carol via `/auth/dev-token`, because the point is the same
    question under different permissions.
 It is a pure client over the SSE API, so nothing needs server rendering (it is not Next.js); the build is
static `dist/` files on S3 behind CloudFront (nginx in Docker locally). `mcp_server/server.py` exposes the registry's tools to an MCP
client for the principal in `HR_TOKEN`, every call through `run_tool`.

## Ports and adapters

`interfaces.py` defines the ports as `Protocol`s; each layer ships adapters selected by settings, never by
edit.

| Port | Adapters | Selected by |
|---|---|---|
| `Authorizer` | `CedarLocalAuthorizer`, `VerifiedPermissionsAuthorizer` | `HR_AUTHZ_BACKEND` |
| `Retriever` | `PgHybridRetriever` (tests inject a fetch; evals inject a fake) | `get_retriever()` |
| `FactStore` | `PgFactStore` | `get_fact_store()` |
| `Embedder` | `FastEmbedEmbedder`, `BedrockEmbedder` | `HR_EMBED_BACKEND` |
| `Reranker` | `FastEmbedReranker`, `BedrockReranker`, `NoopReranker` | `HR_RERANK_BACKEND` |
| `ModelGateway` | `FakeGateway`, `BedrockGateway` | `HR_MODEL_BACKEND` |
| `Tool` | `SearchDocumentsTool`, `FactsSqlTool`, `CalculatorTool`, `CreateTicketTool` | `ToolRegistry` |

## Stores, by purpose

| Purpose | Store | What lives there | Why this store |
|---|---|---|---|
| Authority | S3 (`S3_RAW_BUCKET`) or `data/edgar/`; EDGAR itself | The original filing bytes; `documents.uri` points back | Re-parse without re-download; the source system stays the truth |
| Search | Postgres: `chunks` (HNSW on `embedding vector(512)`, GIN on `content_tsv`), `facts`, `documents`, `document_acl` | What retrieval ranks, with the ACL rows it filters on | One predicate in one engine ([ADR 0001](adr/0001-pgvector-over-opensearch.md)) |
| Control | Postgres: `ingest_jobs`, `idempotency_keys`, `audit_events`, `eval_records`, LangGraph `checkpoints*`; `principals`, `group_members`, `orgs` | Job state, tool idempotency (rows are never expired), the audit trail, eval scores, thread checkpoints, the identity mirror | Transactional with the data it describes |
| Acceleration | Redis / Valkey | `hr:principal:<org>:<user>` group and role resolution, TTL 300 s | A miss or an outage falls back to Postgres; a hit never grants more than the database would |

## The harness turn: before, during, after

**Before** a tool runs: the `Principal` was built from a verified token; the graph's `policy` node asked
Cedar for `answer` on the Org; `plan` offered the model only `specs_for(principal)`; `run_tool` asks Cedar for
`tool:<name>`; a `write` or `money` tool additionally needs the human's approval (the `interrupt()` inside
`act`) and is refused as `approval_required` without it; then the idempotency key is reserved before the
tool runs, so two concurrent calls with one key run it once.

**During**: the call is bounded by `ToolSpec.timeout_seconds`, retried up to three times on `TransientError`,
`TimeoutError` or `ConnectionError` with exponential backoff, and refused with `circuit_open` after five
failures per tool for thirty seconds; the turn budget counts the call.

**After**: the outcome is stored on the reserved key (a failed run of an idempotent tool releases the key; a
failed non-idempotent run keeps its result so it is never re-run by accident), a `tool.call` audit row records
tool, arguments, outcome and latency, and the retrieval or answer that follows gets its own row. The
checkpointer wrote the node's state; `replay` can show it.
