# Architecture decision record (ADR) 0008: The model selects tools; the harness enforces

Status: accepted · 2026-11

## Context

The first turn graph chose tools by routing alone: after `policy`, code branched to `facts`, `retrieve` or a
write path from the intent label and a regex for the one write tool. It was safe, but a reviewer reading it saw
a pipeline with a language model at the end, not an agent: the model never chose anything that ran.

## Decision

A `plan` node asks the model for tool calls through Bedrock Converse native tool use (`gateway.plan`,
`toolConfig` in, `toolUse` blocks out). The tools it is offered are `registry.specs_for(principal)`, so it only
sees what the caller's roles may use, and the number of calls is capped by the turn's tool-call budget. The
`act` node runs every chosen call through `harness/runner.py::run_tool`: Cedar, the risk gate, idempotency,
timeout, retries, breaker, audit. A write call stops at `interrupt()` inside `act` until a human
approves. A tool name the model invents is dropped by the gateway, and one it forces is refused by Cedar. A
lookup the model planned nothing for still gets one `search_documents`, so a question is never answered
without a retrieval attempt.

The model chooses; it never authorizes. Every guarantee in `docs/SECURITY.md` still sits in code the model
cannot reach.

In production: Claude picks tools via Converse over `specs_for(principal)`.
In this demo: `FakeGateway.plan` runs `plan_heuristic` (facts for numeric questions, a regex for the one write
tool, else search), because the offline stub is not a model and tests must be deterministic; Bedrock also falls back
to it when the planner call fails.

## Alternatives

- **LangGraph `ToolNode` / a ReAct loop.** The model calls tools until it stops. More agentic, but the loop
  count is open-ended, every tool result re-enters the prompt, and the `interrupt()` and budget logic would live
  in the framework's loop instead of in `run_tool`. One planning step with a bounded call list keeps the turn
  replayable and the evidence path simple. Upgrade path: loop `plan` and `act` when a question needs a result
  from one tool to choose the next.
- **No model choice (routing only).** The status quo: deterministic, easy to prove, not agentic.

## Consequences

- A fourth model call per turn (understand, plan, generate, verify), with its latency and cost.
- The plan is a single shot: it cannot react to a tool's result within the turn.
- Tests cover the planner's contract (`tests/test_gateway.py`: never plans a tool it was not offered, parses
  tool-use blocks) and the gates behind it (`tests/test_agent.py`: a tool outside the registry is refused by
  Cedar, an empty plan still searches).
