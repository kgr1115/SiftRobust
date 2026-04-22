import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PenSquare, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { api } from "../api/client";
import type { TabKey } from "../App";
import { SettingsDialog } from "./SettingsDialog";

/**
 * App chrome. Slim top bar with brand + provider/model picker + compose + a
 * settings gear, a tab strip below, and the tab content in a centered column.
 *
 * The provider/model picker is driven by a React Query on /api/settings and
 * /api/catalog, so it's persistent across tabs (state lives in the query cache,
 * not in this component's local state) and changes are reflected everywhere
 * that reads the same query key.
 */
export function Layout({
  tabs,
  activeTab,
  onTabChange,
  onCompose,
  children,
}: {
  tabs: { key: TabKey; label: string }[];
  activeTab: TabKey;
  onTabChange: (t: TabKey) => void;
  onCompose: () => void;
  children: React.ReactNode;
}) {
  const qc = useQueryClient();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
    staleTime: 30_000,
  });
  const catalog = useQuery({
    queryKey: ["catalog"],
    queryFn: () => api.getCatalog(),
    // Pricing + accuracy are static-ish; cache for a while.
    staleTime: 5 * 60_000,
  });

  // Combined (provider, model) options — one row per concrete model.
  const options = useMemo(() => {
    if (!catalog.data) return [];
    return catalog.data.providers.flatMap((p) =>
      p.models.map((m) => ({
        value: `${p.name}::${m.model}`,
        provider: p.name,
        displayProvider: p.display_name,
        model: m.model,
        isDefault: m.is_default,
        // We disable options whose provider has no key — you can still see
        // them in the list but they'd fail at call time.
        disabled: !settings.data?.providers.find(
          (ps) => ps.provider === p.name,
        )?.key_set,
      })),
    );
  }, [catalog.data, settings.data]);

  const currentValue = useMemo(() => {
    if (!settings.data || !catalog.data) return "";
    const provider = settings.data.llm_provider;
    const model =
      settings.data.model ||
      catalog.data.providers.find((p) => p.name === provider)?.default_model ||
      "";
    return `${provider}::${model}`;
  }, [settings.data, catalog.data]);

  const updateMutation = useMutation({
    mutationFn: (args: { provider: string; model: string }) =>
      // Sending model unconditionally means the user can switch between the
      // baked-in default and an explicit override purely through this dropdown.
      api.updateSettings({ llm_provider: args.provider, model: args.model }),
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      qc.invalidateQueries({ queryKey: ["health"] });
    },
  });

  function onPick(value: string) {
    const [provider, model] = value.split("::");
    if (!provider || !model) return;
    updateMutation.mutate({ provider, model });
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-blue-600" />
            <span className="text-lg font-semibold tracking-tight">Sift</span>
            <span className="ml-2 text-xs text-slate-400">
              AI inbox triage
            </span>
          </div>
          <div className="flex items-center gap-2">
            <ProviderModelPicker
              value={currentValue}
              options={options}
              disabled={updateMutation.isPending || !settings.data || !catalog.data}
              onChange={onPick}
            />
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              title="Settings"
              aria-label="Settings"
            >
              <SettingsIcon className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onCompose}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              <PenSquare className="h-4 w-4" />
              Compose
            </button>
          </div>
        </div>
        <nav className="mx-auto max-w-5xl px-6">
          <ul className="flex gap-1">
            {tabs.map((t) => {
              const active = t.key === activeTab;
              return (
                <li key={t.key}>
                  <button
                    type="button"
                    onClick={() => onTabChange(t.key)}
                    className={`rounded-t-md px-3 py-2 text-sm font-medium transition ${
                      active
                        ? "border-b-2 border-blue-600 text-blue-700"
                        : "border-b-2 border-transparent text-slate-500 hover:text-slate-800"
                    }`}
                  >
                    {t.label}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-6">{children}</main>

      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </div>
  );
}

type PickerOption = {
  value: string;
  provider: string;
  displayProvider: string;
  model: string;
  isDefault: boolean;
  disabled: boolean;
};

function ProviderModelPicker({
  value,
  options,
  disabled,
  onChange,
}: {
  value: string;
  options: PickerOption[];
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  // Group by provider so the dropdown has <optgroup>s — matches how a user
  // thinks about "Anthropic / claude-sonnet-4-6".
  const grouped = useMemo(() => {
    const map = new Map<string, { display: string; items: PickerOption[] }>();
    for (const o of options) {
      if (!map.has(o.provider)) {
        map.set(o.provider, { display: o.displayProvider, items: [] });
      }
      map.get(o.provider)!.items.push(o);
    }
    return Array.from(map.entries());
  }, [options]);

  return (
    <select
      className="max-w-[260px] rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-700 disabled:opacity-60"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      title="Provider / model"
    >
      {value === "" && <option value="">Loading…</option>}
      {grouped.map(([providerName, group]) => (
        <optgroup key={providerName} label={group.display}>
          {group.items.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.model}
              {o.isDefault ? " (default)" : ""}
              {o.disabled ? " — no key" : ""}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
