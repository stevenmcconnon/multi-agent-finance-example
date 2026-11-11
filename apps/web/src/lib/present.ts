// Pure wording helpers: what a person reads instead of a model id, an HTTP body or a reason code. No deps, so node --test runs them.

export function modelName(id: string): string {
  if (id === "fake" || id === "offline-stub") return "offline stub";
  if (id.includes("claude-sonnet-5-5")) return "Claude Sonnet 5.5";
  if (id.includes("claude-haiku-5-5")) return "Claude Haiku 5.5";
  return id;
}

/** "" for the placeholder the ingest pipeline gives text before the first heading. */
export const sectionLabel = (section?: string | null) => (!section || section.trim().toLowerCase() === "preamble" ? "" : section);

export const SESSION_EXPIRED = "Your session expired. Pick a persona to continue.";
export const STREAM_CLOSED = "The connection closed before the turn finished. Ask again.";
export const THREAD_OWNER = "This thread belongs to someone else, because a thread belongs to the person who opened it. Start a new thread to continue.";

const GUARDRAIL_REASON: Record<string, string> = {
  too_long: "it is too long",
  control_characters: "it contains control characters",
  empty_after_redaction: "nothing is left of it once personal data is removed",
  bedrock_guardrail: "the content filter blocked it",
};

/** One short sentence for a failed API call; never the raw JSON body. */
export function errorSentence(status: number, body: string): string {
  let detail: unknown = body;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail ?? "";
  } catch {
    // not JSON: keep the text
  }
  if (status === 401) return SESSION_EXPIRED;
  if (status === 403) return typeof detail === "string" && detail.includes("thread") ? THREAD_OWNER : "This identity is not permitted to do that.";
  if (status === 429) return "Too many questions in a minute. Wait a moment and try again.";
  if (status === 422) {
    // the input guardrail sends {reasons: [...]}; FastAPI's own validation sends [{msg}]
    const raw = Array.isArray(detail) ? detail.map((d) => (d as { msg?: unknown }).msg) : ((detail as { reasons?: unknown } | null)?.reasons ?? []);
    const reasons = (Array.isArray(raw) ? raw : []).filter((r): r is string => typeof r === "string").map((r) => GUARDRAIL_REASON[r] ?? r.replaceAll("_", " "));
    return reasons.length ? `The question was not accepted: ${reasons.join("; ")}.` : "The question was not accepted.";
  }
  return typeof detail === "string" && detail ? `The API answered ${status}: ${detail.slice(0, 200)}` : `The API answered ${status}.`;
}

// kind: "answer" = the full answer card; "done" = a completed action (green); "none" = no answer (amber).
export type Outcome = { kind: "answer" | "done" | "none"; badge: string; headline: string };

const ANSWERED: Outcome = { kind: "answer", badge: "Answered", headline: "" };
const none = (headline: string, badge = "No answer"): Outcome => ({ kind: "none", badge, headline });
const BY_REASON: Record<string, Outcome> = {
  answered: ANSWERED,
  no_relevant_evidence: none("None of the documents this person may read answer the question"),
  out_of_scope: none("Not a question about the filings"),
  not_grounded: none("Withheld: the draft failed verification"),
  policy_denied: none("Not permitted"),
  model_declined: none("The model declined"),
  action_done: { kind: "done", badge: "Action completed", headline: "" },
  action_denied: none("Action not approved", "No action"),
  action_forbidden: none("Not permitted for this role", "No action"),
  action_failed: none("The action failed", "No action"),
};

/** Answer.reason is absent on an older API, so `abstained` decides then (and for a reason this build does not know). */
export function answerOutcome(reason: string | null | undefined, abstained: boolean): Outcome {
  return (reason ? BY_REASON[reason] : undefined) ?? (abstained ? none("") : ANSWERED);
}

export function outcomeStrip(o: Outcome, citations: number): string {
  if (o.kind === "answer") return `Answered · ${citations} citation${citations === 1 ? "" : "s"}`;
  return [o.badge, o.headline].filter(Boolean).join(" · ");
}
