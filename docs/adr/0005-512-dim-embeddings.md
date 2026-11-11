# Architecture decision record (ADR) 0005: 512-dimension embeddings on both backends

Status: accepted · 2026-11

## Context

The local stack must embed with no network and no cost; Amazon Web Services (AWS) embeds with Titan Text Embeddings v2. The
`chunks.embedding` column has one fixed dimension under one Hierarchical Navigable Small World (HNSW) index; changing it means a re-embed and
a rebuild.

## Decision

`chunks.embedding vector(512)`. Titan v2 is called with `dimensions=512` (a native output size, not a
truncation); locally `jinaai/jina-embeddings-v2-small-en` via fastembed, also 512. Both adapters assert the
returned length (`retrieval/embed.py`) and `chunks.embedding_model` records which model wrote each row.

## Alternatives

- **1024 (Titan v2 default) or 1536/3072.** Higher recall ceilings in published benchmarks, twice to six
  times the index memory and build time, and no local model at those sizes with the same footprint.
- **256.** Smaller still, with a measurable recall cost on Titan's own evaluations.
- **Different dimensions per backend.** Would need two columns or two indexes and would make the local
  stack a different system from the deployed one.

## Consequences

- One schema, one index, one query for both backends; a local test of the dense query is a real test.
- Vectors from the two models are not comparable: an index embedded locally must be re-embedded when the
  backend switches to Titan (`embedding_model` says which rows need it).
- No retrieval-quality figure is claimed for either model in this repository; measuring it on the Electronic Data Gathering, Analysis, and Retrieval system (EDGAR)
  corpus is the first thing a production tuning pass does.
