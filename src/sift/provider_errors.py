"""Cross-provider exception classification.

Each LLM SDK (anthropic, openai, google-genai) raises its own concrete
exception types on failure. The API layer needs to translate these into a
small set of stable ``error_type`` strings so the frontend can render a
specific message ("your Anthropic balance is too low — pick another provider")
rather than a generic 500.

This module does *only* classification, not retry/recovery. ``_retry_on_rate_limit``
in ``llm.py`` still handles transient 429s before they reach here; anything
that bubbles up to the API is a user-actionable error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorType = Literal["auth", "balance", "rate_limit", "bad_request", "other"]


@dataclass(frozen=True)
class ClassifiedError:
    """A provider error reshaped for the UI."""
    error_type: ErrorType
    message: str        # short, user-facing
    detail: str         # raw text for logs / "show details" toggle
    status_code: int    # suggested HTTP status to return


def _status_of(exc: BaseException) -> int | None:
    """Best-effort pull an HTTP status off an SDK exception."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return int(status)
    resp = getattr(exc, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", None)
        if status is not None:
            return int(status)
    # google-genai wraps HTTP errors under .code
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    return None


def _text_of(exc: BaseException) -> str:
    """Flatten the exception to a searchable lowercase string."""
    parts: list[str] = [type(exc).__name__, str(exc)]
    # anthropic.BadRequestError / openai.BadRequestError expose .body['error']['message']
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            parts.append(str(err.get("message", "")))
            parts.append(str(err.get("type", "")))
    return " ".join(parts).lower()


def classify_provider_exception(exc: BaseException) -> ClassifiedError:
    """Reduce an arbitrary provider SDK exception to a typed error.

    The order matters: balance/credit issues are a special case of 400, and
    auth failures share a status code family with permission errors. We check
    the message text before falling back on status codes.
    """
    text = _text_of(exc)
    status = _status_of(exc)
    raw_detail = str(exc)[:500]

    # Balance / billing — this is the specific case the UI wants to guide the
    # user through. Each provider phrases it slightly differently.
    balance_markers = (
        "credit balance is too low",
        "insufficient credits",
        "insufficient_quota",
        "billing",
        "you exceeded your current quota",
    )
    if any(m in text for m in balance_markers):
        return ClassifiedError(
            error_type="balance",
            message="This provider is out of credits. Switch to another provider or top up.",
            detail=raw_detail,
            status_code=402,
        )

    # Auth: missing/invalid API key.
    if status in (401, 403) or "authenticationerror" in text or "permission" in text or "api key" in text:
        return ClassifiedError(
            error_type="auth",
            message="This provider rejected the API key. Check the key in Settings.",
            detail=raw_detail,
            status_code=401,
        )

    # Rate-limit: reached here only after _retry_on_rate_limit gave up.
    if status == 429 or "ratelimit" in text or "resource_exhausted" in text or "resourceexhausted" in text:
        return ClassifiedError(
            error_type="rate_limit",
            message="This provider is rate-limited. Try again shortly or switch providers.",
            detail=raw_detail,
            status_code=429,
        )

    # Generic 4xx: model not found, prompt too long, etc. Surface the raw
    # message so it's obvious what to fix.
    if status is not None and 400 <= status < 500:
        return ClassifiedError(
            error_type="bad_request",
            message=f"Provider rejected the request ({status}).",
            detail=raw_detail,
            status_code=status,
        )

    # Everything else — network blips, 5xx, unexpected SDK bugs.
    return ClassifiedError(
        error_type="other",
        message="The LLM provider returned an unexpected error.",
        detail=raw_detail,
        status_code=502,
    )
