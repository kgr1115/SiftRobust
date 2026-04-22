"""FastAPI backend that powers the SiftRobust web UI.

What the API exposes:

  * ``GET /api/health`` — readiness probe plus configured provider/model.
  * ``GET /api/inbox`` — list recent INBOX threads.
  * ``POST /api/classify`` — classify a set of threads (hits the cache).
  * ``POST /api/draft`` — generate drafts for the threads the classifier
    flagged as ``urgent`` or ``needs_reply``.
  * ``GET /api/brief`` — end-to-end pipeline: fetch + classify + draft +
    render. The UI's "Morning brief" tab calls this one.
  * ``GET /api/labels`` / ``POST /api/labels`` / ``DELETE /api/labels/{id}``
    — label CRUD.
  * ``POST /api/threads/{id}/archive`` — archive a single thread.
  * ``POST /api/threads/{id}/labels`` / ``DELETE /api/threads/{id}/labels/{label_id}``
    — per-thread label manipulation.
  * ``POST /api/threads/{id}/mark_read`` / ``POST /api/threads/{id}/mark_unread``
  * ``POST /api/apply`` — AI-driven bulk action run. Honors ``ActionPolicy.dry_run``.
  * ``GET /api/drafts`` / ``POST /api/drafts`` / ``POST /api/drafts/{id}/send``
    — Gmail Drafts listing, rich-editor push, and send.
  * ``POST /api/compose`` — send/draft a brand-new outbound email.

Design notes:

  * Everything Gmail-touching depends on :func:`sift.gmail_client.get_service`.
  * The API is session-less; auth is the OAuth token on disk. That makes
    the robust version a single-user local tool, which is the right shape
    for a portfolio demo running on your laptop — not a SaaS.
  * Pydantic models double as request/response schemas; FastAPI auto-generates
    the OpenAPI doc at /docs for free.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import actions, brief as brief_mod, cache, catalog, gmail_client, settings
from .classifier import classify_threads
from .config import CONFIG, reload_env_and_config
from .drafter import draft_replies
from .provider_errors import classify_provider_exception
from .models import (
    ActionPolicy,
    ApplyReport,
    Brief,
    Category,
    Classification,
    ComposeRequest,
    Draft,
    Label,
    SentThread,
    Thread,
)

logger = logging.getLogger(__name__)


def _raise_provider_http(exc: Exception) -> "None":
    """Re-raise a provider exception as a structured HTTPException.

    The UI reads ``detail.error_type`` to decide whether to show a generic
    error toast or a targeted one with a "Switch provider" action. Keeping
    the shape consistent across endpoints means one frontend error handler
    can cover all of them.
    """
    classified = classify_provider_exception(exc)
    logger.warning(
        "provider error on %s: %s (%s)",
        CONFIG.llm_provider, classified.error_type, classified.detail,
    )
    raise HTTPException(
        status_code=classified.status_code,
        detail={
            "error_type": classified.error_type,
            "provider": CONFIG.llm_provider,
            "model": CONFIG.model,
            "message": classified.message,
            "detail": classified.detail,
        },
    ) from exc


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str = "ok"
    provider: str
    model: str | None
    db_path: str


class InboxResponse(BaseModel):
    threads: list[Thread]


class SentResponse(BaseModel):
    threads: list[SentThread]


class ClassifyRequest(BaseModel):
    threads: list[Thread]
    use_cache: bool = True


class ClassifyResponse(BaseModel):
    classifications: list[Classification]


class DraftRequest(BaseModel):
    threads: list[Thread]
    classifications: list[Classification]
    use_cache: bool = True


class DraftResponse(BaseModel):
    drafts: dict[str, Draft]


class BriefResponse(BaseModel):
    """The full pipeline output. Everything the UI's main page needs."""
    brief: Brief
    markdown: str
    classifications: list[Classification]
    drafts: dict[str, Draft]


class CreateLabelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApplyLabelRequest(BaseModel):
    label_ids: list[str] = Field(min_length=1)


class ApplyPolicyRequest(BaseModel):
    """Bulk-action request. Reuses ActionPolicy verbatim so the UI can send
    exactly what the CLI does."""
    threads: list[Thread]
    classifications: list[Classification]
    policy: ActionPolicy


class PushDraftRequest(BaseModel):
    draft: Draft
    # Rich-text UI sends both plain and HTML so non-Gmail clients still render.
    body_html: str | None = None


class PushDraftResponse(BaseModel):
    draft: Draft
    gmail_draft_id: str


class SendResponse(BaseModel):
    id: str
    mode: str


# --- settings / catalog ----------------------------------------------------
class ProviderKeyState(BaseModel):
    """One row of the per-provider key status.

    Never returns the raw key — only whether one is set, plus a masked tail
    so the user can eyeball *which* key is loaded.
    """
    provider: str                  # "anthropic", "openai", "google", "groq"
    env_var: str                   # "ANTHROPIC_API_KEY"
    key_set: bool
    masked: str                    # e.g. "sk-ant-…abcd" or "" if unset


class SettingsResponse(BaseModel):
    llm_provider: str
    model: str | None              # None means "use provider default"
    providers: list[ProviderKeyState]


class SettingsUpdateRequest(BaseModel):
    """Partial update. Any field omitted is left alone.

    ``api_keys`` maps env-var name -> new value. Pass an empty string to
    *unset* a key. Masked values (containing ``…``) are ignored so the UI
    can safely round-trip whatever it displayed without clobbering the
    real secret.
    """
    llm_provider: str | None = None
    model: str | None = None       # use "" to clear the override
    api_keys: dict[str, str] | None = None


class CatalogModel(BaseModel):
    provider: str
    model: str
    input_per_mtok: float
    output_per_mtok: float
    is_default: bool
    accuracy: float | None = None
    per_category_recall: dict[str, float] | None = None
    # Set when accuracy is from a sibling model under the same provider
    # (i.e. the exact model wasn't in the comparison run). Null = exact match.
    eval_model: str | None = None


class CatalogProvider(BaseModel):
    name: str
    display_name: str
    env_var: str
    default_model: str
    models: list[CatalogModel]


class CatalogResponse(BaseModel):
    providers: list[CatalogProvider]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="SiftRobust API",
        description=(
            "Gmail triage + action backend. Wraps the classifier, drafter, "
            "Gmail client, and AI-driven bulk-action layer behind a clean REST surface."
        ),
        version="0.1.0",
    )

    # The Vite dev server runs on :5173 by default. Tighten this for prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- health -----------------------------------------------------------
    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            provider=CONFIG.llm_provider,
            model=CONFIG.model,
            db_path=str(cache.init_db()),
        )

    # --- settings --------------------------------------------------------
    # The settings endpoints drive the header dropdown + settings dialog so
    # the user can swap provider/model and paste API keys without touching
    # .env directly. Writes go through ``settings.upsert_env_values`` (atomic
    # + comment-preserving), then we reload dotenv and the CONFIG singleton
    # so subsequent LLM calls pick up the change without a process restart.

    def _current_settings() -> SettingsResponse:
        env = settings.read_env_file()
        rows: list[ProviderKeyState] = []
        for spec in catalog.build_catalog():
            # Prefer the in-process env (reflects live changes) and fall back
            # to the .env file so "key set" stays correct even before a reload.
            raw = os.getenv(spec.env_var) or env.get(spec.env_var, "")
            rows.append(
                ProviderKeyState(
                    provider=spec.name,
                    env_var=spec.env_var,
                    key_set=bool(raw),
                    masked=settings.mask_secret(raw) if raw else "",
                )
            )
        return SettingsResponse(
            llm_provider=CONFIG.llm_provider,
            model=CONFIG.model,
            providers=rows,
        )

    @app.get("/api/settings", response_model=SettingsResponse)
    def get_settings() -> SettingsResponse:
        return _current_settings()

    @app.put("/api/settings", response_model=SettingsResponse)
    def put_settings(req: SettingsUpdateRequest) -> SettingsResponse:
        updates: dict[str, str] = {}
        known_providers = {p.name for p in catalog.build_catalog()}

        if req.llm_provider is not None:
            if req.llm_provider not in known_providers:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider: {req.llm_provider!r}",
                )
            updates["LLM_PROVIDER"] = req.llm_provider

        if req.model is not None:
            # Empty string means "clear the override"; settings handles that
            # by deleting the line entirely.
            updates["SIFT_MODEL"] = req.model.strip()

        if req.api_keys:
            for env_var, value in req.api_keys.items():
                if env_var not in settings.ALLOWED_KEYS:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refusing to write disallowed env var: {env_var}",
                    )
                # Ignore masked values the UI may have round-tripped so we
                # don't accidentally overwrite the real key with "sk-…abcd".
                if "…" in value:
                    continue
                updates[env_var] = value.strip()

        if not updates:
            return _current_settings()

        try:
            settings.upsert_env_values(updates)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Update the in-process env too so provider clients (which read
        # os.environ on next _client() call) pick it up immediately.
        for k, v in updates.items():
            if v == "":
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        # Rebuild the cached provider so a stale lru_cache entry doesn't
        # keep using the old model or a dead client.
        try:
            from .providers.registry import get_provider  # local import
            get_provider.cache_clear()
        except Exception:  # pragma: no cover — defensive
            pass

        reload_env_and_config()
        return _current_settings()

    # --- catalog (read-only) ---------------------------------------------
    @app.get("/api/catalog", response_model=CatalogResponse)
    def get_catalog() -> CatalogResponse:
        providers: list[CatalogProvider] = []
        for p in catalog.build_catalog():
            providers.append(
                CatalogProvider(
                    name=p.name,
                    display_name=p.display_name,
                    env_var=p.env_var,
                    default_model=p.default_model,
                    models=[
                        CatalogModel(
                            provider=m.provider,
                            model=m.model,
                            input_per_mtok=m.input_per_mtok,
                            output_per_mtok=m.output_per_mtok,
                            is_default=m.is_default,
                            accuracy=m.accuracy,
                            per_category_recall=m.per_category_recall,
                            eval_model=m.eval_model,
                        )
                        for m in p.models
                    ],
                )
            )
        return CatalogResponse(providers=providers)

    # --- inbox ------------------------------------------------------------
    @app.get("/api/inbox", response_model=InboxResponse)
    def inbox(limit: int = 25, q: str | None = None) -> InboxResponse:
        try:
            threads = gmail_client.list_inbox(limit=limit, query=q)
        except gmail_client.GmailAuthError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return InboxResponse(threads=threads)

    # --- sent -------------------------------------------------------------
    @app.get("/api/sent", response_model=SentResponse)
    def sent(limit: int = 25, q: str | None = None) -> SentResponse:
        try:
            threads = gmail_client.list_sent_threads(limit=limit, query=q)
        except gmail_client.GmailAuthError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return SentResponse(threads=threads)

    # --- classify / draft ------------------------------------------------
    # Both of these call the LLM provider, so any provider-side failure
    # (bad key, zero balance, model deprecated) surfaces here. ``_raise_provider_http``
    # normalizes the exception into a typed response the UI can act on.
    @app.post("/api/classify", response_model=ClassifyResponse)
    def classify(req: ClassifyRequest) -> ClassifyResponse:
        try:
            classifications = classify_threads(req.threads, use_cache=req.use_cache)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — classify everything uniformly
            _raise_provider_http(e)
        return ClassifyResponse(classifications=classifications)

    @app.post("/api/draft", response_model=DraftResponse)
    def draft(req: DraftRequest) -> DraftResponse:
        try:
            drafts = draft_replies(
                req.threads,
                req.classifications,
                use_cache=req.use_cache,
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            _raise_provider_http(e)
        return DraftResponse(drafts=drafts)

    # --- full-pipeline brief --------------------------------------------
    @app.get("/api/brief", response_model=BriefResponse)
    def brief(limit: int = 25) -> BriefResponse:
        try:
            threads = gmail_client.list_inbox(limit=limit)
        except gmail_client.GmailAuthError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

        try:
            classifications = classify_threads(threads)
            drafts = draft_replies(threads, classifications)
        except Exception as e:  # noqa: BLE001 — funnel through provider_errors
            _raise_provider_http(e)
        b = brief_mod.build_brief(threads, classifications, drafts)
        return BriefResponse(
            brief=b,
            markdown=brief_mod.render_brief(b),
            classifications=classifications,
            drafts=drafts,
        )

    # --- labels ----------------------------------------------------------
    @app.get("/api/labels", response_model=list[Label])
    def get_labels() -> list[Label]:
        try:
            return gmail_client.list_labels()
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.post("/api/labels", response_model=Label, status_code=201)
    def post_label(req: CreateLabelRequest) -> Label:
        try:
            return gmail_client.create_label(req.name)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    # --- per-thread actions ---------------------------------------------
    @app.post("/api/threads/{thread_id}/archive")
    def archive_thread(thread_id: str) -> dict[str, Any]:
        result = actions.archive(thread_id)
        if not result.applied:
            raise HTTPException(status_code=502, detail=result.note)
        return {"ok": True}

    @app.post("/api/threads/{thread_id}/labels")
    def add_thread_labels(thread_id: str, req: ApplyLabelRequest) -> dict[str, Any]:
        try:
            gmail_client.apply_labels(thread_id, req.label_ids)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"ok": True}

    @app.delete("/api/threads/{thread_id}/labels/{label_id}")
    def remove_thread_label(thread_id: str, label_id: str) -> dict[str, Any]:
        try:
            gmail_client.remove_label(thread_id, label_id)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"ok": True}

    @app.post("/api/threads/{thread_id}/mark_read")
    def mark_read(thread_id: str) -> dict[str, Any]:
        try:
            gmail_client.mark_read(thread_id)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"ok": True}

    @app.post("/api/threads/{thread_id}/mark_unread")
    def mark_unread(thread_id: str) -> dict[str, Any]:
        try:
            gmail_client.mark_unread(thread_id)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"ok": True}

    # --- AI bulk apply ---------------------------------------------------
    @app.post("/api/apply", response_model=ApplyReport)
    def apply(req: ApplyPolicyRequest) -> ApplyReport:
        try:
            return actions.apply_classifications(
                req.threads, req.classifications, req.policy
            )
        except ValueError as e:
            # _enforce_safe_categories raises ValueError on bad policies.
            raise HTTPException(status_code=400, detail=str(e)) from e

    # --- drafts + send ---------------------------------------------------
    @app.get("/api/drafts")
    def list_drafts(limit: int = 50) -> list[dict[str, Any]]:
        try:
            return gmail_client.list_drafts(limit=limit)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.post("/api/drafts", response_model=PushDraftResponse)
    def push_or_update_draft(req: PushDraftRequest) -> PushDraftResponse:
        try:
            gmail_draft_id = gmail_client.push_draft(
                req.draft, body_html=req.body_html
            )
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

        stamped = req.draft.model_copy(update={"gmail_draft_id": gmail_draft_id})
        return PushDraftResponse(draft=stamped, gmail_draft_id=gmail_draft_id)

    @app.post("/api/drafts/{draft_id}/send", response_model=SendResponse)
    def send_existing_draft(draft_id: str) -> SendResponse:
        try:
            sent_id = gmail_client.send_draft(draft_id)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return SendResponse(id=sent_id, mode="sent")

    # --- compose new -----------------------------------------------------
    @app.post("/api/compose", response_model=SendResponse)
    def compose(req: ComposeRequest) -> SendResponse:
        try:
            result = gmail_client.compose(req)
        except gmail_client.GmailActionError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return SendResponse(id=result["id"], mode=result["mode"])

    # --- cache admin -----------------------------------------------------
    @app.get("/api/cache/stats")
    def cache_stats() -> dict[str, int]:
        return cache.stats()

    class CacheClearRequest(BaseModel):
        table: str | None = None

    @app.post("/api/cache/clear")
    def cache_clear(req: CacheClearRequest) -> dict[str, int]:
        try:
            n = cache.clear(req.table)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"cleared": n}

    # --- category helper (UI dropdown) -----------------------------------
    @app.get("/api/categories", response_model=list[str])
    def list_categories() -> list[str]:
        return [c.value for c in Category]

    return app


# Module-level instance for `uvicorn sift.api:app` one-liners.
app = create_app()
