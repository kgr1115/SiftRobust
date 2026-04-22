import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Archive, Tag, Mail, MailOpen, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { Classification, Draft, Label, Thread } from "../types";
import { formatDate } from "../lib/utils";
import { CategoryBadge } from "./CategoryBadge";
import { DraftEditor } from "./DraftEditor";

/**
 * A single inbox row. Collapsed by default; expanding reveals the classifier's
 * reasoning, the AI draft (if any), and the editor. Quick-actions live on the
 * right — archive / label / mark read — so common moves are one click.
 */
export function ThreadCard({
  thread,
  classification,
  draft,
  labels,
}: {
  thread: Thread;
  classification: Classification | null;
  draft: Draft | null;
  labels: Label[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [localDraft, setLocalDraft] = useState<Draft | null>(draft);
  const qc = useQueryClient();

  // Map known system labels to short friendly names for the chip row.
  const userLabelsById = Object.fromEntries(
    labels.filter((l) => l.type === "user").map((l) => [l.id, l.name]),
  );

  const archive = useMutation({
    mutationFn: () => api.archiveThread(thread.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const markRead = useMutation({
    mutationFn: () => (thread.unread ? api.markRead(thread.id) : api.markUnread(thread.id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const removeLabel = useMutation({
    mutationFn: (labelId: string) => api.removeThreadLabel(thread.id, labelId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });

  const appliedUserLabels = thread.label_ids
    .filter((id) => id in userLabelsById)
    .map((id) => ({ id, name: userLabelsById[id] }));

  return (
    <div
      className={`rounded-xl border bg-white transition ${
        thread.unread ? "border-slate-300 shadow-sm" : "border-slate-200"
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-slate-50"
      >
        <div className="pt-1 text-slate-400">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className={`truncate text-sm ${thread.unread ? "font-semibold" : "font-medium"} text-slate-900`}>
              {thread.from_name}
            </span>
            {classification && (
              <CategoryBadge category={classification.category} confidence={classification.confidence} />
            )}
            <span className="ml-auto shrink-0 text-xs text-slate-400">
              {formatDate(thread.received_at)}
            </span>
          </div>
          <div className="mt-0.5 truncate text-sm text-slate-700">{thread.subject}</div>
          <div className="mt-0.5 truncate text-xs text-slate-500">
            {classification?.one_line_summary ?? thread.snippet}
          </div>
          {appliedUserLabels.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {appliedUserLabels.map((lbl) => (
                <span
                  key={lbl.id}
                  className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-700"
                >
                  <Tag className="h-3 w-3" />
                  {lbl.name}
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      removeLabel.mutate(lbl.id);
                    }}
                    className="ml-1 rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                    aria-label={`Remove label ${lbl.name}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1 pt-1">
          <IconButton
            title="Archive"
            onClick={(e) => {
              e.stopPropagation();
              archive.mutate();
            }}
          >
            <Archive className="h-4 w-4" />
          </IconButton>
          <IconButton
            title={thread.unread ? "Mark read" : "Mark unread"}
            onClick={(e) => {
              e.stopPropagation();
              markRead.mutate();
            }}
          >
            {thread.unread ? <MailOpen className="h-4 w-4" /> : <Mail className="h-4 w-4" />}
          </IconButton>
          <LabelPicker thread={thread} labels={labels} />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-100 px-4 py-4">
          {classification?.reason && (
            <div className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <span className="font-semibold text-slate-700">Why this category:</span>{" "}
              {classification.reason}
            </div>
          )}
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700">
            {thread.body}
          </pre>
          {localDraft && (
            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                AI draft
              </div>
              <DraftEditor draft={localDraft} onSaved={setLocalDraft} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function IconButton({
  children,
  onClick,
  title,
}: {
  children: React.ReactNode;
  onClick: (e: React.MouseEvent) => void;
  title: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
    >
      {children}
    </button>
  );
}

// A compact menu for applying user labels to a thread. Close-on-outside-click
// is intentionally simple: we rely on the click propagation from the parent
// card to dismiss via the parent onClick.
function LabelPicker({ thread, labels }: { thread: Thread; labels: Label[] }) {
  const [open, setOpen] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const qc = useQueryClient();

  const userLabels = labels.filter((l) => l.type === "user");

  const apply = useMutation({
    mutationFn: (labelId: string) => api.addThreadLabels(thread.id, [labelId]),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });

  const createAndApply = useMutation({
    mutationFn: async (name: string) => {
      const created = await api.createLabel(name);
      await api.addThreadLabels(thread.id, [created.id]);
      return created;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["labels"] });
      setNewLabel("");
      setOpen(false);
    },
  });

  return (
    <div className="relative">
      <button
        type="button"
        title="Labels"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
      >
        <Tag className="h-4 w-4" />
      </button>
      {open && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute right-0 top-8 z-10 w-56 rounded-lg border border-slate-200 bg-white p-2 text-sm shadow-lg"
        >
          <div className="mb-1 px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Apply label
          </div>
          <ul className="max-h-48 overflow-auto">
            {userLabels.length === 0 && (
              <li className="px-2 py-1 text-xs text-slate-500">No user labels yet.</li>
            )}
            {userLabels.map((lbl) => (
              <li key={lbl.id}>
                <button
                  type="button"
                  onClick={() => apply.mutate(lbl.id)}
                  className="w-full rounded px-2 py-1 text-left hover:bg-slate-100"
                >
                  {lbl.name}
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-2 border-t border-slate-100 pt-2">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (newLabel.trim()) createAndApply.mutate(newLabel.trim());
              }}
              className="flex items-center gap-1"
            >
              <input
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="New label…"
                className="flex-1 rounded border border-slate-200 px-2 py-1 text-xs focus:border-blue-400 focus:outline-none"
              />
              <button
                type="submit"
                className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white hover:bg-blue-700"
              >
                Add
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
