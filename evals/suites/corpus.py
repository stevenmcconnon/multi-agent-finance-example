"""The in-memory corpus and fake stores the eval suites run over.

What is real: finance_agents.agent.graph (nodes, routing, HITL interrupt), harness.runner.run_tool (Cedar,
idempotency, retries, breakers, audit), the Cedar policies in db/policies, the FakeGateway, MemorySaver.
What is faked: the SQL. `FakeRetriever` and `FakeFactStore` stand in for PgHybridRetriever / PgFactStore and
apply the same org + reader-group predicate retrieval/sql.py applies in SQL, so the suites exercise every
layer above the database without Postgres. tests/test_retrieval.py proves the SQL itself.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from finance_agents.authz import audit
from finance_agents.contracts import Chunk, Fact, Principal, RetrievalRequest, RetrievalResult
from finance_agents.ingest.acl import groups_for

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
ORG = UUID("00000000-0000-0000-0000-000000000001")  # acme (db/migrations/0002_seed_dev.sql)
OTHER_ORG = UUID("00000000-0000-0000-0000-000000000002")
NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Mirrors 0002_seed_dev.sql; the eval never reads the token's hint, only these resolved memberships.
USERS = {
    "alice": Principal(user_id="alice", org_id=ORG, org_slug="acme", groups=["finance", "analysts"], roles=["analyst"]),
    "bob": Principal(user_id="bob", org_id=ORG, org_slug="acme", groups=["engineering"], roles=["viewer"]),
    "carol": Principal(user_id="carol", org_id=ORG, org_slug="acme", groups=[], roles=["admin"]),
}


def _doc(org: UUID, ticker: str, form: str, sections: list[tuple[str, str]]) -> dict[str, Any]:
    """One fixture document. Readers follow ingest/acl.py exactly, so the fixture cannot drift from ingestion."""
    doc_id = uuid5(NS, f"{org}:{ticker}:{form}")
    return {
        "id": doc_id,
        "org_id": org,
        "ticker": ticker,
        "form": form,
        "title": f"{ticker} {form} (fixture)",
        "uri": f"fixture://{ticker}/{form}",
        "readers": {f"group:{g}" for g in groups_for(form)},
        "chunks": [
            Chunk(
                id=uuid5(NS, f"{doc_id}:{i}"),
                document_id=doc_id,
                org_id=org,
                ordinal=i,
                section=section,
                content=text,
                uri=f"fixture://{ticker}/{form}",
                title=f"{ticker} {form} (fixture)",
                version="1",
            )
            for i, (section, text) in enumerate(sections)
        ],
    }


# Section names are real EDGAR item headings; the sentences are fixture prose, not claims about the issuers.
_TEN_K = [
    (
        "Item 1. Business",
        (
            "The company designs, manufactures and markets products and services described in this report. "
            "Its business is organized into reportable segments listed in the notes."
        ),
    ),
    (
        "Item 1A. Risk Factors",
        (
            "The risk factors in this section describe conditions that could affect the business, "
            "results of operations and financial condition. Competition and supply constraints are among them."
        ),
    ),
    (
        "Item 7. Management's Discussion and Analysis",
        (
            "Management's discussion and analysis covers results of operations, "
            "liquidity and capital resources for the fiscal year covered by this annual report."
        ),
    ),
]
_EIGHT_K = [
    (
        "Item 2.02 Results of Operations and Financial Condition",
        (
            "The registrant issued a press release announcing "
            "financial results for the quarter. The press release is furnished as an exhibit."
        ),
    ),
    (
        "Item 5.02 Departure of Directors or Certain Officers",
        (
            "The registrant announced a change in its board of directors "
            "and executive officers as described in this current report."
        ),
    ),
    (
        "Item 8.01 Other Events",
        (
            "The registrant reported other events including a dividend declaration and a conference call "
            "to discuss the quarter."
        ),
    ),
]
_GLOBEX = [
    ("Item 1. Business", "Globex Corporation operates in another organization's tenant and is never visible to acme.")
]

CORPUS: list[dict[str, Any]] = [
    _doc(ORG, "AAPL", "10-K", _TEN_K),
    _doc(ORG, "AAPL", "8-K", _EIGHT_K),
    _doc(ORG, "MSFT", "10-K", _TEN_K),
    _doc(ORG, "MSFT", "8-K", _EIGHT_K),
    _doc(OTHER_ORG, "GLBX", "10-K", _GLOBEX),
]
DOCS_BY_ID = {d["id"]: d for d in CORPUS}


def may_read(principal: Principal, doc: dict[str, Any]) -> bool:
    """The predicate of retrieval/sql.py, in Python: same org AND (user or any group) in document_acl."""
    ids = {f"user:{principal.user_id}", *(f"group:{g}" for g in principal.groups)}
    return doc["org_id"] == principal.org_id and bool(doc["readers"] & ids)


def unauthorized_doc_ids(principal: Principal) -> set[str]:
    return {str(d["id"]) for d in CORPUS if not may_read(principal, d)}


def _overlap(question: str, text: str) -> int:
    words = {w for w in question.lower().replace("?", " ").split() if len(w) > 3}
    return sum(1 for w in text.lower().replace(".", " ").split() if w in words)


class FakeRetriever:
    """ACL predicate first (as SQL does), then a word-overlap ranking, then an audit row shaped like hybrid.py's."""

    def __init__(self, corpus: list[dict[str, Any]] = CORPUS) -> None:
        self._corpus = corpus

    async def retrieve(self, req: RetrievalRequest) -> RetrievalResult:
        started = time.perf_counter()
        text = req.query.rewritten or req.query.original
        scored = [
            (c, _overlap(text, f"{c.section} {c.content}"))
            for d in self._corpus
            if may_read(req.principal, d)
            for c in d["chunks"]
        ]
        tickers = {d["ticker"] for d in self._corpus if d["ticker"] in text.upper().split()}
        if tickers:
            scored = [(c, s) for c, s in scored if DOCS_BY_ID[c.document_id]["ticker"] in tickers]
        # shortcut: a zero-overlap chunk is dropped, standing in for the verifier's "evidence does not answer";
        # a dense index returns nearest neighbours regardless, and the real graph abstains at verify instead.
        candidates = [c for c, s in sorted(scored, key=lambda cs: -cs[1]) if s > 0]
        top = [c.model_copy(update={"score": float(s)}) for c, s in sorted(scored, key=lambda cs: -cs[1]) if s > 0][
            : req.rerank_top_n
        ]
        latency_ms = int((time.perf_counter() - started) * 1000)
        await audit.record(
            "retrieval",
            req.principal,
            {
                "query": req.query.original,
                "rewritten": req.query.rewritten,
                "intent": req.query.intent.value,
                "candidate_count": len(candidates),
                "returned": len(top),
                "document_ids": sorted({str(c.document_id) for c in top}),
                "chunk_ids": [str(c.id) for c in top],
                "acl_filtered": True,
                "latency_ms": latency_ms,
            },
            thread_id=req.filters.get("thread_id"),
        )
        return RetrievalResult(chunks=top, candidate_count=len(candidates), acl_filtered=True, latency_ms=latency_ms)


class FakeFactStore:
    """One synthetic fact per 10-K (value 1.0, flagged `fixture`), readable by the 10-K's readers only."""

    async def query(self, principal: Principal, entity: str, concept: str, period_end: str | None = None) -> list[Fact]:
        return [
            Fact(
                id=uuid5(NS, f"{d['id']}:{concept}"),
                document_id=d["id"],
                entity=entity,
                concept=concept,
                period_start=None,
                period_end=None,
                value=1.0,
                unit="USD",
                provenance={"fixture": True, "uri": d["uri"], "raw_text": f"{concept} fixture value"},
            )
            for d in CORPUS
            if d["form"] == "10-K" and d["ticker"] == entity.upper() and may_read(principal, d)
        ]


def load_cases(name: str) -> list[dict[str, Any]]:
    with (DATASETS / name).open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


# Fields that echo the caller's own words (the question, tool arguments). A document id typed INTO a question
# is the user's input, not a leak; the leak checks walk every other field, answer text included.
ECHO_FIELDS = {"query", "rewritten", "question", "arguments"}


def ids_in(value: Any, *, skip_echo: bool = True) -> set[str]:
    """Every UUID-looking string reachable from `value`, excluding echo fields when asked."""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if skip_echo and k in ECHO_FIELDS:
                continue
            found |= ids_in(v, skip_echo=skip_echo)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            found |= ids_in(v, skip_echo=skip_echo)
    elif isinstance(value, str):
        for did in DOCS_BY_ID:
            if str(did) in value:
                found.add(str(did))
    return found
