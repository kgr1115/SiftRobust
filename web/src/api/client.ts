// Thin fetch wrapper around the FastAPI backend. Everything returns typed
// promises so React Query gets clean cache keys and inference.

import type {
  ActionPolicy,
  ApplyReport,
  BriefResponse,
  Catalog,
  Classification,
  ComposeRequest,
  Draft,
  DraftListItem,
  Health,
  Label,
  SentThread,
  Settings,
  SettingsUpdateRequest,
  Thread,
} from "../types";

/**
 * Structured API error. The backend returns ``detail`` as either a plain
 * string (settings / Gmail endpoints) or an object ``{error_type, provider,
 * message, detail}`` for LLM-provider failures (see api.py ``_raise_provider_http``).
 * Throwing a typed error lets the UI branch on ``error_type`` without
 * string-matching.
 */
export type ApiErrorType =
  | "auth"
  | "balance"
  | "rate_limit"
  | "bad_request"
  | "other"
  | "unknown";

export class ApiError extends Error {
  status: number;
  errorType: ApiErrorType;
  provider?: string;
  model?: string | null;
  detail?: string;

  constructor(args: {
    status: number;
    message: string;
    errorType: ApiErrorType;
    provider?: string;
    model?: string | null;
    detail?: string;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.status = args.status;
    this.errorType = args.errorType;
    this.provider = args.provider;
    this.model = args.model;
    this.detail = args.detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const rawText = await res.text().catch(() => "");
    // Try to parse FastAPI's JSON error envelope; fall back gracefully.
    let parsed: unknown = undefined;
    if (rawText) {
      try {
        parsed = JSON.parse(rawText);
      } catch {
        parsed = undefined;
      }
    }
    const detail =
      parsed && typeof parsed === "object" && parsed !== null
        ? (parsed as { detail?: unknown }).detail
        : undefined;

    if (detail && typeof detail === "object" && detail !== null) {
      const d = detail as {
        error_type?: string;
        provider?: string;
        model?: string | null;
        message?: string;
        detail?: string;
      };
      throw new ApiError({
        status: res.status,
        message: d.message ?? `Request failed (${res.status})`,
        errorType: (d.error_type as ApiErrorType) ?? "unknown",
        provider: d.provider,
        model: d.model,
        detail: d.detail,
      });
    }

    const message =
      typeof detail === "string"
        ? detail
        : rawText || res.statusText || `Request failed (${res.status})`;
    throw new ApiError({
      status: res.status,
      message: `${init?.method ?? "GET"} ${path} failed: ${res.status} ${message}`,
      errorType: "unknown",
    });
  }
  // Some endpoints return 204/empty body on success.
  const text = await res.text();
  return (text ? JSON.parse(text) : (undefined as unknown)) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  // Inbox / pipeline
  inbox: (limit = 25, q?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set("q", q);
    return request<{ threads: Thread[] }>(`/api/inbox?${params}`);
  },
  sent: (limit = 25, q?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set("q", q);
    return request<{ threads: SentThread[] }>(`/api/sent?${params}`);
  },
  brief: (limit = 25) =>
    request<BriefResponse>(`/api/brief?limit=${encodeURIComponent(limit)}`),
  classify: (threads: Thread[], useCache = true) =>
    request<{ classifications: Classification[] }>("/api/classify", {
      method: "POST",
      body: JSON.stringify({ threads, use_cache: useCache }),
    }),
  draft: (
    threads: Thread[],
    classifications: Classification[],
    useCache = true,
  ) =>
    request<{ drafts: Record<string, Draft> }>("/api/draft", {
      method: "POST",
      body: JSON.stringify({ threads, classifications, use_cache: useCache }),
    }),

  // Labels
  listLabels: () => request<Label[]>("/api/labels"),
  createLabel: (name: string) =>
    request<Label>("/api/labels", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  // Per-thread actions
  archiveThread: (threadId: string) =>
    request<{ ok: true }>(`/api/threads/${encodeURIComponent(threadId)}/archive`, {
      method: "POST",
    }),
  addThreadLabels: (threadId: string, labelIds: string[]) =>
    request<{ ok: true }>(
      `/api/threads/${encodeURIComponent(threadId)}/labels`,
      { method: "POST", body: JSON.stringify({ label_ids: labelIds }) },
    ),
  removeThreadLabel: (threadId: string, labelId: string) =>
    request<{ ok: true }>(
      `/api/threads/${encodeURIComponent(threadId)}/labels/${encodeURIComponent(labelId)}`,
      { method: "DELETE" },
    ),
  markRead: (threadId: string) =>
    request<{ ok: true }>(`/api/threads/${encodeURIComponent(threadId)}/mark_read`, {
      method: "POST",
    }),
  markUnread: (threadId: string) =>
    request<{ ok: true }>(
      `/api/threads/${encodeURIComponent(threadId)}/mark_unread`,
      { method: "POST" },
    ),

  // AI bulk apply
  apply: (
    threads: Thread[],
    classifications: Classification[],
    policy: ActionPolicy,
  ) =>
    request<ApplyReport>("/api/apply", {
      method: "POST",
      body: JSON.stringify({ threads, classifications, policy }),
    }),

  // Drafts + compose
  listDrafts: () => request<DraftListItem[]>("/api/drafts"),
  pushDraft: (draft: Draft, bodyHtml?: string) =>
    request<{ draft: Draft; gmail_draft_id: string }>("/api/drafts", {
      method: "POST",
      body: JSON.stringify({ draft, body_html: bodyHtml }),
    }),
  sendDraft: (draftId: string) =>
    request<{ id: string; mode: string }>(
      `/api/drafts/${encodeURIComponent(draftId)}/send`,
      { method: "POST" },
    ),
  compose: (req: ComposeRequest) =>
    request<{ id: string; mode: string }>("/api/compose", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  // Settings / catalog
  getSettings: () => request<Settings>("/api/settings"),
  updateSettings: (req: SettingsUpdateRequest) =>
    request<Settings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  getCatalog: () => request<Catalog>("/api/catalog"),
};
