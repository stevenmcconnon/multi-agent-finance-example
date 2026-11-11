"""Shared offline fixtures for the harness/agent tests: audit to a list, idempotency to a dict, fixed chunks."""

from __future__ import annotations

import os
import uuid
from uuid import UUID

import pytest

os.environ.setdefault("OTEL_SDK_DISABLED", "true")  # no span exporters during tests
from finance_agents.authz import audit
from finance_agents.contracts import Chunk, Fact, Principal, RetrievalRequest, RetrievalResult
from finance_agents.harness import runner

ORG = UUID("00000000-0000-0000-0000-000000000001")
ALICE = Principal(user_id="alice", org_id=ORG, org_slug="acme", groups=["finance", "analysts"], roles=["analyst"])
BOB = Principal(user_id="bob", org_id=ORG, org_slug="acme", groups=["engineering"], roles=["viewer"])
CAROL = Principal(user_id="carol", org_id=ORG, org_slug="acme", groups=[], roles=["admin"])
DOC = uuid.uuid4()
CHUNKS = [
    Chunk(
        id=uuid.uuid4(),
        document_id=DOC,
        org_id=ORG,
        ordinal=0,
        section="Item 7",
        title="AAPL 10-K",
        uri="https://sec.gov/x",
        version="1",
        score=0.9,
        content="Revenue grew 8 percent in fiscal 2023. Services reached a new record.",
    ),
    Chunk(
        id=uuid.uuid4(),
        document_id=DOC,
        org_id=ORG,
        ordinal=1,
        section="Item 8",
        title="AAPL 10-K",
        uri="https://sec.gov/x",
        version="1",
        score=0.5,
        content="Net income was 97 billion dollars.",
    ),
]


class FixedRetriever:
    """Returns CHUNKS for anyone in `allowed`, nothing for everyone else."""

    def __init__(self, allowed: set[str] = frozenset({"alice", "carol"})):
        self.allowed, self.requests = allowed, []

    async def retrieve(self, req: RetrievalRequest) -> RetrievalResult:
        self.requests.append(req)
        chunks = CHUNKS if req.principal.user_id in self.allowed else []
        return RetrievalResult(chunks=chunks, candidate_count=len(chunks), acl_filtered=True, latency_ms=1)


class FixedFactStore:
    def __init__(self):
        self.calls = []

    async def query(self, principal, entity, concept, period_end=None):
        self.calls.append((principal.user_id, entity, concept))
        return [
            Fact(
                id=uuid.uuid4(),
                document_id=DOC,
                entity=entity,
                concept=concept,
                period_start=None,
                period_end=None,
                value=383_285_000_000,
                unit="USD",
                provenance={"uri": "https://sec.gov/x"},
            )
        ]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    events: list[tuple] = []

    async def sink(kind, principal, payload, thread_id):
        events.append((kind, principal, payload, thread_id))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    monkeypatch.setattr(runner, "IDEMPOTENCY", {})
    monkeypatch.setattr(runner, "_breakers", {})
    return events
