import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Search, Sparkles } from "lucide-react";
import { api } from "../api/client";
import { ThreadCard } from "./ThreadCard";
import { ProviderErrorBanner } from "./ProviderErrorBanner";
import { SettingsDialog } from "./SettingsDialog";
import type { Classification, Draft } from "../types";

/**
 * Inbox pane. Loads recent threads, then classifies them (cached on the
 * server) so each ThreadCard has its category badge. Drafting replies is
 * lazy — we only ask the backend to draft replies for the first N threads
 * that actually need one, which keeps token spend sane.
 */
export function InboxView() {
  const qc = useQueryClient();
  const [limit, setLimit] = useState(25);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  // Settings dialog is owned here so provider-error banners can open it with
  // one click. The header has its own independent instance; both end up
  // reading/writing the same ["settings"] React Query cache key.
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Tiny debounce: don't hammer the Gmail search endpoint on each keystroke.
  useEffect(() => {
    const h = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(h);
  }, [query]);

  const inbox = useQuery({
    queryKey: ["inbox", limit, debouncedQuery],
    queryFn: () => api.inbox(limit, debouncedQuery || undefined),
  });

  const labels = useQuery({
    queryKey: ["labels"],
    queryFn: () => api.listLabels(),
    staleTime: 30_000,
  });

  const threads = inbox.data?.threads ?? [];

  // Classify everything we're showing. Server caches per-thread so repeats
  // are cheap.
  const classify = useQuery({
    queryKey: ["classify", threads.map((t) => t.id).join(",")],
    queryFn: () =>
      threads.length === 0
        ? Promise.resolve({ classifications: [] as Classification[] })
        : api.classify(threads),
    enabled: threads.length > 0,
  });

  const classByThread: Record<string, Classification> = useMemo(() => {
    const out: Record<string, Classification> = {};
    for (const c of classify.data?.classifications ?? []) out[c.thread_id] = c;
    return out;
  }, [classify.data]);

  // Draft only for threads that need a reply. This is the expensive step,
  // so make it explicit via a button.
  const replyCandidates = useMemo(
    () =>
      threads.filter((t) => {
        const c = classByThread[t.id];
        return c && (c.category === "urgent" || c.category === "needs_reply");
      }),
    [threads, classByThread],
  );

  const drafting = useMutation({
    mutationFn: async () => {
      const cs = replyCandidates
        .map((t) => classByThread[t.id])
        .filter(Boolean);
      return api.draft(replyCandidates, cs);
    },
    onSuccess: (res) => {
      // Shove drafts into query cache so ThreadCards render them without
      // re-fetching. The shape mirrors api.draft().
      qc.setQueryData(["drafts-map"], (old: Record<string, Draft> | undefined) => ({
        ...(old ?? {}),
        ...res.drafts,
      }));
    },
  });

  const draftsByThread =
    qc.getQueryData<Record<string, Draft>>(["drafts-map"]) ?? {};

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search threads (Gmail query syntax)"
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
          onClick={() => inbox.refetch()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          title="Refresh inbox"
        >
          <RefreshCw className={`h-4 w-4 ${inbox.isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
        <button
          type="button"
          onClick={() => drafting.mutate()}
          disabled={replyCandidates.length === 0 || drafting.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          title="Draft replies for all threads marked urgent or needs_reply"
        >
          <Sparkles className="h-4 w-4" />
          {drafting.isPending
            ? "Drafting…"
            : `Draft replies (${replyCandidates.length})`}
        </button>
      </div>

      {inbox.isLoading && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
          Loading inbox…
        </div>
      )}
      {inbox.error && (
        <ProviderErrorBanner
          error={inbox.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => inbox.refetch()}
        />
      )}
      {classify.error && (
        <ProviderErrorBanner
          error={classify.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => classify.refetch()}
        />
      )}
      {drafting.error && (
        <ProviderErrorBanner
          error={drafting.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => drafting.mutate()}
        />
      )}

      <div className="flex flex-col gap-2">
        {threads.map((t) => (
          <ThreadCard
            key={t.id}
            thread={t}
            classification={classByThread[t.id] ?? null}
            draft={draftsByThread[t.id] ?? null}
            labels={labels.data ?? []}
          />
        ))}
        {!inbox.isLoading && threads.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
            Nothing to show. Try widening the search or bumping the limit.
          </div>
        )}
      </div>

      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}
