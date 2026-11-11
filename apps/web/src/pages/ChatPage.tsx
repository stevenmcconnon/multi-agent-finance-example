import { Link, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import EventTimeline, { type TimelineItem } from "@/components/EventTimeline";
import { PERSONAS, approve, errorMessage, getMe, getToken, streamChat, switchPersona } from "@/lib/api";
import { answerOutcome, outcomeStrip } from "@/lib/present";

type Turn = { question: string; items: TimelineItem[]; user: string; groups: string[] };
// The exact strings pinned in docs/CONTRACTS.md ("Example questions"); DEMO.md and the tests use the same ones.
const EXAMPLES = [
  { q: "What risks does Apple describe about its supply chain?", path: "Annual report lookup: alice gets a cited answer, bob gets none" },
  { q: "What was Apple's revenue in fiscal 2026?", path: "A number from the filing's structured data, cited to its source row" },
  { q: "How did Apple's net income change from fiscal 2024 to fiscal 2026?", path: "Two figures plus the calculator tool" },
  { q: "Which Microsoft director is not standing for re-election?", path: "Current report lookup: alice and bob can both read it" },
  { q: "Create a ticket to review Apple's 10-K risk section", path: "A write action that waits for human approval (carol); other roles are refused" },
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
  const [notice, setNotice] = useState<string | null>(null);
  const reAsking = useRef(false); // "Re-ask as" switches persona itself and keeps the compare view
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!getToken()) navigate("/", { replace: true });
  }, [navigate]);

  useEffect(() => {
    // the input is disabled during a turn, which drops focus; give it back without scrolling away from the answer
    if (!busy) input.current?.focus({ preventScroll: true });
  }, [busy]);

  const pushEvent = (item: TimelineItem) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, items: [...t.items, item] } : t)));

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
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
    if (!prior) return start(q, threadId, turns.length === 0);
    // out of the compare view: an example is a new question, so it gets a new thread
    const tid = crypto.randomUUID();
    setPrior(null);
    setThreadId(tid);
    start(q, tid, true);
  };

  const reAsk = (t: Turn, user: Persona) =>
    run(async () => {
      reAsking.current = true;
      try {
        await switchPersona(user);
      } finally {
        reAsking.current = false;
      }
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
    setNotice(null);
    setShowExamples(false);
  };

  useEffect(() => {
    // A thread belongs to whoever opened it (the API answers 403 to anyone else), so a header persona switch starts a new one.
    const onPersona = (e: Event) => {
      if (reAsking.current) return;
      newThread();
      setNotice(`Switched to ${(e as CustomEvent<string>).detail}. Started a new thread, because a thread belongs to the person who opened it.`);
    };
    window.addEventListener("hr-persona", onPersona);
    return () => window.removeEventListener("hr-persona", onPersona);
  }, []);

  const column = (list: Turn[], live: boolean) =>
    list.map((t, i) => {
      const policy = t.items.find((it) => it.event.type === "policy")?.event;
      const answer = t.items.find((it) => it.event.type === "answer")?.event;
      const done = answer !== undefined;
      const outcome = answer?.type === "answer" ? answerOutcome(answer.data.reason, answer.data.abstained) : null;
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
          {prior && outcome && answer?.type === "answer" && (
            // the two results sit at different heights under their traces; this line puts them side by side
            <p className={`rounded px-3 py-2 text-sm font-semibold ${outcome.kind === "none" ? "bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-200" : "bg-green-100 text-green-900 dark:bg-green-900/50 dark:text-green-200"}`}>
              {outcomeStrip(outcome, answer.data.citations.length)}
            </p>
          )}
          <div className="rounded-xl border border-blue-300 bg-blue-50/70 px-4 py-3 shadow-sm dark:border-blue-800 dark:bg-blue-950/25">
            <p className="text-[0.68rem] font-bold tracking-[0.18em] text-blue-700 dark:text-blue-300">USER QUESTION</p>
            <p className="mt-1 break-words font-medium">{t.question}</p>
          </div>
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

      {notice && <p className="rounded border border-neutral-300 bg-neutral-100 p-3 text-sm dark:border-neutral-700 dark:bg-neutral-900">{notice}</p>}

      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}

      {(turns.length === 0 || showExamples) && (
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
          ref={input}
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
