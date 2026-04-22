import { AlertTriangle, Settings as SettingsIcon } from "lucide-react";
import { ApiError } from "../api/client";

/**
 * Inline error banner tuned to the LLM-provider failure modes the backend
 * classifies in ``src/sift/provider_errors.py``. When the error carries an
 * ``error_type`` we show a tailored message and an "Open settings" button
 * so the user can swap providers or paste a fresh key in one click.
 *
 * For plain (non-ApiError) failures it falls back to the message text.
 */
export function ProviderErrorBanner({
  error,
  onOpenSettings,
  onRetry,
}: {
  error: unknown;
  onOpenSettings?: () => void;
  onRetry?: () => void;
}) {
  if (!error) return null;

  let title = "Something went wrong";
  let message = error instanceof Error ? error.message : String(error);
  let tone: "error" | "warn" = "error";
  let showSettings = false;
  let detail: string | undefined;
  let provider: string | undefined;

  if (error instanceof ApiError) {
    provider = error.provider;
    detail = error.detail;
    switch (error.errorType) {
      case "balance":
        title = provider
          ? `${providerLabel(provider)} is out of credits`
          : "LLM provider is out of credits";
        message =
          "Switch to another provider in Settings, or top up this one and try again.";
        tone = "warn";
        showSettings = true;
        break;
      case "auth":
        title = provider
          ? `${providerLabel(provider)} rejected the API key`
          : "Provider rejected the API key";
        message =
          "Open Settings to paste a fresh key, or pick a provider that already has one set.";
        tone = "error";
        showSettings = true;
        break;
      case "rate_limit":
        title = "Rate limit hit";
        message = error.message;
        tone = "warn";
        showSettings = true;
        break;
      case "bad_request":
        title = "Provider rejected the request";
        message = error.message;
        tone = "error";
        showSettings = true;
        break;
      case "other":
        title = "LLM provider error";
        message = error.message;
        tone = "error";
        showSettings = true;
        break;
      default:
        // "unknown" or non-LLM error — fall through to default rendering.
        break;
    }
  }

  const toneClasses =
    tone === "warn"
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : "border-red-200 bg-red-50 text-red-800";

  return (
    <div
      role="alert"
      className={`flex flex-col gap-2 rounded-lg border p-4 text-sm ${toneClasses}`}
    >
      <div className="flex items-start gap-2">
        <AlertTriangle
          className={`mt-0.5 h-4 w-4 shrink-0 ${tone === "warn" ? "text-amber-600" : "text-red-600"}`}
        />
        <div className="flex-1">
          <div className="font-medium">{title}</div>
          <div className={tone === "warn" ? "text-amber-800" : "text-red-700"}>
            {message}
          </div>
          {detail && detail !== message && (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs opacity-75">
                Details
              </summary>
              <pre className="mt-1 whitespace-pre-wrap break-words text-xs opacity-75">
                {detail}
              </pre>
            </details>
          )}
        </div>
      </div>
      {(showSettings || onRetry) && (
        <div className="flex gap-2 pl-6">
          {showSettings && onOpenSettings && (
            <button
              type="button"
              onClick={onOpenSettings}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <SettingsIcon className="h-3.5 w-3.5" />
              Open settings
            </button>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function providerLabel(name: string): string {
  switch (name) {
    case "anthropic":
      return "Anthropic";
    case "openai":
      return "OpenAI";
    case "google":
      return "Google";
    case "groq":
      return "Groq";
    default:
      return name.charAt(0).toUpperCase() + name.slice(1);
  }
}
