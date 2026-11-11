# agent/

The L6 Orchestration layer: one question-and-answer turn as a LangGraph state machine, checkpointed so it can pause for a human and be replayed.

## Files

| File | What it owns |
|---|---|
| `graph.py` | The Supervisor: nodes `understand -> policy -> plan -> act -> generate -> verify -> answer or abstain`, routing, checkpointer, and the entry points `run_turn`, `stream_turn`, `approve`, `replay`. |
| `team.py` | The six agents as small classes: `Guard`, `Planner`, `Researcher`, `Approver`, `Writer`, `Judge`, each with a `name` and a `kind`; the prompts (`Writer.SYSTEM`, `Judge.SYSTEM`) and `evidence()`, the only thing a model is shown. |
| `__init__.py` | Points to the entry points pinned in `docs/CONTRACTS.md`. |

## How it is used

`api/app.py` calls `stream_turn()` for `/chat` and `approve()` for `/chat/{thread_id}/approve`. Each node in `graph.py` is a thin call into the agent in `team.py` that owns the work. `Guard` and `Researcher` are deterministic; `Planner`, `Writer` and `Judge` call the model; `Approver` is a person.

## Rules that hold here

- Nodes delegate to `team.py`. Status events carry `agent` and `kind` (asserted in `tests/test_agent.py`).
- The agent never imports `psycopg`: `test_agent_never_imports_psycopg` in `tests/test_architecture.py`.
- `Guard` and `Researcher` never touch the gateway: `test_deterministic_agents_never_reference_the_gateway`.
- The Researcher keeps the required document search and caps trailing planned read calls to the remaining
  tool budget instead of turning an overlong plan into an error.
- Unreadable verifier output is `grounded=False`; unsupported output never becomes an answer. A token-budget
  failure ends the stream with `error {code:"budget_exceeded"}` and no `answer`.
- A numeric question is answered from facts: `facts_sql` runs first, the document search is skipped when facts come back, and a change between two periods comes from the `calculator` tool (`test_numeric_question_routes_through_facts`, `test_two_named_years_are_filtered_and_the_change_comes_from_the_calculator`).
- A write tool on the caller's menu pauses in `Approver.ask` (an `interrupt`) until a human approves; the tool still goes through `run_tool`. A write tool the caller was not offered goes straight to `run_tool`, where Cedar refuses it (`test_ticket_request_from_a_role_without_the_tool_is_refused_at_the_door`).
- Every `Answer` carries a `reason` (`docs/CONTRACTS.md`) and a sentence a person can read for it.
- When the graph changes, update the node table in `docs/ARCHITECTURE.md` and `docs/diagrams/multi-agent-rag-architecture.svg` in the same commit (see `docs/diagrams/README.md`).

## Production vs this demo

In production: `AsyncPostgresSaver` checkpoints on the control-plane database, and with `HR_MODE=aws` no checkpointer means no boot.
In this demo: a per-process `MemorySaver` with a warning is the fallback, so the API runs without Postgres.

## Read next

- [`../harness/README.md`](../harness/README.md), the tool door the Researcher uses.
- [`../../docs/TOUR.md`](../../../docs/TOUR.md) stops 7 to 11.
