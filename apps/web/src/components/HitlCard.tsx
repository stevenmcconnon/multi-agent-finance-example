type Props = {
  tool: string;
  arguments: Record<string, unknown>;
  reason: string;
  decided?: boolean;
  busy: boolean;
  onDecide: (approved: boolean) => void;
};

export default function HitlCard({ tool, arguments: args, reason, decided, busy, onDecide }: Props) {
  return (
    <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-4 dark:border-amber-700 dark:bg-amber-950/40">
      <p className="break-words font-medium text-amber-900 dark:text-amber-200">Approval required: {tool}</p>
      {reason && <p className="break-words text-sm text-amber-900/80 dark:text-amber-200/80">{reason}</p>}
      <pre className="whitespace-pre-wrap break-words rounded bg-white p-2 text-xs dark:bg-neutral-900">{JSON.stringify(args, null, 2)}</pre>
      {decided === undefined ? (
        <div className="flex gap-2">
          <button disabled={busy} onClick={() => onDecide(true)} className="rounded bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50">
            Approve
          </button>
          <button disabled={busy} onClick={() => onDecide(false)} className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50">
            Deny
          </button>
        </div>
      ) : (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">{decided ? "Approved" : "Denied"} — stream resumed.</p>
      )}
    </div>
  );
}
