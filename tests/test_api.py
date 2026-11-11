"""The HTTP API offline: FastAPI TestClient over the real graph (FakeGateway, MemorySaver, fixed retriever);
identity resolvers and the DB door are stubbed, so no Postgres or Redis is needed."""

from __future__ import annotations

import uuid
from uuid import UUID

import fakeredis
import pytest
from fastapi.testclient import TestClient
from finance_agents import db
from finance_agents.agent import graph
from finance_agents.api import app as api
from finance_agents.authz import audit
from finance_agents.authz.group_cache import GroupCache
from finance_agents.contracts import Chunk, RetrievalResult
from finance_agents.gateway import FakeGateway
from finance_agents.harness import identity, runner
from finance_agents.harness.devtoken import DEV_USERS, mint
from finance_agents.harness.registry import ToolRegistry
from finance_agents.harness.tools import CalculatorTool, CreateTicketTool, FactsSqlTool, SearchDocumentsTool
from langgraph.checkpoint.memory import MemorySaver

ORG = UUID("00000000-0000-0000-0000-000000000001")
CHUNK = Chunk(
    id=uuid.uuid4(),
    document_id=uuid.uuid4(),
    org_id=ORG,
    ordinal=0,
    title="AAPL 10-K",
    uri="https://sec.gov/x",
    content="Revenue grew 8 percent in fiscal 2023.",
)


class _Retriever:
    async def retrieve(self, req):
        return RetrievalResult(chunks=[CHUNK], candidate_count=1, acl_filtered=True, latency_ms=1)


class _Facts:
    async def query(self, principal, entity, concept, period_end=None):
        return []


async def _org(slug: str) -> UUID:
    return ORG


async def _resolve(org_id: UUID, user_id: str) -> dict[str, list[str]]:
    return DEV_USERS[user_id]


async def _noop(*a, **kw):
    return None


@pytest.fixture
def client(monkeypatch):
    async def sink(kind, principal, payload, thread_id):
        pass

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    monkeypatch.setattr(runner, "IDEMPOTENCY", {})
    monkeypatch.setattr(db, "pool", _noop)
    monkeypatch.setattr(db, "close_pool", _noop)
    groups = GroupCache(fakeredis.aioredis.FakeRedis(), resolver=_resolve)

    async def verify(token: str):
        return await identity.verify_token(token, org_resolver=_org, groups=groups)

    monkeypatch.setattr(api, "verify_token", verify)
    registry = ToolRegistry(
        [SearchDocumentsTool(_Retriever()), FactsSqlTool(_Facts()), CalculatorTool(), CreateTicketTool()]
    )
    graph.configure(gateway=FakeGateway(), registry=registry, checkpointer=MemorySaver())
    with TestClient(api.app) as c:
        yield c
    graph.configure()


def _auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(user)}"}


def _events(body: str) -> list[str]:
    return [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event: ")]


def test_dev_token_then_me(client):
    token = client.post("/auth/dev-token", json={"user": "alice"}).json()["token"]
    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["user_id"] == "alice" and me.json()["roles"] == ["analyst"]
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_chat_streams_to_an_answer(client):
    r = client.post("/chat", json={"question": "What did AAPL say about revenue?"}, headers=_auth("alice"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0] == "status" and events[-1] == "answer"


def test_approve_on_a_foreign_thread_is_403(client):
    thread = f"t-{uuid.uuid4().hex}"
    assert _events(client.post("/chat", json={"question": "hi?", "thread_id": thread}, headers=_auth("alice")).text)
    r = client.post(f"/chat/{thread}/approve", json={"approved": True}, headers=_auth("bob"))
    assert r.status_code == 403
    assert client.get(f"/threads/{thread}/replay", headers=_auth("bob")).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"question": "x" * 2001},
        {"question": ""},
        {"question": "ok?", "thread_id": "a" * 65},
        {"question": "ok?", "thread_id": "has space"},
        {"question": "ok?", "thread_id": "../etc"},
    ],
)
def test_chat_input_limits(client, body):
    assert client.post("/chat", json=body, headers=_auth("alice")).status_code == 422


def test_healthz_is_503_when_the_db_is_down(client, monkeypatch):
    async def down(*a, **kw):
        return False

    monkeypatch.setattr(db, "ping", down)
    r = client.get("/healthz")
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["db"] is False


def test_aws_mode_on_local_identity_defaults_refuses_to_boot(monkeypatch):
    monkeypatch.setattr(identity.settings, "hr_mode", "aws")
    monkeypatch.setattr(identity.settings, "hr_jwt_mode", "local")
    with pytest.raises(RuntimeError), TestClient(api.app):
        pass
