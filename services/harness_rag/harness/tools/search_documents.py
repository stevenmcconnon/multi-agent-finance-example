from __future__ import annotations

from pydantic import BaseModel, Field

from ...contracts import (
    Citation,
    Principal,
    QueryIntent,
    RetrievalRequest,
    RiskLevel,
    ToolCall,
    ToolResult,
    ToolSpec,
    UnderstoodQuery,
)
from ...interfaces import Retriever


class SearchInput(BaseModel):
    query: str = Field(min_length=1)
    rewritten: str | None = None
    intent: QueryIntent = QueryIntent.lookup
    top_k: int = Field(default=24, ge=1, le=100)
    rerank_top_n: int = Field(default=8, ge=1, le=50)
    thread_id: str | None = None


class SearchDocumentsTool:
    spec = ToolSpec(
        name="search_documents",
        description="Hybrid ACL-filtered search over the caller's documents; returns chunks with citations.",
        input_schema=SearchInput.model_json_schema(),
        risk=RiskLevel.read,
    )

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    async def run(self, principal: Principal, call: ToolCall) -> ToolResult:
        inp = SearchInput(**call.arguments)
        query = UnderstoodQuery(original=inp.query, rewritten=inp.rewritten or inp.query, intent=inp.intent)
        res = await self._retriever.retrieve(
            RetrievalRequest(
                principal=principal,
                query=query,
                top_k=inp.top_k,
                rerank_top_n=inp.rerank_top_n,
                filters={"thread_id": inp.thread_id} if inp.thread_id else {},
            )
        )
        if not res.acl_filtered:  # a retriever that skipped the ACL predicate never feeds the model
            return ToolResult(tool=self.spec.name, ok=False, error="retrieval_not_acl_filtered")
        return ToolResult(
            tool=self.spec.name,
            ok=True,
            output={
                "candidate_count": res.candidate_count,
                "chunks": [c.model_dump(mode="json") for c in res.chunks],
            },
            citations=[
                Citation(
                    document_id=c.document_id,
                    chunk_id=c.id,
                    title=c.title,
                    uri=c.uri or "",
                    section=c.section,
                    version=c.version,
                    snippet=c.content[:200],
                )
                for c in res.chunks
            ],
        )
