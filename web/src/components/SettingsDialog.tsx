import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, X } from "lucide-react";
import { api } from "../api/client";
import type {
  CatalogModel,
  CatalogProvider,
  ProviderKeyState,
} from "../types";

/**
 * Settings modal. Two things live here:
 *
 *  1. Per-provider API key fields. We show the masked tail of the current
 *     key as the placeholder and leave the input empty so the user can paste
 *     a new one without accidentally editing the existing value. Save only
 *     sends fields that the user actually typed into — masked placeholders
 *     never round-trip to the server (defensive double-check in the backend
 *     too).
 *
 *  2. The pricing + accuracy grid for every provider/model. Accuracy numbers
 *     come from the last provider-comparison eval run; pricing from each
 *     provider's `pricing` dict. Lets the user make an informed pick about
 *     cost/quality tradeoffs without leaving the app.
 */
export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  });
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: () => api.getCatalog(),
  });

  // Local state: map of env_var -> string the user typed. Empty means "no change".
  // We keep a separate "cleared" bookkeeping so "blank out to unset" works.
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [clearedKeys, setClearedKeys] = useState<Set<string>>(new Set());
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  // Reset local edits whenever the dialog reopens with fresh server state.
  useEffect(() => {
    setKeyInputs({});
    setClearedKeys(new Set());
    setSaveMessage(null);
  }, [settings.data?.llm_provider, settings.data?.model]);

  const save = useMutation({
    mutationFn: async () => {
      const api_keys: Record<string, string> = {};
      for (const [envVar, raw] of Object.entries(keyInputs)) {
        const trimmed = raw.trim();
        if (!trimmed) continue;
        api_keys[envVar] = trimmed;
      }
      for (const envVar of clearedKeys) {
        api_keys[envVar] = "";
      }
      return api.updateSettings({
        api_keys: Object.keys(api_keys).length ? api_keys : undefined,
      });
    },
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      qc.invalidateQueries({ queryKey: ["health"] });
      setKeyInputs({});
      setClearedKeys(new Set());
      setSaveMessage("Saved.");
      // Clear the toast after a moment so it doesn't linger.
      setTimeout(() => setSaveMessage(null), 2000);
    },
    onError: (e) => setSaveMessage(`Save failed: ${(e as Error).message}`),
  });

  const loading = settings.isLoading || catalog.isLoading;

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold">Settings</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-100"
            aria-label="Close settings"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading settings…
            </div>
          )}

          {!loading && settings.data && catalog.data && (
            <div className="flex flex-col gap-6">
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  API keys
                </h3>
                <p className="mb-3 text-xs text-slate-500">
                  Keys are written to the repo's <code>.env</code> file. Leave a
                  field blank to keep the existing key; tick <em>clear</em> to
                  remove one.
                </p>
                <div className="flex flex-col gap-3">
                  {catalog.data.providers.map((p) => {
                    const state = settings.data.providers.find(
                      (ps) => ps.provider === p.name,
                    );
                    return (
                      <KeyRow
                        key={p.name}
                        provider={p}
                        state={state}
                        value={keyInputs[p.env_var] ?? ""}
                        cleared={clearedKeys.has(p.env_var)}
                        onChange={(v) =>
                          setKeyInputs((prev) => ({ ...prev, [p.env_var]: v }))
                        }
                        onClearedChange={(c) =>
                          setClearedKeys((prev) => {
                            const next = new Set(prev);
                            if (c) next.add(p.env_var);
                            else next.delete(p.env_var);
                            return next;
                          })
                        }
                      />
                    );
                  })}
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => save.mutate()}
                    disabled={
                      save.isPending ||
                      (Object.values(keyInputs).every((v) => !v.trim()) &&
                        clearedKeys.size === 0)
                    }
                    className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:bg-slate-300"
                  >
                    {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    Save keys
                  </button>
                  {saveMessage && (
                    <span
                      className={`inline-flex items-center gap-1 text-xs ${
                        saveMessage.startsWith("Save failed")
                          ? "text-red-600"
                          : "text-emerald-600"
                      }`}
                    >
                      {!saveMessage.startsWith("Save failed") && (
                        <CheckCircle2 className="h-3.5 w-3.5" />
                      )}
                      {saveMessage}
                    </span>
                  )}
                </div>
              </section>

              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Models, pricing, accuracy
                </h3>
                <p className="mb-3 text-xs text-slate-500">
                  Pricing is per-million tokens (input / output). Accuracy is
                  from the last <code>test_provider_comparison</code> eval run
                  against the 40-thread labeled fixture — run{" "}
                  <code>pytest evals/test_provider_comparison.py -v -s</code> to
                  refresh.
                </p>
                <ModelGrid providers={catalog.data.providers} />
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function KeyRow({
  provider,
  state,
  value,
  cleared,
  onChange,
  onClearedChange,
}: {
  provider: CatalogProvider;
  state: ProviderKeyState | undefined;
  value: string;
  cleared: boolean;
  onChange: (v: string) => void;
  onClearedChange: (c: boolean) => void;
}) {
  const keySet = state?.key_set ?? false;
  const masked = state?.masked ?? "";
  return (
    <div className="grid grid-cols-[140px_1fr_auto] items-center gap-3">
      <div className="flex flex-col">
        <span className="text-sm font-medium text-slate-800">
          {provider.display_name}
        </span>
        <code className="text-[10px] text-slate-400">{provider.env_var}</code>
      </div>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          keySet
            ? `current: ${masked}`
            : `paste ${provider.display_name} key`
        }
        disabled={cleared}
        className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm disabled:bg-slate-100"
      />
      <label className="flex items-center gap-1.5 text-xs text-slate-500">
        <input
          type="checkbox"
          checked={cleared}
          onChange={(e) => onClearedChange(e.target.checked)}
          disabled={!keySet}
        />
        Clear
      </label>
    </div>
  );
}

function ModelGrid({ providers }: { providers: CatalogProvider[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2 font-medium">Provider</th>
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="px-3 py-2 text-right font-medium">In $/MTok</th>
            <th className="px-3 py-2 text-right font-medium">Out $/MTok</th>
            <th className="px-3 py-2 text-right font-medium">Accuracy</th>
          </tr>
        </thead>
        <tbody>
          {providers.flatMap((p) =>
            p.models.map((m, i) => (
              <ModelRow
                key={`${p.name}::${m.model}`}
                provider={p}
                model={m}
                isFirstInProvider={i === 0}
              />
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}

function ModelRow({
  provider,
  model,
  isFirstInProvider,
}: {
  provider: CatalogProvider;
  model: CatalogModel;
  isFirstInProvider: boolean;
}) {
  return (
    <tr
      className={`border-t border-slate-100 ${
        model.is_default ? "bg-blue-50/40" : ""
      }`}
    >
      <td className="px-3 py-2 text-slate-600">
        {isFirstInProvider ? provider.display_name : ""}
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {model.model}
        {model.is_default && (
          <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">
            default
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        ${model.input_per_mtok.toFixed(2)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        ${model.output_per_mtok.toFixed(2)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {model.accuracy == null ? (
          <span className="text-slate-400">—</span>
        ) : (
          <span title={accuracyTooltip(model)}>
            {(model.accuracy * 100).toFixed(1)}%
            {model.eval_model && (
              <sup className="ml-0.5 text-slate-400" title={`from ${model.eval_model}`}>
                *
              </sup>
            )}
          </span>
        )}
      </td>
    </tr>
  );
}

function accuracyTooltip(m: CatalogModel): string {
  if (!m.per_category_recall) return "";
  const parts: string[] = [];
  for (const [cat, val] of Object.entries(m.per_category_recall)) {
    parts.push(`${cat}: ${(val * 100).toFixed(0)}%`);
  }
  const prefix = m.eval_model
    ? `measured on ${m.eval_model} — recall by category:\n`
    : "recall by category:\n";
  return prefix + parts.join("\n");
}
