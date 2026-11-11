"""Retrieval: SQL builder invariants (no DB), RRF ordering, the calculator, and a DB-backed smoke test
that runs only when DATABASE_URL answers."""

from __future__ import annotations

import os
import uuid
from uuid import UUID

import pytest
from finance_agents.authz import audit
from finance_agents.contracts import Principal, QueryIntent, RetrievalRequest, UnderstoodQuery
from finance_agents.retrieval import PgFactStore, PgHybridRetriever, rrf_fuse, sql
from finance_agents.retrieval.calculator import CalculatorError, evaluate
from finance_agents.retrieval.rerank import NoopReranker

ORG = UUID("00000000-0000-0000-0000-000000000001")
BOB = Principal(user_id="bob", org_id=ORG, org_slug="acme", groups=["engineering"], roles=["viewer"])
ALICE = Principal(user_id="alice", org_id=ORG, org_slug="acme", groups=["finance", "analysts"], roles=["analyst"])


@pytest.fixture(autouse=True)
def capture_audit(monkeypatch):
    events = []

    async def sink(kind, principal, payload, thread_id):
        events.append((kind, payload))

    monkeypatch.setattr(audit, "AUDIT_SINK", sink)
    return events


# ---- SQL builder: the ACL predicate is in every candidate query, before any ranking ----
def test_acl_predicate_in_dense_and_sparse_and_facts():
    for q in (sql.DENSE_SQL, sql.SPARSE_SQL, sql.FACTS_SQL):
        assert sql.ACL_PREDICATE in q
        assert "c.org_id = %(org)s" in q
        assert "d.deleted_at IS NULL" in q
        # the predicate sits in WHERE, i.e. before ORDER BY / LIMIT
        assert q.index(sql.ACL_PREDICATE) < q.index("ORDER BY") < q.index("LIMIT")
    assert "<=> %(vec)s::vector" in sql.DENSE_SQL
    assert "websearch_to_tsquery('english', %(q)s)" in sql.SPARSE_SQL and "ts_rank_cd" in sql.SPARSE_SQL


def test_dense_query_only_compares_vectors_of_the_same_embedding_model():
    assert "c.embedding_model = %(model)s" in sql.DENSE_SQL


def test_acl_params_come_from_principal_only():
    assert sql.acl_params(ALICE) == {"org": str(ORG), "user": "alice", "groups": ["finance", "analysts"]}


# ---- RRF ----
def test_rrf_ordering_fixed_input():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    dense = [a, b, c]
    sparse = [b, d, a]
    fused = rrf_fuse([dense, sparse], k=60)
    ids = [cid for cid, _ in fused]
    # b: 1/62 + 1/61 ; a: 1/61 + 1/63 ; c: 1/63 ; d: 1/62
    assert ids == [b, a, d, c]
    scores = dict(fused)
    assert scores[b] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[a] == pytest.approx(1 / 61 + 1 / 63)
    assert scores[d] > scores[c]


# ---- retriever with an injected fetch: fuses, reranks, audits, flags acl_filtered ----
class _FakeEmbedder:
    model_name, dim = "fake", 512

    async def embed(self, texts):
        return [[0.0] * 512 for _ in texts]


def _row(cid, doc, content):
    return {
        "id": cid,
        "document_id": doc,
        "org_id": ORG,
        "ordinal": 0,
        "section": None,
        "content": content,
        "metadata": {},
        "uri": "s3://x",
        "title": "t",
        "version": "1",
    }


async def test_retriever_fuses_and_audits(capture_audit):
    c1, c2, c3, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    seen = []

    async def fetch(q, params):
        seen.append((q, params))
        assert sql.ACL_PREDICATE in q and params["user"] == "alice" and params["groups"] == ["finance", "analysts"]
        if "websearch_to_tsquery" in q:
            return [_row(c2, doc, "two"), _row(c3, doc, "three")]
        return [_row(c1, doc, "one"), _row(c2, doc, "two")]

    r = PgHybridRetriever(_FakeEmbedder(), NoopReranker(), fetch=fetch)
    req = RetrievalRequest(
        principal=ALICE,
        query=UnderstoodQuery(original="revenue 2023", rewritten="revenue 2023", intent=QueryIntent.lookup),
        top_k=10,
        rerank_top_n=2,
    )
    res = await r.retrieve(req)
    assert len(seen) == 2
    assert res.acl_filtered is True and res.candidate_count == 3
    assert [c.id for c in res.chunks] == [c2, c1]  # c2 in both lists ranks first
    kinds = [k for k, _ in capture_audit]
    assert "retrieval" in kinds
    payload = capture_audit[-1][1]
    assert payload["document_ids"] == [str(doc)] and payload["acl_filtered"] is True


# ---- calculator ----
def test_calculator_arithmetic():
    assert evaluate("(1 + 2) * 3 - 4 / 2") == 7.0
    assert evaluate("2 ** 10 % 7") == 2.0
    assert evaluate("-3.5 + +1") == -2.5


@pytest.mark.parametrize(
    "expr",
    ["__import__('os')", "a + 1", "1 if 1 else 2", "max(1,2)", "2 ** 5000", "((9**1000)**1000)**1000", "1/0", ""],
)
def test_calculator_rejects(expr):
    import time

    started = time.perf_counter()
    with pytest.raises(CalculatorError):
        evaluate(expr)
    assert time.perf_counter() - started < 1  # a chained power overflows at once; it never builds the bigint


# ---- DB-backed: skipped unless DATABASE_URL answers ----
def _db_reachable() -> bool:
    import asyncio

    from finance_agents import db

    try:
        return asyncio.run(db.ping(os.environ.get("DATABASE_URL")))
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="DATABASE_URL not reachable")
async def test_db_acl_filters_rows_end_to_end():
    from finance_agents import db

    await db.run_migrations()
    org = str(ORG)
    doc_id = str(uuid.uuid4())
    try:
        await db.execute(
            "INSERT INTO documents (id, org_id, source, source_id, version, title, uri, content_hash) "
            "VALUES (%s, %s, 'upload', %s, '1', 'acl test', 's3://t', 'h')",
            (doc_id, org, doc_id),
        )
        await db.execute(
            "INSERT INTO document_acl (document_id, principal_id) SELECT %s, id FROM principals "
            "WHERE org_id = %s AND kind = 'group' AND external_id = 'finance'",
            (doc_id, org),
        )
        await db.execute(
            "INSERT INTO chunks (document_id, org_id, ordinal, content, embedding, embedding_model) "
            "VALUES (%s, %s, 0, 'the zorbflax quarterly figure rose sharply', %s::vector, 'fake')",
            (doc_id, org, "[" + ",".join(["0.01"] * 512) + "]"),
        )
        store_fetch = db.fetch
        r = PgHybridRetriever(_FakeEmbedder(), NoopReranker(), fetch=store_fetch)
        q = UnderstoodQuery(original="zorbflax", rewritten="zorbflax", intent=QueryIntent.lookup)
        alice = await r.retrieve(RetrievalRequest(principal=ALICE, query=q))
        bob = Principal(user_id="bob", org_id=ORG, org_slug="acme", groups=["engineering"], roles=["viewer"])
        bobs = await r.retrieve(RetrievalRequest(principal=bob, query=q))
        assert any(str(c.document_id) == doc_id for c in alice.chunks)
        assert not any(str(c.document_id) == doc_id for c in bobs.chunks)
    finally:
        await db.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        await db.close_pool()


@pytest.mark.skipif(not _db_reachable(), reason="DATABASE_URL not reachable")
async def test_db_dense_rows_for_a_narrow_reader_survive_the_hnsw_post_filter():
    from finance_agents import db

    await db.run_migrations()
    org = str(ORG)
    wide, narrow = str(uuid.uuid4()), str(uuid.uuid4())

    def vec(*head: float) -> str:
        return "[" + ",".join(str(x) for x in (*head, *([0.0] * (512 - len(head))))) + "]"

    try:
        for doc, grp in ((wide, "finance"), (narrow, "engineering")):
            await db.execute(
                "INSERT INTO documents (id, org_id, source, source_id, version, title, uri, content_hash) "
                "VALUES (%s, %s, 'upload', %s, '1', 'dense test', 's3://t', 'h')",
                (doc, org, doc),
            )
            await db.execute(
                "INSERT INTO document_acl (document_id, principal_id) SELECT %s, id FROM principals "
                "WHERE org_id = %s AND kind = 'group' AND external_id = %s",
                (doc, org, grp),
            )
        # 100 chunks nearer the query than bob's 5: the 40 nearest are all unreadable to him
        for i in range(100):
            await db.execute(
                "INSERT INTO chunks (document_id, org_id, ordinal, content, embedding, embedding_model) "
                "VALUES (%s, %s, %s, 'wide', %s::vector, 'fake')",
                (wide, org, i, vec(1.0, 0.001 * i)),
            )
        for i in range(5):
            await db.execute(
                "INSERT INTO chunks (document_id, org_id, ordinal, content, embedding, embedding_model) "
                "VALUES (%s, %s, %s, 'narrow', %s::vector, 'fake')",
                (narrow, org, i, vec(0.2, 1.0, 0.001 * i)),
            )
        params = {**sql.acl_params(BOB), "vec": vec(1.0), "model": "fake", "k": 200}
        rows = await db.fetch(sql.DENSE_SQL, params, iterative_scan=True)
        assert sum(str(r["document_id"]) == narrow for r in rows) == 5
        assert not any(str(r["document_id"]) == wide for r in rows)
    finally:
        await db.execute("DELETE FROM documents WHERE id IN (%s, %s)", (wide, narrow))
        await db.close_pool()


async def test_rerank_floor_drops_irrelevant_authorized_chunks(monkeypatch):
    from finance_agents.config import settings

    class _ScoredReranker:
        min_score = 0.0

        async def rerank(self, query, chunks, top_n):
            return [c.model_copy(update={"score": s}) for c, s in zip(chunks, [3.2, -4.9], strict=False)]

    rows = [
        {"id": uuid.uuid4(), "document_id": uuid.uuid4(), "org_id": ORG, "ordinal": 0, "content": "a", "metadata": {}},
        {"id": uuid.uuid4(), "document_id": uuid.uuid4(), "org_id": ORG, "ordinal": 0, "content": "b", "metadata": {}},
    ]

    async def fetch(sql_text, params, **_):
        return rows

    monkeypatch.setattr(settings, "hr_rerank_min_score", None)
    r = PgHybridRetriever(_FakeEmbedder(), _ScoredReranker(), fetch=fetch)
    q = UnderstoodQuery(original="x", rewritten="x", intent=QueryIntent.lookup)
    out = await r.retrieve(RetrievalRequest(principal=ALICE, query=q))
    assert [c.score for c in out.chunks] == [3.2]


async def test_fact_query_expands_revenue_aliases_and_carries_document_provenance():
    seen: dict = {}

    async def fetch(sql_text, params, **_):
        seen.update(params)
        return [
            {
                "id": uuid.uuid4(),
                "document_id": uuid.uuid4(),
                "entity": "AAPL",
                "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "period_start": None,
                "period_end": None,
                "value": 1.0,
                "unit": "USD",
                "provenance": {"accn": "x"},
                "document_uri": "https://sec.gov/f",
                "document_title": "AAPL 10-K",
                "document_version": "x",
            }
        ]

    facts = await PgFactStore(fetch=fetch).query(ALICE, "AAPL", "Revenues")
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in seen["concepts"]
    assert facts[0].provenance["uri"] == "https://sec.gov/f" and facts[0].provenance["title"] == "AAPL 10-K"
