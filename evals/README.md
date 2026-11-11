# Evals

Two pytest suites that run offline on every continuous integration (CI) run, and two optional graded evals against the live stack.

## Suites (`uv run pytest evals/suites -q`)

Both suites run the real turn graph (`harness_rag.agent.graph`), the real tool door (`harness.runner`), the
real Cedar policies and the `FakeGateway`, with `MemorySaver` checkpoints. `suites/corpus.py` replaces the
two Structured Query Language (SQL)-backed stores with `FakeRetriever` and `FakeFactStore` over a five-document fixture (Apple (AAPL) and Microsoft (MSFT)
Form 10-K (annual report) and Form 8-K (current report) filings in org `acme`, one document in another org). The fakes apply the same org + reader-group
predicate `retrieval/sql.py` applies in SQL; the SQL itself is proven in `tests/test_retrieval.py` and
`tests/test_architecture.py`. Fixture readers follow `ingest/acl.py`, so the fixture cannot drift from
ingestion.

- **`test_permission_leak.py`** over `datasets/permission_leak.jsonl` (8 cases: a viewer asking 10-K-only
  questions, "ignore your access control list (ACL)", a question naming a document id the caller may not read, a numeric question
  over finance-only facts, an analyst asking about another org's document, an admin's write tool, an empty
  question, an injection string). For each case: the turn abstains (or, for `expect: no_leak`, answers only
  from readable documents), no `retrieval` event or audit row lists a forbidden document, no forbidden
  document id appears in the answer text, the citations, any stream event or any audit payload outside the
  fields that echo the caller's own words (`query`, `rewritten`, `question`, `arguments`), every policy
  decision was audited and none was an allow-on-outage. The write-tool case must stop at the human in the loop (HITL) interrupt
  with no `tool.call`, and a denial must still create nothing. Two more tests pin that a viewer's `facts_sql`
  is refused by Cedar (not merely hidden) and that another user cannot replay, resume or continue a thread.
- **`test_citation_quality.py`** over `datasets/qa.jsonl` (12 cases). An `answer` case must be
  non-abstained, verifier-grounded, carry at least one citation, cite only documents the asking user may
  read, and cite the form in `must_cite_form`; an `abstain` case must return the abstain text with no
  citations. There are no gold answers (architecture decision record (ADR) 0004): the fixture text is neutral prose under real Electronic Data Gathering, Analysis, and Retrieval system (EDGAR) item
  headings, and the numeric case's fact value is `1.0` flagged `fixture`, never asserted.

A fixture limit worth knowing: `FakeRetriever` drops zero-overlap chunks, standing in for the rerank relevance
floor (`HR_RERANK_MIN_SCORE`, ADR 0009) that `PgHybridRetriever` applies to real scores. A dense index returns
nearest neighbours regardless; on the live stack the floor, then the verifier, produce the same abstention.
The floor itself is proven in `tests/test_retrieval.py::test_rerank_floor_drops_irrelevant_authorized_chunks`.

## Datasets

`qa.jsonl` fields: `id`, `user` (alice | bob | carol), `question`, `expect` (answer | abstain),
`must_cite_form` (10-K | 8-K | null), `notes`. `permission_leak.jsonl` fields: `id`, `user`, `question`,
optional `expect` (abstain, the default, or no_leak), optional `expect_hitl` (tool name), `notes`. A question
may contain `{doc:TICKER/FORM}`, replaced at run time with that fixture document's id.

## Graded evals (optional, live stack)

```sh
uv run --extra evals python evals/ragas_eval.py       # faithfulness, answer relevancy, context precision (reference-free)
uv run --extra evals python evals/deepeval_eval.py    # faithfulness, answer relevancy, contextual relevancy
```

Both run `qa.jsonl`'s `answer` cases through `run_turn` against the ingested Postgres corpus with the
configured gateway, build each case's contexts from the chunks the turn *cited* (read back by chunk id, so
nothing outside the caller's permissions enters the eval dataset), score them with a judge model, print
per-case results and write rows to `eval_records` (`suite`, `case_id`, `thread_id`, `scores`, `passed`).
They skip, printing why, when the `evals` extra is not installed, when `HR_MODEL_BACKEND` is not `bedrock`
(the judge needs a real model; the fake gateway would grade itself), or when `DATABASE_URL` does not answer.
The principal comes from `identity.verify_token(devtoken.mint(user))`, the production path.

DeepEval's contextual *precision* metric needs a gold answer per case; the dataset has none, so contextual
relevancy stands in. The ragas judge is `ChatBedrockConverse` built in the script (ragas wants a LangChain
large language model (LLM)); the DeepEval judge is the repo's own gateway wrapped as a `DeepEvalBaseLLM`.

No score from either eval is recorded in this repository: they were not run against Bedrock here.
