"""L6: one turn as a LangGraph StateGraph.

  understand -> policy -> plan -> act (interrupt for write/money tools) -> generate -> verify -> answer | abstain

understand  classify the question (gateway.understand)
policy      Cedar: may this principal get an answer at all; deny -> abstain
plan        the MODEL picks tools (gateway.plan, Bedrock Converse tool use) from `registry.specs_for(principal)`,
            so it only ever sees tools this principal's roles may use; capped at the turn's tool-call budget
act         every planned call goes through `harness.runner.run_tool` (Cedar, idempotency, breaker, audit) in
            order; a write/money call first suspends the turn with interrupt(), resumed by approve(); denied
            calls are skipped with a `tool` event ok=False error="denied". search_documents/facts_sql results
            become the evidence; a lookup the model planned nothing for still gets one search_documents
generate    answer from the evidence only; no evidence -> the model is never called
verify      second model pass; ungrounded -> one regeneration, then abstain
answer      cited Answer | abstain: ABSTAIN_TEXT with the reason audited

Everything the model sees came back from `run_tool`. Models are reached through gateway/, Postgres only
through the checkpointer; nothing here imports a vendor SDK. Entry points (run_turn, stream_turn, approve,
replay) are pinned in docs/CONTRACTS.md.

In production: AsyncPostgresSaver on the control-plane database; Bedrock proposes the tool calls.
In this demo: the same saver (MemorySaver injectable for offline tests); with HR_MODEL_BACKEND=fake the
plan is a deterministic heuristic (gateway/fake.py `plan_heuristic`), because the fake model is offline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..authz import audit
from ..config import settings
from ..contracts import (
    Answer,
    Citation,
    ModelRole,
    Principal,
    RiskLevel,
    ToolCall,
    ToolResult,
    TurnEvent,
    UnderstoodQuery,
    Verification,
)
from ..gateway import ABSTAIN_TEXT, format_evidence, get_gateway
from ..gateway.bedrock import extract_json
from ..harness import runner
from ..harness.budget import BudgetExceeded, TurnBudget
from ..harness.registry import ToolRegistry
from ..harness.tools import default_registry
from ..interfaces import ModelGateway

log = logging.getLogger(__name__)

GENERATE_SYSTEM = (
    "Answer the question using ONLY the numbered evidence. Cite every sentence with its evidence index "
    f"like [1]. If the evidence does not answer the question, reply exactly: {ABSTAIN_TEXT}"
)
VERIFY_SYSTEM = (
    "Check every sentence of ANSWER against EVIDENCE. Reply with JSON only: "
    '{"grounded": bool, "confidence": number 0..1, "unsupported_claims": [str]}'
)
MAX_GENERATE_ATTEMPTS = 2
HITL_RISKS = {RiskLevel.write, RiskLevel.money}
_CITE = re.compile(r"\[(\d+)\]")


class TurnState(TypedDict, total=False):
    principal: dict[str, Any]  # Principal.model_dump(mode="json"); never from the prompt
    question: str
    thread_id: str
    started: float
    node: str  # last node that ran (replay)
    understood: dict[str, Any]
    policy: dict[str, Any]
    chunks: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    draft: str
    verification: dict[str, Any]
    answer: dict[str, Any] | None
    planned_tools: list[dict[str, Any]]  # ToolCall.model_dump(); the model's picks, capped by the budget
    attempts: int
    budget: dict[str, Any]  # dataclasses.asdict(TurnBudget)
    events: list[dict[str, Any]]  # this node's TurnEvents; read from `updates` stream mode


# ---------- wiring (tests inject; the API calls startup()) ----------
_gateway: ModelGateway | None = None
_registry: ToolRegistry | None = None
_checkpointer: BaseCheckpointSaver | None = None
_saver_cm: Any = None
_compiled: Any = None


def configure(
    *,
    gateway: ModelGateway | None = None,
    registry: ToolRegistry | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> None:
    global _gateway, _registry, _checkpointer, _compiled
    _gateway, _registry, _checkpointer, _compiled = gateway, registry, checkpointer, None


def _gw() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = get_gateway()
    return _gateway


def _reg() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = default_registry()
    return _registry


async def startup() -> None:
    """API boot: the Postgres checkpointer and its tables (idempotent).

    In production (HR_MODE=aws): no checkpointer means no boot; the task dies and the ALB never routes to it.
    In this demo (local): a per-process MemorySaver with a warning, so the API runs without Postgres.
    """
    global _checkpointer, _saver_cm, _compiled
    if _checkpointer is None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        try:
            _saver_cm = AsyncPostgresSaver.from_conn_string(settings.database_url)
            saver = await _saver_cm.__aenter__()
            await saver.setup()
        except Exception as exc:
            _saver_cm = None
            if settings.hr_mode != "local":
                raise RuntimeError(f"checkpointer unavailable: {exc}") from exc
            log.warning("checkpointer unavailable (%s); local mode falls back to MemorySaver", exc)
            saver = MemorySaver()
        _checkpointer = saver
        _compiled = None


async def shutdown() -> None:
    global _checkpointer, _saver_cm, _compiled
    if _saver_cm is not None:
        await _saver_cm.__aexit__(None, None, None)
    _checkpointer = _saver_cm = _compiled = None


def _graph() -> Any:
    global _compiled
    if _compiled is None:
        g = StateGraph(TurnState)
        for name, fn in (
            ("understand", understand),
            ("policy", policy),
            ("plan", plan),
            ("act", act),
            ("generate", generate),
            ("verify", verify),
            ("answer", answer),
            ("abstain", abstain),
        ):
            g.add_node(name, fn)
        g.add_edge(START, "understand")
        g.add_edge("understand", "policy")
        g.add_conditional_edges("policy", route, ["plan", "abstain"])
        g.add_conditional_edges("plan", after_plan, ["act", "abstain"])
        g.add_conditional_edges("act", after_act, ["generate", END])
        g.add_conditional_edges("generate", after_generate, ["verify", "abstain"])
        g.add_conditional_edges("verify", after_verify, ["answer", "generate", "abstain"])
        for terminal in ("answer", "abstain"):
            g.add_edge(terminal, END)
        _compiled = g.compile(checkpointer=_checkpointer or MemorySaver())
    return _compiled


# ---------- helpers ----------
def _principal(state: TurnState) -> Principal:
    return Principal(**state["principal"])


def _ev(type_: str, **data: Any) -> dict[str, Any]:
    return {"type": type_, "data": data}


def _risk(tool: str) -> RiskLevel:
    """Unknown to the registry -> treated as read; run_tool's Cedar check refuses it anyway."""
    try:
        return _reg().get(tool).spec.risk
    except KeyError:
        return RiskLevel.read


async def _tool(
    state: TurnState, call: ToolCall, *, approved: bool = False
) -> tuple[ToolResult, dict[str, Any], dict[str, Any]]:
    """Run one tool through the harness door, charging the turn budget. Returns (result, event, budget)."""
    budget = TurnBudget(**state["budget"])
    budget.charge(tool_calls=1)
    res = await runner.run_tool(_principal(state), call, state["thread_id"], _reg(), approved=approved)
    ev = _ev(
        "tool",
        tool=res.tool,
        ok=res.ok,
        latency_ms=res.latency_ms,
        from_cache=res.from_cache,
        error=res.error,
    )
    return res, ev, asdict(budget)


def _evidence(state: TurnState) -> list[tuple[str, Citation]]:
    """What the model may see: facts first (the exact number with its source row), then retrieved chunks,
    each with its citation. Nothing else."""
    items: list[tuple[str, Citation]] = []
    for f in state.get("facts", []):
        prov = f.get("provenance") or {}
        text = f"{f['entity']} {f['concept']} for the period ending {f['period_end']}: {f['value']:,.2f} {f['unit']}."
        items.append(
            (
                text,
                Citation(
                    document_id=f["document_id"],
                    title=prov.get("title") or None,
                    uri=str(prov.get("uri", "")),
                    section=f"XBRL fact {f['concept']} {prov.get('fp') or ''} {prov.get('fy') or ''}".strip(),
                    version=prov.get("version") or prov.get("accn") or None,
                    snippet=text,
                ),
            )
        )
    for c in state.get("chunks", []):
        items.append(
            (
                c["content"],
                Citation(
                    document_id=c["document_id"],
                    chunk_id=c["id"],
                    title=c.get("title"),
                    uri=c.get("uri") or "",
                    section=c.get("section"),
                    version=c.get("version"),
                    snippet=c["content"][:200],
                ),
            )
        )
    return items


def _model_name() -> str:
    gw = _gw()
    return getattr(gw, "model_name", None) or str(getattr(gw, "models", {}).get(ModelRole.generate, type(gw).__name__))


async def _finish(state: TurnState, ans: Answer, node: str, **extra: Any) -> dict[str, Any]:
    payload = ans.model_dump(mode="json")
    await audit.record(
        "answer",
        _principal(state),
        {
            "abstained": ans.abstained,
            "citations": payload["citations"],
            "model": _model_name(),
            "grounded": ans.verification.grounded if ans.verification else None,
            "latency_ms": int((time.time() - state["started"]) * 1000),
            **extra,
        },
        state["thread_id"],
    )
    return {"node": node, "answer": payload, "events": [_ev("answer", **payload)]}


# ---------- nodes ----------
async def understand(state: TurnState) -> dict[str, Any]:
    q = await _gw().understand(state["question"])
    return {
        "node": "understand",
        "understood": q.model_dump(mode="json"),
        "events": [_ev("status", node="understand", message=f"intent={q.intent.value} entities={q.entities}")],
    }


async def policy(state: TurnState) -> dict[str, Any]:
    p = _principal(state)
    d = await runner.authorizer().decide(p, "answer", f"Org::{p.org_id}", {"thread_id": state["thread_id"]})
    dumped = d.model_dump(mode="json")
    return {"node": "policy", "policy": dumped, "events": [_ev("policy", **dumped)]}


def route(state: TurnState) -> str:
    return "plan" if state["policy"]["allow"] else "abstain"


async def plan(state: TurnState) -> dict[str, Any]:
    """The model picks tools from the ones this principal may use; the budget caps how many."""
    specs = _reg().specs_for(_principal(state))
    calls = await _gw().plan(state["question"], UnderstoodQuery(**state["understood"]), specs, None)
    budget = TurnBudget(**state["budget"])
    calls = calls[: max(budget.tool_calls - budget.tool_calls_used, 0)]
    names = [c.tool for c in calls]
    return {
        "node": "plan",
        "planned_tools": [c.model_dump(mode="json") for c in calls],
        "events": [_ev("status", node="plan", message=f"offered={[t.name for t in specs]} planned={names}")],
    }


def after_plan(state: TurnState) -> str:
    if not state["planned_tools"] and state["understood"]["intent"] == "out_of_scope":
        return "abstain"
    return "act"


async def hitl(state: TurnState, call: ToolCall) -> bool:
    """A write/money call stops here until approve() resumes the thread; the act node re-runs with the decision."""
    decision = interrupt(
        {"tool": call.tool, "arguments": call.arguments, "reason": "write tool: a human must approve before it runs"}
    )
    approved = isinstance(decision, dict) and bool(decision.get("approved"))
    note = decision.get("note") if isinstance(decision, dict) else None
    await audit.record(
        "hitl",
        _principal(state),
        {"tool": call.tool, "arguments": call.arguments, "approved": approved, "note": note},
        state["thread_id"],
    )
    return approved


def _fit(calls: list[ToolCall], remaining: int) -> list[ToolCall]:
    """Drop trailing facts_sql calls until the turn's tool-call budget holds them all; the search always stays."""
    drop = max(len(calls) - remaining, 0)
    kept: list[ToolCall] = []
    for c in reversed(calls):
        if drop and c.tool == "facts_sql":
            drop -= 1
            continue
        kept.append(c)
    return kept[::-1]


def _idem_key(thread_id: str, call: ToolCall) -> str:
    canon = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"), default=str)
    return f"{thread_id}:{call.tool}:{hashlib.sha256(canon.encode()).hexdigest()[:16]}"


async def act(state: TurnState) -> dict[str, Any]:
    """Run the planned calls in order through the harness door; collect evidence and write outcomes."""
    calls = [ToolCall(**c) for c in state["planned_tools"]]
    u = state["understood"]
    if not any(c.tool == "search_documents" for c in calls) and u["intent"] != "out_of_scope":
        # A question is never answered without a retrieval attempt, whatever the model planned.
        calls.append(
            ToolCall(
                tool="search_documents",
                arguments={"query": u["original"], "rewritten": u["rewritten"], "intent": u["intent"]},
            )
        )
    b0 = TurnBudget(**state["budget"])
    calls = _fit(calls, b0.tool_calls - b0.tool_calls_used)
    # Approvals first, so a resume re-running this node never repeats a read; the human's wait is not turn time.
    gated = {i: await hitl(state, c) for i, c in enumerate(calls) if _risk(c.tool) in HITL_RISKS}
    budget = {**state["budget"], "deadline": TurnBudget().deadline} if gated else state["budget"]
    events: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    outcomes: list[tuple[str, bool]] = []
    for i, call in enumerate(calls):
        if i in gated and not gated[i]:
            events.append(_ev("tool", tool=call.tool, ok=False, latency_ms=0, from_cache=False, error="denied"))
            outcomes.append((f"{call.tool} was not approved; nothing was changed.", False))
            continue
        if call.tool == "search_documents":
            call.arguments.setdefault("thread_id", state["thread_id"])
        if i in gated:
            call.idempotency_key = call.idempotency_key or _idem_key(state["thread_id"], call)
        res, ev, budget = await _tool({**state, "budget": budget}, call, approved=i in gated)
        events.append(ev)
        if call.tool == "search_documents":
            got = res.output["chunks"] if res.ok else []
            chunks.extend(got)
            summary = [
                {
                    "document_id": c["document_id"],
                    "title": c.get("title"),
                    "section": c.get("section"),
                    "score": c.get("score"),
                }
                for c in got
            ]
            events.append(
                _ev("retrieval", candidate_count=res.output.get("candidate_count", 0) if res.ok else 0, chunks=summary)
            )
        elif call.tool == "facts_sql" and res.ok:
            found.extend(res.output["facts"])
        elif i in gated:
            text = f"{call.tool} done: {json.dumps(res.output)}" if res.ok else f"{call.tool} failed: {res.error}"
            outcomes.append((text, res.ok))
    out: dict[str, Any] = {
        "node": "act",
        "chunks": chunks,
        "facts": found,
        "budget": budget,
        "events": [_ev("status", node="act", message=f"ran={[c.tool for c in calls]}"), *events],
    }
    if outcomes and not _evidence({**state, "chunks": chunks, "facts": found}):
        # A pure action turn: the write outcome IS the answer; there is nothing for the model to ground.
        ok = all(k for _, k in outcomes)
        ans = Answer(
            text=" ".join(t for t, _ in outcomes),
            citations=[],
            confidence=1.0 if ok else 0.0,
            abstained=not ok,
            thread_id=state["thread_id"],
        )
        fin = await _finish(state, ans, "act", tools=[c.tool for c in calls], ok=ok)
        out = {**out, **fin, "events": [*out["events"], *fin["events"]]}
    return out


def after_act(state: TurnState) -> str:
    return END if state.get("answer") else "generate"


async def generate(state: TurnState) -> dict[str, Any]:
    evidence = _evidence(state)
    if not evidence:  # no authorized evidence -> the model is never called
        return {
            "node": "generate",
            "draft": "",
            "events": [_ev("status", node="generate", message="no authorized evidence; abstaining")],
        }
    system = GENERATE_SYSTEM
    unsupported = (state.get("verification") or {}).get("unsupported_claims") or []
    if unsupported:
        system += "\nYour previous draft made claims the evidence does not support; drop them: " + " | ".join(
            unsupported
        )
    prompt = f"QUESTION: {state['question']}\n\nEVIDENCE:\n{format_evidence([t for t, _ in evidence])}"
    text, usage = await _gw().complete(ModelRole.generate, system, [{"role": "user", "content": prompt}])
    budget = TurnBudget(**state["budget"])
    budget.charge(tokens=usage.input_tokens + usage.output_tokens)
    return {
        "node": "generate",
        "draft": text.strip(),
        "attempts": state["attempts"] + 1,
        "budget": asdict(budget),
        "events": [_ev("token", text=text)],
    }


def after_generate(state: TurnState) -> str:
    return "verify" if state["draft"] and state["draft"] != ABSTAIN_TEXT else "abstain"


async def verify(state: TurnState) -> dict[str, Any]:
    texts = [t for t, _ in _evidence(state)]
    prompt = f"ANSWER:\n{state['draft']}\n\nEVIDENCE:\n{format_evidence(texts)}"
    text, usage = await _gw().complete(
        ModelRole.verify,
        VERIFY_SYSTEM,
        [{"role": "user", "content": prompt}],
        max_tokens=400,
        json_schema=Verification.model_json_schema(),
    )
    try:
        parsed = extract_json(text)
        v = Verification(
            grounded=bool(parsed.get("grounded")),
            confidence=float(parsed.get("confidence", 0)),
            unsupported_claims=[str(c) for c in parsed.get("unsupported_claims", [])],
            verifier_model=usage.model,
        )
    except Exception as exc:  # an unparsable verifier is a failed verification, never a pass
        v = Verification(
            grounded=False,
            confidence=0.0,
            unsupported_claims=[f"verifier output unreadable: {exc}"],
            verifier_model=usage.model,
        )
    budget = TurnBudget(**state["budget"])
    budget.charge(tokens=usage.input_tokens + usage.output_tokens)
    return {
        "node": "verify",
        "verification": v.model_dump(),
        "budget": asdict(budget),
        "events": [_ev("status", node="verify", message=f"grounded={v.grounded} confidence={v.confidence}")],
    }


def after_verify(state: TurnState) -> str:
    if state["verification"]["grounded"]:
        return "answer"
    return "generate" if state["attempts"] < MAX_GENERATE_ATTEMPTS else "abstain"


async def answer(state: TurnState) -> dict[str, Any]:
    evidence = _evidence(state)
    cited = sorted({int(n) for n in _CITE.findall(state["draft"]) if 0 < int(n) <= len(evidence)})
    citations = [evidence[i - 1][1] for i in cited] or [c for _, c in evidence]
    v = Verification(**state["verification"])
    ans = Answer(
        text=state["draft"],
        citations=citations,
        confidence=v.confidence,
        verification=v,
        thread_id=state["thread_id"],
    )
    return await _finish(state, ans, "answer")


async def abstain(state: TurnState) -> dict[str, Any]:
    if not state["policy"]["allow"]:
        reason = "policy_denied"
    elif state["understood"]["intent"] == "out_of_scope":
        reason = "out_of_scope"
    elif not _evidence(state):
        reason = "no_authorized_evidence"
    else:
        reason = "not_grounded"
    v = Verification(**state["verification"]) if state.get("verification") else None
    ans = Answer(
        text=ABSTAIN_TEXT,
        citations=[],
        confidence=0.0,
        abstained=True,
        verification=v,
        thread_id=state["thread_id"],
    )
    return await _finish(state, ans, "abstain", reason=reason)


# ---------- entry points (docs/CONTRACTS.md) ----------
def _initial(principal: Principal, question: str, thread_id: str) -> TurnState:
    return {
        "principal": principal.model_dump(mode="json"),
        "question": question,
        "thread_id": thread_id,
        "started": time.time(),
        "understood": {},
        "policy": {},
        "verification": {},
        "chunks": [],
        "facts": [],
        "draft": "",
        "answer": None,
        "planned_tools": [],
        "attempts": 0,
        "budget": asdict(TurnBudget()),
        "events": [],
    }


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


async def _assert_owner(principal: Principal, thread_id: str) -> Any:
    """A thread belongs to the principal who started it; anyone else gets PermissionError (API: 403)."""
    snap = await _graph().aget_state(_config(thread_id))
    owner = (snap.values or {}).get("principal")
    if owner and (owner["user_id"] != principal.user_id or owner["org_id"] != str(principal.org_id)):
        raise PermissionError(f"thread {thread_id} belongs to another principal")
    return snap


def _to_events(update: dict[str, Any]) -> Iterator[TurnEvent]:
    for node, delta in update.items():
        if node == "__interrupt__":
            for intr in delta:
                yield TurnEvent(type="hitl", data=intr.value)
        else:
            for e in (delta or {}).get("events", []):
                yield TurnEvent(**e)


async def _stream(inp: Any, thread_id: str) -> AsyncIterator[TurnEvent]:
    try:
        async for update in _graph().astream(inp, _config(thread_id), stream_mode="updates"):
            for ev in _to_events(update):
                yield ev
    except BudgetExceeded as exc:
        yield TurnEvent(type="error", data={"code": "budget_exceeded", "message": str(exc)})
    except Exception as exc:
        log.exception("turn failed thread=%s", thread_id)
        yield TurnEvent(type="error", data={"code": type(exc).__name__, "message": str(exc)})


async def run_turn(principal: Principal, question: str, thread_id: str) -> Answer:
    await _assert_owner(principal, thread_id)
    result = await _graph().ainvoke(_initial(principal, question, thread_id), _config(thread_id))
    if result.get("answer"):
        return Answer(**result["answer"])
    snap = await _graph().aget_state(_config(thread_id))
    pending: dict[str, Any] = next((i.value for t in snap.tasks for i in t.interrupts), {})
    return Answer(
        text=f"Approval required for {pending.get('tool')}; resume with approve().",
        citations=[],
        confidence=0.0,
        abstained=True,
        thread_id=thread_id,
    )


async def stream_turn(principal: Principal, question: str, thread_id: str) -> AsyncIterator[TurnEvent]:
    await _assert_owner(principal, thread_id)
    async for ev in _stream(_initial(principal, question, thread_id), thread_id):
        yield ev


async def approve(principal: Principal, thread_id: str, approved: bool, note: str | None) -> AsyncIterator[TurnEvent]:
    snap = await _assert_owner(principal, thread_id)
    if not any(t.interrupts for t in snap.tasks):
        yield TurnEvent(type="error", data={"code": "nothing_pending", "message": "no approval is pending"})
        return
    # The write runs as the approver is NOW (roles may have changed while the thread waited), never as saved.
    resume: Any = Command(
        resume={"approved": approved, "note": note}, update={"principal": principal.model_dump(mode="json")}
    )
    async for ev in _stream(resume, thread_id):
        yield ev


async def replay(principal: Principal, thread_id: str) -> list[dict]:
    await _assert_owner(principal, thread_id)
    out: list[dict] = []
    async for snap in _graph().aget_state_history(_config(thread_id)):
        v = snap.values or {}
        out.append(
            {
                "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                "step": snap.metadata.get("step") if snap.metadata else None,
                "node": v.get("node") or "__start__",
                "state_summary": {
                    "intent": (v.get("understood") or {}).get("intent"),
                    "allowed": (v.get("policy") or {}).get("allow"),
                    "chunks": len(v.get("chunks") or []),
                    "facts": len(v.get("facts") or []),
                    "attempts": v.get("attempts", 0),
                    "planned_tools": [c["tool"] for c in v.get("planned_tools") or []],
                    "answered": bool(v.get("answer")),
                    "abstained": (v.get("answer") or {}).get("abstained"),
                    "next": list(snap.next),
                },
                "at": snap.created_at,
            }
        )
    out.reverse()
    return out
