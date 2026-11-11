import { Link, useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { errorMessage, getReplay, getToken } from "@/lib/api";
import type { Checkpoint } from "@/lib/schemas";

export default function ReplayPage() {
  const { id } = useParams() as { id: string };
  const navigate = useNavigate();
  const [checkpoints, setCheckpoints] = useState<Checkpoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      navigate("/", { replace: true });
      return;
    }
    getReplay(id).then(setCheckpoints, (e) => setError(errorMessage(e)));
  }, [id, navigate]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold">Replay</h1>
        <span className="truncate font-mono text-xs text-neutral-500">thread {id}</span>
        <Link to="/chat" className="ml-auto text-sm underline">Back to chat</Link>
      </div>
      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
      {checkpoints === null && !error && <p className="text-sm text-neutral-500">Loading…</p>}
      {checkpoints?.length === 0 && <p className="text-sm text-neutral-500">No checkpoints for this thread.</p>}
      <ol className="space-y-4 border-l-2 border-neutral-300 pl-4 dark:border-neutral-700">
        {checkpoints?.map((c) => (
          <li key={c.checkpoint_id} className="relative">
            <span className="absolute -left-[23px] top-1.5 h-3 w-3 rounded-full bg-blue-600" />
            <div className="flex flex-wrap items-baseline gap-2 text-sm">
              <span className="font-mono text-xs text-neutral-500">step {c.step}</span>
              <span className="font-medium">{c.node}</span>
              <time className="text-xs text-neutral-500">{new Date(c.at).toLocaleString()}</time>
            </div>
            <pre className="mt-1 overflow-x-auto rounded bg-white p-2 text-xs dark:bg-neutral-900">
              {typeof c.state_summary === "string" ? c.state_summary : JSON.stringify(c.state_summary, null, 2)}
            </pre>
          </li>
        ))}
      </ol>
    </div>
  );
}
