# Cross-layer contracts (pinned before parallel build)

Types: `services/finance_agents/contracts.py`. Ports: `interfaces.py`. Settings: `config.py` (env names in `.env.example`).

## Agent entry points (`finance_agents.agent.graph`)
- `async def run_turn(principal: Principal, question: str, thread_id: str) -> Answer`
- `async def stream_turn(principal: Principal, question: str, thread_id: str) -> AsyncIterator[TurnEvent]`
- `async def approve(principal: Principal, thread_id: str, approved: bool, note: str | None) -> AsyncIterator[TurnEvent]` resumes a human in the loop (HITL) interrupt
- `async def replay(principal: Principal, thread_id: str) -> list[dict]` checkpoints oldest→newest: `{checkpoint_id, step, node, state_summary, at}`

## Hypertext Transfer Protocol (HTTP) and application programming interface (API) routes (FastAPI, `finance_agents.api.app`) — all routes except /healthz, /metrics, /auth/dev-token need `Authorization: Bearer <jwt>`
- `GET /healthz` → `{ok, db, redis}`; HTTP 503 when `ok` is false (Postgres or Redis down)
- `GET /metrics` Prometheus
- `POST /auth/dev-token {user: "alice"|"bob"|"carol"}` → `{token}` ONLY when HR_JWT_MODE=local; 404 otherwise
- `GET /me` → Principal
- `POST /chat {question, thread_id?}` → server-sent events (SSE) stream of `TurnEvent` (`event: <type>`, `data: <json>`); last event `answer` or `hitl` or `error`
- `POST /chat/{thread_id}/approve {approved: bool, note?}` → SSE stream (resumes)
- `GET /threads/{thread_id}/replay` → replay list
- `GET /documents?limit=` → documents the caller may read (id, title, uri, version, metadata)

## TurnEvent.data by type
- status: `{node, message}` · policy: `PolicyDecision` · retrieval: `{candidate_count, chunks:[{document_id, title, section, score}]}`
- tool: `{tool, ok, latency_ms, from_cache, error?}` (`error` may be `forbidden`, `approval_required`, `denied`, `circuit_open`, `in_progress`) · token: `{text}` · hitl: `{tool, arguments, reason}`
- answer: `Answer` · error: `{code, message}`
- guardrail: `{stage: "input", injection_suspected, redactions:[kind]}` (first event of a `/chat` stream) · `{stage: "output", redactions:[kind], uncited}` (just before `answer`)

## JSON Web Token (JWT) → Principal (`harness.identity`)
The token says WHO; the database says WHAT (groups and roles via `authz.group_cache.resolve_from_db`; token groups are a hint only). `exp` is REQUIRED in both modes; a missing or past `exp` is a 401.
- Local (`HR_JWT_MODE=local`, HMAC with SHA-256 signature (HS256) with `HR_JWT_SECRET`, `iss` `multi-agent-finance-example-local`): `sub` = user external_id, `org` = org slug, `groups`/`roles` (hint), `iat`, `exp`. Minted by `/auth/dev-token` and `harness.devtoken`.
- Cognito (`HR_JWT_MODE=cognito`, RSA with SHA-256 signature (RS256) against the pool's JSON Web Key Set (JWKS)): must be an identity (ID) token (`token_use` = `id`) with `iss` = `https://cognito-idp.<region>.amazonaws.com/<pool id>` and `aud` = the app client id. User external_id = `cognito:username` (Terraform creates users named `alice`/`bob`/`carol`, matching the seed rows; the pool does NOT use email as username). Org slug = the single `cognito:groups` entry named `org:<slug>` (`org:acme` for the demo users); zero or two or more `org:` groups is a 401. `sub` (a UUID) is not used as an identity. Both claims are in a Cognito ID token by default; no custom attribute or pre-token trigger is needed.
- Boot guard (`identity.check_config()`): with `HR_MODE=aws`, `HR_JWT_MODE=local` or the default `HR_JWT_SECRET` raises at startup instead of leaving `/auth/dev-token` as an admin door.

## Dev identities (db/migrations/0002_seed_dev.sql)
org `acme` id `00000000-0000-0000-0000-000000000001`; alice (finance, analysts; analyst), bob (engineering; viewer), carol (admin).
Ingestion access control list (ACL) mapping: Form 10-K (annual report) and Form 10-Q (quarterly report) → groups `finance`,`analysts`; Form 8-K (current report) → `engineering`,`finance`,`analysts`. So bob can read 8-Ks only.

## Ports
API `8000`, web `3000`, Postgres `5432`, Valkey `6379`, Temporal `7233` (user interface (UI) `8233`), Phoenix `6006`, OpenTelemetry (OTel) collector `4318`.

## Ingestion durability decision
Temporal owns ingestion durability (retries, backoff, dead-letter via `ingest_jobs.status='dead'`). There is NO Simple Queue Service (SQS) in the ingestion path and no queue settings. Architecture decision record (ADR) 0003 records when SQS alone would win.

## Auth flow decision
Cognito: authorization code + Proof Key for Code Exchange (PKCE) in the browser (no client secret). Never the implicit flow.

## The agent team (pinned 2026-11-11, Steve: "it's supposed to be a multi-agentic workflow with deterministic and non-deterministic parts")
One LangGraph graph is the **Supervisor**: deterministic routing between specialized agents, checkpointed, replayable.
| Agent | Kind | Owns | Model role |
|---|---|---|---|
| Guard | deterministic | identity → Principal, Cedar `answer` decision, fail closed | none |
| Planner | model (non-deterministic) | understands the question, chooses tools from `registry.specs_for(principal)` via Bedrock Converse tool use | `understand`, `plan` |
| Researcher | deterministic | runs the planned calls through `harness.runner.run_tool`, assembles evidence (facts first, then chunks), relevance floor | none |
| Approver | human | approves or denies write/money tools through the interrupt | none |
| Writer | model (non-deterministic) | grounded draft from the evidence only, cites by index | `generate` |
| Judge | model (non-deterministic, second cheaper model) | verifies every claim against the evidence; one retry, then abstain | `verify` |
`status` TurnEvents carry `{node, agent, kind: "deterministic"|"model"|"human", message}`. Node names stay (`understand`, `policy`, `plan`, `act`, `generate`, `verify`, `answer`); `agent` is the team member acting in that node.

## Guardrails (pinned 2026-11-11)
`guardrails/` is a belt to the policy layer's braces, never a replacement: `check_input` runs before `graph.stream_turn` (hard failures are 422 `{reasons}`; personally identifiable information (PII) is redacted to `[redacted-<kind>]` in the question the graph sees, a prompt-injection match is tagged, not blocked), `check_output` runs on the final `answer` (text redacted, `uncited` flagged), and `authz.audit._jsonable` redacts every string so no PII lands in `audit_events`. `POST /chat` and `POST /chat/{id}/approve` return 429 with `Retry-After` past `HR_RATE_LIMIT_PER_MINUTE` per principal (Redis key `hr:rl:<org>:<user>:<minute>`; Redis down = allow). Audit kind `guardrail` records one row per turn with the input verdict. When `BEDROCK_GUARDRAIL_ID` is set, every Converse call carries `guardrailConfig` and `ApplyGuardrail` runs on the question and the answer. Table of every guardrail: `docs/GUARDRAILS.md`.

## Demo-hardening decisions (pinned 2026-11-11)

**`GET /config`** (no auth) → `{"model_backend": "fake"|"bedrock", "models": {"generate": str, "verify": str, "embed": str, "rerank": str}, "auth_mode": "local"|"cognito"}`. The web app shows it so nobody mistakes the offline stub for a language model.

**`Answer.reason`** (new field, string, always set): `answered` · `no_relevant_evidence` (the reader's permitted documents do not answer it, or the reader has none) · `out_of_scope` · `policy_denied` · `not_grounded` · `action_done` · `action_denied` (the human said no) · `action_forbidden` (the caller's role may not use the tool). `Answer.text` carries a sentence a person can read for each; the web headline is chosen from `reason`.

**Numeric questions.** Structured facts are the evidence. When `facts_sql` returns rows for a numeric question the document search is skipped; with no rows the search runs as the fallback. Years named in the question filter the facts by fiscal year. For exactly two periods of one concept the Researcher runs the `calculator` tool and adds the change (absolute and percent) as its own evidence line.

**Thread ownership in the UI.** Switching persona always starts a new thread (a thread belongs to the principal who opened it; the API answers 403 otherwise).

**Retrieval event wording.** Every candidate already passed the permission filter in SQL. `candidate_count` = authorized candidates; `chunks` = what the reranker kept above the relevance floor.

**Demo corpus (pinned).** `make ingest` loads a fixed manifest, not "the newest filings", so the example questions keep working: AAPL 10-K `0000320193-26-000026`, MSFT 10-K `0001193125-26-323660`, AAPL 8-K `0001140361-26-042798` (notes issuance), MSFT 8-K `0001193125-26-447173` (notes issuance), MSFT 8-K `0001193125-26-448357` (a director not standing for re-election). `cli run --tickers ... --forms ... --limit N` still ingests the newest N filings **per form** per ticker.

**Example questions (web chips, DEMO.md, tests all use these exact strings).**
1. `What risks does Apple describe about its supply chain?`: 10-K lookup; alice answers, bob abstains.
2. `What was Apple's revenue in fiscal 2026?`: facts table; one figure, cited to the XBRL fact.
3. `How did Apple's net income change from fiscal 2024 to fiscal 2026?`: two facts plus the calculator.
4. `Which Microsoft director is not standing for re-election?`: 8-K lookup; alice and bob both answer.
5. `Create a ticket to review Apple's 10-K risk section`: write tool; carol gets the approval card, alice and bob are told their role may not.

**Personas.** alice: analyst, reads 10-K and 8-K. bob: viewer, reads 8-K only. carol: operations admin, may run write tools after approval, belongs to no reader group and so reads no filings (separation of duties; say so wherever carol is introduced).

**Bedrock models (cloud mode).** Generation and planning `us.anthropic.claude-sonnet-5-5`, verification and classification `us.anthropic.claude-haiku-5-5` (inference profile ids; the bare model ids cannot be invoked on demand). No `temperature`/`top_p`. Thinking is always on, so output caps leave room for it and text is read from `text` blocks only.
