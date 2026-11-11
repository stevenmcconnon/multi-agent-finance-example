# api/

An L7 entry point: the HTTP API the web app calls. It calls into L5 (identity, guardrails) and L6 (the agent graph) and holds no rule of its own.

## Files

| File | What it owns |
|---|---|
| `app.py` | The FastAPI app: lifespan boot, CORS (Cross-Origin Resource Sharing), bearer-token dependency, and every route. |
| `__init__.py` | Package marker. |

Routes: `GET /healthz`, `GET /metrics`, `GET /config`, `POST /auth/dev-token`, `GET /me`, `POST /chat` (server-sent events, SSE), `POST /chat/{thread_id}/approve`, `GET /threads/{thread_id}/replay`, `GET /documents`.

## How it is used

`uvicorn finance_agents.api.app:app --port 8000`. The web client in `apps/web` calls these routes. Each turn
streams events whose SSE `event` is the `TurnEvent.type`; `guardrail` is first for input and immediately
precedes `answer` for output.

## Rules that hold here

- Every route except `/healthz`, `/metrics`, `/config` and `/auth/dev-token` needs a bearer token; any identity failure is a 401.
- `/config` names the model backend and models with no secrets, so the web app can show "Models: offline stub" (`test_config_names_the_offline_stub_without_auth`).
- `/documents` reuses the retrieval predicate (`sql.SCOPE_PREDICATE`, which includes `ACL_PREDICATE`): `test_documents_route_reuses_the_predicate` in `tests/test_architecture.py`.
- `/auth/dev-token` answers 404 unless `HR_JWT_MODE=local`, and 400 for an unknown dev user.
- Asking on, approving or replaying a thread you do not own is a 403: `test_approve_on_a_foreign_thread_is_403` in `tests/test_api.py`.
- With `HR_MODE=aws` and local identity defaults the app refuses to boot (`test_aws_mode_on_local_identity_defaults_refuses_to_boot`). `/healthz` answers 503 when the database is down.
- Input checks reject malformed text, tag suspected injection, and redact personal data before the graph;
  output checks redact again and flag uncited prose. Both appear as `guardrail` events.
- `/chat` and `/chat/{thread_id}/approve` share a per-principal Redis rate limit and return 429 with
  `Retry-After`; Redis failure allows the authenticated request and logs a warning.
- Input limits and guardrail behavior are tested in `tests/test_api.py` and `tests/test_guardrails.py`.

## Read next

- [`../agent/README.md`](../agent/README.md).
- [`../../docs/CONTRACTS.md`](../../../docs/CONTRACTS.md), the pinned routes.
- [`../../../docs/GUARDRAILS.md`](../../../docs/GUARDRAILS.md), the control inventory.
