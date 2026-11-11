# ADR 0001: pgvector + tsvector in Postgres, not OpenSearch

Status: accepted · 2026-11

## Context

Retrieval needs dense (embedding) and sparse (keyword) candidates, fused and reranked, with an item-level
permission filter that must apply before any ranking. The control plane (jobs, audit, checkpoints,
idempotency, identity mirror) already needs a relational database.

## Decision

One Postgres (RDS 16) holds the search index and the control plane: `chunks.embedding vector(512)` under an
HNSW index for cosine, `chunks.content_tsv` as a generated `tsvector` under GIN, `facts` for structured
numbers, and `document_acl` next to them. The permission filter is one SQL predicate
(`retrieval/sql.py::ACL_PREDICATE`) in the `WHERE` of every candidate query. Fusion is reciprocal rank
fusion in Python over the two ACL-filtered lists.

## Alternatives

- **OpenSearch (k-NN + BM25) with document-level security.** Better BM25, analyzers, field boosts;
  independent scaling of the index; mature filtered k-NN. Costs a second store, a second ACL
  representation to keep in sync, a second place to prove the invariant, and its own cluster to run.
- **A managed vector database** (Pinecone, Weaviate, Bedrock Knowledge Bases). Fast to start; metadata
  filters stand in for ACLs but the ACL rows live elsewhere, so correctness is a sync job.
- **Postgres without pgvector** (sparse only). Loses semantic recall on paraphrased questions.

## Consequences

- The invariant is provable with a string test over three SQL constants
  (`tests/test_architecture.py::test_every_chunk_or_fact_query_carries_the_acl_predicate`) and one DB test.
- Sparse retrieval is Postgres full-text (`websearch_to_tsquery`, `ts_rank_cd`), not BM25; fusion softens
  the gap. The README's sizing section states when this stops being enough: BM25 features, independent
  index scaling, or ACL change volume beyond row updates.
- HNSW build time and memory are the first scaling limit; the `EXISTS` predicate is the planner's hardest
  decision at tens of millions of chunks (partition by org, or denormalize readers onto chunks).
