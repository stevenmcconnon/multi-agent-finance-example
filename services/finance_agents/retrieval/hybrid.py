"""PgHybridRetriever: dense (pgvector cosine) + sparse (tsvector) candidates, each ACL-filtered in SQL,
fused with Reciprocal Rank Fusion (k=60), then cross-encoder reranked to rerank_top_n.

The ACL predicate (retrieval/sql.py) is part of BOTH candidate queries. Nothing downstream of SQL
adds or removes authorization; `acl_filtered=True` on the result is a statement about the SQL.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID

from ..authz import audit
from ..config import settings
from ..contracts import Chunk, RetrievalRequest, RetrievalResult
from ..interfaces import Embedder, Reranker
from . import sql

RRF_K = 60

Fetch = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]


def rrf_fuse(ranked_lists: Iterable[list[UUID]], k: int = RRF_K) -> list[tuple[UUID, float]]:
    """Reciprocal Rank Fusion: score(id) = sum over lists of 1 / (k + rank), rank starting at 1.

    Returns (id, score) sorted by score desc; ties broken by first appearance (stable).
    """
    scores: dict[UUID, float] = {}
    order: dict[UUID, int] = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            order.setdefault(cid, len(order))
    return sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def row_to_chunk(row: dict[str, Any]) -> Chunk:
    return Chunk(
        id=row["id"],
        document_id=row["document_id"],
        org_id=row["org_id"],
        ordinal=row["ordinal"],
        section=row.get("section"),
        content=row["content"],
        metadata=row.get("metadata") or {},
        uri=row.get("uri"),
        title=row.get("title"),
        version=row.get("version"),
    )


class PgHybridRetriever:
    def __init__(self, embedder: Embedder, reranker: Reranker, fetch: Fetch | None = None) -> None:
        self._embedder = embedder
        self._reranker = reranker
        self._fetch = fetch  # injected in tests; defaults to db.fetch

    async def _run(self, query: str, params: dict[str, Any], *, dense: bool = False) -> list[dict[str, Any]]:
        if self._fetch is not None:
            return await self._fetch(query, params)
        from .. import db

        return await db.fetch(query, params, iterative_scan=dense)

    async def retrieve(self, req: RetrievalRequest) -> RetrievalResult:
        started = time.perf_counter()
        base = sql.acl_params(req.principal)
        text = req.query.rewritten or req.query.original

        vec = (await self._embedder.embed([text]))[0]
        dense_rows = await self._run(
            sql.DENSE_SQL, {**base, "vec": vec, "model": self._embedder.model_name, "k": req.top_k}, dense=True
        )
        sparse_rows = await self._run(sql.SPARSE_SQL, {**base, "q": text, "k": req.top_k})

        by_id: dict[UUID, Chunk] = {}
        for row in [*dense_rows, *sparse_rows]:
            by_id.setdefault(row["id"], row_to_chunk(row))
        fused = rrf_fuse([[r["id"] for r in dense_rows], [r["id"] for r in sparse_rows]])
        candidates = [by_id[cid].model_copy(update={"score": score}) for cid, score in fused]

        top = await self._reranker.rerank(req.query.original, candidates, req.rerank_top_n) if candidates else []
        # Authorized but irrelevant evidence is still no evidence: a reader allowed only the wrong documents must
        # end in abstention, not in a confident answer stitched from boilerplate (QA 2026-11-10, bob's 8-Ks).
        floor = settings.hr_rerank_min_score if settings.hr_rerank_min_score is not None else self._reranker.min_score
        top = [c for c in top if c.score is None or c.score >= floor]
        latency_ms = int((time.perf_counter() - started) * 1000)

        await audit.record(
            "retrieval",
            req.principal,
            {
                "query": req.query.original,
                "rewritten": req.query.rewritten,
                "intent": req.query.intent.value,
                "dense": len(dense_rows),
                "sparse": len(sparse_rows),
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
