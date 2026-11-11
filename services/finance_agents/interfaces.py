"""Ports. Adapters live in their layer packages; swap by config, never by edit."""

from __future__ import annotations

from typing import Protocol

from .contracts import (
    Chunk,
    Fact,
    ModelRole,
    ModelUsage,
    PolicyDecision,
    Principal,
    RetrievalRequest,
    RetrievalResult,
    ToolCall,
    ToolResult,
    ToolSpec,
    UnderstoodQuery,
)


class Authorizer(Protocol):
    async def decide(
        self, principal: Principal, action: str, resource: str, context: dict | None = None
    ) -> PolicyDecision: ...


class Retriever(Protocol):
    async def retrieve(self, req: RetrievalRequest) -> RetrievalResult: ...


class FactStore(Protocol):
    async def query(
        self, principal: Principal, entity: str, concept: str, period_end: str | None = None
    ) -> list[Fact]: ...


class Embedder(Protocol):
    model_name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    min_score: float  # evidence scored below this counts as absent; scale is the backend's own

    async def rerank(self, query: str, chunks: list[Chunk], top_n: int) -> list[Chunk]: ...


class ModelRefused(Exception):
    """The model declined to answer (refusal, content filter, guardrail). The turn abstains, reason model_declined."""


class ModelCallFailed(Exception):
    """A model call failed at the vendor. `code` is the vendor's error code (AccessDeniedException, ...)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class ModelGateway(Protocol):
    async def complete(
        self,
        role: ModelRole,
        system: str,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> tuple[str, ModelUsage]: ...
    async def understand(self, question: str) -> UnderstoodQuery: ...
    async def plan(self, question: str, understood: UnderstoodQuery, tools: list[ToolSpec]) -> list[ToolCall]:
        """Zero or more tool calls the model wants, drawn ONLY from `tools` (what the principal may use)."""
        ...


class Tool(Protocol):
    spec: ToolSpec

    async def run(self, principal: Principal, call: ToolCall) -> ToolResult: ...
