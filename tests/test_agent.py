"""The LangGraph turn, offline: FakeGateway, an injected retriever with fixed chunks, MemorySaver."""

from __future__ import annotations

import re
import uuid
from datetime import date
from uuid import UUID

import pytest
from finance_agents.agent import graph, team
from finance_agents.agent.graph import approve, replay, run_turn, stream_turn
from finance_agents.authz import audit
from finance_agents.contracts import Chunk, Fact, ModelRole, Principal, RetrievalResult, ToolCall
from finance_agents.gateway import ABSTAIN_TEXT, FakeGateway
from finance_agents.harness import runner
from finance_agents.harness.registry import ToolRegistry
from finance_agents.harness.tools import CalculatorTool, CreateTicketTool, FactsSqlTool, SearchDocumentsTool
from finance_agents.interfaces import ModelCallFailed, ModelRefused
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
    """One fiscal-2023 fact per query; `rows` (period_end, value) replaces it for the two-period tests."""

    def __init__(self) -> None:
        self.calls = []
        self.rows = [(date(2023, 9, 30), 383_285_000_000)]

    async def query(self, principal, entity, concept, period_end=None):
        self.calls.append((entity, concept))
        return [
            Fact(
                id=uuid.uuid4(),
                document_id=DOC,
                entity=entity,
                concept=concept,
                period_start=end.replace(year=end.year - 1),
                period_end=end,
                value=value,
                unit="USD",
                # `fy` is the FILING's fiscal year in EDGAR data, the same on every row: never the filter.
                provenance={"uri": "https://www.sec.gov/x", "raw_text": "Total net sales 383,285", "fy": 2023},
            )
            for end, value in self.rows
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
    assert answer_audit["model"] == "offline-stub" and len(answer_audit["citations"]) >= 1
    assert "latency_ms" in answer_audit and ans.reason == answer_audit["reason"] == "answered"


async def test_zero_authorized_chunks_abstains_without_calling_the_model(wired):
    events = [e async for e in stream_turn(BOB, "What did AAPL say about net sales?", "t-bob")]
    assert events[-1].type == "answer" and events[-1].data["abstained"] is True
    assert events[-1].data["reason"] == "no_relevant_evidence" and events[-1].data["citations"] == []
    assert events[-1].data["text"] == "None of the documents you are allowed to read answer that question."
    assert wired["retriever"].calls == 1 and wired["gateway"].completions == 0
    retrieval = next(e for e in events if e.type == "retrieval")
    assert retrieval.data["candidate_count"] == 0 and retrieval.data["chunks"] == []
    assert [e.type for e in events][:2] == ["status", "policy"]


async def test_out_of_scope_abstains_before_retrieval(wired):
    ans = await run_turn(ALICE, "hello there", "t-oos")
    assert ans.abstained is True and wired["retriever"].calls == 0
    assert ans.reason == "out_of_scope" and ans.text == graph.ABSTAIN_MESSAGES["out_of_scope"]


async def test_numeric_question_routes_through_facts(wired):
    events = [e async for e in stream_turn(ALICE, "What was AAPL revenue in 2023?", "t-num")]
    tools = [e.data["tool"] for e in events if e.type == "tool"]
    assert tools == ["facts_sql"] and wired["retriever"].calls == 0  # facts answered it: no document search
    assert wired["facts"].calls == [("AAPL", "Revenues")]
    assert events[-1].type == "answer" and events[-1].data["reason"] == "answered"
    line = (
        "AAPL revenue for the fiscal year ended 2023-09-30: "
        "$383.29 billion (USD 383,285,000,000; XBRL concept Revenues)."
    )
    assert events[-1].data["text"] == f"{line} [1]"
    assert [c["snippet"] for c in events[-1].data["citations"]] == [line]


async def test_numeric_question_with_no_facts_falls_back_to_the_document_search(wired):
    wired["facts"].rows = []
    events = [e async for e in stream_turn(ALICE, "What were AAPL net sales in 2023?", "t-num-fallback")]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["facts_sql", "search_documents"]
    assert events[-1].data["reason"] == "answered" and events[-1].data["citations"][0]["chunk_id"] == str(CHUNKS[0].id)


async def test_two_named_years_are_filtered_and_the_change_comes_from_the_calculator(wired):
    wired["facts"].rows = [
        (date(2024, 9, 28), 93_736_000_000),
        (date(2025, 9, 27), 112_010_000_000),
        (date(2026, 9, 26), 132_175_000_000),
        (date(2026, 9, 26), 132_175_000_000),  # the same fact reported twice
    ]
    question = "How did Apple's net income change from fiscal 2024 to fiscal 2026?"
    events = [e async for e in stream_turn(ALICE, question, "t-compare")]
    tools = [(e.data["tool"], e.data["ok"]) for e in events if e.type == "tool"]
    assert tools == [("facts_sql", True), ("calculator", True), ("calculator", True)]
    text = events[-1].data["text"]
    assert "2025-09-27" not in text and text.count("XBRL concept NetIncomeLoss") == 2
    assert (
        "Change in AAPL net income from the fiscal year ended 2024-09-28 to 2026-09-26: "
        "+$38.44 billion (+41.0%), computed by the calculator tool. [3]"
    ) in text
    assert events[-1].data["reason"] == "answered" and events[-1].data["verification"]["grounded"] is True
    # each source is labelled with its own period, never the filing's fiscal year; the change with what made it
    assert [c["section"] for c in events[-1].data["citations"]] == [
        "XBRL fact NetIncomeLoss, period ended 2024-09-28",
        "XBRL fact NetIncomeLoss, period ended 2026-09-26",
        "Calculator: derived from the two facts above",
    ]
    calc_audits = [p for k, p, _ in wired["events"] if k == "tool.call" and p["tool"] == "calculator"]
    assert len(calc_audits) == 2  # through the door: audited and charged


THREE_YEARS = [(date(2024, 9, 28), 391_035_000_000), (date(2026, 9, 26), 478_834_000_000), (date(2025, 9, 27), 1)]


@pytest.mark.parametrize(
    "question, lines",
    [
        ("What was Apple's revenue?", ["AAPL revenue for the fiscal year ended 2026-09-26"]),
        ("How did Apple's revenue grow?", ["AAPL revenue for the fiscal year ended 2026-09-26"]),
        (
            "Compare Apple and Microsoft revenue",
            ["AAPL revenue for the fiscal year ended 2026-09-26", "MSFT revenue for the fiscal year ended 2026-09-26"],
        ),
    ],
)
async def test_no_year_named_keeps_only_the_latest_period_and_runs_no_calculator(wired, question, lines):
    wired["facts"].rows = THREE_YEARS
    events = [e async for e in stream_turn(ALICE, question, f"t-latest-{len(question)}")]
    assert {e.data["tool"] for e in events if e.type == "tool"} == {"facts_sql"}
    assert [c["snippet"].split(":")[0] for c in events[-1].data["citations"]] == lines


def test_latest_period_is_per_entity_and_concept_with_both_revenue_concepts_as_one():
    def fact(entity, concept, end):
        return {"entity": entity, "concept": concept, "period_end": end, "value": 1}

    facts = [
        fact("AAPL", "Revenues", "2024-09-28"),
        fact("AAPL", "RevenueFromContractWithCustomerExcludingAssessedTax", "2026-09-26"),
        fact("AAPL", "NetIncomeLoss", "2025-09-27"),
        fact("MSFT", "Revenues", "2026-06-30"),
    ]
    assert team.Researcher._select(facts, None) == facts[1:]
    balance = {**fact("AAPL", "Assets", "2026-09-26"), "unit": "USD", "document_id": DOC}  # an instant: no start
    assert team.evidence({"facts": [balance]})[0][1].section == "XBRL fact Assets, as of 2026-09-26"  # type: ignore[typeddict-item]
    assert team.Researcher._select(facts, "2024") == facts[:1]  # a named year still filters
    assert team.Researcher._select(facts, "1999") == facts  # and a year no fact has changes nothing


async def test_two_periods_without_a_comparison_question_get_no_change_line(wired):
    wired["facts"].rows = [(date(2024, 9, 28), 1_000_000), (date(2026, 9, 26), 2_000_000)]
    events = [e async for e in stream_turn(ALICE, "What was AAPL revenue in 2019?", "t-no-compare")]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["facts_sql"]  # numeric intent: no calculator
    assert events[-1].data["text"].count("XBRL concept") == 2 and "Change in" not in events[-1].data["text"]


async def test_change_line_is_skipped_when_the_role_may_not_use_the_calculator(wired):
    wired["facts"].rows = [(date(2024, 9, 28), 1_000_000), (date(2026, 9, 26), 2_000_000)]
    registry = ToolRegistry([SearchDocumentsTool(wired["retriever"]), FactsSqlTool(wired["facts"])])
    graph.configure(gateway=wired["gateway"], registry=registry, checkpointer=MemorySaver())
    events = [e async for e in stream_turn(ALICE, "How did AAPL revenue change from 2024 to 2026?", "t-no-calc")]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["facts_sql"]
    assert "Change in" not in events[-1].data["text"] and events[-1].data["reason"] == "answered"


async def test_viewer_is_never_offered_facts_sql_but_still_answers_from_docs(wired):
    events = [e async for e in stream_turn(BOB, "What was AAPL revenue in 2023?", "t-num-bob")]
    plan_ev = next(e for e in events if e.type == "status" and e.data["node"] == "plan")
    assert "facts_sql" not in plan_ev.data["message"] and "search_documents" in plan_ev.data["message"]
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["search_documents"]
    assert wired["facts"].calls == [] and events[-1].type == "answer"


async def test_planned_tool_outside_the_principals_registry_is_refused_by_cedar(wired, monkeypatch):
    """Defence in depth: a model that ignores the offered list still cannot run the tool (run_tool's Cedar check)."""

    async def rogue(question, understood, tools):
        assert "facts_sql" not in {t.name for t in tools}
        return [ToolCall(tool="facts_sql", arguments={"entity": "AAPL", "concept": "Revenues"})]

    monkeypatch.setattr(wired["gateway"], "plan", rogue)
    events = [e async for e in stream_turn(BOB, "What was AAPL revenue in 2023?", "t-rogue")]
    facts_ev = next(e for e in events if e.type == "tool" and e.data["tool"] == "facts_sql")
    assert facts_ev.data["ok"] is False and facts_ev.data["error"] == "forbidden"
    assert wired["facts"].calls == []
    assert events[-1].type == "answer"  # the retrieval fallback still ran


async def test_lookup_with_an_empty_plan_still_searches_once(wired, monkeypatch):
    async def nothing(question, understood, tools):
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
    done = resumed[-1].data
    assert resumed[-1].type == "answer" and done["reason"] == "action_done" and done["abstained"] is False
    assert re.fullmatch(r"Ticket TCK-[0-9A-F]{8} created: review the 10-K ACLs\.", done["text"])
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
    assert resumed[-1].data["reason"] == "action_denied"
    assert resumed[-1].data["text"] == "The ticket was not approved; nothing was changed."
    tool_ev = next(e for e in resumed if e.type == "tool")
    assert tool_ev.data == {
        "tool": "create_ticket",
        "ok": False,
        "latency_ms": 0,
        "from_cache": False,
        "error": "denied",
    }
    assert not any(p.get("event") == "ticket.created" for k, p, _ in wired["events"] if k == "hitl")


@pytest.mark.parametrize("who, role", [(ALICE, "analyst"), (BOB, "viewer")], ids=["alice", "bob"])
async def test_ticket_request_from_a_role_without_the_tool_is_refused_at_the_door(wired, monkeypatch, who, role):
    """The model is never shown create_ticket, yet the request still reaches run_tool: Cedar refuses it, the
    refusal is audited, and the answer says which role it needs. No approval card for a call that cannot run."""
    real_plan, seen = wired["gateway"].plan, []

    async def spy(question, understood, tools):
        seen.extend(t.name for t in tools)
        return await real_plan(question, understood, tools)

    monkeypatch.setattr(wired["gateway"], "plan", spy)
    events = [e async for e in stream_turn(who, "Create a ticket to review Apple's 10-K risk section", "t-forbid")]
    assert "create_ticket" not in seen and not any(e.type == "hitl" for e in events)
    tool_ev = next(e for e in events if e.type == "tool")
    assert tool_ev.data["tool"] == "create_ticket" and tool_ev.data["error"] == "forbidden"
    last = events[-1]
    assert last.type == "answer" and last.data["reason"] == "action_forbidden" and last.data["abstained"] is True
    assert last.data["text"] == (
        f"Your role ({role}) may not create tickets; that needs the admin role. Nothing was changed."
    )
    audited = [p for k, p, _ in wired["events"] if k == "tool.call"]
    assert [(p["tool"], p["error"]) for p in audited] == [("create_ticket", "forbidden")]
    assert wired["retriever"].calls == 0 and wired["gateway"].completions == 0


async def test_a_rogue_plan_of_an_unoffered_write_tool_gets_no_approval_card(wired, monkeypatch):
    """Defence in depth: a model that plans create_ticket for alice anyway goes straight to Cedar's refusal."""

    async def rogue(question, understood, tools):
        return [ToolCall(tool="create_ticket", arguments={"title": "escalate"})]

    monkeypatch.setattr(wired["gateway"], "plan", rogue)
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales?", "t-alice-ticket")]
    assert not any(e.type == "hitl" for e in events)
    tool_ev = next(e for e in events if e.type == "tool" and e.data["tool"] == "create_ticket")
    assert tool_ev.data["ok"] is False and tool_ev.data["error"] == "forbidden"
    assert not any(p.get("event") == "ticket.created" for k, p, _ in wired["events"] if k == "hitl")


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
    assert abstain_audit["reason"] == ans.reason == "not_grounded"
    assert ans.text == "A draft answer was written but failed verification against the evidence, so it was withheld."


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
    assert resumed[-1].data["reason"] == "action_forbidden"
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
    assert tools.count("facts_sql") == 7 and tools.count("search_documents") == 0  # facts found: search skipped


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
        ("Planner", "model"),
        ("Researcher", "deterministic"),
        ("Writer", "model"),
        ("Judge", "model"),
    ]
    assert [s["node"] for s in statuses] == ["understand", "plan", "act", "generate", "verify"]
    assert [e.type for e in events].count("policy") == 1  # the Guard's one row


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
    assert [e.data["tool"] for e in events if e.type == "tool"] == ["facts_sql"]
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


async def test_more_planned_calculations_than_budget_is_capped_not_an_error(wired, monkeypatch):
    """A plan of 8 read tools plus the forced search is 9 > budget 8: act drops the last non-search call."""
    monkeypatch.setattr(graph.settings, "hr_turn_tool_call_budget", 8)

    async def eight(question, understood, tools):
        return [ToolCall(tool="calculator", arguments={"expression": f"{i}+1"}) for i in range(8)]

    monkeypatch.setattr(wired["gateway"], "plan", eight)
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales?", "t-cap-calc")]
    assert not any(e.type == "error" for e in events)
    tools = [e.data["tool"] for e in events if e.type == "tool"]
    assert tools.count("calculator") == 7 and tools.count("search_documents") == 1
    assert events[-1].type == "answer" and events[-1].data["abstained"] is False


async def test_unreadable_verifier_output_fails_verification(wired, monkeypatch):
    """The Judge never passes a draft it could not check: garbage from the verify model is grounded=False."""
    gw = wired["gateway"]
    real = gw.complete

    async def garbage(role, system, messages, **kw):
        text, usage = await real(role, system, messages, **kw)
        return ("not json at all", usage) if role.value == "verify" else (text, usage)

    monkeypatch.setattr(gw, "complete", garbage)
    ans = await run_turn(ALICE, "What did AAPL say about net sales?", "t-garbage")
    assert ans.abstained is True and ans.verification is not None and ans.verification.grounded is False
    assert any("unreadable" in c for c in ans.verification.unsupported_claims)


async def test_token_budget_exceeded_ends_the_stream_with_an_error_event(wired, monkeypatch):
    monkeypatch.setattr(graph.settings, "hr_turn_token_budget", 1)
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales?", "t-tokens")]
    assert events[-1].type == "error" and events[-1].data["code"] == "budget_exceeded"
    assert not any(e.type == "answer" for e in events)


# ---------- Answer.reason and model failures (docs/CONTRACTS.md "Demo-hardening decisions") ----------
async def test_writer_sentinel_means_no_relevant_evidence(wired):
    """Evidence came back but none of it is about the question: the Writer says so, the reader gets the reason."""
    ans = await run_turn(ALICE, "Which director plans to retire?", "t-irrelevant")
    assert wired["retriever"].calls == 1 and wired["gateway"].completions == 1
    assert ans.abstained and ans.reason == "no_relevant_evidence"
    assert ans.text == graph.ABSTAIN_MESSAGES["no_relevant_evidence"] != ABSTAIN_TEXT


@pytest.mark.parametrize("role", [ModelRole.generate, ModelRole.verify])
async def test_model_refusal_abstains_with_model_declined(wired, monkeypatch, role):
    real = wired["gateway"].complete

    async def refusing(r, system, messages, **kw):
        if r == role:
            raise ModelRefused("stopReason refusal")
        return await real(r, system, messages, **kw)

    monkeypatch.setattr(wired["gateway"], "complete", refusing)
    ans = await run_turn(ALICE, "What did AAPL say about net sales?", f"t-declined-{role.value}")
    assert ans.abstained and ans.reason == "model_declined"
    assert ans.text == "The model declined to answer this request."


async def test_a_failed_model_call_ends_the_turn_with_an_error_event(wired, monkeypatch):
    async def down(role, system, messages, **kw):
        raise ModelCallFailed("Bedrock generate call failed (AccessDeniedException) for model us.x: no", "AccessDenied")

    monkeypatch.setattr(wired["gateway"], "complete", down)
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales?", "t-model-down")]
    assert events[-1].type == "error" and not any(e.type == "answer" for e in events)
    assert "AccessDeniedException" in events[-1].data["message"] and "us.x" in events[-1].data["message"]


async def test_a_gateway_fallback_is_named_in_the_status_message(wired, monkeypatch):
    """No silent stubs: a gateway that answered from the heuristic after a model error says so on the timeline."""
    from finance_agents.gateway import note

    real_understand, real_plan = wired["gateway"].understand, wired["gateway"].plan

    async def understand(question):
        note("classifier call failed (ThrottlingException); used the deterministic fallback")
        return await real_understand(question)

    async def plan(question, understood, tools):
        note("planner call failed (AccessDeniedException); used the deterministic fallback")
        return await real_plan(question, understood, tools)

    monkeypatch.setattr(wired["gateway"], "understand", understand)
    monkeypatch.setattr(wired["gateway"], "plan", plan)
    events = [e async for e in stream_turn(ALICE, "What did AAPL say about net sales?", "t-fallback")]
    messages = {e.data["node"]: e.data["message"] for e in events if e.type == "status"}
    assert messages["understand"].endswith(
        "; classifier call failed (ThrottlingException); used the deterministic fallback"
    )
    assert messages["plan"].endswith("; planner call failed (AccessDeniedException); used the deterministic fallback")
    assert "fallback" not in messages["act"] and events[-1].data["reason"] == "answered"
