"""End-to-end smoke test of the MCP stdio server: spawn it as a subprocess with a dev token, list tools,
call them. Skipped when DATABASE_URL does not answer (identity and retrieval are DB-backed)."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import anyio
import pytest
from harness_rag.harness.devtoken import mint
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _db_reachable() -> bool:
    from harness_rag import db

    try:
        return asyncio.run(db.ping(os.environ.get("DATABASE_URL")))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="DATABASE_URL not reachable")

TIMEOUT_S = 120  # the first search loads the embedding models


def _params(user: str) -> StdioServerParameters:
    env = {**os.environ, "HR_TOKEN": mint(user), "OTEL_SDK_DISABLED": "true"}
    return StdioServerParameters(command=sys.executable, args=["-m", "harness_rag.mcp_server.server"], env=env)


def _payload(result) -> dict:
    assert not result.is_error, result.content
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def _session_run(user: str, fn):
    with anyio.fail_after(TIMEOUT_S):
        async with stdio_client(_params(user)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


QUERY = "Apple supply chain risk"


async def test_alice_tools_calculator_and_search():
    async def go(session):
        names = {t.name for t in (await session.list_tools()).tools}
        calc = _payload(await session.call_tool("calculator", {"expression": "2+2*3"}))
        search = _payload(await session.call_tool("search_documents", {"query": QUERY}))
        return names, calc, search

    names, calc, search = await _session_run("alice", go)
    assert {"search_documents", "facts_sql", "calculator"} <= names
    assert "create_ticket" not in names

    assert calc["ok"], calc
    out = calc["output"]
    value = out["value"] if isinstance(out, dict) else out
    assert float(value) == 8

    assert search["ok"], search
    titles = [c.get("title", "") for c in search["citations"]]
    assert any("10-K" in t for t in titles), titles


async def test_bob_is_restricted():
    async def go(session):
        names = {t.name for t in (await session.list_tools()).tools}
        search = _payload(await session.call_tool("search_documents", {"query": QUERY}))
        return names, search

    names, search = await _session_run("bob", go)
    assert "facts_sql" not in names
    assert "create_ticket" not in names
    assert search["ok"], search
    assert not any("10-K" in c.get("title", "") for c in search["citations"]), search["citations"]
    assert "10-K" not in json.dumps(search["output"])


async def test_carol_write_tool_over_mcp_needs_approval():
    """Audit #2: the write tool is listed for an admin but run_tool refuses it without the UI's approval."""

    async def go(session):
        names = {t.name for t in (await session.list_tools()).tools}
        ticket = _payload(await session.call_tool("create_ticket", {"title": "mcp test"}))
        return names, ticket

    names, ticket = await _session_run("carol", go)
    assert "create_ticket" in names
    assert ticket["ok"] is False and ticket["error"] == "approval_required"
