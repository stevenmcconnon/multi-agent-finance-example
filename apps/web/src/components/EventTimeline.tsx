import type { TurnEvent } from "@/lib/schemas";
import AnswerCard from "./AnswerCard";
import HitlCard from "./HitlCard";

export type TimelineItem = { event: TurnEvent; hitlDecision?: boolean };

type Props = { items: TimelineItem[]; busy: boolean; onDecide: (approved: boolean) => void };

const Badge = ({ ok, children }: { ok: boolean; children: React.ReactNode }) => (
  <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${ok ? "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200" : "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200"}`}>
    {children}
  </span>
);

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="w-20 shrink-0 pt-0.5 text-right font-mono text-xs uppercase text-neutral-500">{label}</span>
      <div className="min-w-0 flex-1 text-sm">{children}</div>
    </li>
  );
}

export default function EventTimeline({ items, busy, onDecide }: Props) {
  // token events are folded into one streaming preview, shown only until the answer arrives
  const tokens = items.filter((i) => i.event.type === "token").map((i) => (i.event.data as { text: string }).text).join("");
  const hasAnswer = items.some((i) => i.event.type === "answer");
  return (
    <ol className="space-y-2 border-l border-neutral-200 pl-3 dark:border-neutral-800">
      {items.map(({ event: e, hitlDecision }, idx) => {
        switch (e.type) {
          case "status":
            return (
              <Row key={idx} label="status">
                <span className="font-mono">{e.data.node}</span>
                {e.data.message && <span className="text-neutral-500"> — {e.data.message}</span>}
              </Row>
            );
          case "policy":
            return (
              <Row key={idx} label="policy">
                <Badge ok={e.data.allow}>{e.data.allow ? "allow" : "deny"}</Badge>{" "}
                <span className="font-mono">{e.data.action}</span> on <span className="font-mono">{e.data.resource}</span>
                {e.data.fail_closed && <span className="ml-2 font-semibold text-red-700 dark:text-red-300">fail-closed (decided by outage)</span>}
                {e.data.reasons.length > 0 && <ul className="ml-4 list-disc text-neutral-600 dark:text-neutral-400">{e.data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
                {e.data.policy_ids.length > 0 && <p className="text-xs text-neutral-500">policies: {e.data.policy_ids.join(", ")}</p>}
              </Row>
            );
          case "retrieval":
            return (
              <Row key={idx} label="retrieval">
                {e.data.candidate_count} candidates → {e.data.chunks.length} authorized
                <ul className="mt-1 space-y-0.5">
                  {e.data.chunks.map((c, i) => (
                    <li key={`${c.document_id}-${i}`} className="flex flex-wrap gap-x-2 text-neutral-700 dark:text-neutral-300">
                      <span className="break-words">{c.title ?? c.document_id}</span>
                      {c.section && <span className="text-neutral-500">{c.section}</span>}
                      {c.score != null && <span className="font-mono text-xs text-neutral-500">{c.score.toFixed(3)}</span>}
                    </li>
                  ))}
                </ul>
              </Row>
            );
          case "tool":
            return (
              <Row key={idx} label="tool">
                <Badge ok={e.data.ok}>{e.data.ok ? "ok" : "failed"}</Badge> <span className="font-mono">{e.data.tool}</span>
                <span className="text-neutral-500"> · {e.data.latency_ms} ms{e.data.from_cache && " · cached"}</span>
                {e.data.error && <p className="text-red-700 dark:text-red-300">{e.data.error}</p>}
              </Row>
            );
          case "token":
            return null;
          case "hitl":
            return (
              <Row key={idx} label="hitl">
                <HitlCard tool={e.data.tool} arguments={e.data.arguments} reason={e.data.reason} decided={hitlDecision} busy={busy} onDecide={onDecide} />
              </Row>
            );
          case "answer":
            return (
              <Row key={idx} label="answer">
                <AnswerCard answer={e.data} />
              </Row>
            );
          case "error":
            return (
              <Row key={idx} label="error">
                <p className="rounded border border-red-300 bg-red-50 p-2 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
                  <span className="font-mono">{e.data.code}</span>: {e.data.message}
                </p>
              </Row>
            );
        }
      })}
      {tokens && !hasAnswer && (
        <Row label="drafting">
          <p className="whitespace-pre-wrap break-words text-neutral-600 dark:text-neutral-400">{tokens}</p>
        </Row>
      )}
      {busy && (
        <Row label="">
          <span className="animate-pulse text-neutral-500">working…</span>
        </Row>
      )}
    </ol>
  );
}
