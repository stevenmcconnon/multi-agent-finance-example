"""Embedder adapters. Both produce 512-dim vectors, the `chunks.embedding vector(512)` column.

fastembed : jinaai/jina-embeddings-v2-small-en (512, local ONNX; downloaded lazily on first use)
bedrock   : amazon.titan-embed-text-v2:0 with dimensions=512 (native; no truncation)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from botocore.exceptions import ClientError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings

log = logging.getLogger(__name__)

EMBED_DIM = 512


class FastEmbedEmbedder:
    model_name = "jinaai/jina-embeddings-v2-small-en"
    dim = EMBED_DIM

    def __init__(self, model_name: str | None = None) -> None:
        if model_name:
            self.model_name = model_name
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding  # heavy import, deferred

                    self._model = await asyncio.to_thread(TextEmbedding, model_name=self.model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._load()
        vectors = await asyncio.to_thread(lambda: [v.tolist() for v in model.embed(texts)])
        for v in vectors:
            if len(v) != self.dim:
                raise ValueError(f"{self.model_name} returned dim {len(v)}, expected {self.dim}")
        return vectors


class BedrockEmbedder:
    """Titan Text Embeddings v2 through bedrock-runtime invoke_model, dimensions=512 (native)."""

    dim = EMBED_DIM

    def __init__(self, client: Any | None = None, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.bedrock_embedding_model
        self.model_name = f"{self.model_id}@{self.dim}"
        self._client = client

    def _boto(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
        return self._client

    # Titan throttles: retry with backoff on ClientError (ThrottlingException etc.), then give up.
    @retry(
        retry=retry_if_exception_type(ClientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5),
        reraise=True,
    )
    def _one(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": self.dim, "normalize": True})
        resp = self._boto().invoke_model(
            modelId=self.model_id, body=body, accept="application/json", contentType="application/json"
        )
        payload = json.loads(resp["body"].read())
        vec = payload["embedding"]
        if len(vec) != self.dim:
            raise ValueError(f"{self.model_id} returned dim {len(vec)}, expected {self.dim}")
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # In production: batch endpoints / provisioned throughput. In this demo: Titan v2 takes one text per
        # call, because the API has no batch form; concurrency is capped so a 10-K cannot flood it.
        sem = asyncio.Semaphore(8)

        async def one(t: str) -> list[float]:
            async with sem:
                return await asyncio.to_thread(self._one, t)

        return list(await asyncio.gather(*(one(t) for t in texts)))


def get_embedder(backend: str | None = None) -> FastEmbedEmbedder | BedrockEmbedder:
    name = (backend or settings.hr_embed_backend).lower()
    if name == "fastembed":
        return FastEmbedEmbedder()
    if name == "bedrock":
        return BedrockEmbedder()
    raise ValueError(f"unknown HR_EMBED_BACKEND {name!r} (fastembed | bedrock)")
