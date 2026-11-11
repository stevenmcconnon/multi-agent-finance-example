"""Harness: the tool runner's doors (Cedar, idempotency, retry, breaker) and the turn budget. Offline."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import pytest
from harness_rag.authz import audit
from harness_rag.contracts import Principal, RiskLevel, ToolCall, ToolResult, ToolSpec
from harness_rag.harness import runner
from harness_rag.harness.budget import BudgetExceeded, TurnBudget
from harness_rag.harness.registry import ToolRegistry
from harness_rag.harness.runner import TransientError, run_tool
from harness_rag.harness.tools import CalculatorTool, FactsSqlTool

ORG = UUID("00000000-0000-0000-0000-000000000001")
ALICE = Principal(user_id="alice", org_id=ORG, org_slug="acme", groups=["finance", "analysts"], roles=["analyst"])
BOB = Principal(user_id="bob", org_id=ORG, org_slug="acme", groups=["engineering"], roles=["viewer"])
CAROL = Principal(user_id="carol", org_id=ORG, org_slug="acme", groups=[], roles=["admin"])


class _Store:
    async def query(self, principal, entity, concept, period_end=None):
        return []


class _Flaky:
    """Registered under a real tool name so Cedar knows the action; fails `fail_times` then succeeds."""

    spec = ToolSpec(
        name="calculator",
        description="flaky",
        input_schema={},
        risk=RiskLevel.read,
        required_roles=["analyst", "admin"],
        timeout_seconds=1.0,
    )

    def __init__(self, fail_times: int, exc: type[Exception] = TransientError) -> None:
        self.fail_times, self.exc, self.calls = fail_times, exc, 0

    async def run(self, principal, call):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc("boom")
        return ToolResult(tool="calculator", ok=True, output={"value": 42})


@pytest.fixture(autouse=True)
def harness_env(monkeypatch):
    events = []

    async def sink(kind, principal, payload, thread_id):
        events.append((kind, payload))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    monkeypatch.setattr(runner, "IDEMPOTENCY", {})
    runner._breakers.clear()
    return events


async def test_bob_cannot_call_facts_sql(harness_env):
    reg = ToolRegistry([FactsSqlTool(_Store())])
    res = await run_tool(
        BOB, ToolCall(tool="facts_sql", arguments={"entity": "AAPL", "concept": "Revenues"}), "t1", reg
    )
    assert res.ok is False and res.error == "forbidden"
    assert ("tool.call", {"tool": "facts_sql"}) in [
        (k, {"tool": p["tool"]}) for k, p in harness_env if k == "tool.call"
    ]
    assert [p["allow"] for k, p in harness_env if k == "policy.decision"] == [False]


async def test_idempotent_second_call_is_cached():
    reg = ToolRegistry([CalculatorTool()])
    call = ToolCall(tool="calculator", arguments={"expression": "6*7"}, idempotency_key="k1")
    first = await run_tool(ALICE, call, "t1", reg)
    second = await run_tool(ALICE, call, "t1", reg)
    assert first.ok and first.output == {"value": 42.0} and first.from_cache is False
    assert second.from_cache is True and second.output == first.output


async def test_transient_errors_are_retried_then_succeed():
    tool = _Flaky(fail_times=2)
    res = await run_tool(ALICE, ToolCall(tool="calculator", arguments={}), "t1", ToolRegistry([tool]))
    assert res.ok is True and tool.calls == 3


async def test_non_transient_error_is_not_retried():
    tool = _Flaky(fail_times=1, exc=ValueError)
    res = await run_tool(ALICE, ToolCall(tool="calculator", arguments={}), "t1", ToolRegistry([tool]))
    assert res.ok is False and res.error.startswith("ValueError") and tool.calls == 1


async def test_breaker_opens_after_five_failures():
    tool = _Flaky(fail_times=99, exc=ValueError)
    reg = ToolRegistry([tool])
    for _ in range(5):
        res = await run_tool(ALICE, ToolCall(tool="calculator", arguments={}), "t1", reg)
        assert res.error.startswith("ValueError")
    res = await run_tool(ALICE, ToolCall(tool="calculator", arguments={}), "t1", reg)
    assert res.ok is False and res.error == "circuit_open" and tool.calls == 5


def test_budget_exceeded_raises():
    b = TurnBudget(tokens=100, tool_calls=2, deadline=time.monotonic() + 60)
    b.charge(tokens=60, tool_calls=1)
    with pytest.raises(BudgetExceeded):
        b.charge(tokens=50)
    with pytest.raises(BudgetExceeded):
        b.charge(tool_calls=2)
    with pytest.raises(BudgetExceeded):
        TurnBudget(deadline=time.monotonic() - 1).charge()


def test_specs_for_hides_tools_by_role():
    reg = ToolRegistry([CalculatorTool(), FactsSqlTool(_Store())])
    assert [s.name for s in reg.specs_for(BOB)] == []
    assert sorted(s.name for s in reg.specs_for(ALICE)) == ["calculator", "facts_sql"]


class _Write:
    """A write-risk tool under the real create_ticket name (Cedar allows admins); counts its runs."""

    spec = ToolSpec(
        name="create_ticket", description="w", input_schema={}, risk=RiskLevel.write, required_roles=["admin"]
    )

    def __init__(self, delay: float = 0.0) -> None:
        self.calls, self.delay = 0, delay

    async def run(self, principal, call):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ToolResult(tool="create_ticket", ok=True, output={"ticket_id": f"TCK-{self.calls}"})


async def test_write_tool_needs_explicit_approval(harness_env):
    tool = _Write()
    call = ToolCall(tool="create_ticket", arguments={"title": "x"})
    res = await run_tool(CAROL, call, "t1", ToolRegistry([tool]))
    assert res.ok is False and res.error == "approval_required" and tool.calls == 0
    assert [p["error"] for k, p in harness_env if k == "tool.call"] == ["approval_required"]
    res = await run_tool(CAROL, call, "t1", ToolRegistry([tool]), approved=True)
    assert res.ok is True and tool.calls == 1


async def test_idempotency_key_distinguishes_arguments():
    tool = _Write()
    reg = ToolRegistry([tool])
    a = ToolCall(tool="create_ticket", arguments={"title": "a"}, idempotency_key="t:create_ticket:aaaa")
    b = ToolCall(tool="create_ticket", arguments={"title": "b"}, idempotency_key="t:create_ticket:bbbb")
    first = await run_tool(CAROL, a, "t1", reg, approved=True)
    second = await run_tool(CAROL, b, "t1", reg, approved=True)
    again = await run_tool(CAROL, a, "t1", reg, approved=True)
    assert tool.calls == 2 and first.output != second.output
    assert again.from_cache is True and again.output == first.output


async def test_concurrent_same_key_runs_the_tool_once():
    tool = _Write(delay=0.1)
    call = ToolCall(tool="create_ticket", arguments={"title": "x"}, idempotency_key="k")
    r1, r2 = await asyncio.gather(
        run_tool(CAROL, call, "t1", ToolRegistry([tool]), approved=True),
        run_tool(CAROL, call, "t1", ToolRegistry([tool]), approved=True),
    )
    assert tool.calls == 1
    assert sorted([r1.from_cache, r2.from_cache]) == [False, True] and r1.output == r2.output


async def test_reserved_key_still_running_is_reported_not_rerun(monkeypatch):
    """Simulated reserve conflict: someone else holds the key with no response yet past the wait."""
    monkeypatch.setattr(runner, "IN_PROGRESS_WAIT_S", 0.1)
    runner.IDEMPOTENCY[f"{ORG}:create_ticket:k"] = None
    tool = _Write()
    call = ToolCall(tool="create_ticket", arguments={"title": "x"}, idempotency_key="k")
    res = await run_tool(CAROL, call, "t1", ToolRegistry([tool]), approved=True)
    assert res.ok is False and res.error == "in_progress" and tool.calls == 0


async def test_failed_idempotent_run_releases_its_key():
    tool = _Flaky(fail_times=1, exc=ValueError)
    reg = ToolRegistry([tool])
    call = ToolCall(tool="calculator", arguments={}, idempotency_key="k")
    assert (await run_tool(ALICE, call, "t1", reg)).ok is False
    assert (await run_tool(ALICE, call, "t1", reg)).ok is True and tool.calls == 2
