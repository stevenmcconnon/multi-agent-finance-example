import { Link, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { errorMessage, setToken } from "@/lib/api";
import { finishLogin } from "@/lib/auth";

export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    finishLogin(new URLSearchParams(window.location.search)).then(
      (idToken) => {
        setToken(idToken);
        navigate("/chat", { replace: true });
      },
      (e) => setError(errorMessage(e)),
    );
  }, [navigate]);

  return (
    <div className="mx-auto max-w-md pt-10 text-sm">
      {error ? (
        <p className="rounded border border-red-300 bg-red-50 p-3 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
          {error} — <Link to="/" className="underline">back to sign in</Link>
        </p>
      ) : (
        <p className="text-neutral-500">Completing sign in…</p>
      )}
    </div>
  );
}
