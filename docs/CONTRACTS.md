# Cross-layer contracts (pinned before parallel build)

Types: `services/harness_rag/contracts.py`. Ports: `interfaces.py`. Settings: `config.py` (env names in `.env.example`).

## Agent entry points (`harness_rag.agent.graph`)
- `async def run_turn(principal: Principal, question: str, thread_id: str) -> Answer`
- `async def stream_turn(principal: Principal, question: str, thread_id: str) -> AsyncIterator[TurnEvent]`
- `async def approve(principal: Principal, thread_id: str, approved: bool, note: str | None) -> AsyncIterator[TurnEvent]` resumes a HITL interrupt
- `async def replay(principal: Principal, thread_id: str) -> list[dict]` checkpoints oldest→newest: `{checkpoint_id, step, node, state_summary, at}`

## HTTP API (FastAPI, `harness_rag.api.app`) — all routes except /healthz, /metrics, /auth/dev-token need `Authorization: Bearer <jwt>`
- `GET /healthz` → `{ok, db, redis}`; HTTP 503 when `ok` is false (Postgres or Redis down)
- `GET /metrics` Prometheus
- `POST /auth/dev-token {user: "alice"|"bob"|"carol"}` → `{token}` ONLY when HR_JWT_MODE=local; 404 otherwise
- `GET /me` → Principal
- `POST /chat {question, thread_id?}` → SSE stream of `TurnEvent` (`event: <type>`, `data: <json>`); last event `answer` or `hitl` or `error`
- `POST /chat/{thread_id}/approve {approved: bool, note?}` → SSE stream (resumes)
- `GET /threads/{thread_id}/replay` → replay list
- `GET /documents?limit=` → documents the caller may read (id, title, uri, version, metadata)

## TurnEvent.data by type
- status: `{node, message}` · policy: `PolicyDecision` · retrieval: `{candidate_count, chunks:[{document_id, title, section, score}]}`
- tool: `{tool, ok, latency_ms, from_cache, error?}` (`error` may be `forbidden`, `approval_required`, `denied`, `circuit_open`, `in_progress`) · token: `{text}` · hitl: `{tool, arguments, reason}`
- answer: `Answer` · error: `{code, message}`

## JWT → Principal (`harness.identity`)
The token says WHO; the database says WHAT (groups and roles via `authz.group_cache.resolve_from_db`; token groups are a hint only). `exp` is REQUIRED in both modes; a missing or past `exp` is a 401.
- Local (`HR_JWT_MODE=local`, HS256 with `HR_JWT_SECRET`, `iss` `harness-rag-local`): `sub` = user external_id, `org` = org slug, `groups`/`roles` (hint), `iat`, `exp`. Minted by `/auth/dev-token` and `harness.devtoken`.
- Cognito (`HR_JWT_MODE=cognito`, RS256 against the pool's JWKS): must be an ID token (`token_use` = `id`) with `iss` = `https://cognito-idp.<region>.amazonaws.com/<pool id>` and `aud` = the app client id. User external_id = `cognito:username` (Terraform creates users named `alice`/`bob`/`carol`, matching the seed rows; the pool does NOT use email as username). Org slug = the single `cognito:groups` entry named `org:<slug>` (`org:acme` for the demo users); zero or two or more `org:` groups is a 401. `sub` (a UUID) is not used as an identity. Both claims are in a Cognito ID token by default; no custom attribute or pre-token trigger is needed.
- Boot guard (`identity.check_config()`): with `HR_MODE=aws`, `HR_JWT_MODE=local` or the default `HR_JWT_SECRET` raises at startup instead of leaving `/auth/dev-token` as an admin door.

## Dev identities (db/migrations/0002_seed_dev.sql)
org `acme` id `00000000-0000-0000-0000-000000000001`; alice (finance, analysts; analyst), bob (engineering; viewer), carol (admin).
Ingestion ACL mapping: form 10-K/10-Q → groups `finance`,`analysts`; form 8-K → `engineering`,`finance`,`analysts`. So bob can read 8-Ks only.

## Ports
API `8000`, web `3000`, Postgres `5432`, Valkey `6379`, Temporal `7233` (UI `8233`), Phoenix `6006`, OTel collector `4318`.

## Ingestion durability decision
Temporal owns ingestion durability (retries, backoff, dead-letter via `ingest_jobs.status='dead'`). There is NO SQS in the ingestion path and no queue settings. ADR 0003 records when SQS alone would win.

## Auth flow decision
Cognito: authorization code + PKCE in the browser (no client secret). Never the implicit flow.
