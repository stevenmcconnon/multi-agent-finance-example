# api/

The HTTP edge between L7 Experience (the web app) and L6 Orchestration (the agent graph).

## Files

| File | What it owns |
|---|---|
| `app.py` | The FastAPI app: lifespan boot, CORS (Cross-Origin Resource Sharing), bearer-token dependency, and every route. |
| `__init__.py` | Package marker. |

Routes: `GET /healthz`, `POST /auth/dev-token`, `GET /me`, `POST /chat` (server-sent events, SSE), `POST /chat/{thread_id}/approve`, `GET /threads/{thread_id}/replay`, `GET /documents`.

## How it is used

`uvicorn harness_rag.api.app:app --port 8000`. The web client in `apps/web` calls these routes. Each turn streams events whose SSE `event` is the `TurnEvent.type`.

## Rules that hold here

- Every route except `/healthz` and `/auth/dev-token` needs a bearer token; any identity failure is a 401.
- `/documents` reuses the retrieval predicate (`sql.SCOPE_PREDICATE`, which includes `ACL_PREDICATE`): `test_documents_route_reuses_the_predicate` in `tests/test_architecture.py`.
- `/auth/dev-token` answers 404 unless `HR_JWT_MODE=local`.
- Approving or replaying a thread you do not own is refused: `test_approve_on_a_foreign_thread_is_403` in `tests/test_api.py`.
- With `HR_MODE=aws` and local identity defaults the app refuses to boot (`test_aws_mode_on_local_identity_defaults_refuses_to_boot`). `/healthz` answers 503 when the database is down.
- Input limits are tested (`test_chat_input_limits`).

## Read next

- [`../agent/README.md`](../agent/README.md).
- [`../../docs/CONTRACTS.md`](../../../docs/CONTRACTS.md), the pinned routes.
