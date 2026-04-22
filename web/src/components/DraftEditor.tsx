import { useEffect, useMemo, useRef, useState } from "react";
import ReactQuill from "react-quill-new";
import { Send, Save, Loader2 } from "lucide-react";
import { api } from "../api/client";
import type { Draft } from "../types";

/**
 * Rich-text draft editor. Quill lets Kyle bold, italicize, link, and list —
 * useful when the AI-drafted reply is a good skeleton but needs one hyperlink
 * or an inline bullet list before sending. The "Save to Gmail" button writes
 * to Gmail Drafts; "Send" dispatches it via the Gmail API.
 *
 * Local state strategy: we keep the HTML + plaintext copies in component
 * state so the user can edit freely, but push to the server only on explicit
 * Save — autosave to Gmail Drafts on every keystroke would nuke the user's
 * rate budget.
 */
export function DraftEditor({
  draft,
  onSaved,
}: {
  draft: Draft;
  onSaved?: (next: Draft) => void;
}) {
  const [subject, setSubject] = useState(draft.subject);
  const [html, setHtml] = useState<string>(() => bodyToHtml(draft.body));
  const [gmailDraftId, setGmailDraftId] = useState<string | null>(
    draft.gmail_draft_id ?? null,
  );
  const [status, setStatus] = useState<
    { kind: "idle" } | { kind: "saving" } | { kind: "sending" } | { kind: "error"; msg: string } | { kind: "saved" } | { kind: "sent" }
  >({ kind: "idle" });
  const lastDraftId = useRef(draft.thread_id);

  // Reset local edits when the incoming draft changes (new thread selected).
  useEffect(() => {
    if (lastDraftId.current !== draft.thread_id) {
      setSubject(draft.subject);
      setHtml(bodyToHtml(draft.body));
      setGmailDraftId(draft.gmail_draft_id ?? null);
      setStatus({ kind: "idle" });
      lastDraftId.current = draft.thread_id;
    }
  }, [draft]);

  const plain = useMemo(() => htmlToPlain(html), [html]);

  async function save(sendAfter: boolean) {
    setStatus({ kind: sendAfter ? "sending" : "saving" });
    try {
      const payload: Draft = {
        ...draft,
        subject,
        body: plain,
        gmail_draft_id: gmailDraftId,
      };
      const saved = await api.pushDraft(payload, html);
      setGmailDraftId(saved.gmail_draft_id);
      onSaved?.(saved.draft);
      if (sendAfter) {
        const sent = await api.sendDraft(saved.gmail_draft_id);
        setStatus({ kind: "sent" });
        console.info("Sent message", sent.id);
      } else {
        setStatus({ kind: "saved" });
      }
    } catch (e) {
      setStatus({ kind: "error", msg: (e as Error).message });
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100"
        placeholder="Subject"
      />
      <ReactQuill theme="snow" value={html} onChange={setHtml} modules={QUILL_MODULES} />
      {draft.tone_notes && (
        <div className="text-xs italic text-slate-500">
          Tone notes: {draft.tone_notes}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => save(false)}
          disabled={status.kind === "saving" || status.kind === "sending"}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          {status.kind === "saving" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save to Gmail Drafts
        </button>
        <button
          onClick={() => save(true)}
          disabled={status.kind === "saving" || status.kind === "sending"}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {status.kind === "sending" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Send
        </button>
        <StatusLine status={status} />
      </div>
    </div>
  );
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

function StatusLine({ status }: { status: Parameters<typeof DraftEditor>[0] extends unknown ? any : never }) {
  if (status.kind === "saved") return <span className="text-xs text-emerald-600">Saved to Gmail Drafts.</span>;
  if (status.kind === "sent") return <span className="text-xs text-emerald-600">Sent.</span>;
  if (status.kind === "error") return <span className="text-xs text-red-600">{status.msg}</span>;
  return null;
}

// The AI drafter returns plain text. We wrap it in simple <p> tags so Quill
// renders paragraph breaks instead of folding them into a single blob.
function bodyToHtml(body: string): string {
  if (!body) return "";
  if (body.trim().startsWith("<")) return body; // already HTML
  return body
    .split(/\n{2,}/)
    .map((para) => `<p>${escapeHtml(para).replace(/\n/g, "<br />")}</p>`)
    .join("");
}

function htmlToPlain(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent || div.innerText || "").replace(/\u00a0/g, " ").trim();
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
