# agent/

The L6 Orchestration layer: one question-and-answer turn as a LangGraph state machine, checkpointed so it can pause for a human and be replayed.

## Files

| File | What it owns |
|---|---|
| `graph.py` | The Supervisor: nodes `understand -> policy -> plan -> act -> generate -> verify -> answer or abstain`, routing, checkpointer, and the entry points `run_turn`, `stream_turn`, `approve`, `replay`. |
| `team.py` | The six agents as small classes: `Guard`, `Planner`, `Researcher`, `Approver`, `Writer`, `Judge`, each with a `name` and a `kind`. |
| `__init__.py` | Points to the entry points pinned in `docs/CONTRACTS.md`. |

## How it is used

`api/app.py` calls `stream_turn()` for `/chat` and `approve()` for `/chat/{thread_id}/approve`. Each node in `graph.py` is a thin call into the agent in `team.py` that owns the work. `Guard` and `Researcher` are deterministic; `Planner`, `Writer` and `Judge` call the model; `Approver` is a person.

## Rules that hold here

- Nodes delegate to `team.py`. Status events carry `agent` and `kind` (asserted in `tests/test_agent.py`).
- The agent never imports `psycopg`: `test_agent_never_imports_psycopg` in `tests/test_architecture.py`.
- `Guard` and `Researcher` never touch the gateway: `test_deterministic_agents_never_reference_the_gateway`.
- Write and money tools pause on an `interrupt` until a human approves; the tool still goes through `run_tool`.
- When the graph changes, update the state diagram in the root README in the same commit (see `docs/diagrams/README.md`).

## Production vs this demo

In production: `AsyncPostgresSaver` checkpoints on the control-plane database, and with `HR_MODE=aws` no checkpointer means no boot.
In this demo: a per-process `MemorySaver` with a warning is the fallback, so the API runs without Postgres.

## Read next

- [`../harness/README.md`](../harness/README.md), the tool door the Researcher uses.
- [`../../docs/TOUR.md`](../../../docs/TOUR.md) stops 7 to 10.
