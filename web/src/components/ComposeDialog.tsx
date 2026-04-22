import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactQuill from "react-quill-new";
import { X, Send, Save, Loader2 } from "lucide-react";
import { api } from "../api/client";

/**
 * Compose new outbound message. Modal overlay with the same Quill toolbar
 * as reply drafts so formatting is consistent. Save-as-draft vs Send is a
 * simple toggle — the backend handles both through /api/compose.
 */
export function ComposeDialog({ onClose }: { onClose: () => void }) {
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [html, setHtml] = useState("");

  const submit = useMutation({
    mutationFn: async (sendNow: boolean) => {
      return api.compose({
        to,
        cc: cc || null,
        subject,
        body: htmlToPlain(html),
        body_html: html,
        save_as_draft: !sendNow,
      });
    },
    onSuccess: (res) => {
      if (res.mode === "sent") onClose();
    },
  });

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-2xl flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h2 className="text-sm font-semibold">New message</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-100"
            aria-label="Close compose"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-col gap-2 px-5 py-4">
          <Field label="To">
            <input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="recipient@example.com"
              className="w-full border-0 px-0 py-1 text-sm focus:outline-none focus:ring-0"
            />
          </Field>
          <Field label="Cc">
            <input
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              placeholder="optional"
              className="w-full border-0 px-0 py-1 text-sm focus:outline-none focus:ring-0"
            />
          </Field>
          <Field label="Subject">
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full border-0 px-0 py-1 text-sm focus:outline-none focus:ring-0"
            />
          </Field>
        </div>

        <div className="px-5 pb-4">
          <ReactQuill
            theme="snow"
            value={html}
            onChange={setHtml}
            modules={QUILL_MODULES}
            placeholder="Write your message…"
          />
        </div>

        <div className="flex items-center gap-2 border-t border-slate-100 px-5 py-3">
          <button
            type="button"
            onClick={() => submit.mutate(false)}
            disabled={submit.isPending || !to || !subject}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            {submit.isPending && submit.variables === false ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save as draft
          </button>
          <button
            type="button"
            onClick={() => submit.mutate(true)}
            disabled={submit.isPending || !to || !subject}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {submit.isPending && submit.variables === true ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            Send
          </button>
          <StatusLine mutation={submit} />
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-3 border-b border-slate-100 pb-1">
      <span className="w-14 shrink-0 text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </span>
      {children}
    </label>
  );
}

function StatusLine({
  mutation,
}: {
  mutation: { error: unknown; data: { mode: string } | undefined; isSuccess: boolean };
}) {
  if (mutation.error)
    return (
      <span className="text-xs text-red-600">
        {(mutation.error as Error).message}
      </span>
    );
  if (mutation.isSuccess)
    return (
      <span className="text-xs text-emerald-600">
        {mutation.data?.mode === "sent" ? "Sent." : "Saved to Drafts."}
      </span>
    );
  return null;
}

const QUILL_MODULES = {
  toolbar: [
    [{ header: [false, 2, 3] }],
    ["bold", "italic", "underline"],
    [{ list: "ordered" }, { list: "bullet" }],
    ["link", "blockquote"],
    ["clean"],
  ],
};

function htmlToPlain(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent || div.innerText || "").replace(/\u00a0/g, " ").trim();
}
