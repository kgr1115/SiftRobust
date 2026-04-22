import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Tag } from "lucide-react";
import { api } from "../api/client";

/**
 * Labels pane. Lists user and system labels side-by-side with thread counts
 * and a create-new input. Rename/delete are deliberately omitted for now —
 * label hygiene on Gmail is gnarly and destructive, so we keep those moves
 * in Gmail itself.
 */
export function LabelManager() {
  const qc = useQueryClient();
  const labels = useQuery({
    queryKey: ["labels"],
    queryFn: () => api.listLabels(),
  });
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: (n: string) => api.createLabel(n),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["labels"] });
      setName("");
    },
  });

  const userLabels = (labels.data ?? []).filter((l) => l.type === "user");
  const systemLabels = (labels.data ?? []).filter((l) => l.type === "system");

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <Tag className="h-4 w-4 text-blue-600" />
          Create a label
        </h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate(name.trim());
          }}
          className="flex items-center gap-2"
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Follow-up/Q3"
            className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
          <button
            type="submit"
            disabled={!name.trim() || create.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {create.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Add
          </button>
        </form>
        {create.error && (
          <p className="mt-2 text-xs text-red-600">
            {(create.error as Error).message}
          </p>
        )}
        <p className="mt-2 text-xs text-slate-500">
          Supports Gmail's nested-label syntax with <code>/</code> separators.
        </p>
      </div>

      <LabelList title={`Your labels (${userLabels.length})`} labels={userLabels} />
      <LabelList
        title={`System labels (${systemLabels.length})`}
        labels={systemLabels}
        subtle
      />
    </div>
  );
}

function LabelList({
  title,
  labels,
  subtle,
}: {
  title: string;
  labels: { id: string; name: string; threads_total?: number | null }[];
  subtle?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border bg-white p-5 shadow-sm ${
        subtle ? "border-slate-100" : "border-slate-200"
      }`}
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {labels.length === 0 ? (
        <p className="text-sm text-slate-500">No labels.</p>
      ) : (
        <ul className="grid gap-1 sm:grid-cols-2">
          {labels.map((l) => (
            <li
              key={l.id}
              className="flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-slate-50"
            >
              <span className="truncate">{l.name}</span>
              <span className="ml-2 text-xs text-slate-400">
                {l.threads_total ?? 0}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
