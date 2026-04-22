import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Sparkles, Loader2 } from "lucide-react";
import { api } from "../api/client";
import { ProviderErrorBanner } from "./ProviderErrorBanner";
import { SettingsDialog } from "./SettingsDialog";
import type { BriefResponse } from "../types";

/**
 * Morning-brief tab. One button -> one full-pipeline call that classifies,
 * drafts, and renders a deterministic markdown summary Kyle can skim over
 * coffee. The markdown is shown raw-ish (mono spacing) so the structure of
 * the sections stays obvious without a heavy markdown renderer.
 */
export function MorningBrief() {
  const [limit, setLimit] = useState(25);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const run = useMutation({
    mutationFn: () => api.brief(limit),
  });

  const data: BriefResponse | undefined = run.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-sm"
        >
          {[10, 25, 50].map((n) => (
            <option key={n} value={n}>
              {n} threads
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {run.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          Generate morning brief
        </button>
        {data && (
          <span className="text-xs text-slate-500">
            Generated{" "}
            {new Date(data.brief.generated_at).toLocaleTimeString(undefined, {
              hour: "numeric",
              minute: "2-digit",
            })}{" "}
            · {data.brief.items.length} items
          </span>
        )}
      </div>

      {run.error && (
        <ProviderErrorBanner
          error={run.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => run.mutate()}
        />
      )}

      {data && (
        <article className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-800">
            {data.markdown}
          </pre>
        </article>
      )}

      {!data && !run.isPending && (
        <div className="rounded-lg border border-dashed border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
          Run the brief to see urgent items, needs-reply threads with AI
          drafts, and a roll-up of FYI / newsletter traffic — all in one pass.
        </div>
      )}

      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}
