"""L4 Knowledge/Retrieval: hybrid ACL-filtered retrieval, the fact store, embedders, rerankers."""

from __future__ import annotations

from ..interfaces import FactStore, Retriever
from .calculator import CalculatorError, evaluate
from .embed import get_embedder
from .facts import PgFactStore
from .hybrid import PgHybridRetriever, rrf_fuse
from .rerank import get_reranker

__all__ = [
    "CalculatorError",
    "PgFactStore",
    "PgHybridRetriever",
    "evaluate",
    "get_embedder",
    "get_fact_store",
    "get_reranker",
    "get_retriever",
    "rrf_fuse",
]


def get_retriever() -> Retriever:
    return PgHybridRetriever(get_embedder(), get_reranker())


def get_fact_store() -> FactStore:
    return PgFactStore()
