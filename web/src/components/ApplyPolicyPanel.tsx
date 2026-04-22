import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Shield, Zap } from "lucide-react";
import { api } from "../api/client";
import { ProviderErrorBanner } from "./ProviderErrorBanner";
import { SettingsDialog } from "./SettingsDialog";
import { CATEGORY_VALUES } from "../types";
import type { ActionPolicy, ApplyReport, Category, Classification, Thread } from "../types";

const SAFE_BULK: Category[] = ["fyi", "newsletter", "trash"];

/**
 * The "batch apply" panel. Kyle picks a batch size and policy (dry-run by
 * default), and the backend runs classify → action pipeline. Only the
 * categories in SAFE_BULK can be bulk-auto-actioned; urgent / needs_reply
 * always stay in the inbox for Kyle's eyes. The UI mirrors that rule so
 * there's no way to ship a footgun config.
 */
export function ApplyPolicyPanel() {
  const [limit, setLimit] = useState(50);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [policy, setPolicy] = useState<ActionPolicy>({
    dry_run: true,
    min_confidence: 0.7,
    apply_labels: {},
    archive_categories: ["newsletter", "trash"],
    mark_read_categories: ["newsletter", "fyi"],
  });

  const inbox = useQuery({
    queryKey: ["inbox-apply", limit],
    queryFn: () => api.inbox(limit),
  });
  const threads: Thread[] = inbox.data?.threads ?? [];

  const classify = useQuery({
    queryKey: ["classify-apply", threads.map((t) => t.id).join(",")],
    queryFn: () =>
      threads.length === 0
        ? Promise.resolve({ classifications: [] as Classification[] })
        : api.classify(threads),
    enabled: threads.length > 0,
  });

  const classifications = classify.data?.classifications ?? [];

  const byCategory = useMemo(() => {
    const out: Record<Category, number> = {
      urgent: 0,
      needs_reply: 0,
      fyi: 0,
      newsletter: 0,
      trash: 0,
    };
    for (const c of classifications) out[c.category] += 1;
    return out;
  }, [classifications]);

  const run = useMutation({
    mutationFn: (): Promise<ApplyReport> =>
      api.apply(threads, classifications, policy),
  });

  const toggleIn = (
    key: "archive_categories" | "mark_read_categories",
    cat: Category,
  ) => {
    setPolicy((p) => {
      const set = new Set(p[key]);
      if (set.has(cat)) set.delete(cat);
      else set.add(cat);
      return { ...p, [key]: Array.from(set) };
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-2">
          <Shield className="h-4 w-4 text-blue-600" />
          <h2 className="text-sm font-semibold">Policy</h2>
          <span className="ml-auto text-xs text-slate-500">
            Urgent and needs-reply never auto-action — they always stay for you.
          </span>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={policy.dry_run}
              onChange={(e) =>
                setPolicy((p) => ({ ...p, dry_run: e.target.checked }))
              }
              className="h-4 w-4 rounded border-slate-300"
            />
            <span className="font-medium">Dry run</span>
            <span className="text-xs text-slate-500">
              (preview actions without touching Gmail)
            </span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <span className="font-medium">Min confidence</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={policy.min_confidence}
              onChange={(e) =>
                setPolicy((p) => ({
                  ...p,
                  min_confidence: Number(e.target.value),
                }))
              }
              className="flex-1"
            />
            <span className="tabular-nums text-xs text-slate-600">
              {Math.round(policy.min_confidence * 100)}%
            </span>
          </label>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <PolicyRow
            title="Archive"
            hint="Move the thread out of the inbox."
            categories={SAFE_BULK}
            selected={policy.archive_categories}
            onToggle={(c) => toggleIn("archive_categories", c)}
          />
          <PolicyRow
            title="Mark read"
            hint="Drops the unread badge."
            categories={SAFE_BULK}
            selected={policy.mark_read_categories}
            onToggle={(c) => toggleIn("mark_read_categories", c)}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-sm"
        >
          {[25, 50, 100, 200].map((n) => (
            <option key={n} value={n}>
              {n} threads
            </option>
          ))}
        </select>
        <span className="text-xs text-slate-500">
          {threads.length} loaded · classified breakdown:{" "}
          {CATEGORY_VALUES.map((c) => `${c} ${byCategory[c]}`).join(" · ")}
        </span>
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={
            run.isPending || threads.length === 0 || classifications.length === 0
          }
          className={`ml-auto inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-60 ${
            policy.dry_run
              ? "bg-slate-600 hover:bg-slate-700"
              : "bg-red-600 hover:bg-red-700"
          }`}
        >
          {run.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Zap className="h-4 w-4" />
          )}
          {policy.dry_run ? "Preview actions" : "Apply to Gmail"}
        </button>
      </div>

      {classify.error && (
        <ProviderErrorBanner
          error={classify.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => classify.refetch()}
        />
      )}

      {run.error && (
        <ProviderErrorBanner
          error={run.error}
          onOpenSettings={() => setSettingsOpen(true)}
          onRetry={() => run.mutate()}
        />
      )}

      {run.data && <ApplyResults report={run.data} />}

      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}

function PolicyRow({
  title,
  hint,
  categories,
  selected,
  onToggle,
}: {
  title: string;
  hint: string;
  categories: Category[];
  selected: Category[];
  onToggle: (c: Category) => void;
}) {
  const set = new Set(selected);
  return (
    <div>
      <div className="mb-1 flex items-baseline gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          {title}
        </h3>
        <span className="text-[11px] text-slate-400">{hint}</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {categories.map((c) => {
          const on = set.has(c);
          return (
            <button
              type="button"
              key={c}
              onClick={() => onToggle(c)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                on
                  ? "border-blue-400 bg-blue-50 text-blue-700"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {c}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ApplyResults({ report }: { report: ApplyReport }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-baseline gap-3">
        <h3 className="text-sm font-semibold">
          {report.dry_run ? "Preview" : "Applied"}
        </h3>
        <span className="text-xs text-slate-500">
          {report.total_threads} threads · skipped{" "}
          {report.skipped_low_confidence} low-confidence ·{" "}
          {report.results.length} action
          {report.results.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="max-h-80 overflow-auto rounded-lg border border-slate-100">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">Thread</th>
              <th className="px-3 py-2 font-medium">Action</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Note</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {report.results.map((r, i) => (
              <tr key={`${r.thread_id}-${i}`}>
                <td className="px-3 py-1.5 font-mono text-[10px] text-slate-500">
                  {r.thread_id.slice(0, 10)}…
                </td>
                <td className="px-3 py-1.5">{r.action}</td>
                <td className="px-3 py-1.5">
                  {r.applied ? (
                    <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">
                      applied
                    </span>
                  ) : (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
                      skipped
                    </span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-slate-600">{r.note}</td>
              </tr>
            ))}
            {report.results.length === 0 && (
              <tr>
                <td
                  colSpan={4}
                  className="px-3 py-3 text-center text-slate-500"
                >
                  No actions triggered for this batch.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
