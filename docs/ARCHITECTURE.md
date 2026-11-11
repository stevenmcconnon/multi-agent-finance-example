# Architecture

multi-agent-finance-example is seven layers with one direction of dependency: a layer imports the typed contracts in
`services/finance_agents/contracts.py`, the ports in `interfaces.py`, and the layers below it. Nothing imports
upward. The README's stack diagram is the picture; this page is the text.

## Layers

### L1 Cloud / Platform (`infra/`)

Terraform modules under `infra/terraform/modules`: `network` (Virtual Private Cloud (VPC), public subnets, security groups, VPC
endpoints for Bedrock, Secrets Manager, Amazon Elastic Container Registry (ECR), CloudWatch Logs, Amazon Simple Storage Service (S3)), `database` (Amazon Relational Database Service (RDS) Postgres 16 with
pgvector, master password in Secrets Manager), `cache` (ElastiCache Valkey), `identity` (Cognito user pool,
groups, demo users, Proof Key for Code Exchange (PKCE) client), `policy` (Verified Permissions store, schema and the policies from
`db/policies`), `guardrails` (Bedrock content and prompt-attack filters, personal-data anonymization, and a
denied investment-advice topic), `compute` (Amazon Elastic Container Service (ECS) cluster, Application Load Balancer (ALB), task definitions and services for `api`, `ingest-worker`,
`temporal`, `phoenix`, autoscaling on processor load, an Amazon Web Services (AWS) Distro for OpenTelemetry (ADOT) sidecar), `web` (S3 bucket behind CloudFront for the
single-page application (SPA)), `observability` (ADOT log group, optional Managed Grafana), `schedule` (EventBridge Scheduler
running the ingest command-line interface (CLI) as an ECS task, daily, for the newest filing per form per ticker). `envs/dev` wires them. `infra/otel` holds the collector config.
The Terraform passes `fmt` and `validate` in continuous integration (CI) and has not been applied; the Bedrock, Bedrock Guardrails, Cognito and Temporal-worker
paths are unit-tested with stubbed clients and have not been run live. Locally, `docker-compose.yml` pins every image version and publishes the
[ports in CONTRACTS.md](CONTRACTS.md#ports); `WEB_PORT` and `API_PORT` move the web app and the API on the host.

    In production: private subnets, network address translation (NAT) or egress-only endpoints, remote Terraform state with locking.
    In this demo: public subnets without NAT and local state, because a clone must `init` with no
    pre-existing resource and the NAT gateway alone would be a fifth of the monthly cost.

### L2 Data / Pipelines (`ingest/`)

`edgar.py` (fair-access client, 10 requests/second, `User-Agent` from `EDGAR_USER_AGENT`) ->
`parse.py` (HyperText Markup Language (HTML) to sections named from the U.S. Securities and Exchange Commission (SEC) item headings, "Cover page" then for example "Item 1A. Risk Factors", to ~700-word chunks with 80-word sentence-aligned overlap) ->
`facts.py` (eXtensible Business Reporting Language (XBRL) `companyfacts` filtered to the filing's accession) -> `writer.py` (documents,
`document_acl`, chunks with embeddings, facts, in one transaction; unchanged content hash writes nothing).
`acl.py` maps a form to reader groups. `jobs.py` tracks each `(org, source, source_id, version)` in
`ingest_jobs` with a sha256 idempotency key; the fifth failure sets `status='dead'`. `workflow.py` makes it
durable on Temporal: `DiscoverWorkflow` starts one `IngestFilingWorkflow` per filing whose workflow id *is*
the idempotency key, so a second start of the same version is refused by Temporal itself. There is no Simple Queue Service (SQS)
and no queue module (architecture decision record [ADR 0003](adr/0003-temporal-owns-ingestion-durability.md)). SEC Electronic Data Gathering, Analysis, and Retrieval system (EDGAR) primary documents are
HTML, so there is no Portable Document Format (PDF) parser. Discovery logs and skips an unknown ticker so
one typo does not fail and retry the rest of a batch.

    In production: PDF via PyMuPDF or Textract, for sources that ship PDFs.
    In this demo: HTML only, because EDGAR primary documents are HTML.

The CLI (`ingest/cli.py`) has four commands: `demo` (the five pinned filings in `ingest/demo_corpus.py`; `make ingest`, and
`make ingest-rebuild` to parse and embed them again), `run` (the newest filings per form per ticker; `make ingest-latest`),
`worker` and `migrate`. `make ingest` ends with `make warm`, one real question that loads the embedding and reranking models.

    In production: every ingest runs as Temporal workflows on a worker fleet, and the corpus is whatever the source holds.
    In this demo: the quickstart runs the same activities inline in the CLI (`docker compose --profile temporal up -d` adds the
    worker and `--temporal` uses it) over a pinned corpus, because the example questions must keep working and one laptop needs no fleet.

### L3 Models (`gateway/`, plus `retrieval/embed.py` and `retrieval/rerank.py`)

The only modules that speak to a model vendor (`tests/test_architecture.py::test_boto3_only_behind_the_doors`
enforces the list). `ModelGateway.complete(role, ...)` picks a model per `ModelRole`: generate (Claude
Sonnet 5.5, `us.anthropic.claude-sonnet-5-5`), verify and understand (Claude Haiku 5.5, `us.anthropic.claude-haiku-5-5`; both are inference profile ids), through `bedrock-runtime converse`. `plan` is a fourth
call: Converse with `toolConfig` (native tool use) on the generate model, over the tool specs the harness
offers; if that call fails, the gateway falls back to the deterministic heuristic and says so in the node's status event. `FakeGateway`, the offline stub
(it reports itself as `offline-stub`), answers deterministically from the numbered evidence lines in the prompt and plans with `plan_heuristic`; it is what tests and the $0
quickstart run. `make up-bedrock` runs the local stack with the Claude models instead (embeddings and reranking stay local); it calls AWS and costs money per question. Embedders produce 512-dimension vectors on both backends ([ADR 0005](adr/0005-512-dim-embeddings.md));
rerankers are Cohere Rerank 3.5 or a local MiniLM cross-encoder (`Xenova/ms-marco-MiniLM-L-12-v2`), with `none` as a degraded mode.
When `BEDROCK_GUARDRAIL_ID` is configured, every Converse call carries `guardrailConfig`; the API also calls
the same managed guardrail directly on the question and final answer. It is disabled in the offline local
stack. The deterministic checks that still run locally are owned by L5, below.

### L4 Knowledge / Retrieval (`retrieval/`)

`retrieval/sql.py` owns the one access control list (ACL) predicate and the three candidate queries that embed it (dense cosine
over the Hierarchical Navigable Small World index (HNSW), sparse `websearch_to_tsquery` over the generated `tsvector`, facts by entity and
concept). `hybrid.py` runs dense and sparse with the same parameters, fuses with reciprocal rank fusion
(k = 60), reranks to `rerank_top_n`, drops chunks the reranker scores below its own floor (`Reranker.min_score`: 0.0 for the local MiniLM cross-encoder logits, 0.15 for Cohere Rerank's 0..1 relevance; `HR_RERANK_MIN_SCORE` overrides it)
(authorized but irrelevant evidence counts as absent; [ADR 0009](adr/0009-rerank-relevance-floor.md)), and
writes one `retrieval` audit row. The dense query is pinned to the stored `embedding_model` and runs with
`hnsw.iterative_scan = relaxed_order`, so a reader permitted few chunks still gets `k` rows past the filter. `facts.py` reads structured
numbers so the numeric path never asks a model for a figure; `calculator.py` is an
abstract-syntax-tree-walked evaluator whose float arithmetic, length limit, and exponent cap make chained
powers fail immediately instead of pinning the process on a large integer.

### L5 Artificial Intelligence (AI) Harness (`harness/`, `authz/` and `guardrails/`)

`identity.py` turns a JSON Web Token (JWT) into a `Principal`: signature, `exp` (required) and, for Cognito, issuer, audience
and `token_use` are verified; the user is `cognito:username` and the org the one `org:<slug>` group (locally
`sub` and `org`). Groups and roles are then *resolved from the database*; the token's claims are a hint that
never wins. `check_config()` refuses to boot `HR_MODE=aws` on local JWT settings. `registry.py` lists
tools with their `ToolSpec` (JavaScript Object Notation (JSON) schema, risk level `read | write | money` (`money` is reserved; no tool uses it), required roles, timeout,
idempotency). `runner.run_tool` is the one door for a tool call from the agent or Model Context Protocol (MCP) server: Cedar
`tool:<name>` on the caller's Org; then the risk gate (a `write` tool returns `approval_required`
unless the caller passes `approved=True`, which only the graph does, after a human approved); then an
idempotency reservation (`INSERT ... ON CONFLICT DO NOTHING RETURNING`; the key is
`org:tool:<thread>:<tool>:<sha256(args)[:16]>`, the graph supplying the last three parts); then a timeout and
Tenacity retries on transient errors inside a per-tool pybreaker circuit breaker; then an audit row. MCP therefore
cannot run a write tool. `budget.py` charges tokens,
tool calls and wall clock per turn and raises `BudgetExceeded`.

`authz/` is the policy part of the harness (Cedar namespace `FinanceAgents`): `cedar_local.py` and `verified_permissions.py` implement `Authorizer.decide`
over the same `entities.py` mapping, fail closed on any error, and audit every decision. `group_cache.py`
caches principal resolution in Redis with a time to live (TTL) and resolves from Postgres on a miss or a Redis failure.
`audit.py` is the single writer of `audit_events`; every string passes through the personal-data redactor
before it is stored.

`guardrails/` owns the turn boundary around that harness. `input.py` rejects over-length or
control-character input, tags prompt-injection patterns without treating them as authorization, and
redacts e-mail addresses, United States phone numbers, Social Security numbers, and payment-card numbers
before the graph or audit sees them. `output.py` redacts the final answer and flags uncited prose.
`ratelimit.py` applies a shared per-principal fixed-window limit through Redis to `/chat` and
`/chat/{id}/approve`; Redis failure allows the authenticated request and logs a warning. `bedrock.py` is
the managed-guardrail adapter used in cloud mode. The complete control/test map and deliberate local
limitations are in [GUARDRAILS.md](GUARDRAILS.md).

### L6 Orchestration (`agent/graph.py`, `agent/team.py`)

One LangGraph `StateGraph` per turn is the **Supervisor**: deterministic routing between the agents of the
team pinned in [CONTRACTS.md](CONTRACTS.md) "The agent team". Each node is a thin call into the agent class in
`team.py` that owns the work, and the `status` event carries `{node, agent, kind, message}`:

| Node | Agent | Kind |
|---|---|---|
| `understand` | Planner | model |
| `policy` | Guard | deterministic |
| `plan` | Planner | model |
| `act` | Researcher, and the Approver (human) when `interrupt()` parks a write call | deterministic / human |
| `generate` | Writer | model |
| `verify` | Judge (the second, cheaper model) | model |
| `answer`, `abstain` | Supervisor | deterministic |

    In production: a model-driven supervisor with handoffs and a re-plan loop after evidence arrives
    (a second `plan()` call after the Researcher reports, with the evidence so far in the prompt); a research sub-loop under the same
    `run_tool` door; approvals routed to Slack or Teams; multi-day human waits in Temporal.
    In this demo: fixed edges and one pass, because one question per turn is provable and replayable.

The turn is `understand -> policy -> plan -> act -> generate -> verify -> answer |
abstain`. `plan` asks the model for tool calls through Bedrock Converse native tool use and shows it only
`registry.specs_for(principal)`; `act` runs each chosen call through `harness.runner.run_tool` (Cedar,
idempotency, retries, breaker, audit). The Researcher preserves the required document search and caps
trailing planned read calls to the remaining tool budget. For a numeric question the facts are the evidence:
`facts_sql` runs first, the document search is skipped when facts come back (it is the fallback when none do),
and for exactly two periods of one concept the Researcher runs the `calculator` tool and adds the change as its
own evidence line. A request for a write tool the caller was not offered is still sent to `run_tool`, so the
refusal is Cedar's audited decision, not a hidden menu. Before a write call on the caller's menu runs,
`Approver.ask` parks the thread with `interrupt()` inside `act` itself (there is no
separate `hitl` node) until `approve()` resumes it with a `Command`; the node re-runs with the decision. The model's evidence is built only from what those calls
returned; with none, the model is not called and the turn abstains. A draft the second model finds
ungrounded is regenerated once with the unsupported claims named, then abstained; unreadable verifier output
is a failed verification, never an allow. Every `Answer` carries a `reason` and a sentence a person can read for it. The checkpointer is
`AsyncPostgresSaver` (tables created on startup) or an injected `MemorySaver`; `replay` lists a thread's
checkpoints; `_assert_owner` keeps a thread with the principal who started it.

**Two orchestrators, two grains.** LangGraph orchestrates a turn: seconds long, a checkpoint per node,
replayable, resumable after a human decision. Temporal orchestrates ingestion: minutes to hours per batch,
activities retried with backoff, a dead letter per filing. Neither is used for the other's job; a turn never
waits on Temporal and an ingest never runs inside a graph node.

**Deterministic by default.** Identity resolution, the Cedar decision, the Structured Query Language (SQL) ACL filter, reciprocal rank
fusion, the facts table and the calculator, turn budgets, breakers and idempotency are all deterministic.
Only the four model calls (understand, plan, generate, verify) are not, and each one sits between a
deterministic gate before it (policy, `specs_for`, the ACL-filtered evidence) and after it (the Cedar check
in `run_tool`, the verifier's JSON parsed into `Verification`, the citation map). A non-deterministic step
can therefore change *which authorized evidence* ranks first and *how the answer reads*, never *who sees
what* or *what runs*.

### L7 Experience (`apps/web`, `api/`, `mcp_server/`)

`api/` and `mcp_server/` are the two entry points; both call into L5 (identity, guardrails, `run_tool`) and neither holds a rule of its own.
`api/app.py` is FastAPI: bearer auth on every route but `/healthz`, `/metrics`, `/config` (which models and auth mode are running, so the web app can say so) and the local-only
`/auth/dev-token`; `/healthz` answers 503 when Postgres or Redis is down, and at startup `check_config()` and
(in `aws` mode) the Postgres checkpointer fail fast, while in local mode the process stays up degraded.
`/chat` and `/chat/{thread_id}/approve` are rate-limited per principal and stream `TurnEvent`s as
server-sent events (SSE). A chat stream begins with the input `guardrail` event and emits the output
`guardrail` event immediately before `answer`; `/threads/{id}/replay`
and `/documents` (which reuses the ACL predicate). `apps/web` is a Vite + React + TypeScript SPA with React Router,
Tailwind and Zod validation of every payload: sign-in, chat with the event timeline and the approval card,
replay, and the documents page that proves item-level permissions by listing what the signed-in identity may
read. In local mode a persona switcher in the header (which starts a new thread, because a thread belongs to the
principal who opened it) and "Re-ask as <persona>" chips run the same question
as another seeded user in a second column; that is the demo's mechanism for showing permissions.

    In production: one person, one Cognito sign-in; no persona switch.
    In this demo: switch among alice, bob and carol via `/auth/dev-token`, because the point is the same
    question under different permissions.
 It is a pure client over the application programming interface (API) that streams SSE, so nothing needs server rendering (it is not Next.js); the build is
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
| Search | Postgres: `chunks` (HNSW on `embedding vector(512)`, Generalized Inverted Index (GIN) on `content_tsv`), `facts`, `documents`, `document_acl` | What retrieval ranks, with the ACL rows it filters on | One predicate in one engine ([ADR 0001](adr/0001-pgvector-over-opensearch.md)) |
| Control | Postgres: `ingest_jobs`, `idempotency_keys`, `audit_events`, `eval_records`, LangGraph `checkpoints*`; `principals`, `group_members`, `orgs` | Job state, tool idempotency (rows are never expired), the audit trail, eval scores, thread checkpoints, the identity mirror | Transactional with the data it describes |
| Acceleration | Redis / Valkey | `hr:principal:<org>:<user>` group and role resolution, TTL 300 s; `hr:rl:<org>:<user>:<minute>` request counters | Group-cache failure falls back to Postgres; rate-limit failure allows the authenticated request and warns |

## The harness turn: before, during, after

**Before** the graph runs: the API applies the per-principal rate limit, checks and redacts the question,
records the guardrail verdict without personal data, and emits the input `guardrail` event. Then the
`Principal` built from a verified token reaches the graph's `policy` node, which asks Cedar for `answer` on
the Org; `plan` offered the model only `specs_for(principal)`; `run_tool` asks Cedar for
`tool:<name>`; a `write` tool additionally needs the human's approval (the `interrupt()` inside
`act`) and is refused as `approval_required` without it; then the idempotency key is reserved before the
tool runs, so two concurrent calls with one key run it once.

**During**: the call is bounded by `ToolSpec.timeout_seconds`, retried up to three times on `TransientError`,
`TimeoutError` or `ConnectionError` with exponential backoff, and refused with `circuit_open` after five
failures per tool for thirty seconds; the turn budget counts the call.

**After**: the outcome is stored on the reserved key (a failed run of an idempotent tool releases the key; a
failed non-idempotent run keeps its result so it is never re-run by accident), a `tool.call` audit row records
tool, arguments, outcome and latency, a TOOL span joins the turn's trace, and the retrieval or answer that follows gets its own row. The
checkpointer wrote the node's state; `replay` can show it. At the API boundary the output guardrail redacts
the final text, flags uncited prose, emits its event, and leaves `answer` as the final stream event.
