import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { AUTH_MODE, PERSONAS, clearToken, errorMessage, getMe, getToken, switchPersona } from "@/lib/api";
import type { Principal } from "@/lib/schemas";

export default function Nav() {
  const [me, setMe] = useState<Principal | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const { pathname } = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (getToken()) getMe().then(setMe, () => setMe(null));
  }, [pathname]);

  useEffect(() => {
    const refresh = () => getMe().then(setMe, () => setMe(null));
    window.addEventListener("hr-persona", refresh);
    return () => window.removeEventListener("hr-persona", refresh);
  }, []);

  const switchTo = async (user: (typeof PERSONAS)[number]["user"]) => {
    try {
      await switchPersona(user);
    } catch (e) {
      setSwitchError(errorMessage(e));
    }
  };

  const signOut = () => {
    clearToken();
    setMe(null);
    navigate("/");
  };

  const link = (href: string, label: string) => (
    <Link
      to={href}
      className={`rounded px-2 py-1 hover:bg-neutral-200 dark:hover:bg-neutral-800 ${
        pathname.startsWith(href) ? "font-semibold text-neutral-900 dark:text-white" : "text-neutral-600 dark:text-neutral-400"
      }`}
    >
      {label}
    </Link>
  );

  return (
    <header className="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2 px-4 py-2 text-sm">
        <Link to="/" className="mr-2 font-semibold tracking-tight">
          Harness RAG
        </Link>
        {me && link("/chat", "Chat")}
        {me && link("/documents", "Documents")}
        <span className="ml-auto flex flex-wrap items-center gap-2 text-neutral-600 dark:text-neutral-400">
          {me ? (
            <>
              <span>
                <span className="font-medium text-neutral-900 dark:text-neutral-100">{me.user_id}</span>
                <span className="mx-1">@{me.org_slug}</span>
              </span>
              {me.roles.map((r) => (
                <span key={r} className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800 dark:bg-blue-900/50 dark:text-blue-200">
                  {r}
                </span>
              ))}
              {me.groups.map((g) => (
                <span key={g} className="rounded bg-neutral-200 px-1.5 py-0.5 text-xs dark:bg-neutral-800">
                  {g}
                </span>
              ))}
              {AUTH_MODE === "local" && (
                // In production: one person, one sign-in (Cognito). In this demo: a persona switcher, because the point
                // of the demo is to show the same question under different permissions.
                <select
                  aria-label="Switch persona"
                  value={me.user_id}
                  onChange={(e) => switchTo(e.target.value as (typeof PERSONAS)[number]["user"])}
                  className="rounded border border-neutral-300 bg-white px-1 py-0.5 text-xs dark:border-neutral-700 dark:bg-neutral-900"
                >
                  {PERSONAS.map((p) => (
                    <option key={p.user} value={p.user}>
                      {p.user} ({p.role})
                    </option>
                  ))}
                </select>
              )}
              {switchError && <span className="text-xs text-red-700 dark:text-red-300">{switchError}</span>}
              <button onClick={signOut} className="underline hover:text-neutral-900 dark:hover:text-white">
                Sign out
              </button>
            </>
          ) : (
            <span>Not signed in</span>
          )}
        </span>
      </div>
    </header>
  );
}
