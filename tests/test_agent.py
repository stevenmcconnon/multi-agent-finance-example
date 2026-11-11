"""The LangGraph turn, offline: FakeGateway, an injected retriever with fixed chunks, MemorySaver."""

from __future__ import annotations

import uuid
from datetime import date
from uuid import UUID

import pytest
from harness_rag.agent import graph, team
from harness_rag.agent.graph import approve, replay, run_turn, stream_turn
from harness_rag.authz import audit
from harness_rag.contracts import Chunk, Fact, ModelRole, Principal, RetrievalResult, ToolCall
from harness_rag.gateway import ABSTAIN_TEXT, FakeGateway
from harness_rag.harness import runner
from harness_rag.harness.registry import ToolRegistry
from harness_rag.harness.tools import CalculatorTool, CreateTicketTool, FactsSqlTool, SearchDocumentsTool
from langgraph.checkpoint.memory import MemorySaver

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
        ordinal=i,
        section="Item 7",
        title="AAPL 10-K 2023",
        uri="https://www.sec.gov/x",
        version="1",
        score=0.9 - i / 10,
        content=text,
    )
    for i, text in enumerate(["Net sales rose 2 percent in fiscal 2023. Services grew.", "iPhone revenue was flat."])
]


class _Retriever:
    """Alice sees the fixed chunks; everyone else is authorized for nothing."""

    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, req):
        self.calls += 1
        chunks = CHUNKS if req.principal.user_id == "alice" else []
        return RetrievalResult(chunks=chunks, candidate_count=len(chunks), acl_filtered=True, latency_ms=1)


class _Facts:
    def __init__(self) -> None:
        self.calls = []

    async def query(self, principal, entity, concept, period_end=None):
        self.calls.append((entity, concept))
        return [
            Fact(
                id=uuid.uuid4(),
                document_id=DOC,
                entity=entity,
                concept=concept,
                period_start=None,
                period_end=date(2023, 9, 30),
                value=383_285_000_000,
                unit="USD",
                provenance={"uri": "https://www.sec.gov/x", "raw_text": "Total net sales 383,285"},
            )
        ]


class _SpyGateway(FakeGateway):
    def __init__(self) -> None:
        self.completions = 0

    async def complete(self, *a, **kw):
        self.completions += 1
        return await super().complete(*a, **kw)


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    events = []

    async def sink(kind, principal, payload, thread_id):
        events.append((kind, payload, thread_id))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    monkeypatch.setattr(runner, "IDEMPOTENCY", {})
    runner._breakers.clear()
    gw, retriever, facts = _SpyGateway(), _Retriever(), _Facts()
    registry = ToolRegistry([SearchDocumentsTool(retriever), FactsSqlTool(facts), CalculatorTool(), CreateTicketTool()])
    graph.configure(gateway=gw, registry=registry, checkpointer=MemorySaver())
    yield {"events": events, "gateway": gw, "retriever": retriever, "facts": facts}
    graph.configure()


async def test_alice_lookup_turn_is_cited_and_grounded(wired):
    ans = await run_turn(ALICE, "What did AAPL say about net sales in 2023?", "t-alice")
    assert ans.abstained is False and len(ans.citations) >= 1
    assert ans.citations[0].document_id == DOC and ans.citations[0].chunk_id == CHUNKS[0].id
    assert ans.verification is not None and ans.verification.grounded is True
    assert "[1]" in ans.text
    kinds = [k for k, _, _ in wired["events"]]
    assert kinds.count("answer") == 1
    answer_audit = next(p for k, p, _ in wired["events"] if k == "answer")
    assert answer_audit["model"] == "fake" and len(answer_audit["citations"]) >= 1 and "latency_ms" in answer_audit


async def test_zero_authorized_chunks_abstains_without_calling_the_model(wired):
    events = [e async for e in stream_turn(BOB, "What did AAPL say about net sales?", "t-bob")]
    assert events[-1].type == "answer" and events[-1].data["abstained"] is True
    assert events[-1].data["text"] == ABSTAIN_TEXT and events[-1].data["citations"] == []
    assert wired["retriever"].calls == 1 and wired["gateway"].completions == 0
    retrieval = next(e for e in events if e.type == "retrieval")
    assert retrieval.data["candidate_count"] == 0 and retrieval.data["chunks"] == []
    assert [e.type for e in events][:2] == ["status", "policy"]


async def test_out_of_scope_abstains_before_retrieval(wired):
    ans = await run_turn(ALICE, "hello there", "t-oos")
    assert ans.abstained is True and wired["retriever"].calls == 0


async def test_numeric_question_routes_through_facts(wired):
    events = [e async for e in stream_turn(ALICE, "What was AAPL revenue in 2023?", "t-num")]
    tools = [e.data["tool"] for e in events if e.type == "tool"]
    assert tools == ["facts_sql", "search_documents"]
    assert wired["facts"].calls == [("AAPL", "Revenues")]
    assert events[-1].type == "answer" and events[-1].data["abstained"] is False
    assert any("383,285,000,000.00 USD" in c["snippet"] for c in events[-1].data["citations"])


async def test_viewer_is_never_offered_facts_sql_but_still_answers_from_docs(wired):
    events = [e async for e in stream_turn(BOB, "What was AAPL revenue in 2023?", "t-num-bob")]
    plan_ev = next(e for e in events if e.type == "status" and e.data["node"] == "plan")
    assert "facts_sql" not in plan_ev.data["message"] and "search_documents" in plan_ev.data["message"]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["search_documents"]
    assert wired["facts"].calls == [] and events[-1].type == "answer"


async def test_planned_tool_outside_the_principals_registry_is_refused_by_cedar(wired, monkeypatch):
    """Defence in depth: a model that ignores the offered list still cannot run the tool (run_tool's Cedar check)."""

    async def rogue(question, understood, tools, evidence_summary):
        assert "facts_sql" not in {t.name for t in tools}
        return [ToolCall(tool="facts_sql", arguments={"entity": "AAPL", "concept": "Revenues"})]

    monkeypatch.setattr(wired["gateway"], "plan", rogue)
    events = [e async for e in stream_turn(BOB, "What was AAPL revenue in 2023?", "t-rogue")]
    facts_ev = next(e for e in events if e.type == "tool" and e.data["tool"] == "facts_sql")
    assert facts_ev.data["ok"] is False and facts_ev.data["error"] == "forbidden"
    assert wired["facts"].calls == []
    assert events[-1].type == "answer"  # the retrieval fallback still ran


async def test_lookup_with_an_empty_plan_still_searches_once(wired, monkeypatch):
    async def nothing(question, understood, tools, evidence_summary):
        return []

    monkeypatch.setattr(wired["gateway"], "plan", nothing)
    ans = await run_turn(ALICE, "What did AAPL say about net sales?", "t-empty-plan")
    assert wired["retriever"].calls == 1 and ans.abstained is False


async def test_create_ticket_by_carol_stops_at_hitl_and_approve_runs_it(wired):
    events = [e async for e in stream_turn(CAROL, "Create a ticket: review the 10-K ACLs", "t-hitl")]
    assert events[-1].type == "hitl"
    assert events[-1].data["tool"] == "create_ticket" and events[-1].data["arguments"] == {
        "title": "review the 10-K ACLs"
    }
    assert wired["retriever"].calls == 0

    resumed = [e async for e in approve(CAROL, "t-hitl", True, "ok")]
    tool_ev = next(e for e in resumed if e.type == "tool")
    assert tool_ev.data["tool"] == "create_ticket" and tool_ev.data["ok"] is True
    assert resumed[-1].type == "answer" and "TCK-" in resumed[-1].data["text"]
    hitl_audits = [p for k, p, _ in wired["events"] if k == "hitl"]
    assert any(p.get("approved") is True for p in hitl_audits) and any(
        p.get("event") == "ticket.created" for p in hitl_audits
    )
    # nothing left to approve
    again = [e async for e in approve(CAROL, "t-hitl", True, None)]
    assert again[-1].type == "error" and again[-1].data["code"] == "nothing_pending"


async def test_create_ticket_declined_changes_nothing(wired):
    [e async for e in stream_turn(CAROL, "open a ticket about the ACL audit", "t-decline")]
    resumed = [e async for e in approve(CAROL, "t-decline", False, "not now")]
    assert resumed[-1].type == "answer" and resumed[-1].data["abstained"] is True
    tool_ev = next(e for e in resumed if e.type == "tool")
    assert tool_ev.data == {
        "tool": "create_ticket",
        "ok": False,
        "latency_ms": 0,
        "from_cache": False,
        "error": "denied",
    }
    assert not any(p.get("event") == "ticket.created" for k, p, _ in wired["events"] if k == "hitl")


async def test_create_ticket_by_alice_is_forbidden_by_cedar(wired, monkeypatch):
    """Alice is never offered create_ticket; a model that plans it anyway is stopped at hitl, then by Cedar."""

    async def rogue(question, understood, tools, evidence_summary):
        return [ToolCall(tool="create_ticket", arguments={"title": "escalate"})]

    monkeypatch.setattr(wired["gateway"], "plan", rogue)
    events = [e async for e in stream_turn(ALICE, "create a ticket: escalate", "t-alice-ticket")]
    assert events[-1].type == "hitl"
    resumed = [e async for e in approve(ALICE, "t-alice-ticket", True, None)]
    tool_ev = next(e for e in resumed if e.type == "tool")
    assert tool_ev.data["ok"] is False and tool_ev.data["error"] == "forbidden"


async def test_replay_returns_ordered_checkpoints(wired):
    await run_turn(ALICE, "What did AAPL say about net sales?", "t-replay")
    cps = await replay(ALICE, "t-replay")
    assert len(cps) >= 3
    assert [c["step"] for c in cps] == sorted(c["step"] for c in cps)
    assert cps[0]["node"] == "__start__" and cps[-1]["node"] == "answer" and cps[-1]["state_summary"]["answered"]
    nodes = [c["node"] for c in cps]
    assert nodes.index("plan") < nodes.index("act") < nodes.index("generate")
    assert cps[nodes.index("plan")]["state_summary"]["planned_tools"] == ["search_documents"]
    assert all({"checkpoint_id", "step", "node", "state_summary", "at"} <= set(c) for c in cps)


async def test_thread_belongs_to_its_owner(wired):
    await run_turn(ALICE, "What did AAPL say about net sales?", "t-owned")
    with pytest.raises(PermissionError):
        await replay(BOB, "t-owned")
    with pytest.raises(PermissionError):
        await run_turn(BOB, "anything?", "t-owned")


async def test_ungrounded_draft_is_regenerated_then_abstains(wired, monkeypatch):
    gw = wired["gateway"]
    real = gw.complete

    async def lying(role, system, messages, **kw):
        text, usage = await real(role, system, messages, **kw)
        return (text + " Apple also bought a bank. [1]", usage) if role.value == "generate" else (text, usage)

    monkeypatch.setattr(gw, "complete", lying)
    ans = await run_turn(ALICE, "What did AAPL say about net sales?", "t-lie")
    assert ans.abstained is True and ans.verification is not None and ans.verification.grounded is False
    assert "Apple also bought a bank." in ans.verification.unsupported_claims
    abstain_audit = next(p for k, p, _ in wired["events"] if k == "answer")
    assert abstain_audit["reason"] == "not_grounded"


async def test_two_approved_tickets_in_one_thread_both_run(wired):
    """Audit #3: the idempotency key carries the arguments, so a second ticket is not the first one's cached id."""
    [e async for e in stream_turn(CAROL, "create a ticket: first thing", "t-two")]
    one = [e async for e in approve(CAROL, "t-two", True, None)]
    [e async for e in stream_turn(CAROL, "create a ticket: second thing", "t-two")]
    two = [e async for e in approve(CAROL, "t-two", True, None)]
    created = [p["ticket_id"] for k, p, _ in wired["events"] if k == "hitl" and p.get("event") == "ticket.created"]
    assert len(set(created)) == 2
    assert one[-1].data["text"] != two[-1].data["text"]
    assert all(e.data["from_cache"] is False for e in one + two if e.type == "tool")


async def test_approve_runs_as_the_current_principal_not_the_saved_one(wired):
    """Audit #6: carol lost admin while the thread waited; the approved write is refused by Cedar."""
    [e async for e in stream_turn(CAROL, "create a ticket: escalate", "t-demoted")]
    demoted = CAROL.model_copy(update={"roles": []})
    resumed = [e async for e in approve(demoted, "t-demoted", True, None)]
    tool_ev = next(e for e in resumed if e.type == "tool")
    assert tool_ev.data["ok"] is False and tool_ev.data["error"] == "forbidden"
    hitl_audit = next(p for k, p, _ in wired["events"] if k == "hitl" and "approved" in p)
    assert hitl_audit["approved"] is True
    assert not any(p.get("event") == "ticket.created" for k, p, _ in wired["events"] if k == "hitl")


async def test_ticket_title_drops_a_leading_to(wired):
    events = [e async for e in stream_turn(CAROL, "create a ticket to review Apple's 10-K risk section", "t-title")]
    assert events[-1].data["arguments"] == {"title": "review Apple's 10-K risk section"}


async def test_verification_does_not_carry_into_the_next_turn(wired, monkeypatch):
    """Audit #7: turn 1 abstains with unsupported claims; turn 2 on the same thread starts clean."""
    gw = wired["gateway"]
    real = gw.complete
    systems: list[str] = []

    async def lying(role, system, messages, **kw):
        systems.append(system)
        text, usage = await real(role, system, messages, **kw)
        return (text + " Apple also bought a bank. [1]", usage) if role.value == "generate" else (text, usage)

    monkeypatch.setattr(gw, "complete", lying)
    first = await run_turn(ALICE, "What did AAPL say about net sales?", "t-carry")
    assert first.abstained and first.verification.unsupported_claims
    monkeypatch.setattr(gw, "complete", real)
    systems.clear()

    async def spy(role, system, messages, **kw):
        systems.append(system)
        return await real(role, system, messages, **kw)

    monkeypatch.setattr(gw, "complete", spy)
    second = await run_turn(ALICE, "What did AAPL say about net sales?", "t-carry")
    assert second.abstained is False and second.verification.grounded is True
    assert not any("drop them" in s for s in systems)


async def test_more_planned_facts_than_budget_is_capped_not_an_error(wired, monkeypatch):
    """Audit #13: 4 entities x 2 concepts = 8 facts calls + the search > budget 8; act trims facts and answers."""
    monkeypatch.setattr(graph.settings, "hr_turn_tool_call_budget", 8)
    events = [
        e async for e in stream_turn(ALICE, "What was AAPL MSFT AMZN GOOGL revenue and net income in 2023?", "t-cap")
    ]
    assert not any(e.type == "error" for e in events)
    assert events[-1].type == "answer" and events[-1].data["abstained"] is False
    tools = [e.data["tool"] for e in events if e.type == "tool"]
    assert tools.count("facts_sql") == 7 and tools.count("search_documents") == 1


async def test_startup_without_postgres_falls_back_only_in_local_mode(monkeypatch):
    monkeypatch.setattr(graph.settings, "database_url", "postgresql://x:x@127.0.0.1:1/x")
    graph.configure()
    monkeypatch.setattr(graph.settings, "hr_mode", "aws")
    with pytest.raises(RuntimeError):
        await graph.startup()
    monkeypatch.setattr(graph.settings, "hr_mode", "local")
    await graph.startup()
    assert isinstance(graph._checkpointer, MemorySaver)
    await graph.shutdown()


# ---------- the agent team (docs/CONTRACTS.md "The agent team") ----------
async def test_alice_turn_names_the_agent_and_kind_on_every_status_event(wired):
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales in 2023?", "t-team")]
    statuses = [e.data for e in events if e.type == "status"]
    assert all({"node", "agent", "kind", "message"} <= set(s) for s in statuses)
    assert [(s["agent"], s["kind"]) for s in statuses] == [
        ("Planner", "model"),
        ("Guard", "deterministic"),
        ("Planner", "model"),
        ("Researcher", "deterministic"),
        ("Writer", "model"),
        ("Judge", "model"),
    ]
    assert [s["node"] for s in statuses] == ["understand", "policy", "plan", "act", "generate", "verify"]


async def test_researcher_never_calls_the_gateway(wired, monkeypatch):
    """The act node is deterministic: with the model dead it still runs every planned tool."""
    gw = wired["gateway"]

    async def dead(*a, **kw):
        raise AssertionError("the Researcher reached the model")

    events: list = []
    real_act = graph.act

    async def act_with_dead_model(state):
        monkeypatch.setattr(gw, "complete", dead)
        try:
            return await real_act(state)
        finally:
            monkeypatch.setattr(gw, "complete", type(gw).complete.__get__(gw))

    monkeypatch.setattr(graph, "act", act_with_dead_model)
    graph.configure(gateway=gw, registry=graph._reg(), checkpointer=MemorySaver())  # recompile with the wrapped node
    events = [e async for e in stream_turn(ALICE, "What was AAPL revenue in 2023?", "t-no-model")]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["facts_sql", "search_documents"]
    assert next(e for e in events if e.type == "status" and e.data["node"] == "act").data["agent"] == "Researcher"
    assert events[-1].type == "answer" and events[-1].data["abstained"] is False


async def test_judge_uses_the_verify_role(wired, monkeypatch):
    gw = wired["gateway"]
    real = gw.complete
    roles: list = []

    async def spy(role, system, messages, **kw):
        roles.append((role, system))
        return await real(role, system, messages, **kw)

    monkeypatch.setattr(gw, "complete", spy)
    await run_turn(ALICE, "What did AAPL say about net sales?", "t-judge")
    assert [r for r, _ in roles] == [ModelRole.generate, ModelRole.verify]
    assert roles[0][1] == team.Writer.SYSTEM and roles[1][1] == team.Judge.SYSTEM
    assert team.Judge.kind == "model" and team.Writer.kind == "model"
