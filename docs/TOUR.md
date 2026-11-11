# Code tour, in request order

A guided walk from the bearer token to the stored checkpoint. Each stop names the file and the function, what
it decides and what it refuses. Read it with the code open; it is how a new engineer learns where a change
goes, and it lets the owner narrate the system by clicking through it.

## 1. Token -> Principal

`services/finance_agents/api/app.py` `current_principal` reads the `Authorization: Bearer` header (a JSON Web Token (JWT)) and calls
`services/finance_agents/harness/identity.py` `verify_token`. `decode_claims` verifies the signature, `exp` (required) and,
for Cognito, issuer, audience and `token_use` (a shared-secret signature (HS256) with a dev secret locally; a public-key signature (RS256) checked against the JSON Web Key Set (JWKS) on Amazon Web Services (AWS));
`identity_from_claims` takes `cognito:username` and the `org:<slug>` group there, `sub` and `org` locally. Then the important
line: groups and roles come from `GroupCache.get(org_id, sub)`, which resolves from `principals` and
`group_members` in Postgres (`authz/group_cache.py` `resolve_from_db`), cached in Redis for 300 seconds. The
token's own `groups` claim is never copied into the `Principal`. A failed resolution raises `InvalidToken`
(401), never "no groups, carry on".

What to notice: `contracts.py` `Principal` has the docstring "Built ONLY from a verified JWT by
harness.identity". Nothing else constructs one in production code.

## 2. Input guardrail

`api/app.py` `chat` applies the per-principal Redis rate limit, then calls
`guardrails/input.py` `check_input` before the graph sees the question. Over-length text, control characters,
or a question that becomes empty after redaction returns 422. Recognized e-mail, United States phone,
Social Security and payment-card shapes become `[redacted-<kind>]`; recognized prompt-injection phrases are
tagged, not treated as authorization. The redacted verdict is audited and the first server-sent event is
`guardrail {stage:"input", injection_suspected, redactions}`. When configured in cloud mode,
`BedrockGuardrail.apply(..., "INPUT")` adds the managed content, prompt-attack, personal-data and denied-topic
checks. The local stack keeps only the deterministic checks and Redis counter.

What to notice: input guardrails reduce unsafe or sensitive content, but they do not decide access. The
verified Principal, Cedar and the database predicate still own authorization. Full map:
[GUARDRAILS.md](GUARDRAILS.md).

## 3. Principal -> Cedar

Two places ask Cedar, both through the `Authorizer` port (`interfaces.py`):

- `agent/graph.py` `policy` node: `decide(principal, "answer", "Org::<org>")`. The org-wide question "may
  this principal be answered at all" (`db/policies/documents.cedar` `org_read`).
- `harness/runner.py` `run_tool`: `decide(principal, "tool:<name>", "Org::<org>")` before every tool call
  (`db/policies/tools.cedar`: read tools need analyst or admin; write and money tools need admin;
  `cross_org_forbid` denies anything outside the caller's org).

`authz/entities.py` `build_entities` turns the `Principal` into Cedar entities, precomputing `acl_ids`
(`user:alice`, `group:finance`, ...) because Cedar has no map. `authz/cedar_local.py` `decide` and
`authz/verified_permissions.py` `decide` share that mapping; both return `allow=False, fail_closed=True` on
any error and audit every decision as `policy.decision`.

What to notice: the per-document `document_acl_read` policy exists and is tested, but the hot path does not
call Cedar once per chunk. Row-level filtering is the Structured Query Language (SQL) predicate in the next stop; Cedar decides the
coarse question and the tools.

## 4. The SQL predicate

`services/finance_agents/retrieval/sql.py` `ACL_PREDICATE`: an `EXISTS` over `document_acl` joined to
`principals`, matching the caller's user id or any of the caller's group names, in the caller's org.
`SCOPE_PREDICATE` adds `c.org_id = %(org)s AND d.deleted_at IS NULL`. It is spliced into `DENSE_SQL`,
`SPARSE_SQL` and `FACTS_SQL` in the `WHERE`, before `ORDER BY` and `LIMIT`. `acl_params(principal)` is the
only source of the parameters.

`tests/test_architecture.py` `test_every_chunk_or_fact_query_carries_the_acl_predicate` finds every `*_SQL`
constant over `chunks` or `facts` and asserts the predicate is present and precedes `LIMIT`. Add a fourth
query without it and the build fails.

## 5. Hybrid retrieval and reciprocal rank fusion (RRF)

`retrieval/hybrid.py` `PgHybridRetriever.retrieve`: embed the rewritten query (`Embedder.embed`), run
`DENSE_SQL` (cosine over the Hierarchical Navigable Small World index (HNSW), pinned to the stored `embedding_model`, with
`hnsw.iterative_scan = relaxed_order` set by `db.fetch`) and `SPARSE_SQL` (`websearch_to_tsquery` over the generated
`tsvector`) with the same access control list (ACL) parameters, then `rrf_fuse` the two id lists with k = 60. Everything fused was
already authorized; `acl_filtered=True` on the result is a statement about the SQL, not a flag anyone can
set from data.

`tests/test_retrieval.py` `test_rrf_ordering_fixed_input` pins the fusion arithmetic; `test_retriever_fuses_and_audits`
injects a fetch that asserts the predicate and the principal's groups on each call.

## 6. Rerank

Still in `retrieve`: `Reranker.rerank(query.original, candidates, rerank_top_n)`. `retrieval/rerank.py`
offers `BAAI/bge-reranker-base` locally, Cohere Rerank 3.5 through `bedrock-runtime invoke_model`, or
`NoopReranker` for a degraded mode. Documents are sent as text only and mapped back by index. The reranker
receives candidates that passed the predicate and nothing else. Then the relevance floor: chunks scoring below
`HR_RERANK_MIN_SCORE` are dropped, so readable-but-irrelevant evidence counts as absent and the turn abstains
(this is why bob abstains on the Apple supply-chain question; architecture decision record [ADR 0009](adr/0009-rerank-relevance-floor.md)).

`retrieve` ends with `audit.record("retrieval", ...)`: query, counts, and the document and chunk ids of what
was returned.

## 7. The tool door

`harness/tools/search_documents.py` `SearchDocumentsTool.run` wraps the retriever; it returns
`retrieval_not_acl_filtered` if a retriever ever came back unmarked. In the graph, `plan` asks the model to
choose tools from `registry.specs_for(principal)` (`gateway.plan`, Bedrock Converse native tool use; the fake
gateway's `plan_heuristic` is deterministic) and `act` runs each choice. `harness/runner.py` `run_tool` is
what `act` calls: Cedar (stop 3); the risk gate (a write or money tool returns `approval_required` unless
`approved=True`); the idempotency reservation (`INSERT ... ON CONFLICT DO NOTHING RETURNING`); then the call
inside `asyncio.timeout`, Tenacity (three attempts on `TransientError`, `TimeoutError`, `ConnectionError`) and a
per-tool pybreaker; then `audit.record("tool.call", ...)`. `harness/budget.py` `TurnBudget.charge` is called
by the graph around each tool and model call.

`mcp_server/server.py` binds the same registry to a Model Context Protocol (MCP) server for the principal in `HR_TOKEN`; a Claude
Desktop call lands in the same `run_tool`, so a write tool answers `approval_required` there: only the graph passes `approved=True`.

## 8. Generate

`agent/graph.py` `act` stores the tools' chunks, facts and citations in state (a lookup the model planned
nothing for still gets one `search_documents`); `_evidence` builds the
numbered evidence list from `state["chunks"]` and `state["facts"]` and nothing else. `generate` sends
`GENERATE_SYSTEM` ("answer using ONLY the numbered evidence, cite each sentence with [n], else reply exactly
with the abstain text") and the evidence to `ModelGateway.complete(ModelRole.generate, ...)`. With no
evidence, the model is not called at all. `gateway/bedrock.py` picks Claude Sonnet 4.5 for this role;
`gateway/fake.py` builds a deterministic answer from the evidence lines.

## 9. Verify

`verify` node: `ModelRole.verify` (Claude Haiku 4.5 on Bedrock) receives `ANSWER` and `EVIDENCE` and
returns `{grounded, confidence, unsupported_claims}` as JavaScript Object Notation (JSON), parsed by `extract_json` into a
`Verification`. An unparsable verifier is a failed verification. `after_verify`: grounded -> `answer`;
otherwise regenerate once with the unsupported claims named in the system prompt, then `abstain`.
`tests/test_agent.py::test_ungrounded_draft_is_regenerated_then_abstains` proves the loop and its ceiling.

## 10. Answer, output guardrail, audit

`answer` node maps the `[n]` markers in the draft back to citations (`Citation` carries document id, chunk
id, title, uri, section, version, snippet) and `_finish` writes the `answer` audit row: abstained, grounded,
citations, model, latency. `abstain` writes the same row with a reason (`policy_denied`, `out_of_scope`,
`no_authorized_evidence`, `not_grounded`). At the API boundary `_guard_output` runs
`guardrails/output.py` `check_output`, redacts personal data, flags prose with zero citations, optionally
applies the managed Bedrock Guardrail, emits `guardrail {stage:"output", redactions, uncited}`, and then
leaves `answer` as the final event. `api/app.py` `_sse` streams the sequence.

## 11. Checkpoint, replay, human in the loop (HITL)

Every node's return is a checkpoint written by the LangGraph saver: `AsyncPostgresSaver` on the control
database (`graph.startup`), `MemorySaver` in tests. `replay` lists a thread's checkpoints oldest first with
a state summary; `_assert_owner` checks the thread's stored principal against the caller before `run_turn`,
`stream_turn`, `approve` and `replay`. For a write or money tool chosen by `plan`, `act`
calls the `hitl()` helper, whose `interrupt()` parks the thread (it is a helper inside `act`, not a graph
node); `approve()` resumes it with `Command(resume=...)`. The node re-runs with the decision: denied ->
audited, nothing changes; approved -> `run_tool(..., approved=True)` with an idempotency key from
`_idem_key` (`thread:tool:sha256(args)[:16]`, prefixed with the org and tool by the runner), so a second
resume cannot run it twice and a second ticket with different arguments still runs.

## Where a change goes

| You want to | Edit | And its test |
|---|---|---|
| Add a reader rule (e.g. a role may read every filing) | `db/policies/documents.cedar` or the ACL rows written by `ingest/acl.py` | `tests/test_authz.py` |
| Add a tool | `harness/tools/<name>.py`, register in `tools/__init__.py`, add `tool:<name>` to `db/policies/schema.cedarschema` and `tools.cedar` | `tests/test_harness.py`, `tests/test_authz.py` |
| Change ranking | `retrieval/hybrid.py` (fusion) or `retrieval/rerank.py` | `tests/test_retrieval.py` |
| Change a query | `retrieval/sql.py` only | `tests/test_architecture.py` guards the predicate |
| Add a graph node | `agent/graph.py` `_graph()`, the README and ARCHITECTURE state diagrams | `tests/test_agent.py` |
| Add a source system | `ingest/<source>.py` + an `acl.py` mapping; `writer.py` is source-agnostic | `tests/test_ingest.py` |
| Change turn-boundary safety | `guardrails/` and `api/app.py`; update `GUARDRAILS.md` | `tests/test_guardrails.py`, `tests/test_api.py` |
