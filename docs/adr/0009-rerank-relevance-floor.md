# Architecture decision record (ADR) 0009: A relevance floor after rerank

Status: accepted · 2026-11

## Context

Authorization decides what a user may read; it says nothing about whether it answers the question. Exploratory
Quality assurance (QA) on 2026-11-10 (`docs/QA_2026-11-10.md`) asked the Apple supply-chain question as bob, who may read only the
Form 8-K (current report) filings. Retrieval returned six authorized 8-K chunks with negative reranker scores, and the generator and the
verifier produced a confident answer about U.S. Securities and Exchange Commission (SEC) boilerplate. No permission was broken, but the user got a
nonsense answer where an abstention was right.

## Decision

After reranking, `retrieval/hybrid.py` drops every chunk whose score is below the reranker's own `min_score`
(Best General Embeddings (BGE) cross-encoder logits: 0.0; Cohere Rerank relevance: 0.15); `HR_RERANK_MIN_SCORE` overrides it. Authorized but irrelevant evidence counts as absent: with nothing left, `act` has no evidence, the
generator is never called and the turn abstains with `no_authorized_evidence`. A chunk with no score (the
no-op reranker) is kept. The floor runs after the Structured Query Language (SQL) predicate, so it can only remove evidence, never add it.

In production: the floor is tuned against the production reranker's score scale on labelled questions.
In this demo: per-backend defaults, because the local cross-encoder emits logits where negative reads as irrelevant and the one
observed failure sat well below zero; it was not tuned on a labelled set.

## Alternatives

- **Rely on the verifier.** It checks the draft against the evidence, and the draft was grounded in irrelevant
  evidence, so it passed.
- **Top-k only.** Always returns something; that is the failure.
- **A relevance step in the prompt.** Asks the model to police what the harness can decide in code.

## Consequences

- A user allowed only unhelpful documents gets an honest abstention, which is also what the permission-leak
  suite expects of a viewer asking a question only the Form 10-K (annual report) could answer.
- The floor is scale-dependent, so it lives on the reranker, not in one global number;
  set the variable when switching `HR_RERANK_BACKEND`. A floor set too high abstains on answerable questions.
- Proven by `tests/test_retrieval.py::test_rerank_floor_drops_irrelevant_authorized_chunks`; the eval fixture
  stands in for it with zero-overlap filtering (`evals/README.md`).
