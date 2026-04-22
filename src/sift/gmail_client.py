"""Gmail connector: OAuth 2.0 flow, thread fetching, draft pushing, and actions.

Design notes
------------
* **Scopes**: ``gmail.modify``. One scope gives us everything the robust
  version needs: read threads and messages, push drafts, archive (remove
  INBOX), apply/remove labels, create user labels, mark-read, and send
  messages the user has composed in the UI. We still never call
  ``users.messages.delete`` — the action layer goes through archive or
  trash instead so nothing is ever unrecoverable without user action.

* **Token storage**: credentials.json (OAuth client secret) sits at the repo
  root and is loaded once. token.json (user-granted access/refresh tokens)
  is written next to it after the first browser flow and reused thereafter.
  Both are in ``.gitignore``.

* **Refresh vs. re-auth**: google-auth handles silent refresh for expired
  access tokens. If the refresh token itself has been revoked or expired
  (testing-mode apps expire refresh tokens after 7 days), we fall back to
  re-running the browser flow.

* **Thread → Thread mapping**: Gmail's thread shape is deeply nested MIME.
  We flatten it to :class:`sift.models.Thread` by taking the last inbound
  message's sender/subject/date and joining the full message bodies into
  one text blob. That mirrors the shape of our fixture inbox and keeps the
  classifier/drafter code identical for real and synthetic inputs.

* **Relative paths**: ``credentials.json`` / ``token.json`` are resolved
  relative to :data:`sift.config.PROJECT_ROOT` if not absolute, so the
  ``sift`` CLI works regardless of the user's cwd.
"""

from __future__ import annotations

import base64
import html
import logging
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Gmail's batch endpoint hard-caps at 100 sub-requests per multipart HTTP call.
_GMAIL_BATCH_MAX = 100

from .config import CONFIG, PROJECT_ROOT
from .models import ComposeRequest, Draft, Label, SentThread, Thread

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------
# gmail.modify covers: read inbox, push drafts, archive (remove INBOX label),
# apply/remove labels, create labels, mark read/unread, and send messages.
# We still avoid `gmail.send`-only and full-access scopes; `modify` gives us
# every write needed by the action layer without granting delete-for-good.
SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _resolve(path: Path) -> Path:
    """Resolve a configured path against PROJECT_ROOT if it's relative."""
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def credentials_file() -> Path:
    return _resolve(CONFIG.google_credentials_path)


def token_file() -> Path:
    return _resolve(CONFIG.google_token_path)


class GmailAuthError(RuntimeError):
    """Raised when OAuth setup is incomplete or credentials are invalid."""


class GmailActionError(RuntimeError):
    """Raised when a Gmail write operation fails in a way worth surfacing."""


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------
def _load_cached_creds() -> Credentials | None:
    tok = token_file()
    if not tok.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(tok), SCOPES)
    except ValueError:
        # Scopes changed or file is malformed — force a fresh auth run.
        logger.warning("Cached token.json is stale (scopes changed?); re-authenticating.")
        return None


def _run_browser_flow() -> Credentials:
    """Kick off the desktop OAuth flow. Opens the user's browser."""
    creds_path = credentials_file()
    if not creds_path.exists():
        raise GmailAuthError(
            f"credentials.json not found at {creds_path}. "
            "See docs/gmail_setup.md to create a Google Cloud OAuth client "
            "and download the client secret."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    # port=0 picks a free local port; the flow spins up a tiny HTTP server
    # to receive the OAuth redirect.
    return flow.run_local_server(port=0, prompt="consent")


def get_credentials(*, force_refresh: bool = False) -> Credentials:
    """Return valid OAuth credentials, running the browser flow if needed.

    Order of operations:
      1. Load token.json if present.
      2. If it's valid, return it (unless force_refresh).
      3. If it has a refresh token and is expired, try refreshing silently.
      4. Otherwise run the full browser flow.

    The resulting token is always written to token.json so subsequent calls
    don't need the browser.
    """
    creds = _load_cached_creds()

    if creds and creds.valid and not force_refresh:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_token(creds)
            return creds
        except Exception as e:  # noqa: BLE001 — any refresh failure means re-auth.
            logger.warning("Silent token refresh failed (%s); re-running browser flow.", e)

    creds = _run_browser_flow()
    _write_token(creds)
    return creds


def _write_token(creds: Credentials) -> None:
    tok = token_file()
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(creds.to_json(), encoding="utf-8")
    logger.info("Wrote Gmail token cache to %s", tok)


def get_service(*, creds: Credentials | None = None) -> Any:
    """Return a Gmail API service client (v1)."""
    c = creds or get_credentials()
    # cache_discovery=False silences a noisy warning on fresh envs.
    return build("gmail", "v1", credentials=c, cache_discovery=False)


def whoami(service: Any | None = None) -> str:
    """Return the authenticated user's primary email address."""
    svc = service or get_service()
    profile = svc.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


# ---------------------------------------------------------------------------
# MIME body extraction
# ---------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _decode_b64url(data: str) -> str:
    """Gmail returns message bodies as base64url-encoded UTF-8."""
    if not data:
        return ""
    # Gmail uses URL-safe base64 without padding; pad before decoding.
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to decode base64url body")
        return ""


def _strip_html(raw: str) -> str:
    """Naive HTML → text. Good enough for classifier input.

    We deliberately avoid BeautifulSoup to keep deps small; the classifier
    doesn't need pixel-perfect text, just enough to read.
    """
    # Drop <script> / <style> blocks entirely.
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    # Turn <br>, </p>, </div> into newlines so paragraphs survive.
    raw = re.sub(r"<(br\s*/?|/p|/div|/li)>", "\n", raw, flags=re.IGNORECASE)
    # Strip remaining tags.
    raw = _HTML_TAG_RE.sub("", raw)
    # Unescape &amp; etc.
    raw = html.unescape(raw)
    # Collapse runs of whitespace.
    raw = _WHITESPACE_RE.sub(" ", raw)
    raw = _BLANK_LINES_RE.sub("\n\n", raw)
    return raw.strip()


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail payload tree and pull out the best plain-text body.

    Preference order: text/plain > text/html (stripped) > empty.
    Visits every part; a sibling ``text/plain`` beats an ancestor's HTML.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            plain_parts.append(_decode_b64url(data))
        elif mime == "text/html" and data:
            html_parts.append(_decode_b64url(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)

    if plain_parts:
        return "\n\n".join(p.strip() for p in plain_parts if p.strip())
    if html_parts:
        return _strip_html("\n\n".join(html_parts))
    return ""


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------
def _header(msg: dict[str, Any], name: str) -> str:
    """Case-insensitive header lookup on a Gmail message resource."""
    for h in msg.get("payload", {}).get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_date(raw: str) -> datetime:
    """Parse an RFC 2822 Date header into an aware UTC datetime."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _split_from(raw: str) -> tuple[str, str]:
    """Return (display_name, email_addr) from a "Name <addr@x>" header."""
    name, addr = parseaddr(raw)
    return (name or addr.split("@")[0], addr)


# ---------------------------------------------------------------------------
# Thread fetching
# ---------------------------------------------------------------------------
def _thread_to_model(thread_resource: dict[str, Any], self_email: str) -> Thread | None:
    """Convert a Gmail thread resource into our Thread model.

    Returns None if the thread is effectively empty or contains only messages
    the user sent (nothing inbound to classify).
    """
    messages = thread_resource.get("messages", []) or []
    if not messages:
        return None

    # Find the most recent *inbound* message — one where the user isn't the
    # primary sender. If every message is from the user (rare but possible
    # for INBOX threads via filters), we skip it.
    inbound = [m for m in messages if self_email.lower() not in _header(m, "From").lower()]
    primary = inbound[-1] if inbound else messages[-1]
    if not inbound:
        # Nothing to classify — this thread only contains outbound messages.
        return None

    from_name, from_addr = _split_from(_header(primary, "From"))
    to_addr = _header(primary, "To") or self_email
    subject = _header(primary, "Subject") or "(no subject)"
    date = _parse_date(_header(primary, "Date"))

    # Body: join all messages in chronological order so Claude sees full
    # context for reply-y threads. Prefix each with its sender so the LLM
    # can tell who said what.
    body_chunks: list[str] = []
    for m in messages:
        who, _ = _split_from(_header(m, "From"))
        body = _extract_body(m.get("payload", {}))
        if not body:
            continue
        body_chunks.append(f"[{who}]\n{body}")
    body = "\n\n---\n\n".join(body_chunks) if body_chunks else thread_resource.get("snippet", "")

    # Collect label ids and unread flag from the thread's messages. Gmail
    # surfaces `labelIds` on each message rather than the thread; we
    # union across messages and treat the thread as unread if *any*
    # message is unread, which matches Gmail's UI.
    label_ids: set[str] = set()
    for m in messages:
        for lid in m.get("labelIds", []) or []:
            label_ids.add(lid)
    unread = "UNREAD" in label_ids

    return Thread(
        id=thread_resource["id"],
        **{"from": from_addr},
        from_name=from_name,
        to=to_addr,
        subject=subject,
        received_at=date,
        body=body,
        label_ids=sorted(label_ids),
        snippet=thread_resource.get("snippet", "") or "",
        unread=unread,
    )


def _batch_get(
    svc: Any,
    ids: list[str],
    build_request: Any,
    *,
    kind: str = "item",
) -> list[dict[str, Any]]:
    """Fetch many resources in one HTTP round-trip via Gmail batch.

    ``build_request`` is called with each id and must return a prepared
    ``HttpRequest`` (e.g. ``svc.users().threads().get(...)``). Responses are
    returned in the *input* order; per-sub-request failures are logged and
    skipped, matching the prior serial loop's "skip and continue" behaviour.

    Gmail's batch endpoint caps at 100 sub-requests per multipart call, so
    we chunk. In practice the inbox UI caps at 100 threads, so this is a
    single batch for typical loads — one HTTPS round-trip instead of N.
    """
    if not ids:
        return []

    results: dict[str, dict[str, Any]] = {}

    def _cb(request_id: str, response: Any, exception: Any) -> None:
        if exception is not None:
            logger.warning(
                "Skipping %s %s (fetch failed: %s)", kind, request_id, exception
            )
            return
        results[request_id] = response

    for start in range(0, len(ids), _GMAIL_BATCH_MAX):
        chunk = ids[start : start + _GMAIL_BATCH_MAX]
        batch = svc.new_batch_http_request(callback=_cb)
        for rid in chunk:
            batch.add(build_request(rid), request_id=rid)
        try:
            batch.execute()
        except HttpError as e:
            # If the batch endpoint itself fails we can still fall back to
            # serial calls so the UI degrades gracefully rather than going
            # blank. Keeps the old behaviour available as a safety net.
            logger.warning(
                "Gmail batch failed (%s); falling back to serial fetches", e
            )
            for rid in chunk:
                if rid in results:
                    continue
                try:
                    results[rid] = build_request(rid).execute()
                except HttpError as inner:
                    logger.warning(
                        "Skipping %s %s (fetch failed: %s)", kind, rid, inner
                    )

    return [results[rid] for rid in ids if rid in results]


def fetch_recent_threads(
    limit: int = 25,
    *,
    query: str | None = None,
    label_ids: list[str] | None = None,
    service: Any | None = None,
) -> list[Thread]:
    """Fetch up to ``limit`` recent threads from the authenticated inbox.

    Parameters
    ----------
    limit :
        Upper bound on threads returned. Defaults to 25 to keep demos cheap.
    query :
        Optional Gmail search query (e.g. ``"is:unread newer_than:2d"``).
        Takes precedence over ``label_ids`` when both are set.
    label_ids :
        Gmail label ids to filter by; defaults to ``["INBOX"]`` which hides
        sent-only threads and spam. Ignored if ``query`` is supplied.
    """
    svc = service or get_service()
    self_email = whoami(svc)

    list_kwargs: dict[str, Any] = {"userId": "me", "maxResults": limit}
    if query:
        list_kwargs["q"] = query
    else:
        list_kwargs["labelIds"] = label_ids or ["INBOX"]

    try:
        resp = svc.users().threads().list(**list_kwargs).execute()
    except HttpError as e:
        raise GmailAuthError(f"Gmail API error while listing threads: {e}") from e

    thread_stubs = resp.get("threads", []) or []
    logger.info("Gmail returned %d thread stubs (limit=%d)", len(thread_stubs), limit)

    full_threads = _batch_get(
        svc,
        [s["id"] for s in thread_stubs],
        lambda tid: svc.users().threads().get(userId="me", id=tid, format="full"),
        kind="thread",
    )

    threads: list[Thread] = []
    for full in full_threads:
        model = _thread_to_model(full, self_email)
        if model is not None:
            threads.append(model)

    # Newest first, matching what users expect in a morning brief.
    threads.sort(key=lambda t: t.received_at, reverse=True)
    return threads


# Alias used by the robust UI; `list_inbox` reads more naturally from the
# action layer and keeps a clean seam for future pagination/search flags.
def list_inbox(
    limit: int = 25,
    *,
    query: str | None = None,
    service: Any | None = None,
) -> list[Thread]:
    """Read the inbox. Thin wrapper around :func:`fetch_recent_threads`."""
    return fetch_recent_threads(limit=limit, query=query, service=service)


# ---------------------------------------------------------------------------
# Draft push / update
# ---------------------------------------------------------------------------
def _get_thread_raw(service: Any, thread_id: str) -> dict[str, Any]:
    return service.users().threads().get(
        userId="me",
        id=thread_id,
        format="metadata",
        metadataHeaders=[
            "From", "To", "Cc", "Subject",
            "Message-ID", "References", "In-Reply-To",
        ],
    ).execute()


def _build_reply_mime(
    *,
    to_addr: str,
    from_addr: str,
    subject: str,
    body: str,
    in_reply_to: str,
    references: str,
    body_html: str | None = None,
) -> str:
    """Construct an RFC 2822 reply and return it base64url-encoded.

    When ``body_html`` is provided we emit a ``multipart/alternative`` message
    so Gmail users see the rich HTML version while plain-text clients fall
    back to ``body``.
    """
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    if body_html:
        msg = MIMEMultipart("alternative")
        msg["To"] = to_addr
        msg["From"] = from_addr
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        refs = " ".join(s for s in [references, in_reply_to] if s).strip()
        if refs:
            msg["References"] = refs
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        raw_bytes = msg.as_bytes()
    else:
        msg = EmailMessage()
        msg["To"] = to_addr
        msg["From"] = from_addr
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        refs = " ".join(s for s in [references, in_reply_to] if s).strip()
        if refs:
            msg["References"] = refs
        msg.set_content(body)
        raw_bytes = msg.as_bytes()

    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def _reply_raw_for_draft(
    svc: Any,
    draft: Draft,
    *,
    body_html: str | None = None,
) -> tuple[str, str]:
    """Return ``(raw_b64, thread_id)`` ready for a drafts.create/update call."""
    me = whoami(svc)
    thread = _get_thread_raw(svc, draft.thread_id)
    messages = thread.get("messages", []) or []
    if not messages:
        raise GmailActionError(f"Thread {draft.thread_id} has no messages; can't draft reply.")
    inbound = [m for m in messages if me.lower() not in _header(m, "From").lower()]
    parent = inbound[-1] if inbound else messages[-1]

    _from_name, from_addr = _split_from(_header(parent, "From"))
    message_id = _header(parent, "Message-ID")
    references = _header(parent, "References")

    raw_b64 = _build_reply_mime(
        to_addr=from_addr,
        from_addr=me,
        subject=draft.subject,
        body=draft.body,
        in_reply_to=message_id,
        references=references,
        body_html=body_html,
    )
    return raw_b64, draft.thread_id


def push_draft(
    draft: Draft,
    *,
    service: Any | None = None,
    body_html: str | None = None,
) -> str:
    """Push a drafted reply into the user's Gmail Drafts folder.

    Returns the draft's Gmail id. If ``draft.gmail_draft_id`` is set we
    ``drafts.update`` the existing draft in place; otherwise we create a
    new one. This is what makes "AI draft, then user edits in the UI, then
    user clicks Send" feel like editing a single artifact rather than
    accumulating ghosts in Gmail Drafts.
    """
    svc = service or get_service()
    raw_b64, thread_id = _reply_raw_for_draft(svc, draft, body_html=body_html)
    body = {"message": {"raw": raw_b64, "threadId": thread_id}}

    try:
        if draft.gmail_draft_id:
            created = svc.users().drafts().update(
                userId="me", id=draft.gmail_draft_id, body=body
            ).execute()
        else:
            created = svc.users().drafts().create(userId="me", body=body).execute()
    except HttpError as e:
        raise GmailActionError(f"Failed to push draft for thread {thread_id}: {e}") from e

    draft_id = created.get("id", "")
    logger.info("Pushed Gmail draft %s for thread %s", draft_id, thread_id)
    return draft_id


def update_draft(
    draft: Draft,
    *,
    service: Any | None = None,
    body_html: str | None = None,
) -> str:
    """Replace an existing Gmail draft's contents. Alias for push_draft with
    a clearer name when the caller knows the draft already exists.
    """
    if not draft.gmail_draft_id:
        raise GmailActionError("update_draft requires draft.gmail_draft_id; use push_draft to create.")
    return push_draft(draft, service=service, body_html=body_html)


def list_drafts(
    limit: int = 50,
    *,
    service: Any | None = None,
) -> list[dict[str, Any]]:
    """List the user's Gmail Drafts (metadata). Thin pass-through."""
    svc = service or get_service()
    try:
        resp = svc.users().drafts().list(userId="me", maxResults=limit).execute()
    except HttpError as e:
        raise GmailActionError(f"Failed to list drafts: {e}") from e
    drafts = resp.get("drafts", []) or []
    # Enrich with the linked message's threadId and subject so the UI can
    # render a meaningful list without a second round-trip per row.
    enriched: list[dict[str, Any]] = []
    for d in drafts:
        did = d.get("id")
        try:
            full = svc.users().drafts().get(userId="me", id=did, format="metadata").execute()
        except HttpError:
            continue
        msg = full.get("message", {}) or {}
        enriched.append(
            {
                "id": did,
                "thread_id": msg.get("threadId", ""),
                "subject": next(
                    (h.get("value", "") for h in msg.get("payload", {}).get("headers", []) or []
                     if h.get("name", "").lower() == "subject"),
                    "",
                ),
                "snippet": msg.get("snippet", "") or "",
            }
        )
    return enriched


# ---------------------------------------------------------------------------
# Sent-thread browsing (for the Sent tab in the UI)
# ---------------------------------------------------------------------------
def _sent_thread_to_model(
    thread_resource: dict[str, Any],
    self_email: str,
) -> SentThread | None:
    """Convert a Gmail thread in the Sent folder into a :class:`SentThread`.

    Mirrors :func:`_thread_to_model` but flipped: we pick the user's most
    recent *outbound* message as the primary row, pull ``To`` instead of
    ``From`` into the model, and skip threads that contain no outbound
    messages at all (can happen if Gmail threads a reply back into a
    Sent-labeled thread after the other party wrote back).
    """
    messages = thread_resource.get("messages", []) or []
    if not messages:
        return None

    outbound = [m for m in messages if self_email.lower() in _header(m, "From").lower()]
    if not outbound:
        return None
    primary = outbound[-1]

    to_raw = _header(primary, "To") or ""
    to_name, to_addr = _split_from(to_raw)
    if not to_addr:
        # No recipient parsed — usually a draft that slipped into Sent. Skip.
        return None

    subject = _header(primary, "Subject") or "(no subject)"
    date = _parse_date(_header(primary, "Date"))

    # Join the whole thread body in chronological order, same as the inbound
    # mapper — useful when the user wants to read the back-and-forth.
    body_chunks: list[str] = []
    for m in messages:
        who, _ = _split_from(_header(m, "From"))
        body = _extract_body(m.get("payload", {}))
        if not body:
            continue
        body_chunks.append(f"[{who}]\n{body}")
    body = "\n\n---\n\n".join(body_chunks) if body_chunks else thread_resource.get("snippet", "")

    label_ids: set[str] = set()
    for m in messages:
        for lid in m.get("labelIds", []) or []:
            label_ids.add(lid)

    return SentThread(
        id=thread_resource["id"],
        to=to_addr,
        to_name=to_name or to_addr.split("@")[0],
        subject=subject,
        sent_at=date,
        body=body,
        snippet=thread_resource.get("snippet", "") or "",
        label_ids=sorted(label_ids),
    )


def list_sent_threads(
    limit: int = 25,
    *,
    query: str | None = None,
    service: Any | None = None,
) -> list[SentThread]:
    """Fetch up to ``limit`` recent threads from the user's Sent folder.

    Parameters
    ----------
    limit :
        Upper bound on threads returned.
    query :
        Optional Gmail search query. If supplied, takes precedence over the
        ``SENT`` label filter — but callers should usually include ``in:sent``
        in the query themselves if they want to stay within the Sent view.
    """
    svc = service or get_service()
    self_email = whoami(svc)

    list_kwargs: dict[str, Any] = {"userId": "me", "maxResults": limit}
    if query:
        list_kwargs["q"] = query
    else:
        list_kwargs["labelIds"] = ["SENT"]

    try:
        resp = svc.users().threads().list(**list_kwargs).execute()
    except HttpError as e:
        raise GmailAuthError(f"Gmail API error while listing sent threads: {e}") from e

    stubs = resp.get("threads", []) or []
    logger.info("Gmail returned %d sent-thread stubs (limit=%d)", len(stubs), limit)

    full_threads = _batch_get(
        svc,
        [s["id"] for s in stubs],
        lambda tid: svc.users().threads().get(userId="me", id=tid, format="full"),
        kind="sent thread",
    )

    out: list[SentThread] = []
    for full in full_threads:
        model = _sent_thread_to_model(full, self_email)
        if model is not None:
            out.append(model)

    out.sort(key=lambda t: t.sent_at, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Sent-message fetching (for voice learning)
# ---------------------------------------------------------------------------
def fetch_sent_messages(
    limit: int = 50,
    *,
    service: Any | None = None,
) -> list[dict[str, str]]:
    """Fetch up to ``limit`` recent messages from the user's Sent folder."""
    svc = service or get_service()
    try:
        resp = svc.users().messages().list(
            userId="me", labelIds=["SENT"], maxResults=limit
        ).execute()
    except HttpError as e:
        raise GmailAuthError(f"Gmail API error while listing sent messages: {e}") from e

    stubs = resp.get("messages", []) or []
    logger.info("Gmail returned %d sent-message stubs (limit=%d)", len(stubs), limit)

    full_messages = _batch_get(
        svc,
        [s["id"] for s in stubs],
        lambda mid: svc.users().messages().get(userId="me", id=mid, format="full"),
        kind="sent message",
    )

    out: list[dict[str, str]] = []
    for full in full_messages:
        body = _extract_body(full.get("payload", {}))
        if not body.strip():
            continue
        out.append(
            {
                "subject": _header(full, "Subject"),
                "to": _header(full, "To"),
                "body": body,
            }
        )
    return out


def push_drafts(drafts: list[Draft], *, service: Any | None = None) -> dict[str, str]:
    """Push many drafts; returns {thread_id: gmail_draft_id}."""
    svc = service or get_service()
    out: dict[str, str] = {}
    for d in drafts:
        try:
            out[d.thread_id] = push_draft(d, service=svc)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to push draft for thread %s", d.thread_id)
    return out


# ---------------------------------------------------------------------------
# Action primitives (robust-version additions)
# ---------------------------------------------------------------------------
INBOX_LABEL = "INBOX"
UNREAD_LABEL = "UNREAD"


def _modify_thread(
    svc: Any,
    thread_id: str,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    body: dict[str, list[str]] = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    if not body:
        return
    try:
        svc.users().threads().modify(userId="me", id=thread_id, body=body).execute()
    except HttpError as e:
        raise GmailActionError(f"threads.modify failed for {thread_id}: {e}") from e


def archive_thread(thread_id: str, *, service: Any | None = None) -> None:
    """Remove the INBOX label from a thread. Mirrors Gmail's 'Archive' button."""
    svc = service or get_service()
    _modify_thread(svc, thread_id, remove=[INBOX_LABEL])
    logger.info("Archived thread %s", thread_id)


def mark_read(thread_id: str, *, service: Any | None = None) -> None:
    """Mark every message in a thread as read."""
    svc = service or get_service()
    _modify_thread(svc, thread_id, remove=[UNREAD_LABEL])


def mark_unread(thread_id: str, *, service: Any | None = None) -> None:
    svc = service or get_service()
    _modify_thread(svc, thread_id, add=[UNREAD_LABEL])


def apply_label(thread_id: str, label_id: str, *, service: Any | None = None) -> None:
    """Add a single label to a thread by label id."""
    svc = service or get_service()
    _modify_thread(svc, thread_id, add=[label_id])


def apply_labels(
    thread_id: str,
    label_ids: list[str],
    *,
    service: Any | None = None,
) -> None:
    """Add multiple labels at once."""
    if not label_ids:
        return
    svc = service or get_service()
    _modify_thread(svc, thread_id, add=list(label_ids))


def remove_label(thread_id: str, label_id: str, *, service: Any | None = None) -> None:
    svc = service or get_service()
    _modify_thread(svc, thread_id, remove=[label_id])


def remove_labels(
    thread_id: str,
    label_ids: list[str],
    *,
    service: Any | None = None,
) -> None:
    if not label_ids:
        return
    svc = service or get_service()
    _modify_thread(svc, thread_id, remove=list(label_ids))


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def list_labels(*, service: Any | None = None) -> list[Label]:
    """Return every label the authenticated user has access to, as Label models."""
    svc = service or get_service()
    try:
        resp = svc.users().labels().list(userId="me").execute()
    except HttpError as e:
        raise GmailActionError(f"Failed to list labels: {e}") from e

    out: list[Label] = []
    for row in resp.get("labels", []) or []:
        out.append(
            Label(
                id=row.get("id", ""),
                name=row.get("name", ""),
                type=row.get("type", "user"),
                messages_total=row.get("messagesTotal"),
                threads_total=row.get("threadsTotal"),
            )
        )
    # Stable sort: system labels first, then user labels alphabetically.
    out.sort(key=lambda label: (label.type != "system", label.name.lower()))
    return out


def find_label_by_name(name: str, *, service: Any | None = None) -> Label | None:
    """Case-insensitive lookup by label name. Returns None on miss."""
    for lbl in list_labels(service=service):
        if lbl.name.lower() == name.lower():
            return lbl
    return None


def create_label(
    name: str,
    *,
    service: Any | None = None,
    label_list_visibility: str = "labelShow",
    message_list_visibility: str = "show",
) -> Label:
    """Create a user label. Returns the existing label if one with this name
    already exists (idempotent — the action layer creates labels on demand).
    """
    svc = service or get_service()
    existing = find_label_by_name(name, service=svc)
    if existing is not None:
        return existing

    try:
        created = svc.users().labels().create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": label_list_visibility,
                "messageListVisibility": message_list_visibility,
            },
        ).execute()
    except HttpError as e:
        raise GmailActionError(f"Failed to create label {name!r}: {e}") from e

    logger.info("Created Gmail label %s (id=%s)", name, created.get("id"))
    return Label(
        id=created.get("id", ""),
        name=created.get("name", name),
        type=created.get("type", "user"),
    )


def ensure_labels(
    names: list[str],
    *,
    service: Any | None = None,
) -> dict[str, Label]:
    """Return a map of ``name -> Label``, creating any that don't exist."""
    svc = service or get_service()
    existing = {lbl.name.lower(): lbl for lbl in list_labels(service=svc)}
    out: dict[str, Label] = {}
    for name in names:
        if name.lower() in existing:
            out[name] = existing[name.lower()]
        else:
            out[name] = create_label(name, service=svc)
    return out


# ---------------------------------------------------------------------------
# Compose + send
# ---------------------------------------------------------------------------
def _build_compose_mime(req: ComposeRequest, from_addr: str) -> str:
    """Build the raw base64url-encoded MIME for a fresh outbound message."""
    if req.body_html:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = req.to
        if req.cc:
            msg["Cc"] = req.cc
        if req.bcc:
            msg["Bcc"] = req.bcc
        msg["Subject"] = req.subject
        msg.attach(MIMEText(req.body, "plain", "utf-8"))
        msg.attach(MIMEText(req.body_html, "html", "utf-8"))
        raw_bytes = msg.as_bytes()
    else:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = req.to
        if req.cc:
            msg["Cc"] = req.cc
        if req.bcc:
            msg["Bcc"] = req.bcc
        msg["Subject"] = req.subject
        msg.set_content(req.body)
        raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def compose(
    req: ComposeRequest,
    *,
    service: Any | None = None,
) -> dict[str, str]:
    """Create a new Gmail draft OR send directly based on ``req.save_as_draft``.

    Returns ``{"id": <draft_or_message_id>, "mode": "draft"|"sent"}``.
    """
    svc = service or get_service()
    me = whoami(svc)
    raw_b64 = _build_compose_mime(req, from_addr=me)

    try:
        if req.save_as_draft:
            created = svc.users().drafts().create(
                userId="me", body={"message": {"raw": raw_b64}}
            ).execute()
            return {"id": created.get("id", ""), "mode": "draft"}
        sent = svc.users().messages().send(
            userId="me", body={"raw": raw_b64}
        ).execute()
        return {"id": sent.get("id", ""), "mode": "sent"}
    except HttpError as e:
        raise GmailActionError(f"Compose failed: {e}") from e


def send_draft(draft_id: str, *, service: Any | None = None) -> str:
    """Send an existing Gmail draft. Returns the sent message id."""
    svc = service or get_service()
    try:
        sent = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    except HttpError as e:
        raise GmailActionError(f"Failed to send draft {draft_id}: {e}") from e
    return sent.get("id", "")
