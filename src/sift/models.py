"""Pydantic data models shared across the pipeline.

Why Pydantic: we're passing JSON back and forth between Claude's tool-use outputs,
the cache, the UI, and the evals. A single source-of-truth schema means the
classifier can't quietly return an unexpected shape and ripple breakage downstream.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    """The five triage buckets. Order is display-priority (urgent first)."""

    URGENT = "urgent"
    NEEDS_REPLY = "needs_reply"
    FYI = "fyi"
    NEWSLETTER = "newsletter"
    TRASH = "trash"


# Helpful for schemas / prompts that need the literal strings.
CATEGORY_VALUES = [c.value for c in Category]


class Thread(BaseModel):
    """A normalized email thread — the input to the classifier and drafter.

    This is deliberately a subset of Gmail's full thread schema; we don't want
    Claude sifting through MIME headers when a few clean fields will do.
    """

    id: str
    from_: str = Field(alias="from")
    from_name: str
    to: str
    subject: str
    received_at: datetime
    body: str

    # Robust-version additions: populated only when the thread came from Gmail.
    # Left optional so fixtures-mode and older callers stay source-compatible.
    label_ids: list[str] = Field(default_factory=list)
    snippet: str = ""
    unread: bool = False

    model_config = {"populate_by_name": True}


class LabeledThread(Thread):
    """A thread with a ground-truth label. Used for evals and the synthetic fixture inbox."""

    label: Category
    notes: str = ""


class Classification(BaseModel):
    """The classifier's structured output for a single thread."""

    thread_id: str
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    one_line_summary: str
    reason: str


class Draft(BaseModel):
    """A draft reply for a single thread."""

    thread_id: str
    subject: str
    body: str
    tone_notes: str = ""  # Why Claude chose this tone — useful for debugging and writeups.
    # Populated once pushed to Gmail; used to edit the existing draft in place.
    gmail_draft_id: str | None = None


class BriefItem(BaseModel):
    """One row in the morning brief — a classified thread, with an optional draft."""

    thread: Thread
    classification: Classification
    draft: Draft | None = None


class Brief(BaseModel):
    """The rendered morning brief."""

    generated_at: datetime
    items: list[BriefItem]

    def by_category(self, cat: Category) -> list[BriefItem]:
        return [i for i in self.items if i.classification.category == cat]


class VoiceProfile(BaseModel):
    """A compressed description of how the user writes.

    Populated either from a hand-written default or by :mod:`sift.voice` after
    ingesting the user's Gmail Sent folder. Cached per user email so a freshly
    learned profile persists across runs.
    """

    summary: str
    style_examples: list[str] = Field(default_factory=list)

    # Populated when the profile is learned (not present on the hardcoded default).
    user_email: str | None = None
    learned_at: datetime | None = None

    def render_for_prompt(self) -> str:
        """Format this profile for injection into the drafter's system prompt."""
        lines = [self.summary]
        if self.style_examples:
            lines.append("")
            lines.append("Example replies the user has actually sent:")
            for i, ex in enumerate(self.style_examples, 1):
                lines.append(f"\n--- Example {i} ---\n{ex}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Robust-version additions — action layer + label manager
# ---------------------------------------------------------------------------
class Label(BaseModel):
    """A Gmail label. Used by the label manager UI + action layer.

    ``type`` is "system" for Gmail's built-ins (INBOX, UNREAD, ...) and "user"
    for labels the user created. We expose system labels read-only in the UI.
    """

    id: str
    name: str
    type: str = "user"
    messages_total: int | None = None
    threads_total: int | None = None


class ActionPolicy(BaseModel):
    """Maps classifier categories to bulk actions.

    Used by :mod:`sift.actions` for the AI-driven "apply" workflow. Every
    action is optional; leave a category un-mapped to skip it. ``min_confidence``
    gates every automatic action so low-confidence classifications fall through
    to manual review.
    """

    # If True, simulate only — never call Gmail modify endpoints.
    dry_run: bool = True

    # Skip any classification below this confidence.
    min_confidence: float = 0.75

    # Category → list of label names to apply. Label is created if it doesn't exist.
    apply_labels: dict[Category, list[str]] = Field(default_factory=dict)

    # Categories for which to archive (remove INBOX label).
    archive_categories: list[Category] = Field(default_factory=list)

    # Categories for which to mark-as-read.
    mark_read_categories: list[Category] = Field(default_factory=list)


class ActionResult(BaseModel):
    """The outcome of a single action against a single thread."""

    thread_id: str
    action: str  # e.g. "archive", "apply_label:Sift/Newsletter", "mark_read"
    applied: bool
    note: str = ""  # error message or dry-run explanation


class ApplyReport(BaseModel):
    """Summary of an :func:`sift.actions.apply_classifications` run."""

    dry_run: bool
    total_threads: int
    skipped_low_confidence: int
    results: list[ActionResult] = Field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return sum(1 for r in self.results if r.applied)


class SentThread(BaseModel):
    """A thread from the Sent folder, rendered from the user's outbound side.

    The regular :class:`Thread` is shaped around *incoming* mail (``from_`` is
    the other person, ``to`` is the user). For the Sent view that polarity is
    wrong, so we use a dedicated model where ``to`` is whoever the user wrote
    to. Keeps the UI types honest and prevents the classifier from ever being
    asked to triage a thread the user sent themselves.
    """

    id: str
    to: str
    to_name: str
    subject: str
    sent_at: datetime
    body: str
    snippet: str = ""
    label_ids: list[str] = Field(default_factory=list)


class ComposeRequest(BaseModel):
    """Shape of a compose-new-email request from the UI."""

    to: str
    subject: str
    body: str
    # If present, draft is written as HTML (multipart/alternative with a plain
    # fallback auto-generated from the tags). Otherwise plain text.
    body_html: str | None = None
    cc: str | None = None
    bcc: str | None = None
    # If true, push straight to Gmail Drafts. If false, send via users.messages.send.
    save_as_draft: bool = True
