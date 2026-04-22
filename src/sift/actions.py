"""AI-driven bulk action layer.

Takes a batch of classified threads and applies the user's :class:`ActionPolicy`
to them: archive trash, label newsletters, mark receipts as read, whatever
the policy says. The classifier does the thinking; this module does the
doing — the two are kept separate so we can unit-test the policy logic
without any Gmail mocks, and so the UI can preview what *would* happen
(``dry_run=True``) before any write hits Google.

Safety gates (in order):

  1. **Dry-run default.** ``ActionPolicy.dry_run`` starts True — the UI
     has to flip it off explicitly before a single label moves.
  2. **Confidence floor.** Classifications below ``min_confidence`` are
     skipped entirely. Classifier errors (confidence == 0.0) never trigger
     an action even if the category happens to be in the policy.
  3. **Safe categories only.** We deliberately don't support bulk actions
     on ``urgent`` or ``needs_reply``; those categories should always get
     eyeball time, and the UI doesn't expose them in the policy builder.

The output :class:`ApplyReport` is the single source of truth for "what
the robot did" — the UI renders it, the CLI prints it, tests assert on it.
"""

from __future__ import annotations

import logging
from typing import Any

from . import gmail_client
from .models import (
    ActionPolicy,
    ActionResult,
    ApplyReport,
    Category,
    Classification,
    Thread,
)

logger = logging.getLogger(__name__)

# Categories the UI is allowed to target in a bulk policy. Keeping this
# explicit means a future "be aggressive" mode can't accidentally auto-archive
# an urgent email just because someone misconfigured the policy.
SAFE_AUTO_CATEGORIES: frozenset[Category] = frozenset(
    {Category.FYI, Category.NEWSLETTER, Category.TRASH}
)


def _resolve_label_ids(
    policy: ActionPolicy,
    *,
    service: Any,
    dry_run: bool,
) -> dict[str, str]:
    """Map every label name referenced in the policy to a Gmail label id.

    Creates missing labels as a side effect (unless ``dry_run``). Returns
    ``{label_name: label_id}``. In dry-run mode, unknown labels get a
    placeholder ``"<would-create>"`` id so the report is still legible.
    """
    wanted: set[str] = set()
    for names in policy.apply_labels.values():
        for n in names:
            wanted.add(n)
    if not wanted:
        return {}

    existing = {lbl.name.lower(): lbl for lbl in gmail_client.list_labels(service=service)}
    resolved: dict[str, str] = {}
    for name in wanted:
        hit = existing.get(name.lower())
        if hit is not None:
            resolved[name] = hit.id
            continue
        if dry_run:
            resolved[name] = "<would-create>"
            continue
        created = gmail_client.create_label(name, service=service)
        resolved[name] = created.id
    return resolved


def _plan_for_thread(
    thread_id: str,
    category: Category,
    policy: ActionPolicy,
    label_ids: dict[str, str],
) -> list[tuple[str, dict[str, Any]]]:
    """Return the list of actions implied by the policy for a single thread.

    Each action is ``(action_name, details_dict)``. Kept declarative so the
    dry-run report has the same shape as the applied-for-real report.
    """
    plan: list[tuple[str, dict[str, Any]]] = []

    label_names = policy.apply_labels.get(category, [])
    for name in label_names:
        plan.append((
            f"apply_label:{name}",
            {"label_name": name, "label_id": label_ids.get(name, "")},
        ))

    if category in policy.archive_categories:
        plan.append(("archive", {}))

    if category in policy.mark_read_categories:
        plan.append(("mark_read", {}))

    return plan


def apply_classifications(
    threads: list[Thread],
    classifications: list[Classification],
    policy: ActionPolicy,
    *,
    service: Any | None = None,
) -> ApplyReport:
    """Apply ``policy`` to every thread whose classification qualifies.

    ``service`` is the Gmail API client (for tests / reuse); if None we
    build one lazily. In dry-run mode no Gmail write happens — the service
    is still consulted to resolve existing label ids.
    """
    _enforce_safe_categories(policy)

    class_by_id = {c.thread_id: c for c in classifications}
    dry_run = policy.dry_run
    svc = service
    if svc is None and any(
        policy.apply_labels.get(c.category) or c.category in policy.archive_categories
        or c.category in policy.mark_read_categories
        for c in classifications
    ):
        svc = gmail_client.get_service()

    label_ids = _resolve_label_ids(policy, service=svc, dry_run=dry_run) if svc else {}

    results: list[ActionResult] = []
    skipped_low = 0

    for t in threads:
        cls = class_by_id.get(t.id)
        if cls is None:
            continue
        if cls.confidence < policy.min_confidence:
            skipped_low += 1
            continue
        plan = _plan_for_thread(t.id, cls.category, policy, label_ids)
        for action_name, details in plan:
            result = _apply_one(t.id, action_name, details, dry_run=dry_run, service=svc)
            results.append(result)

    return ApplyReport(
        dry_run=dry_run,
        total_threads=len(threads),
        skipped_low_confidence=skipped_low,
        results=results,
    )


def _enforce_safe_categories(policy: ActionPolicy) -> None:
    """Reject a policy that targets categories we refuse to automate.

    We'd rather fail loudly at the edge of the action layer than quietly
    archive an urgent thread because a bad UI interaction added `urgent`
    to the archive list.
    """
    offenders: set[Category] = set()
    for cat in policy.apply_labels:
        if cat not in SAFE_AUTO_CATEGORIES:
            offenders.add(cat)
    for cat in policy.archive_categories:
        if cat not in SAFE_AUTO_CATEGORIES:
            offenders.add(cat)
    for cat in policy.mark_read_categories:
        if cat not in SAFE_AUTO_CATEGORIES:
            offenders.add(cat)
    if offenders:
        raise ValueError(
            f"ActionPolicy targets non-safe categories: {sorted(c.value for c in offenders)}. "
            f"Bulk auto-actions are only allowed for {sorted(c.value for c in SAFE_AUTO_CATEGORIES)}."
        )


def _apply_one(
    thread_id: str,
    action_name: str,
    details: dict[str, Any],
    *,
    dry_run: bool,
    service: Any | None,
) -> ActionResult:
    """Execute a single planned action and return the result row."""
    if dry_run:
        return ActionResult(
            thread_id=thread_id,
            action=action_name,
            applied=False,
            note="dry-run: no change made",
        )

    if service is None:  # pragma: no cover — guarded upstream
        service = gmail_client.get_service()

    try:
        if action_name.startswith("apply_label:"):
            label_id = details.get("label_id")
            if not label_id:
                return ActionResult(
                    thread_id=thread_id,
                    action=action_name,
                    applied=False,
                    note="missing resolved label id",
                )
            gmail_client.apply_label(thread_id, label_id, service=service)
        elif action_name == "archive":
            gmail_client.archive_thread(thread_id, service=service)
        elif action_name == "mark_read":
            gmail_client.mark_read(thread_id, service=service)
        else:
            return ActionResult(
                thread_id=thread_id,
                action=action_name,
                applied=False,
                note=f"unknown action {action_name!r}",
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("Action %s failed for %s", action_name, thread_id)
        return ActionResult(
            thread_id=thread_id,
            action=action_name,
            applied=False,
            note=f"error: {e}",
        )

    return ActionResult(thread_id=thread_id, action=action_name, applied=True)


# ---------------------------------------------------------------------------
# Convenience helpers for individual (non-bulk) writes driven by the UI.
# Thin wrappers so the UI never has to import gmail_client directly and the
# return shape always matches the bulk path's ActionResult.
# ---------------------------------------------------------------------------
def archive(thread_id: str, *, service: Any | None = None) -> ActionResult:
    try:
        gmail_client.archive_thread(thread_id, service=service)
    except Exception as e:  # noqa: BLE001
        return ActionResult(thread_id=thread_id, action="archive", applied=False, note=f"error: {e}")
    return ActionResult(thread_id=thread_id, action="archive", applied=True)


def add_label_by_name(
    thread_id: str,
    label_name: str,
    *,
    service: Any | None = None,
) -> ActionResult:
    """Apply a user label, creating it if necessary."""
    try:
        svc = service or gmail_client.get_service()
        label = gmail_client.create_label(label_name, service=svc)
        gmail_client.apply_label(thread_id, label.id, service=svc)
    except Exception as e:  # noqa: BLE001
        return ActionResult(
            thread_id=thread_id,
            action=f"apply_label:{label_name}",
            applied=False,
            note=f"error: {e}",
        )
    return ActionResult(
        thread_id=thread_id, action=f"apply_label:{label_name}", applied=True
    )


def remove_label_by_id(
    thread_id: str,
    label_id: str,
    *,
    service: Any | None = None,
) -> ActionResult:
    try:
        gmail_client.remove_label(thread_id, label_id, service=service)
    except Exception as e:  # noqa: BLE001
        return ActionResult(
            thread_id=thread_id,
            action=f"remove_label:{label_id}",
            applied=False,
            note=f"error: {e}",
        )
    return ActionResult(
        thread_id=thread_id, action=f"remove_label:{label_id}", applied=True
    )
