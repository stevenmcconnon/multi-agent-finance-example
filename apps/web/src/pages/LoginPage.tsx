import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { AUTH_MODE, devToken, errorMessage, setToken } from "@/lib/api";
import { startLogin } from "@/lib/auth";

const IDENTITIES = [
  { user: "alice", desc: "analyst · finance, analysts" },
  { user: "bob", desc: "viewer · engineering" },
  { user: "carol", desc: "admin" },
] as const;


export default function LoginPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const pick = async (user: (typeof IDENTITIES)[number]["user"]) => {
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
          Governed RAG over SEC filings. Every answer is policy-checked, ACL-filtered and verified.
        </p>
      </div>
      {AUTH_MODE === "local" ? (
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-wide text-neutral-500">Dev identities (local mode)</p>
          {IDENTITIES.map((i) => (
            <button
              key={i.user}
              disabled={busy !== null}
              onClick={() => pick(i.user)}
              className="flex w-full items-center justify-between rounded border border-neutral-300 bg-white px-4 py-3 text-left hover:border-blue-500 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900"
            >
              <span className="font-medium">{i.user}</span>
              <span className="text-sm text-neutral-500">{busy === i.user ? "signing in…" : i.desc}</span>
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
