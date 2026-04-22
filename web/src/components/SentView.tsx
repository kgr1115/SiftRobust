import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, RefreshCw, Search, SendHorizontal } from "lucide-react";
import { api } from "../api/client";
import { formatDate } from "../lib/utils";

/**
 * Sent pane. Read-only list of threads from Gmail's Sent folder, keyed on the
 * user's most recent outbound message in each thread. Deliberately lighter
 * than the Inbox view: no classify, no draft, no bulk-action surface — sent
 * mail is a record, not something to triage.
 *
 * Each row expands to show the plain-text body of the whole thread so Kyle
 * can verify what actually went out (useful for post-send sanity checks on
 * the Compose flow).
 */
export function SentView() {
  const [limit, setLimit] = useState(25);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    const h = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(h);
  }, [query]);

  const sent = useQuery({
    queryKey: ["sent", limit, debouncedQuery],
    queryFn: () => api.sent(limit, debouncedQuery || undefined),
  });

  const threads = sent.data?.threads ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <SendHorizontal className="h-4 w-4 text-blue-600" />
          Sent
        </h2>
        <span className="text-xs text-slate-500">
          {threads.length} thread{threads.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sent (Gmail query syntax — e.g. to:foo@bar.com)"
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-8 pr-3 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </div>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-sm"
        >
          {[10, 25, 50, 100].map((n) => (
            <option key={n} value={n}>
              {n} threads
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => sent.refetch()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          title="Refresh sent"
        >
          <RefreshCw className={`h-4 w-4 ${sent.isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {sent.isLoading && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
          Loading sent…
        </div>
      )}
      {sent.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {(sent.error as Error).message}
        </div>
      )}

      <ul className="flex flex-col gap-2">
        {threads.map((t) => {
          const open = openId === t.id;
          return (
            <li
              key={t.id}
              className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
            >
              <button
                type="button"
                onClick={() => setOpenId(open ? null : t.id)}
                className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-slate-50"
              >
                <div className="mt-0.5 text-slate-400">
                  {open ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="truncate text-sm font-medium text-slate-900">
                      To {t.to_name}{" "}
                      <span className="text-slate-400">&lt;{t.to}&gt;</span>
                    </div>
                    <div className="shrink-0 text-xs text-slate-500">
                      {formatDate(t.sent_at)}
                    </div>
                  </div>
                  <div className="truncate text-sm text-slate-700">
                    {t.subject || "(no subject)"}
                  </div>
                  <div className="truncate text-xs text-slate-500">
                    {t.snippet}
                  </div>
                </div>
              </button>
              {open && (
                <div className="border-t border-slate-100 bg-slate-50 px-4 py-3">
                  <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-slate-700">
                    {t.body || "(empty body)"}
                  </pre>
                </div>
              )}
            </li>
          );
        })}
        {!sent.isLoading && threads.length === 0 && (
          <li className="rounded-lg border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
            Nothing in Sent. Try widening the search or bumping the limit.
          </li>
        )}
      </ul>
    </div>
  );
}
