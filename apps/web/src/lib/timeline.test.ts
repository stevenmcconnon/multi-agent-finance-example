import assert from "node:assert/strict";
import test from "node:test";
import { TurnEvent as TurnEventSchema, type TurnEvent } from "./schemas.ts";
import { groupTimelineItems, timelinePhase } from "./timeline.ts";

const status = (node: string, agent: string, kind: "deterministic" | "model"): TurnEvent => ({
  type: "status",
  data: { node, agent, kind, message: "" },
});

test("maps the agent graph nodes to reader-facing phases", () => {
  assert.equal(timelinePhase({ type: "guardrail", data: { stage: "input", redactions: [] } }), "guardrail");
  assert.equal(timelinePhase(status("understand", "Planner", "model")), "interpret");
  assert.equal(timelinePhase(status("policy", "Guard", "deterministic")), "authorization");
  assert.equal(timelinePhase(status("act", "Researcher", "deterministic")), "retrieval");
  assert.equal(timelinePhase(status("verify", "Judge", "model")), "verification");
});

test("accepts the guardrail events emitted at both ends of the API stream", () => {
  assert.deepEqual(TurnEventSchema.parse({ type: "guardrail", data: { stage: "input", injection_suspected: true, redactions: ["email"] } }).data, {
    stage: "input",
    injection_suspected: true,
    redactions: ["email"],
  });
  assert.equal(TurnEventSchema.parse({ type: "guardrail", data: { stage: "output", redactions: [], uncited: false } }).type, "guardrail");
});

test("groups adjacent phase events and leaves streaming tokens out of the trace cards", () => {
  const items = [
    { event: status("act", "Researcher", "deterministic") },
    { event: { type: "tool", data: { tool: "search_documents", ok: true, latency_ms: 12, from_cache: false } } as TurnEvent },
    { event: { type: "token", data: { text: "draft" } } as TurnEvent },
    { event: status("verify", "Judge", "model") },
  ];
  const groups = groupTimelineItems(items);
  assert.deepEqual(groups.map((group) => [group.phase, group.items.length]), [
    ["retrieval", 2],
    ["verification", 1],
  ]);
});
