"""The agent team (docs/CONTRACTS.md "The agent team"). graph.py is the Supervisor that routes between them.

  Guard       deterministic  Cedar `answer` decision, fail closed            (node policy)
  Planner     model          understand the question, pick tools            (nodes understand, plan)
  Researcher  deterministic  run the plan through run_tool, collect evidence (node act)
  Approver    human          approve/deny write/money tools via interrupt   (inside act)
  Writer      model          grounded draft from the evidence only          (node generate)
  Judge       model          verify every claim; second model role          (node verify)

Each agent is one small class with `name`, `kind` and the one method its node calls. The prompts live on the
agent that owns them. Deterministic agents never see the gateway (tests/test_architecture.py pins this).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from langgraph.types import interrupt

from ..authz import audit
from ..contracts import (
    Citation,
    ModelRole,
    Principal,
    RiskLevel,
    ToolCall,
    ToolResult,
    UnderstoodQuery,
    Verification,
)
from ..gateway import ABSTAIN_TEXT, format_evidence
from ..gateway.bedrock import extract_json
from ..harness import runner
from ..harness.budget import TurnBudget
from ..harness.registry import ToolRegistry
from ..interfaces import ModelGateway

if TYPE_CHECKING:
    from .graph import TurnState

Kind = Literal["deterministic", "model", "human"]
HITL_RISKS = {RiskLevel.write, RiskLevel.money}


def ev(type_: str, **data: Any) -> dict[str, Any]:
    return {"type": type_, "data": data}


def status(agent: Guard | Planner | Researcher | Writer | Judge, node: str, message: str) -> dict[str, Any]:
    return ev("status", node=node, agent=agent.name, kind=agent.kind, message=message)


def principal_of(state: TurnState) -> Principal:
    return Principal(**state["principal"])


def evidence(state: TurnState) -> list[tuple[str, Citation]]:
    """The Researcher's product, what the model may see: facts first (the exact number with its source row),
    then retrieved chunks, each with its citation. Nothing else."""
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


class Guard:
    """Deterministic: may this principal get an answer at all (Cedar `answer` on Org::<org>), fail closed.

    Identity is already a Principal by the time the graph runs: the API verified the token and resolved roles
    and groups before run_turn/stream_turn; the Guard never reads a header or a prompt."""

    name: ClassVar[str] = "Guard"
    kind: ClassVar[Kind] = "deterministic"

    async def decide(self, state: TurnState) -> dict[str, Any]:
        p = principal_of(state)
        d = await runner.authorizer().decide(p, "answer", f"Org::{p.org_id}", {"thread_id": state["thread_id"]})
        return d.model_dump(mode="json")


class Planner:
    """Model: understands the question (role `understand`) and picks tools (role `plan`, Bedrock Converse tool
    use) from the specs this principal's roles may use; the turn budget caps how many."""

    name: ClassVar[str] = "Planner"
    kind: ClassVar[Kind] = "model"

    def __init__(self, gateway: ModelGateway, registry: ToolRegistry) -> None:
        self.gateway, self.registry = gateway, registry

    async def understand(self, question: str) -> UnderstoodQuery:
        return await self.gateway.understand(question)

    async def plan(self, state: TurnState) -> tuple[list[ToolCall], list[str]]:
        """Returns (planned calls, names of the tools offered)."""
        specs = self.registry.specs_for(principal_of(state))
        calls = await self.gateway.plan(state["question"], UnderstoodQuery(**state["understood"]), specs)
        budget = TurnBudget(**state["budget"])
        return calls[: max(budget.tool_calls - budget.tool_calls_used, 0)], [t.name for t in specs]


class Approver:
    """Human: a write/money call stops at interrupt() until approve() resumes the thread with the decision."""

    name: ClassVar[str] = "Approver"
    kind: ClassVar[Kind] = "human"

    async def ask(self, state: TurnState, call: ToolCall) -> bool:
        decision = interrupt(
            {
                "tool": call.tool,
                "arguments": call.arguments,
                "reason": "write tool: a human must approve before it runs",
            }
        )
        approved = isinstance(decision, dict) and bool(decision.get("approved"))
        note = decision.get("note") if isinstance(decision, dict) else None
        await audit.record(
            "hitl",
            principal_of(state),
            {"tool": call.tool, "arguments": call.arguments, "approved": approved, "note": note},
            state["thread_id"],
        )
        return approved


class Researcher:
    """Deterministic, no model: runs the planned calls in order through `harness.runner.run_tool` (Cedar,
    idempotency, breaker, audit), fits them to the budget, asks the Approver before a write/money call and
    collects the evidence (facts, chunks) plus the write outcomes."""

    name: ClassVar[str] = "Researcher"
    kind: ClassVar[Kind] = "deterministic"

    def __init__(self, registry: ToolRegistry, approver: Approver) -> None:
        self.registry, self.approver = registry, approver

    def _risk(self, tool: str) -> RiskLevel:
        """Unknown to the registry -> treated as read; run_tool's Cedar check refuses it anyway."""
        try:
            return self.registry.get(tool).spec.risk
        except KeyError:
            return RiskLevel.read

    @staticmethod
    def _fit(calls: list[ToolCall], remaining: int) -> list[ToolCall]:
        """Drop trailing calls until the turn's tool-call budget holds them all; the search always stays."""
        drop = max(len(calls) - remaining, 0)
        kept: list[ToolCall] = []
        for c in reversed(calls):
            if drop and c.tool != "search_documents":
                drop -= 1
                continue
            kept.append(c)
        return kept[::-1]

    @staticmethod
    def _idem_key(thread_id: str, call: ToolCall) -> str:
        canon = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"), default=str)
        return f"{thread_id}:{call.tool}:{hashlib.sha256(canon.encode()).hexdigest()[:16]}"

    async def _tool(
        self, state: TurnState, call: ToolCall, *, approved: bool
    ) -> tuple[ToolResult, dict[str, Any], dict[str, Any]]:
        """Run one tool through the harness door, charging the turn budget. Returns (result, event, budget)."""
        budget = TurnBudget(**state["budget"])
        budget.charge(tool_calls=1)
        res = await runner.run_tool(principal_of(state), call, state["thread_id"], self.registry, approved=approved)
        e = ev("tool", tool=res.tool, ok=res.ok, latency_ms=res.latency_ms, from_cache=res.from_cache, error=res.error)
        return res, e, asdict(budget)

    async def research(self, state: TurnState) -> tuple[dict[str, Any], list[tuple[str, bool]]]:
        """Returns (state update for the act node, write outcomes as (text, ok))."""
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
        calls = self._fit(calls, b0.tool_calls - b0.tool_calls_used)
        # Approvals first, so a resume re-running this node never repeats a read; the human's wait is not turn time.
        gated = {i: await self.approver.ask(state, c) for i, c in enumerate(calls) if self._risk(c.tool) in HITL_RISKS}
        budget = {**state["budget"], "deadline": TurnBudget().deadline} if gated else state["budget"]
        events: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        found: list[dict[str, Any]] = []
        outcomes: list[tuple[str, bool]] = []
        for i, call in enumerate(calls):
            if i in gated and not gated[i]:
                events.append(ev("tool", tool=call.tool, ok=False, latency_ms=0, from_cache=False, error="denied"))
                outcomes.append((f"{call.tool} was not approved; nothing was changed.", False))
                continue
            if call.tool == "search_documents":
                call.arguments.setdefault("thread_id", state["thread_id"])
            if i in gated:
                call.idempotency_key = call.idempotency_key or self._idem_key(state["thread_id"], call)
            res, e, budget = await self._tool({**state, "budget": budget}, call, approved=i in gated)
            events.append(e)
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
                    ev(
                        "retrieval",
                        candidate_count=res.output.get("candidate_count", 0) if res.ok else 0,
                        chunks=summary,
                    )
                )
            elif call.tool == "facts_sql" and res.ok:
                found.extend(res.output["facts"])
            elif i in gated:
                text = f"{call.tool} done: {json.dumps(res.output)}" if res.ok else f"{call.tool} failed: {res.error}"
                outcomes.append((text, res.ok))
        update = {
            "node": "act",
            "chunks": chunks,
            "facts": found,
            "budget": budget,
            "events": [status(self, "act", f"ran={[c.tool for c in calls]}"), *events],
        }
        return update, outcomes


class Writer:
    """Model (role `generate`): a draft from the numbered evidence only, every sentence cited by index."""

    name: ClassVar[str] = "Writer"
    kind: ClassVar[Kind] = "model"
    SYSTEM = (
        "Answer the question using ONLY the numbered evidence. Cite every sentence with its evidence index "
        f"like [1]. If the evidence does not answer the question, reply exactly: {ABSTAIN_TEXT}"
    )

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    async def draft(self, state: TurnState, items: list[tuple[str, Citation]]) -> tuple[str, int]:
        """Returns (draft text, tokens used). The caller guarantees `items` is non-empty."""
        system = self.SYSTEM
        unsupported = (state.get("verification") or {}).get("unsupported_claims") or []
        if unsupported:
            system += "\nYour previous draft made claims the evidence does not support; drop them: " + " | ".join(
                unsupported
            )
        prompt = f"QUESTION: {state['question']}\n\nEVIDENCE:\n{format_evidence([t for t, _ in items])}"
        text, usage = await self.gateway.complete(ModelRole.generate, system, [{"role": "user", "content": prompt}])
        return text, usage.input_tokens + usage.output_tokens


class Judge:
    """Model (role `verify`, the second, cheaper model): checks every sentence of the draft against the evidence.
    An unreadable verdict is a failed verification, never a pass."""

    name: ClassVar[str] = "Judge"
    kind: ClassVar[Kind] = "model"
    SYSTEM = (
        "Check every sentence of ANSWER against EVIDENCE. Reply with JSON only: "
        '{"grounded": bool, "confidence": number 0..1, "unsupported_claims": [str]}'
    )

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    async def verify(self, state: TurnState, items: list[tuple[str, Citation]]) -> tuple[Verification, int]:
        """Returns (verification, tokens used)."""
        prompt = f"ANSWER:\n{state['draft']}\n\nEVIDENCE:\n{format_evidence([t for t, _ in items])}"
        text, usage = await self.gateway.complete(
            ModelRole.verify,
            self.SYSTEM,
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
        except Exception as exc:
            v = Verification(
                grounded=False,
                confidence=0.0,
                unsupported_claims=[f"verifier output unreadable: {exc}"],
                verifier_model=usage.model,
            )
        return v, usage.input_tokens + usage.output_tokens
