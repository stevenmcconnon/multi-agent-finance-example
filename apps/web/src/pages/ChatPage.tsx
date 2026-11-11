import { Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import EventTimeline, { type TimelineItem } from "@/components/EventTimeline";
import { PERSONAS, approve, errorMessage, getMe, getToken, streamChat, switchPersona } from "@/lib/api";

type Turn = { question: string; items: TimelineItem[]; user: string; groups: string[] };
// These match the quickstart corpus (AAPL 10-K, AAPL/MSFT 8-Ks, XBRL facts); change both together.
const EXAMPLES = [
  { q: "What risks does Apple describe about its supply chain?", path: "Document lookup in the 10-K annual report (alice answers, bob abstains)" },
  { q: "What was AAPL revenue for the fiscal year ending September 2026?", path: "Numeric: structured facts from the filing's XBRL (eXtensible Business Reporting Language) data, cited by source row" },
  { q: "How did AAPL net income change from fiscal 2024 to fiscal 2026?", path: "Numeric comparison across periods" },
  { q: "What did Microsoft report in its most recent 8-K?", path: "8-K current report lookup, readable by every persona" },
  { q: "Create a ticket to review Apple's 10-K risk section", path: "Write tool with human approval (admin only)" },
];
type Persona = (typeof PERSONAS)[number]["user"];

export default function ChatPage() {
  const navigate = useNavigate();
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const [turns, setTurns] = useState<Turn[]>([]);
  // shortcut: compare view keeps only the latest two runs, upgrade when more than two personas are compared at once.
  const [prior, setPrior] = useState<Turn | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [showExamples, setShowExamples] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) navigate("/", { replace: true });
  }, [navigate]);

  const pushEvent = (item: TimelineItem) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, items: [...t.items, item] } : t)));

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  // identity is read at ask time so the column header always names who the server saw
  const start = (q: string, tid: string, fresh: boolean) =>
    run(async () => {
      const me = await getMe();
      const turn: Turn = { question: q, items: [], user: me.user_id, groups: me.groups };
      setTurns((ts) => (fresh ? [turn] : [...ts, turn]));
      await streamChat(q, tid, (ev) => pushEvent({ event: ev }));
    });

  const ask = (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setQuestion("");
    start(q, threadId, false);
  };

  const tryExample = (q: string) => {
    if (busy) return;
    setQuestion("");
    start(q, threadId, turns.length === 0);
  };

  const reAsk = (t: Turn, user: Persona) =>
    run(async () => {
      await switchPersona(user);
      const tid = crypto.randomUUID();
      setPrior(t);
      setThreadId(tid);
      setTurns([]);
      const me = await getMe();
      const turn: Turn = { question: t.question, items: [], user: me.user_id, groups: me.groups };
      setTurns([turn]);
      await streamChat(t.question, tid, (ev) => pushEvent({ event: ev }));
    });

  const decide = (approved: boolean) => {
    // mark the pending hitl card decided, then resume the stream into the same turn
    setTurns((ts) =>
      ts.map((t, i) =>
        i === ts.length - 1
          ? { ...t, items: t.items.map((it) => (it.event.type === "hitl" && it.hitlDecision === undefined ? { ...it, hitlDecision: approved } : it)) }
          : t,
      ),
    );
    run(() => approve(threadId, approved, (ev) => pushEvent({ event: ev })));
  };

  const newThread = () => {
    setThreadId(crypto.randomUUID());
    setTurns([]);
    setPrior(null);
    setError(null);
    setShowExamples(false);
  };

  const column = (list: Turn[], live: boolean) =>
    list.map((t, i) => {
      const policy = t.items.find((it) => it.event.type === "policy")?.event;
      const done = t.items.some((it) => it.event.type === "answer");
      const last = live && i === list.length - 1;
      return (
        <section key={i} className="min-w-0 space-y-3">
          {prior && (
            <div className="flex flex-wrap items-center gap-2 border-b border-neutral-300 pb-1 text-sm dark:border-neutral-700">
              <span className="font-semibold">{t.user}</span>
              {t.groups.map((g) => (
                <span key={g} className="rounded bg-neutral-200 px-1.5 py-0.5 text-xs dark:bg-neutral-800">
                  {g}
                </span>
              ))}
              {policy?.type === "policy" && (
                <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${policy.data.allow ? "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200" : "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200"}`}>
                  policy {policy.data.allow ? "allow" : "deny"}
                </span>
              )}
            </div>
          )}
          <p className="rounded bg-neutral-200/60 px-3 py-2 font-medium dark:bg-neutral-800">{t.question}</p>
          {t.items.length > 0 && <p className="text-xs text-neutral-500">Grey = deterministic step, blue = model call, orange = a human decides</p>}
          <EventTimeline items={t.items} busy={busy && last} onDecide={decide} />
          {done && !busy && (
            <div className="flex flex-wrap gap-2">
              {PERSONAS.filter((p) => p.user !== t.user).map((p) => (
                <button key={p.user} onClick={() => reAsk(t, p.user)} className="rounded-full border border-blue-300 px-3 py-1 text-xs text-blue-700 hover:bg-blue-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950">
                  Re-ask as {p.user}
                </button>
              ))}
            </div>
          )}
        </section>
      );
    });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <h1 className="text-xl font-semibold">Chat</h1>
        <span className="truncate font-mono text-xs text-neutral-500">thread {threadId}</span>
        <span className="ml-auto flex gap-3">
          {turns.length > 0 && (
            <Link to={`/threads/${threadId}`} className="underline">
              Replay this turn
            </Link>
          )}
          <button onClick={newThread} disabled={busy} className="rounded border border-neutral-300 px-2 py-1 hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800">
            New thread
          </button>
        </span>
      </div>

      {prior ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="min-w-0 space-y-6">{column([prior], false)}</div>
          <div className="min-w-0 space-y-6">{column(turns, true)}</div>
        </div>
      ) : (
        column(turns, true)
      )}

      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}

      {(turns.length === 0 || showExamples) && !prior && (
        <div className="space-y-2">
          <p className="text-sm font-semibold">Try one of these</p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {EXAMPLES.map((ex) => (
              <li key={ex.q} className="min-w-0">
                <button type="button" onClick={() => tryExample(ex.q)} disabled={busy} className="max-w-full rounded-2xl border border-blue-300 px-3 py-1 text-left text-sm text-blue-700 hover:bg-blue-50 disabled:opacity-50 dark:border-blue-700 dark:text-blue-300 dark:hover:bg-blue-950">
                  {ex.q}
                </button>
                <p className="px-1 text-xs text-neutral-500">{ex.path}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form onSubmit={ask} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={turns.length ? "Follow-up question…" : "Ask about the filings…"}
          disabled={busy}
          className="min-w-0 flex-1 rounded border border-neutral-300 bg-white px-3 py-2 dark:border-neutral-700 dark:bg-neutral-900"
        />
        <button type="submit" disabled={busy || !question.trim()} className="rounded bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50">
          Ask
        </button>
        {turns.length > 0 && (
          <button type="button" onClick={() => setShowExamples((v) => !v)} className="self-center text-sm underline">
            Examples
          </button>
        )}
      </form>
    </div>
  );
}
