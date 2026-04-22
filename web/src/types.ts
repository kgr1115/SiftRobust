// Mirrors the Pydantic models in `src/sift/models.py`. Keeping these in sync
// is manual today; once the project grows we'd generate these from the
// FastAPI OpenAPI schema via openapi-typescript.

export type Category = "urgent" | "needs_reply" | "fyi" | "newsletter" | "trash";

export const CATEGORY_VALUES: Category[] = [
  "urgent",
  "needs_reply",
  "fyi",
  "newsletter",
  "trash",
];

export interface Thread {
  id: string;
  from: string;
  from_name: string;
  to: string;
  subject: string;
  received_at: string;
  body: string;
  label_ids: string[];
  snippet: string;
  unread: boolean;
}

export interface SentThread {
  id: string;
  to: string;
  to_name: string;
  subject: string;
  sent_at: string;
  body: string;
  snippet: string;
  label_ids: string[];
}

export interface Classification {
  thread_id: string;
  category: Category;
  confidence: number;
  one_line_summary: string;
  reason: string;
}

export interface Draft {
  thread_id: string;
  subject: string;
  body: string;
  tone_notes?: string;
  gmail_draft_id?: string | null;
}

export interface BriefItem {
  thread: Thread;
  classification: Classification;
  draft: Draft | null;
}

export interface Brief {
  generated_at: string;
  items: BriefItem[];
}

export interface BriefResponse {
  brief: Brief;
  markdown: string;
  classifications: Classification[];
  drafts: Record<string, Draft>;
}

export interface Label {
  id: string;
  name: string;
  type: "system" | "user";
  messages_total?: number | null;
  threads_total?: number | null;
}

export interface ActionPolicy {
  dry_run: boolean;
  min_confidence: number;
  apply_labels: Partial<Record<Category, string[]>>;
  archive_categories: Category[];
  mark_read_categories: Category[];
}

export interface ActionResult {
  thread_id: string;
  action: string;
  applied: boolean;
  note: string;
}

export interface ApplyReport {
  dry_run: boolean;
  total_threads: number;
  skipped_low_confidence: number;
  results: ActionResult[];
  applied_count?: number;
}

export interface Health {
  status: string;
  provider: string;
  model: string | null;
  db_path: string;
}

export interface ComposeRequest {
  to: string;
  subject: string;
  body: string;
  body_html?: string | null;
  cc?: string | null;
  bcc?: string | null;
  save_as_draft: boolean;
}

export interface DraftListItem {
  id: string;
  thread_id: string;
  subject: string;
  snippet: string;
}

// --- settings / catalog ----------------------------------------------------

export interface ProviderKeyState {
  provider: string;
  env_var: string;
  key_set: boolean;
  masked: string;
}

export interface Settings {
  llm_provider: string;
  model: string | null;
  providers: ProviderKeyState[];
}

export interface SettingsUpdateRequest {
  llm_provider?: string;
  model?: string; // pass "" to clear the override
  api_keys?: Record<string, string>;
}

export interface CatalogModel {
  provider: string;
  model: string;
  input_per_mtok: number;
  output_per_mtok: number;
  is_default: boolean;
  accuracy: number | null;
  per_category_recall: Record<string, number> | null;
  /** When non-null, accuracy is inherited from this sibling model under the same provider. */
  eval_model: string | null;
}

export interface CatalogProvider {
  name: string;
  display_name: string;
  env_var: string;
  default_model: string;
  models: CatalogModel[];
}

export interface Catalog {
  providers: CatalogProvider[];
}
