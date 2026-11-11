# retrieval/

The L4 Knowledge layer: finds chunks and facts the caller may read, ranks them, and does safe arithmetic.

## Files

| File | What it owns |
|---|---|
| `sql.py` | `ACL_PREDICATE` (access control list rule) and the `DENSE_SQL`, `SPARSE_SQL`, `FACTS_SQL` queries. One owner. |
| `hybrid.py` | `PgHybridRetriever`: dense (pgvector cosine) plus sparse (`tsvector`), fused by Reciprocal Rank Fusion (RRF, k=60), reranked, then a relevance floor. |
| `facts.py` | `PgFactStore`: numbers from `facts`, filtered by the same predicate. |
| `embed.py` | Embedders, both 512 dimensions: fastembed (local) and Titan v2 (Bedrock). |
| `rerank.py` | Rerankers: BGE cross-encoder (local) and Cohere Rerank (Bedrock). |
| `calculator.py` | `evaluate()`: AST-walked arithmetic, no `eval`, capped exponents. |
| `__init__.py` | `get_retriever()`, `get_fact_store()`, `get_embedder()`, `get_reranker()`. |

## How it is used

`harness/tools/search_documents.py` calls `get_retriever().retrieve(req)`; `facts_sql.py` calls `get_fact_store().query(...)`; `api/app.py` reuses the predicate for `/documents`.

## Rules that hold here

- Every query over chunks or facts carries `ACL_PREDICATE` before `LIMIT`, so an unauthorized chunk never enters a ranked list. Enforced by `test_every_chunk_or_fact_query_carries_the_acl_predicate` in `tests/test_architecture.py`; the `/documents` route is checked by `test_documents_route_reuses_the_predicate`.
- Nothing after SQL adds or removes authorization.
- Numbers are read from `facts` and computed with `calculator.py`, never guessed.
- Calculator constants become floats before arithmetic; exponent size and expression length are capped, so
  chained powers overflow immediately instead of constructing a process-blocking big integer.
- Chunks scoring below the reranker's floor are dropped (ADR 0009); no evidence means the turn abstains.
- `boto3` is allowed here only in `embed.py` and `rerank.py` (the test's allowlist).

## Production vs this demo

In production: batch endpoints or provisioned throughput for embeddings.
In this demo: Titan v2 takes one text per call (`embed.py`), because the corpus is small.

## Read next

- [`../../db/README.md`](../../../db/README.md), the tables queried here.
- [`../../docs/adr/0001-pgvector-over-opensearch.md`](../../../docs/adr/0001-pgvector-over-opensearch.md).
