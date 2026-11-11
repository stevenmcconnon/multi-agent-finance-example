"""Fixtures for the eval suites: the real LangGraph turn (MemorySaver, FakeGateway) over corpus.py's fake stores.

What is real: harness_rag.agent.graph (nodes, routing, HITL interrupt), harness.runner.run_tool (Cedar,
idempotency, retries, breakers, audit), the Cedar policies in db/policies, the FakeGateway, MemorySaver.
What is faked: the SQL (corpus.FakeRetriever / corpus.FakeFactStore apply the predicate of retrieval/sql.py in
Python). tests/test_retrieval.py proves the SQL itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from corpus import FakeFactStore, FakeRetriever
from harness_rag.agent import graph
from harness_rag.authz import audit
from harness_rag.gateway import FakeGateway
from harness_rag.harness import runner
from harness_rag.harness.registry import ToolRegistry
from harness_rag.harness.tools import CalculatorTool, CreateTicketTool, FactsSqlTool, SearchDocumentsTool
from langgraph.checkpoint.memory import MemorySaver


@pytest.fixture(scope="session", autouse=True)
def wire_graph():
    """The real graph with MemorySaver, FakeGateway and the fake stores, exactly as tests/test_agent.py wires it."""
    registry = ToolRegistry(
        [SearchDocumentsTool(FakeRetriever()), FactsSqlTool(FakeFactStore()), CalculatorTool(), CreateTicketTool()]
    )
    graph.configure(checkpointer=MemorySaver(), registry=registry, gateway=FakeGateway())
    runner.IDEMPOTENCY = {}
    yield
    graph.configure()
    runner.IDEMPOTENCY = None


@pytest.fixture
def audit_sink(monkeypatch) -> list[dict[str, Any]]:
    """Every audit row of the test as {kind, user, payload, thread_id}; nothing reaches Postgres."""
    rows: list[dict[str, Any]] = []

    async def sink(kind, principal, payload, thread_id):
        rows.append(
            {"kind": kind, "user": principal.user_id if principal else None, "payload": payload, "thread_id": thread_id}
        )

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    return rows
