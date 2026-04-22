import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, Send } from "lucide-react";
import { api } from "../api/client";

/**
 * The drafts that are actually sitting in Gmail right now. Useful to sanity
 * check that "Save to Drafts" did what it said. Each row has a one-click
 * Send — for when Kyle reviewed the draft in Gmail and just wants to fire.
 */
export function DraftsView() {
  const qc = useQueryClient();
  const drafts = useQuery({
    queryKey: ["gmail-drafts"],
    queryFn: () => api.listDrafts(),
  });

  const send = useMutation({
    mutationFn: (draftId: string) => api.sendDraft(draftId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gmail-drafts"] }),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <FileText className="h-4 w-4 text-blue-600" />
          Gmail drafts
        </h2>
        <span className="text-xs text-slate-500">
          {drafts.data?.length ?? 0} pending
        </span>
        <button
          type="button"
          onClick={() => drafts.refetch()}
          className="ml-auto rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {drafts.isLoading && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
          Loading drafts…
        </div>
      )}
      {drafts.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {(drafts.error as Error).message}
        </div>
      )}

      <ul className="flex flex-col gap-2">
        {(drafts.data ?? []).map((d) => (
          <li
            key={d.id}
            className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-900">
                {d.subject || "(no subject)"}
              </div>
              <div className="truncate text-xs text-slate-500">
                {d.snippet || "(empty body)"}
              </div>
              <div className="mt-1 font-mono text-[10px] text-slate-400">
                draft {d.id.slice(0, 10)}… · thread {d.thread_id.slice(0, 10)}…
              </div>
            </div>
            <button
              type="button"
              onClick={() => send.mutate(d.id)}
              disabled={send.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {send.isPending && send.variables === d.id ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              Send
            </button>
          </li>
        ))}
        {!drafts.isLoading && (drafts.data ?? []).length === 0 && (
          <li className="rounded-lg border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
            No drafts in Gmail. Save one from the Inbox to see it here.
          </li>
        )}
      </ul>
    </div>
  );
}
