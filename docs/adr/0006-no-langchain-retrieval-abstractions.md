# ADR 0006: LangGraph for the turn, no LangChain retrieval abstractions

Status: accepted · 2026-11

## Context

LangGraph gives the turn a checkpointed state machine with interrupts, replay and streaming, which the HITL
and replay features need. LangChain also offers retrievers, vector-store wrappers, document loaders and
chains that could replace `retrieval/`, `ingest/parse.py` and the gateway.

## Decision

Use LangGraph (`StateGraph`, `interrupt`, `Command`, the Postgres checkpointer) for orchestration only.
Retrieval, embedding, reranking, parsing and model calls are the repo's own small adapters behind the ports
in `interfaces.py`, with raw SQL in `retrieval/sql.py` and `bedrock-runtime` through boto3 in `gateway/`.
Vendor SDKs are allowed in a named set of files (`tests/test_architecture.py::test_boto3_only_behind_the_doors`).

## Alternatives

- **LangChain `PGVector` + `EnsembleRetriever` + `ContextualCompressionRetriever`.** Less code on day one.
  The ACL predicate would have to be expressed as a metadata filter the wrapper supports, the SQL would be
  generated and unreadable in a review, and proving "the predicate is in every query" would mean testing a
  library's internals.
- **LlamaIndex.** Same trade: fast start, the permission filter lives in someone else's query builder.
- **No framework at all** (hand-written loop with a state table). Possible; LangGraph's checkpointer,
  `interrupt()` and streaming are exactly the parts worth not rewriting.

## Consequences

- The three SQL queries are plain strings a reviewer can read and a test can grep; the invariant is a
  string assertion, not a trust in a library.
- Swapping a model vendor or a vector store is an adapter file, not a dependency upgrade.
- More code than the wrapper route: `retrieval/` is ~400 lines including fusion and the calculator.
