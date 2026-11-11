"""FakeGateway: deterministic, offline. Answers are built ONLY from the evidence in the prompt.

Prompt protocol shared with agent/graph.py and bedrock.py: evidence arrives as numbered lines
"[n] <text>" (see `format_evidence`); the verify role receives "ANSWER:\n...\n\nEVIDENCE:\n[n] ...".
"""

from __future__ import annotations

import json
import re

from ..contracts import ModelRole, ModelUsage, QueryIntent, ToolCall, ToolSpec, UnderstoodQuery

ABSTAIN_TEXT = "I can't answer that from the documents you have access to."

KNOWN_TICKERS = {"AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "IBM", "ORCL", "INTC"}
NUMERIC_WORDS = ("revenue", "net income", "how much", "total", "percent", "change", "margin", "eps")
_TICKER = re.compile(r"\b[A-Z]{1,5}\b")
_EVIDENCE_LINE = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)
_CITE = re.compile(r"\s*\[\d+\]")
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_TICKET = re.compile(
    r"^\s*(?:please\s+)?(?:create|open|file)\s+(?:a\s+)?ticket\b[:\s-]*(?P<title>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def extract_json(text: str) -> dict:
    """The first {...} block in a model reply, parsed."""
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ValueError("model returned no JSON object")
    return json.loads(m.group(0))


def format_evidence(texts: list[str]) -> str:
    return "\n".join(f"[{i}] {' '.join(t.split())}" for i, t in enumerate(texts, start=1))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def understand_heuristic(question: str) -> UnderstoodQuery:
    q = question.strip()
    if not q or not (
        q.endswith("?")
        or re.match(r"^(what|how|when|who|why|which|where|did|does|is|are|list|show|tell)\b", q, re.IGNORECASE)
    ):
        return UnderstoodQuery(original=question, rewritten=q, intent=QueryIntent.out_of_scope)
    entities = sorted({t for t in _TICKER.findall(q) if t in KNOWN_TICKERS or len(t) >= 2 and t not in {"US", "SEC"}})
    lower = q.lower()
    numeric = any(w in lower for w in NUMERIC_WORDS)
    concepts = [c for w, c in (("revenue", "Revenues"), ("net income", "NetIncomeLoss")) if w in lower]
    year = re.search(r"\b(20\d{2})\b", q)
    return UnderstoodQuery(
        original=question,
        rewritten=q.rstrip("?"),
        intent=QueryIntent.numeric if numeric else QueryIntent.lookup,
        entities=entities,
        concepts=concepts,
        period_hint=year.group(1) if year else None,
    )


def plan_heuristic(question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
    """What a model would propose: facts for numeric intents, a ticket on request, else search. Never a
    tool outside `tools`; the harness (Cedar in run_tool) still decides whether a call may run."""
    offered = {t.name for t in tools}
    if (m := _TICKET.match(question)) and "create_ticket" in offered:
        title = re.sub(r"^(?:to|for)\s+", "", m.group("title").strip(), flags=re.IGNORECASE)
        return [ToolCall(tool="create_ticket", arguments={"title": title[:200]})]
    calls: list[ToolCall] = []
    if understood.intent == QueryIntent.numeric and "facts_sql" in offered:
        calls += [
            ToolCall(tool="facts_sql", arguments={"entity": e, "concept": c})
            for e in understood.entities
            for c in understood.concepts
        ]
    if understood.intent != QueryIntent.out_of_scope and "search_documents" in offered:
        calls.append(
            ToolCall(
                tool="search_documents",
                arguments={
                    "query": understood.original,
                    "rewritten": understood.rewritten,
                    "intent": understood.intent.value,
                },
            )
        )
    return calls


class FakeGateway:
    model_name = "fake"

    async def understand(self, question: str) -> UnderstoodQuery:
        return understand_heuristic(question)

    async def plan(self, question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
        return plan_heuristic(question, understood, tools)

    async def complete(
        self,
        role: ModelRole,
        system: str,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> tuple[str, ModelUsage]:
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        usage = ModelUsage(input_tokens=len(prompt) // 4, model=self.model_name)
        if role == ModelRole.verify:
            answer_part, _, evidence_part = prompt.partition("EVIDENCE:")
            answer = answer_part.replace("ANSWER:", "").strip()
            evidence = " ".join(" ".join(t.split()) for _, t in _EVIDENCE_LINE.findall(evidence_part))
            unsupported = [
                s for s in _sentences(_CITE.sub("", answer)) if s and s not in evidence and s != ABSTAIN_TEXT
            ]
            out = json.dumps(
                {
                    "grounded": not unsupported,
                    "confidence": 0.0 if unsupported else 0.9,
                    "unsupported_claims": unsupported,
                }
            )
        else:
            lines = _EVIDENCE_LINE.findall(prompt.partition("EVIDENCE:")[2])
            out = " ".join(f"{_sentences(t)[0]} [{i}]" for i, t in lines if _sentences(t)) or ABSTAIN_TEXT
        usage.output_tokens = len(out) // 4
        return out, usage
