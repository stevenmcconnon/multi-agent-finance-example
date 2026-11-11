# Ponytail audit 2026-11-11 (agent, harness, retrieval, ingest, MCP, authz)

Scope: `services/finance_agents/` except `api/app.py`, `gateway/`, `guardrails/`, `authz/audit.py`, `config.py`,
`contracts.py`; plus `tests/` (except `test_api.py`, `test_guardrails.py`) and `evals/suites/`. The 2026-11-10 audit's
findings were all verified closed or recorded as open by choice; nothing here repeats them.

Checks run: `ruff format --check`, `ruff check`, `mypy`, `pytest tests evals/suites` before (159 passed) and after
(165 passed) with Postgres and Valkey up in compose (the DB-gated tests ran). Each behaviour fix below was reverted once
to prove its test fails without it.

What this repo does: a permission-safe RAG agent over SEC filings. One turn is a LangGraph graph (understand → policy →
plan → act → generate → verify) whose tools all pass through `harness.runner.run_tool` (Cedar, approval, idempotency,
breaker, audit); retrieval filters by ACL in SQL before fusion; ingestion pulls EDGAR filings through Temporal into
Postgres; an MCP server exposes the same tools. Load assumed: a demo for a handful of users that claims a production
shape, so anything that would take the one API process down or lose an approved write counts.

## Must fix

**1. A calculator expression freezes the API for everyone** (`retrieval/calculator.py`)
- What this is: `evaluate` walks the AST with Python ints; the exponent cap checks only the right operand of each `**`.
- Problem: `((9**1000)**1000)**1000` passes every check and builds a 400 MB bigint; measured: still running after 15 s.
  `tool.run` is synchronous CPU work on the event loop, so `asyncio.timeout` cannot interrupt it and every request on
  the process stalls. Smaller chains (`(2**1000)**1000`) escaped as `OverflowError`, not `CalculatorError`. Reachable by
  any analyst over MCP or by a model-planned call.
- Fix: evaluate constants as `float` so an oversized result raises `OverflowError` at once (0.002 s), and map
  `OverflowError` to `CalculatorError`. Test: `test_calculator_rejects` now includes the chained power and asserts the
  rejection takes under one second. **APPLIED.**
- If we skip it: one expression hangs the single API task for all users; the tool timeout does not help.

## Should fix

**2. A plan of more read tools than the budget ends the turn in an error** (`agent/team.py` `Researcher._fit`)
- What this is: the planner caps picks at the budget (8); `act` then appends the mandatory `search_documents`, and
  `_fit` trims only trailing `facts_sql` calls.
- Problem: Bedrock plans 8 `calculator` calls for a lookup → 9 calls, nothing to drop → `BudgetExceeded` on the 9th
  charge → the stream ends with `error` and no answer, although 8 calls were fine.
- Fix: `_fit` drops any trailing call that is not the search. Test:
  `test_more_planned_calculations_than_budget_is_capped_not_an_error`. **APPLIED.**
- If we skip it: a chatty planner turns a valid question into an error.

**3. One mistyped ticker fails the whole ingest batch, and Temporal retries it five times** (`ingest/workflow.py`
`discover`)
- What this is: `ciks[ticker.upper()]`.
- Problem: `--tickers AAPL,FOO` raises `KeyError` inside the `discover` activity; the error is deterministic, so the
  retry policy wastes 5 attempts (≈75 s of backoff) and nothing, including AAPL, is ingested.
- Fix: skip the unknown ticker with a warning. Test: `test_discover_skips_an_unknown_ticker`. **APPLIED.**
- If we skip it: a typo costs a minute and an empty corpus with a confusing stack trace.

**4. No test that an unreadable verifier verdict fails closed** (`agent/team.py` `Judge.verify`)
- What this is: the Judge's `except Exception` turns garbage model output into `grounded=False`.
- Problem: this is the fail-closed half of the grounding guarantee and nothing exercised it; a refactor to
  `grounded=True`-on-error would pass the suite.
- Fix: `test_unreadable_verifier_output_fails_verification`. **APPLIED.**
- If we skip it: the verifier's failure mode can silently flip to "pass".

**5. No test that a blown token budget ends the stream with an `error` event** (`agent/graph.py` `_stream`)
- Problem: the only budget test is on the dataclass; the graph path (`BudgetExceeded` → `error` event, no `answer`)
  was unproven.
- Fix: `test_token_budget_exceeded_ends_the_stream_with_an_error_event`. **APPLIED.**

**6. No test that a hung tool is timed out, retried and reported** (`harness/runner.py` `_guarded`)
- Problem: `TimeoutError` is in `RETRY_ON` and the breaker counts it, but no test runs a tool past
  `timeout_seconds`.
- Fix: `test_hung_tool_times_out_is_retried_and_reported` (3 attempts, error starts with `TimeoutError`). **APPLIED.**

**7. `ModelGateway.plan(..., evidence_summary)` is always `None`** (`interfaces.py`, `gateway/fake.py`,
`gateway/bedrock.py`, `agent/team.py`)
- Problem: the only caller (`Planner.plan`) passes `None`; `bedrock.py` has a branch that never runs; four test
  stubs carry the parameter. `docs/ARCHITECTURE.md:100` calls it a hook for a future research loop; the Ponytail rule
  is no "for later" code.
- Fix: drop the parameter from the Protocol, both gateways, `Planner.plan` and the stubs (≈8 lines).
  **DEFERRED (gateway/fake.py, gateway/bedrock.py, docs/ARCHITECTURE.md are outside this boundary).**

## Nice to have

**8. Pass-through query wrappers** (`retrieval/hybrid.py` `dense_query`/`sparse_query`, `retrieval/facts.py`
`facts_query`) — returned `sql.*` constants; only `tests/test_retrieval.py` called them. Deleted; the test reads
`sql.DENSE_SQL` etc. **APPLIED (-12 lines).**

**9. `graph._principal` duplicated `team.principal_of`** — same one-liner in two files. Deleted; graph imports
`principal_of`. **APPLIED (-4).**

**10. Injection parameters nobody passes** — `default_registry(retriever, fact_store)` and
`get_retriever(embed_backend, rerank_backend)`: every caller (graph, MCP, README) uses the no-argument form; tests build
`ToolRegistry` directly. Removed. **APPLIED (-8).**

**11. A loser of the idempotency race whose winner failed waits the full 2 s, then reports `in_progress`**
(`harness/runner.py` `_idem_wait`) — in DB mode `SELECT response` is `None` both for "running" and "released", so after
the winner of an idempotent tool fails and `DELETE`s the key, the loser never re-reserves. The caller gets a
retryable error, nothing is lost. **LEFT (edge of an edge; fixing it needs a three-state read).**

**12. Cognito JWKS cached forever and fetched synchronously on the event loop** (`harness/identity.py` `_jwks`,
`functools.lru_cache`) — a key rotation needs a restart; the one blocking `httpx.get` happens once per process.
**LEFT (demo; add a TTL when a real pool is wired).**

**13. `_assert_owner` is check-then-write** (`agent/graph.py`) — two users racing on the same user-supplied
`thread_id` both pass the owner check before either checkpoint exists; the second overwrites the first. Needs a guessed
thread id at the same instant. **LEFT (demo; the UI uses UUIDs).**

**14. A turn with both a write and evidence drops the write outcome from the answer** (`agent/graph.py` `act`) —
when Bedrock plans `create_ticket` plus a search, the ticket text is only in the `tool` event; the answer is the
grounded draft. **LEFT (Bedrock-only path; the event is shown; a decision on wording, not a bug).**

**15. A filing whose org has none of the reader groups is written with zero ACL rows** (`ingest/writer.py`) — the
document is readable by nobody and only the audit row (`readers: 0`) says so. **LEFT (seed orgs always have the
groups; a warning log would be the fix).**

**16. `GroupCache.invalidate` has no production caller** (`authz/group_cache.py`) — only `tests/test_authz.py` and
`docs/SECURITY.md` reference it. **LEFT (SECURITY.md names it as the operator hook; that doc is outside this
boundary, so the hook stays).**

**17. The Cedar `Document` resource path runs only in tests** (`authz/entities.py` `build_entities`, policy
`document_acl_read`) — production calls decide `answer`/`tool:*` on `Org::` only; row-level ACL is the SQL predicate.
**LEFT (stated defence-in-depth design; the policy files are outside this boundary).**

**18. `FactsInput.period_end` is an unvalidated string** (`harness/tools/facts_sql.py`) — a model passing `"2023"`
reaches `%(period_end)s::date`, Postgres rejects it and the tool reports a generic failure instead of a schema error.
**LEFT (fails safe; a `date | None` field would reject it earlier).**

Verdict: the money paths hold. Every tool call still crosses `run_tool`; the ACL predicate is in every candidate query
(tests pin it); approvals run as the current principal; the fail-closed branches now all have a test. The one real
operational hole was a CPU-bound calculator expression freezing the single event loop, closed with a two-line change.
The rest is trimming and documented demo limits.

Lean: -49 lines possible / -28 applied (+6 added by the merges; the 7 remaining deferred/left lines of code are findings
7, 16 and 17).

Not checked: the Bedrock, Cognito, AVP and S3 adapters against real AWS; a Temporal worker end to end (activities were
tested inline); concurrent load on the single-connection `AsyncPostgresSaver`; `apps/web`, `api/app.py`, `guardrails/`
and the gateways (other owners this round).
