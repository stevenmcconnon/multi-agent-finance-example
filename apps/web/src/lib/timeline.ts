import type { TurnEvent } from "./schemas.ts";

export type TimelinePhase =
  | "guardrail"
  | "interpret"
  | "authorization"
  | "planning"
  | "retrieval"
  | "approval"
  | "verification"
  | "result"
  | "error"
  | "system";

export function timelinePhase(event: TurnEvent): TimelinePhase {
  switch (event.type) {
    case "guardrail":
      return "guardrail";
    case "policy":
      return "authorization";
    case "retrieval":
    case "tool":
      return "retrieval";
    case "hitl":
      return "approval";
    case "answer":
      return "result";
    case "error":
      return "error";
    case "token":
      return "verification";
    case "status":
      if (event.data.node === "understand") return "interpret";
      if (event.data.node === "policy" || event.data.agent === "Guard") return "authorization";
      if (event.data.node === "plan") return "planning";
      if (event.data.node === "act" || event.data.agent === "Researcher") return "retrieval";
      if (["generate", "verify"].includes(event.data.node) || ["Writer", "Judge"].includes(event.data.agent ?? "")) return "verification";
      return "system";
  }
}

export function groupTimelineItems<T extends { event: TurnEvent }>(items: T[]): Array<{ phase: TimelinePhase; items: T[] }> {
  const groups: Array<{ phase: TimelinePhase; items: T[] }> = [];
  for (const item of items) {
    if (item.event.type === "token") continue;
    const phase = timelinePhase(item.event);
    const last = groups.at(-1);
    if (last?.phase === phase) last.items.push(item);
    else groups.push({ phase, items: [item] });
  }
  return groups;
}
