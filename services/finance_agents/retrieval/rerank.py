"""Reranker adapters.

fastembed : fastembed.rerank.cross_encoder.TextCrossEncoder, Xenova/ms-marco-MiniLM-L-12-v2 (local ONNX, lazy)
bedrock   : cohere.rerank-v3-5:0 through bedrock-runtime `invoke_model`. Chosen over
            bedrock-agent-runtime `rerank` because it needs no knowledge-base/ARN plumbing and the
            request/response is the plain Cohere shape ({query, documents, top_n} -> results[{index,
            relevance_score}]). Documents are sent as text only; ids are mapped back by index.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..config import settings
from ..contracts import Chunk

log = logging.getLogger(__name__)


def _apply(chunks: list[Chunk], scored: list[tuple[int, float]], top_n: int) -> list[Chunk]:
    ordered = sorted(scored, key=lambda t: t[1], reverse=True)[:top_n]
    out: list[Chunk] = []
    for idx, score in ordered:
        out.append(chunks[idx].model_copy(update={"score": float(score)}))
    return out


class FastEmbedReranker:
    # Measured on the two annual reports (2026-11-11): against BAAI/bge-reranker-base this model put the right
    # passage first with a clear positive score more often, in half the time, at a ninth of the download.
    model_name = "Xenova/ms-marco-MiniLM-L-12-v2"
    min_score = 0.0  # cross-encoder logit: negative = the model judges the pair unrelated

    def __init__(self, model_name: str | None = None) -> None:
        if model_name:
            self.model_name = model_name
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    # Library-default threads: a cap of 2 made every search several times slower (measured
                    # 2026-11-11). batch_size=8 in rerank() is what bounds the memory.
                    self._model = await asyncio.to_thread(TextCrossEncoder, model_name=self.model_name)
        return self._model

    async def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        if not chunks:
            return []
        model = await self._load()
        scores = await asyncio.to_thread(lambda: list(model.rerank(query, [c.content for c in chunks], batch_size=8)))
        return _apply(chunks, list(enumerate(float(s) for s in scores)), top_n)


class BedrockReranker:
    min_score = 0.15  # Cohere Rerank relevance is 0..1; below this the passage is off-topic in practice

    def __init__(self, client: Any | None = None, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.bedrock_rerank_model
        self.model_name = self.model_id
        self._client = client

    def _boto(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        return self._client

    def _invoke(self, query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
        body = json.dumps({"query": query, "documents": docs, "top_n": top_n, "api_version": 2})
        resp = self._boto().invoke_model(
            modelId=self.model_id, body=body, accept="application/json", contentType="application/json"
        )
        payload = json.loads(resp["body"].read())
        return [(int(r["index"]), float(r["relevance_score"])) for r in payload.get("results", [])]

    async def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        if not chunks:
            return []
        scored = await asyncio.to_thread(self._invoke, query, [c.content for c in chunks], top_n)
        return _apply(chunks, scored, top_n)


class NoopReranker:
    min_score = 0.0  # RRF scores are small positives; nothing is dropped
    """Keeps fused order; for tests and for a degraded mode when the reranker is unavailable."""

    model_name = "none"

    async def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
        return chunks[:top_n]


def get_reranker(backend: str | None = None) -> FastEmbedReranker | BedrockReranker | NoopReranker:
    name = (backend or settings.hr_rerank_backend).lower()
    if name == "fastembed":
        return FastEmbedReranker()
    if name == "bedrock":
        return BedrockReranker()
    if name == "none":
        return NoopReranker()
    raise ValueError(f"unknown HR_RERANK_BACKEND {name!r} (fastembed | bedrock | none)")
