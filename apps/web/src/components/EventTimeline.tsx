import type { TurnEvent } from "@/lib/schemas";
import { groupTimelineItems, type TimelinePhase } from "@/lib/timeline";
import AnswerCard from "./AnswerCard";
import HitlCard from "./HitlCard";

export type TimelineItem = { event: TurnEvent; hitlDecision?: boolean };

type Props = { items: TimelineItem[]; busy: boolean; onDecide: (approved: boolean) => void };
type Kind = "deterministic" | "model" | "human";

const KIND_STYLE: Record<Kind, string> = {
  deterministic: "bg-teal-100 text-teal-800 dark:bg-teal-900/60 dark:text-teal-200",
  model: "bg-violet-100 text-violet-800 dark:bg-violet-900/60 dark:text-violet-200",
  human: "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200",
};
const KIND_HELP: Record<Kind, string> = {
  deterministic: "Application code makes this decision. The language model cannot change it.",
  model: "A language model interprets, plans, writes, or verifies within deterministic controls.",
  human: "The workflow pauses until a person approves or denies the proposed action.",
};
const AGENT_HELP: Record<string, string> = {
  Guardrail: "Checks and redacts what enters or leaves the turn. It complements authorization; it does not replace it.",
  Planner: "Understands the question and selects only tools this user is allowed to call.",
  Guard: "Checks verified identity and Cedar authorization policy. Errors fail closed.",
  Researcher: "Runs guarded tools and assembles only evidence the current user may read.",
  Approver: "A person who explicitly approves or denies a write or money action.",
  Writer: "Drafts an answer using only the numbered evidence returned by the Researcher.",
  Judge: "Checks the draft against the evidence. Unsupported claims retry once, then abstain.",
  answer: "The grounded answer and its citations, or a safe abstention when evidence is unavailable.",
};

const PHASE: Record<TimelinePhase, { eyebrow: string; title: string; description: string; className: string }> = {
  guardrail: {
    eyebrow: "SAFETY CHECK",
    title: "Inspect and redact the turn boundary",
    description: "Deterministic checks reject malformed input, tag suspected prompt injection, redact personal data, and flag uncited output.",
    className: "border-cyan-300 bg-cyan-50/70 dark:border-cyan-800 dark:bg-cyan-950/25",
  },
  interpret: {
    eyebrow: "INTERPRET",
    title: "Understand the question",
    description: "The Planner identifies intent and entities before any retrieval or tool call.",
    className: "border-violet-300 bg-violet-50/70 dark:border-violet-800 dark:bg-violet-950/25",
  },
  authorization: {
    eyebrow: "AUTHORIZE",
    title: "Check identity and policy",
    description: "The Guard asks Cedar whether this verified principal may answer. A policy outage denies access.",
    className: "border-teal-300 bg-teal-50/70 dark:border-teal-800 dark:bg-teal-950/25",
  },
  planning: {
    eyebrow: "PLAN",
    title: "Choose allowed tools",
    description: "The Planner sees only the tool specifications available to this principal and selects the next calls.",
    className: "border-indigo-300 bg-indigo-50/70 dark:border-indigo-800 dark:bg-indigo-950/25",
  },
  retrieval: {
    eyebrow: "RETRIEVE + ACT",
    title: "Return authorized evidence",
    description: "The Researcher runs every tool through policy, budgets, retries, and audit. Document permissions are filtered inside the query.",
    className: "border-sky-300 bg-sky-50/70 dark:border-sky-800 dark:bg-sky-950/25",
  },
  approval: {
    eyebrow: "HUMAN CHECK",
    title: "Approve or deny the action",
    description: "Write and money tools stop here. Approval resumes the guarded call exactly once; denial changes nothing.",
    className: "border-amber-300 bg-amber-50/70 dark:border-amber-800 dark:bg-amber-950/25",
  },
  verification: {
    eyebrow: "DRAFT + VERIFY",
    title: "Write from evidence, then check every claim",
    description: "The Writer drafts only from retrieved evidence. The Judge verifies grounding before an answer can leave the graph.",
    className: "border-fuchsia-300 bg-fuchsia-50/60 dark:border-fuchsia-800 dark:bg-fuchsia-950/20",
  },
  result: {
    eyebrow: "RESULT",
    title: "Grounded answer or safe abstention",
    description: "A successful answer includes citations. If authorized evidence is missing or verification fails, the system abstains.",
    className: "border-emerald-300 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/20",
  },
  error: {
    eyebrow: "ERROR",
    title: "The turn stopped safely",
    description: "The request failed without bypassing authorization or returning an unsupported answer.",
    className: "border-red-300 bg-red-50/70 dark:border-red-800 dark:bg-red-950/25",
  },
  system: {
    eyebrow: "SYSTEM",
    title: "Orchestration step",
    description: "A checkpointed step in the Supervisor graph.",
    className: "border-neutral-300 bg-white dark:border-neutral-800 dark:bg-neutral-900",
  },
};

function InfoTip({ label, text }: { label: string; text: string }) {
  return (
    <span className="group relative inline-flex shrink-0">
      <button type="button" className="flex h-5 w-5 items-center justify-center rounded-full border border-current text-xs font-bold opacity-60 transition hover:opacity-100 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-blue-500" aria-label={`${label}: ${text}`}>
        ?
      </button>
      <span role="tooltip" className="pointer-events-none absolute right-0 top-7 z-20 w-72 rounded-lg bg-neutral-950 px-3 py-2 text-left text-xs font-normal leading-relaxed text-white opacity-0 shadow-xl transition group-hover:opacity-100 group-focus-within:opacity-100 dark:bg-white dark:text-neutral-950">
        {text}
      </span>
    </span>
  );
}

const Badge = ({ ok, children, title }: { ok: boolean; children: React.ReactNode; title?: string }) => (
  <span title={title} className={`ml-1 rounded px-1.5 py-0.5 text-xs font-medium ${ok ? "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200" : "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200"}`}>
    {children}
  </span>
);

const KindBadge = ({ kind }: { kind?: Kind }) =>
  kind ? <span title={KIND_HELP[kind]} className={`ml-2 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_STYLE[kind]}`}>{kind}</span> : null;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span title={AGENT_HELP[label]} className="w-24 shrink-0 pt-0.5 text-right font-mono text-xs uppercase text-neutral-500 decoration-dotted underline-offset-2 hover:underline">
        {label}
      </span>
      <div className="min-w-0 flex-1 text-sm">{children}</div>
    </li>
  );
}

function EventRow({ item, index, busy, onDecide }: { item: TimelineItem; index: number; busy: boolean; onDecide: (approved: boolean) => void }) {
  const { event: e, hitlDecision } = item;
  switch (e.type) {
    case "guardrail":
      return (
        <Row key={index} label="Guardrail">
          <KindBadge kind="deterministic" />{" "}
          <strong>{e.data.stage === "input" ? "Input checked" : "Output checked"}</strong>
          {e.data.injection_suspected && <Badge ok={false} title="A prompt-injection pattern was detected and recorded. Authorization remains the enforcement boundary.">injection suspected</Badge>}
          {e.data.redactions.length > 0 && <span className="ml-2 text-neutral-600 dark:text-neutral-300">redacted: {e.data.redactions.join(", ")}</span>}
          {e.data.uncited && <Badge ok={false} title="The output contains prose but no citation.">uncited output</Badge>}
          {!e.data.injection_suspected && e.data.redactions.length === 0 && !e.data.uncited && <span className="ml-2 text-neutral-500">no issues found</span>}
        </Row>
      );
    case "status":
      return (
        <Row key={index} label={e.data.agent ?? "status"}>
          <span className="font-mono">{e.data.node}</span>
          <KindBadge kind={e.data.kind} />
          {e.data.message && <span className="text-neutral-500"> — {e.data.message}</span>}
        </Row>
      );
    case "policy":
      return (
        <Row key={index} label="Guard">
          <KindBadge kind="deterministic" />{" "}
          <Badge ok={e.data.allow} title="Cedar's authorization decision for this verified user and action.">{e.data.allow ? "allow" : "deny"}</Badge>{" "}
          <span className="break-all font-mono">{e.data.action}</span> on <span className="break-all font-mono">{e.data.resource}</span>
          {e.data.fail_closed && <span className="ml-2 font-semibold text-red-700 dark:text-red-300">fail-closed (decided by outage)</span>}
          {e.data.reasons.length > 0 && <ul className="ml-4 list-disc text-neutral-600 dark:text-neutral-400">{e.data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
          {e.data.policy_ids.length > 0 && <p className="text-xs text-neutral-500">policies: {e.data.policy_ids.join(", ")}</p>}
        </Row>
      );
    case "retrieval":
      return (
        <Row key={index} label="Researcher">
          <KindBadge kind="deterministic" />{" "}
          <span title="Candidates are found by semantic and keyword search. Only chunks that pass document permissions continue.">
            {e.data.candidate_count} candidates → <strong>{e.data.chunks.length} authorized</strong>
          </span>
          <ul className="mt-2 space-y-1 border-l-2 border-sky-300 pl-3 dark:border-sky-700">
            {e.data.chunks.map((chunk, chunkIndex) => (
              <li key={`${chunk.document_id}-${chunkIndex}`} className="flex flex-wrap gap-x-2 text-neutral-700 dark:text-neutral-300">
                <span className="break-words">{chunk.title ?? chunk.document_id}</span>
                {chunk.section && <span className="text-neutral-500">{chunk.section}</span>}
                {chunk.score != null && <span title="Reranker relevance score" className="font-mono text-xs text-neutral-500">{chunk.score.toFixed(3)}</span>}
              </li>
            ))}
          </ul>
        </Row>
      );
    case "tool":
      return (
        <Row key={index} label="Researcher">
          <KindBadge kind="deterministic" />{" "}
          <Badge ok={e.data.ok} title="Result returned by the policy-enforced tool runner.">{e.data.ok ? "ok" : "failed"}</Badge>{" "}
          <span className="font-mono">{e.data.tool}</span>
          <span className="text-neutral-500"> · {e.data.latency_ms} ms{e.data.from_cache && " · cached"}</span>
          {e.data.error && <p className="text-red-700 dark:text-red-300">{e.data.error}</p>}
        </Row>
      );
    case "token":
      return null;
    case "hitl":
      return (
        <Row key={index} label="Approver">
          <KindBadge kind="human" />
          <HitlCard tool={e.data.tool} arguments={e.data.arguments} reason={e.data.reason} decided={hitlDecision} busy={busy} onDecide={onDecide} />
        </Row>
      );
    case "answer":
      return (
        <Row key={index} label="answer">
          <AnswerCard answer={e.data} />
        </Row>
      );
    case "error":
      return (
        <Row key={index} label="error">
          <p className="rounded border border-red-300 bg-red-50 p-2 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            <span className="font-mono">{e.data.code}</span>: {e.data.message}
          </p>
        </Row>
      );
  }
}

export default function EventTimeline({ items, busy, onDecide }: Props) {
  const tokens = items.filter((item) => item.event.type === "token").map((item) => (item.event.data as { text: string }).text).join("");
  const hasAnswer = items.some((item) => item.event.type === "answer");
  const groups = groupTimelineItems(items);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-neutral-200 bg-white px-4 py-3 text-xs shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
        <strong className="mr-1 text-sm">How to read this turn</strong>
        {(["deterministic", "model", "human"] as const).map((kind) => (
          <span key={kind} title={KIND_HELP[kind]} className={`rounded-full px-2 py-1 font-medium ${KIND_STYLE[kind]}`}>{kind}</span>
        ))}
        <span className="text-neutral-500">Hover or focus a badge, agent name, or ? for context.</span>
      </div>

      {groups.map((group, groupIndex) => {
        const meta = PHASE[group.phase];
        return (
          <section key={`${group.phase}-${groupIndex}`} className={`rounded-xl border p-4 shadow-sm transition hover:shadow-md ${meta.className}`} aria-labelledby={`phase-${groupIndex}`}>
            <header className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[0.68rem] font-bold tracking-[0.18em] text-neutral-500">{meta.eyebrow}</p>
                <h3 id={`phase-${groupIndex}`} className="font-semibold">{meta.title}</h3>
              </div>
              <InfoTip label={meta.title} text={meta.description} />
            </header>
            <ol className="mt-3 space-y-2 border-l-2 border-current/15 pl-2">
              {group.items.map((item, itemIndex) => <EventRow key={itemIndex} item={item} index={itemIndex} busy={busy} onDecide={onDecide} />)}
            </ol>
          </section>
        );
      })}

      {tokens && !hasAnswer && (
        <section className={`rounded-xl border p-4 shadow-sm ${PHASE.verification.className}`}>
          <header className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="text-[0.68rem] font-bold tracking-[0.18em] text-neutral-500">LIVE DRAFT</p>
              <h3 className="font-semibold">Writer is composing from evidence</h3>
            </div>
            <InfoTip label="Live draft" text="This streaming preview is replaced by the verified final answer. It is never treated as the result on its own." />
          </header>
          <p className="whitespace-pre-wrap break-words text-sm text-neutral-600 dark:text-neutral-400">{tokens}</p>
        </section>
      )}

      {busy && <p className="animate-pulse px-4 text-sm text-neutral-500">Supervisor is moving to the next checkpoint…</p>}
    </div>
  );
}
