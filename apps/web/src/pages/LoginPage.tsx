import { useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { AUTH_MODE, PERSONAS, devToken, errorMessage, setToken } from "@/lib/api";
import { startLogin } from "@/lib/auth";


export default function LoginPage() {
  const navigate = useNavigate();
  const notice = (useLocation().state as { notice?: string } | null)?.notice; // set by the header on a 401
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const pick = async (user: (typeof PERSONAS)[number]["user"]) => {
    setBusy(user);
    setError(null);
    try {
      setToken(await devToken(user));
      navigate("/chat");
    } catch (e) {
      setError(errorMessage(e));
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-md space-y-6 pt-10">
      <div>
        <h1 className="text-2xl font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
          A multi-agent assistant over real company filings. Each person only gets answers from documents they are allowed to read, every answer cites its sources, and a human approves any action.
        </p>
      </div>
      {notice && !error && <p className="rounded border border-neutral-300 bg-neutral-100 p-3 text-sm dark:border-neutral-700 dark:bg-neutral-900">{notice}</p>}
      {AUTH_MODE === "local" ? (
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-wide text-neutral-500">Dev identities (local mode)</p>
          {PERSONAS.map((i) => (
            <button
              key={i.user}
              disabled={busy !== null}
              onClick={() => pick(i.user)}
              className="flex w-full items-center justify-between gap-4 rounded border border-neutral-300 bg-white px-4 py-3 text-left hover:border-blue-500 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <span className="font-medium">{i.user}</span>
              <span className="text-right text-sm text-neutral-500">{busy === i.user ? "signing in…" : `${i.role}: ${i.desc}`}</span>
            </button>
          ))}
        </div>
      ) : (
        <button
          disabled={busy !== null}
          onClick={() => {
            setBusy("cognito");
            startLogin().catch((e) => {
              setError(errorMessage(e));
              setBusy(null);
            });
          }}
          className="rounded bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Sign in with Cognito
        </button>
      )}
      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
    </div>
  );
}
