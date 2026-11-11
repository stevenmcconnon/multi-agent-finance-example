"""L6: one turn as a LangGraph StateGraph. This module is the SUPERVISOR: deterministic routing between the
agents in team.py, checkpointed and replayable; every node is a thin call into the agent that owns the work.

  understand -> policy -> plan -> act (interrupt for write/money tools) -> generate -> verify -> answer | abstain

The team (docs/CONTRACTS.md "The agent team"):
  understand  Planner    (model)          classify the question (gateway.understand)
  policy      Guard      (deterministic)  Cedar: may this principal get an answer at all; deny -> abstain
  plan        Planner    (model)          picks tools (gateway.plan, Bedrock Converse tool use) from
                                          `registry.specs_for(principal)`, so it only ever sees tools this
                                          principal's roles may use; capped at the turn's tool-call budget.
                                          An action request for a write tool NOT on that menu is planned here
                                          without the model, so the door refuses it (Cedar, audited)
  act         Researcher (deterministic)  every planned call goes through `harness.runner.run_tool` (Cedar,
                                          idempotency, breaker, audit); facts_sql/search_documents results
                                          become the evidence. A figure is answered from facts (the search is
                                          the fallback when none come back); two periods of one concept get
                                          their change from the calculator tool
              Approver   (human)          a write/money call on the caller's menu first suspends the turn with
                                          interrupt(), resumed by approve(); denied calls are skipped with a
                                          `tool` event ok=False error="denied"
  generate    Writer     (model)          answer from the evidence only; no evidence -> the model is never called
  verify      Judge      (model)          second model pass; ungrounded -> one regeneration, then abstain
  answer | abstain       (supervisor)     cited Answer | abstain. Every Answer carries `reason` and a sentence
                                          a person can read for it (ABSTAIN_MESSAGES); the reason is audited

Everything the model sees came back from `run_tool`. Models are reached through gateway/, Postgres only
through the checkpointer; nothing here imports a vendor SDK. Entry points (run_turn, stream_turn, approve,
replay) are pinned in docs/CONTRACTS.md.

In production: AsyncPostgresSaver on the control-plane database; Bedrock proposes the tool calls.
In this demo: the same saver (MemorySaver injectable for offline tests); with HR_MODEL_BACKEND=fake the
plan is a deterministic heuristic (gateway/fake.py `plan_heuristic`), because the offline stub is not a model.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..authz import audit
from ..config import settings
from ..contracts import Answer, AnswerReason, Citation, ModelRole, Principal, TurnEvent, Verification
from ..gateway import ABSTAIN_TEXT, get_gateway, take_notes
from ..harness.budget import BudgetExceeded, TurnBudget
from ..harness.registry import ToolRegistry
from ..harness.tools import default_registry
from ..interfaces import ModelGateway, ModelRefused
from .team import Approver, Guard, Judge, Planner, Researcher, Writer, principal_of, status
from .team import ev as _ev
from .team import evidence as _evidence

log = logging.getLogger(__name__)

MAX_GENERATE_ATTEMPTS = 2
_CITE = re.compile(r"\[(\d+)\]")
# What the reader is told when the turn does not answer, per Answer.reason (docs/CONTRACTS.md).
ABSTAIN_MESSAGES = {
    "no_relevant_evidence": "None of the documents you are allowed to read answer that question.",
    "out_of_scope": (
        "That is not a question about the filings. Ask about a company's reports, or try one of the examples."
    ),
    "not_grounded": "A draft answer was written but failed verification against the evidence, so it was withheld.",
    "policy_denied": "Your account is not permitted to ask questions here.",
    "model_declined": "The model declined to answer this request.",
}


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
    offered: list[str]  # the tool names on this principal's menu at plan time
    declined: bool  # the model refused (gateway raised ModelRefused)
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
def _notes() -> str:
    """Anything the gateway did instead of the model call this node asked for (a deterministic fallback after
    a model error), appended to the node's status message so the screen never claims a step that did not happen."""
    return "".join(f"; {n}" for n in take_notes())


def _model_name() -> str:
    gw = _gw()
    return getattr(gw, "model_name", None) or str(getattr(gw, "models", {}).get(ModelRole.generate, type(gw).__name__))


async def _finish(state: TurnState, ans: Answer, node: str, **extra: Any) -> dict[str, Any]:
    payload = ans.model_dump(mode="json")
    await audit.record(
        "answer",
        principal_of(state),
        {
            "abstained": ans.abstained,
            "citations": payload["citations"],
            "model": _model_name(),
            "grounded": ans.verification.grounded if ans.verification else None,
            "latency_ms": int((time.time() - state["started"]) * 1000),
            "reason": ans.reason,
            **extra,
        },
        state["thread_id"],
    )
    return {"node": node, "answer": payload, "events": [_ev("answer", **payload)]}


# ---------- nodes: each one hands the work to its agent ----------
async def understand(state: TurnState) -> dict[str, Any]:
    planner = Planner(_gw(), _reg())
    q = await planner.understand(state["question"])
    message = f"intent={q.intent.value} entities={q.entities}" + _notes()
    return {
        "node": "understand",
        "understood": q.model_dump(mode="json"),
        "events": [status(planner, "understand", message)],
    }


async def policy(state: TurnState) -> dict[str, Any]:
    guard = Guard()
    dumped = await guard.decide(state)
    return {
        "node": "policy",
        "policy": dumped,
        "events": [_ev("policy", **dumped)],  # the one Guard row; a status event would say the same thing twice
    }


def route(state: TurnState) -> str:
    return "plan" if state["policy"]["allow"] else "abstain"


async def plan(state: TurnState) -> dict[str, Any]:
    planner = Planner(_gw(), _reg())
    calls, offered = await planner.plan(state)
    events = [status(planner, "plan", f"offered={offered} planned={[c.tool for c in calls]}" + _notes())]
    if state["understood"]["intent"] == "action" and (
        refused := Researcher(_reg(), Approver()).door_calls(state["question"], offered)
    ):
        # The door refuses, never the menu: the request still reaches run_tool, whose Cedar check says no.
        calls = refused
        roles = principal_of(state).roles
        events.append(
            status(
                Guard(),
                "plan",
                f"{[c.tool for c in calls]} not offered to roles {roles}; sent to the policy door to be decided",
            )
        )
    return {
        "node": "plan",
        "planned_tools": [c.model_dump(mode="json") for c in calls],
        "offered": offered,
        "events": events,
    }


def after_plan(state: TurnState) -> str:
    if not state["planned_tools"] and state["understood"]["intent"] in ("out_of_scope", "action"):
        return "abstain"
    return "act"


async def act(state: TurnState) -> dict[str, Any]:
    out, outcomes = await Researcher(_reg(), Approver()).research(state)
    if outcomes and not _evidence({**state, "chunks": out["chunks"], "facts": out["facts"]}):
        # A pure action turn: the write outcome IS the answer; there is nothing for the model to ground.
        reason: AnswerReason = next((r for _, r in outcomes if r != "action_done"), "action_done")
        ok = reason == "action_done"
        ans = Answer(
            text=" ".join(t for t, _ in outcomes),
            citations=[],
            confidence=1.0 if ok else 0.0,
            abstained=not ok,
            reason=reason,
            thread_id=state["thread_id"],
        )
        ran = [e["data"]["tool"] for e in out["events"] if e["type"] == "tool"]
        fin = await _finish(state, ans, "act", tools=ran, ok=ok)
        out = {**out, **fin, "events": [*out["events"], *fin["events"]]}
    return out


def after_act(state: TurnState) -> str:
    return END if state.get("answer") else "generate"


async def generate(state: TurnState) -> dict[str, Any]:
    writer = Writer(_gw())
    items = _evidence(state)
    if not items:  # no authorized evidence -> the model is never called
        return {
            "node": "generate",
            "draft": "",
            "events": [status(writer, "generate", "no evidence this reader may see; the model was not called")],
        }
    try:
        text, tokens = await writer.draft(state, items)
    except ModelRefused as exc:
        return {
            "node": "generate",
            "draft": "",
            "declined": True,
            "events": [status(writer, "generate", f"model declined: {exc}")],
        }
    budget = TurnBudget(**state["budget"])
    budget.charge(tokens=tokens)
    return {
        "node": "generate",
        "draft": text.strip(),
        "attempts": state["attempts"] + 1,
        "budget": asdict(budget),
        "events": [
            status(writer, "generate", f"evidence={len(items)} attempt={state['attempts'] + 1}"),
            _ev("token", text=text),
        ],
    }


def after_generate(state: TurnState) -> str:
    return "verify" if state["draft"] and state["draft"] != ABSTAIN_TEXT else "abstain"


async def verify(state: TurnState) -> dict[str, Any]:
    judge = Judge(_gw())
    try:
        v, tokens = await judge.verify(state, _evidence(state))
    except ModelRefused as exc:
        return {"node": "verify", "declined": True, "events": [status(judge, "verify", f"model declined: {exc}")]}
    budget = TurnBudget(**state["budget"])
    budget.charge(tokens=tokens)
    return {
        "node": "verify",
        "verification": v.model_dump(),
        "budget": asdict(budget),
        "events": [status(judge, "verify", f"grounded={v.grounded} confidence={v.confidence}")],
    }


def after_verify(state: TurnState) -> str:
    if state.get("declined"):
        return "abstain"
    if state["verification"]["grounded"]:
        return "answer"
    return "generate" if state["attempts"] < MAX_GENERATE_ATTEMPTS else "abstain"


def number_citations(draft: str, evidence: list[tuple[str, Citation]]) -> tuple[str, list[Citation]]:
    """The reader's view of the citations: markers renumbered 1..n in the order the answer uses them (the Writer
    cites evidence by its position in the prompt, so a draft can say [7] and [8] with only two sources), and each
    source's snippet set to the sentence that cites it when that sentence is a quotation of the source."""
    order: list[int] = []
    for n in _CITE.findall(draft):
        if 0 < int(n) <= len(evidence) and int(n) not in order:
            order.append(int(n))
    if not order:
        return draft, [c for _, c in evidence]
    new = {old: i for i, old in enumerate(order, start=1)}
    quoted: dict[int, str] = {}
    pos = 0
    for m in _CITE.finditer(draft):
        said, pos = draft[pos : m.start()].strip(), m.end()
        old_n = int(m.group(1))
        if old_n in new and old_n not in quoted and said and said in " ".join(evidence[old_n - 1][0].split()):
            quoted[old_n] = said[:300]
    text = _CITE.sub(lambda m: f"[{new[int(m.group(1))]}]" if int(m.group(1)) in new else "", draft)
    citations = [
        evidence[o - 1][1].model_copy(update={"snippet": quoted[o]}) if o in quoted else evidence[o - 1][1]
        for o in order
    ]
    return text, citations


async def answer(state: TurnState) -> dict[str, Any]:
    text, citations = number_citations(state["draft"], _evidence(state))
    v = Verification(**state["verification"])
    ans = Answer(
        text=text,
        citations=citations,
        confidence=v.confidence,
        reason="answered",
        verification=v,
        thread_id=state["thread_id"],
    )
    return await _finish(state, ans, "answer")


async def abstain(state: TurnState) -> dict[str, Any]:
    reason: AnswerReason
    if not state["policy"]["allow"]:
        reason = "policy_denied"
    elif state["understood"]["intent"] in ("out_of_scope", "action"):  # action: nothing this assistant can do
        reason = "out_of_scope"
    elif state.get("declined"):
        reason = "model_declined"
    elif not _evidence(state) or state["draft"] in ("", ABSTAIN_TEXT):  # nothing readable, or the Writer's sentinel
        reason = "no_relevant_evidence"
    else:
        reason = "not_grounded"
    v = Verification(**state["verification"]) if state.get("verification") else None
    ans = Answer(
        text=ABSTAIN_MESSAGES[reason],
        citations=[],
        confidence=0.0,
        abstained=True,
        reason=reason,
        verification=v,
        thread_id=state["thread_id"],
    )
    return await _finish(state, ans, "abstain")


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
        "offered": [],
        "declined": False,
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
        reason="approval_pending",
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
