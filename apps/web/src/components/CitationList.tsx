import { sectionLabel } from "@/lib/present";
import type { Citation } from "@/lib/schemas";

export default function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <ol className="space-y-1 text-sm">
      {citations.map((c, i) => (
        <li key={`${c.document_id}-${c.chunk_id ?? i}`} className="flex gap-2">
          <span className="shrink-0 text-neutral-500">[{i + 1}]</span>
          <span className="min-w-0 break-words">
            <span className="font-medium">{c.title ?? c.document_id}</span>
            {sectionLabel(c.section) && <span className="text-neutral-500"> · section: {c.section}</span>}
            {c.version && <span className="text-neutral-500"> · version {c.version}</span>}
            {" · "}
            <a href={c.uri} target="_blank" rel="noreferrer" className="text-blue-700 underline dark:text-blue-300">
              Open filing on sec.gov
            </a>
            {c.snippet && <span className="block text-neutral-600 dark:text-neutral-400">“{c.snippet}”</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
