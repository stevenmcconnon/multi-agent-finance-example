# docs/adr/

Architecture decision records (ADRs): one short file per decision, with context, the decision and its consequences. All are accepted as of 2026-11.

## Files

| Record | Decision |
|---|---|
| [`0001-pgvector-over-opensearch.md`](0001-pgvector-over-opensearch.md) | One Postgres holds the vector index, the keyword index, facts, ACLs and the control plane, so the permission filter is one SQL predicate. |
| [`0002-cedar-for-authorization.md`](0002-cedar-for-authorization.md) | Policies are Cedar, run in process locally and by Verified Permissions on AWS, over one entity mapping. |
| [`0003-temporal-owns-ingestion-durability.md`](0003-temporal-owns-ingestion-durability.md) | Temporal owns retries, backoff and at-most-once start for ingestion; no queue service in the path. |
| [`0004-edgar-as-the-corpus.md`](0004-edgar-as-the-corpus.md) | The corpus is SEC EDGAR filings with item ACLs simulated by form type. |
| [`0005-512-dim-embeddings.md`](0005-512-dim-embeddings.md) | Both embedders produce 512 dimensions, so one column and one index serve both. |
| [`0006-no-langchain-retrieval-abstractions.md`](0006-no-langchain-retrieval-abstractions.md) | LangGraph for the turn only; retrieval, embedding, rerank and model calls are our own small adapters. |
| [`0007-pkce-not-implicit.md`](0007-pkce-not-implicit.md) | Cognito authorization code with PKCE; the implicit flow is never used. |
| [`0008-model-driven-tool-selection.md`](0008-model-driven-tool-selection.md) | The model selects tools through Converse tool use; the harness enforces. |
| [`0009-rerank-relevance-floor.md`](0009-rerank-relevance-floor.md) | Chunks below the reranker's score floor are dropped; no evidence means abstain. |

## How it is used

Read the record before changing the thing it governs. To change a decision, add a new numbered record that supersedes the old one.

## Rules that hold here

- Number records in order and never renumber; do not edit an accepted record's decision.
- Each record names the code that implements it, so it can be checked.
- Use the `In production:` and `In this demo:` form where the demo differs.

## Read next

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md).
- [`../TOUR.md`](../TOUR.md).
