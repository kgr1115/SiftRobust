"""Tests for the bulk-action layer.

The point of these tests: verify the safety gates (dry-run default,
confidence floor, safe-category whitelist) without ever touching the real
Gmail API. We feed synthetic threads + classifications in and assert on
the ApplyReport structure. The Gmail service is passed as a fake — every
bulk-apply path accepts a ``service=`` kwarg for exactly this reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sift import actions
from sift.models import (
    ActionPolicy,
    Category,
    Classification,
    Thread,
)


def _thread(thread_id: str) -> Thread:
    return Thread(
        id=thread_id,
        from_="bot@example.com",
        from_name="Bot",
        to="kyle.g.rauch@gmail.com",
        subject="Subject",
        received_at="2026-04-20T10:00:00Z",
        body="Body",
        label_ids=[],
        snippet="",
        unread=True,
    )


def _classify(thread_id: str, category: Category, confidence: float) -> Classification:
    return Classification(
        thread_id=thread_id,
        category=category,
        confidence=confidence,
        one_line_summary="…",
        reason="…",
    )


def test_dry_run_default_and_no_side_effects():
    """Dry-run policies return an applied=False report and never call the service."""
    svc = MagicMock()
    threads = [_thread("t1")]
    classes = [_classify("t1", Category.NEWSLETTER, 0.95)]
    policy = ActionPolicy(
        dry_run=True,
        min_confidence=0.5,
        apply_labels={},
        archive_categories=[Category.NEWSLETTER],
        mark_read_categories=[],
    )

    report = actions.apply_classifications(threads, classes, policy, service=svc)

    assert report.dry_run is True
    assert report.total_threads == 1
    assert len(report.results) == 1
    assert report.results[0].applied is False
    # No Gmail writes happened.
    svc.users.return_value.threads.return_value.modify.assert_not_called()


def test_low_confidence_is_skipped():
    threads = [_thread("t1"), _thread("t2")]
    classes = [
        _classify("t1", Category.TRASH, 0.99),
        _classify("t2", Category.TRASH, 0.40),  # below floor
    ]
    policy = ActionPolicy(
        dry_run=True,
        min_confidence=0.7,
        apply_labels={},
        archive_categories=[Category.TRASH],
        mark_read_categories=[],
    )
    report = actions.apply_classifications(threads, classes, policy)

    assert report.skipped_low_confidence == 1
    # Only t1 should have generated an action.
    assert all(r.thread_id == "t1" for r in report.results)


@pytest.mark.parametrize(
    "field,bad_cat",
    [
        ("archive_categories", Category.URGENT),
        ("archive_categories", Category.NEEDS_REPLY),
        ("mark_read_categories", Category.URGENT),
    ],
)
def test_safe_category_whitelist(field: str, bad_cat: Category):
    """Policies targeting urgent/needs_reply are rejected at the edge."""
    kwargs: dict = {
        "dry_run": True,
        "min_confidence": 0.0,
        "apply_labels": {},
        "archive_categories": [],
        "mark_read_categories": [],
    }
    kwargs[field] = [bad_cat]
    policy = ActionPolicy(**kwargs)

    with pytest.raises(ValueError, match="non-safe"):
        actions.apply_classifications([], [], policy)


def test_empty_batch_returns_empty_report():
    report = actions.apply_classifications(
        [],
        [],
        ActionPolicy(
            dry_run=True,
            min_confidence=0.7,
            apply_labels={},
            archive_categories=[],
            mark_read_categories=[],
        ),
    )
    assert report.total_threads == 0
    assert report.results == []
