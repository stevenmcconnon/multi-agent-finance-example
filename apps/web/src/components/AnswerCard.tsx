import type { Answer } from "@/lib/schemas";
import CitationList from "./CitationList";

export default function AnswerCard({ answer }: { answer: Answer }) {
  const pct = Math.round(answer.confidence * 100);
  const v = answer.verification;
  if (answer.abstained) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950/40">
        <p className="flex flex-wrap items-center gap-2 font-medium text-amber-900 dark:text-amber-200">
          <span className="rounded bg-amber-600 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-white">Abstained</span>
          No authorized evidence, so no answer
        </p>
        {answer.text && <p className="mt-1 text-sm text-amber-900/80 dark:text-amber-200/80">{answer.text}</p>}
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <p className="whitespace-pre-wrap break-words">{answer.text}</p>
      <div className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
        <span>Confidence</span>
        <span className="h-2 w-32 overflow-hidden rounded bg-neutral-200 dark:bg-neutral-800" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <span className={`block h-full ${pct >= 70 ? "bg-green-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500"}`} style={{ width: `${pct}%` }} />
        </span>
        <span>{pct}%</span>
      </div>
      {v && (
        <p className={`text-xs ${v.grounded ? "text-green-700 dark:text-green-300" : "text-red-700 dark:text-red-300"}`}>
          Verification: {v.grounded ? "grounded" : "not grounded"} · {v.unsupported_claims.length} unsupported claim
          {v.unsupported_claims.length === 1 ? "" : "s"}
          {v.verifier_model && <span className="text-neutral-500"> · {v.verifier_model}</span>}
          {v.unsupported_claims.length > 0 && (
            <ul className="ml-4 mt-1 list-disc">
              {v.unsupported_claims.map((c) => <li key={c}>{c}</li>)}
            </ul>
          )}
        </p>
      )}
      <CitationList citations={answer.citations} />
    </div>
  );
}
