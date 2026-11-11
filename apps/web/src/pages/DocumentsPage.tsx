import { useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { errorMessage, getToken, listDocuments } from "@/lib/api";
import type { Document } from "@/lib/schemas";

export default function DocumentsPage() {
  const navigate = useNavigate();
  const [docs, setDocs] = useState<Document[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      navigate("/", { replace: true });
      return;
    }
    listDocuments().then(setDocs, (e) => setError(errorMessage(e)));
  }, [navigate]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Documents</h1>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">Only what the signed-in identity is allowed to read, filtered by the server per document: alice (finance, analysts) sees the 10-Ks and 10-Qs, bob (engineering) sees only the 8-Ks.</p>
      </div>
      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
      {docs === null && !error && <p className="text-sm text-neutral-500">Loading…</p>}
      {docs?.length === 0 && <p className="text-sm text-neutral-500">No documents are readable by this identity.</p>}
      {docs && docs.length > 0 && (
        <div className="overflow-x-auto rounded border border-neutral-200 dark:border-neutral-800">
          <table className="w-full text-sm">
            <thead className="bg-neutral-100 text-left text-xs uppercase text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400">
              <tr>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Form / metadata</th>
                <th className="px-3 py-2">Version</th>
                <th className="px-3 py-2">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {docs.map((d) => {
                const { form, ...rest } = d.metadata as { form?: unknown } & Record<string, unknown>;
                return (
                  <tr key={d.id} className="bg-white dark:bg-neutral-950">
                    <td className="px-3 py-2 font-medium">{d.title}</td>
                    <td className="px-3 py-2">
                      {form != null && <span className="mr-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/50 dark:text-blue-200">{String(form)}</span>}{" "}
                      <span className="text-xs text-neutral-500">
                        {Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ")}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{d.version ?? "—"}</td>
                    <td className="px-3 py-2">
                      <a href={d.uri} target="_blank" rel="noreferrer" className="text-blue-700 underline dark:text-blue-300">open</a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
