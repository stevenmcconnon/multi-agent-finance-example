"""The HTTP API, exactly the routes in docs/CONTRACTS.md.

    uv run uvicorn finance_agents.api.app:app --port 8000

Bearer JWT -> harness.identity.verify_token -> Principal (401 on any failure). Turns stream as SSE
(`event` = TurnEvent.type, `data` = JSON). /documents reuses the retrieval ACL predicate verbatim.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .. import db
from ..agent import graph
from ..authz import audit
from ..config import settings
from ..contracts import Principal, TurnEvent
from ..guardrails import check_input, check_output
from ..guardrails.bedrock import BedrockGuardrail
from ..guardrails.ratelimit import LIMITER
from ..harness import devtoken
from ..harness.identity import InvalidToken, check_config, verify_token
from ..observability import setup_otel
from ..retrieval import sql

log = logging.getLogger(__name__)

# The documents list is the same ACL predicate the retriever uses; `c` is the alias it expects.
DOCUMENTS_SQL = f"""
SELECT d.id, d.title, d.uri, d.version, d.metadata
FROM (SELECT id AS document_id, org_id FROM documents) c
JOIN documents d ON d.id = c.document_id
WHERE {sql.SCOPE_PREDICATE}
ORDER BY d.ingested_at DESC
LIMIT %(k)s
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_config()  # an aws task on local-dev identity defaults dies here, before it serves a request
    try:
        await db.pool()
    except Exception as exc:  # the process stays up so /healthz can say what is wrong (it answers 503)
        log.error("startup degraded (db): %s", exc)
    await graph.startup()  # raises in aws mode without a checkpointer; local mode falls back to MemorySaver
    yield
    await graph.shutdown()
    await db.close_pool()


app = FastAPI(title="multi-agent-finance-example", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.hr_cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
setup_otel(app)

_bearer = HTTPBearer(auto_error=False)


async def current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    try:
        return await verify_token(creds.credentials)
    except InvalidToken as exc:
        raise HTTPException(401, f"invalid token: {exc}") from None


Caller = Annotated[Principal, Depends(current_principal)]


async def rate_limited(principal: Caller) -> None:
    wait = await LIMITER.retry_after(principal)
    if wait:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(wait)})


BEDROCK_GUARDRAIL = BedrockGuardrail()


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ApproveBody(BaseModel):
    approved: bool
    note: str | None = None


class DevTokenBody(BaseModel):
    user: str


async def _guard_output(events: AsyncIterator[TurnEvent], first: TurnEvent | None) -> AsyncIterator[TurnEvent]:
    """The output guardrail: redact the final answer, flag an uncited one; the `answer` event stays last."""
    if first is not None:
        yield first
    async for ev in events:
        if ev.type == "answer":
            out = check_output(ev.data.get("text") or "", len(ev.data.get("citations") or []))
            text = out.text
            if BEDROCK_GUARDRAIL.enabled:
                text = (await BEDROCK_GUARDRAIL.apply(text, "OUTPUT")).text or text
            ev.data["text"] = text
            yield TurnEvent(
                type="guardrail", data={"stage": "output", "redactions": out.redactions, "uncited": out.uncited}
            )
        yield ev


def _sse(events: AsyncIterator[TurnEvent], first: TurnEvent | None = None) -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict[str, Any]]:
        async for ev in _guard_output(events, first):
            yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}

    return EventSourceResponse(gen())


@app.get("/healthz")
async def healthz() -> JSONResponse:
    ok_db = await db.ping()
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        ok_redis = bool(await r.ping())
        await r.aclose()
    except Exception:
        ok_redis = False
    ok = ok_db and ok_redis
    return JSONResponse({"ok": ok, "db": ok_db, "redis": ok_redis}, status_code=200 if ok else 503)


@app.post("/auth/dev-token")
async def dev_token(body: DevTokenBody) -> dict[str, str]:
    if settings.hr_jwt_mode != "local":
        raise HTTPException(404, "not found")
    try:
        return {"token": devtoken.mint(body.user)}
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/me")
async def me(principal: Caller) -> Principal:
    return principal


@app.post("/chat", dependencies=[Depends(rate_limited)])
async def chat(body: ChatBody, principal: Caller) -> EventSourceResponse:
    thread_id = body.thread_id or str(uuid.uuid4())
    try:
        await graph._assert_owner(principal, thread_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    verdict = check_input(body.question)
    await audit.record("guardrail", principal, verdict.model_dump(), thread_id)
    if not verdict.ok:
        raise HTTPException(422, {"reasons": verdict.reasons})
    question = verdict.redacted_question
    if BEDROCK_GUARDRAIL.enabled:
        outcome = await BEDROCK_GUARDRAIL.apply(question, "INPUT")
        if outcome.blocked:
            raise HTTPException(422, {"reasons": ["bedrock_guardrail"], "message": outcome.text})
    first = TurnEvent(
        type="guardrail",
        data={"stage": "input", "injection_suspected": verdict.injection_suspected, "redactions": verdict.redactions},
    )
    return _sse(graph.stream_turn(principal, question, thread_id), first)


@app.post("/chat/{thread_id}/approve", dependencies=[Depends(rate_limited)])
async def chat_approve(thread_id: str, body: ApproveBody, principal: Caller) -> EventSourceResponse:
    try:
        await graph._assert_owner(principal, thread_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    return _sse(graph.approve(principal, thread_id, body.approved, body.note))


@app.get("/threads/{thread_id}/replay")
async def thread_replay(thread_id: str, principal: Caller) -> list[dict]:
    try:
        return await graph.replay(principal, thread_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None


@app.get("/documents")
async def documents(principal: Caller, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> list[dict]:
    return await db.fetch(DOCUMENTS_SQL, {**sql.acl_params(principal), "k": limit})
