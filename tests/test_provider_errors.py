"""Tests for the provider-exception classifier.

These cover the patterns the real SDKs raise in practice:
  * ``anthropic.BadRequestError`` for out-of-credits / balance-too-low
  * ``openai.AuthenticationError`` (401)
  * ``google.api_core.exceptions.ResourceExhausted`` (429 / RESOURCE_EXHAUSTED)
  * ``openai.BadRequestError`` for "model does not exist" (404-flavored)
  * A generic network/unknown exception falls through to ``other``
"""

from __future__ import annotations

import pytest

from sift.provider_errors import classify_provider_exception


class _FakeExc(Exception):
    """Exception factory that mimics the (message, status_code, body) shape
    the SDKs generally expose. Close enough for classification tests."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict | None = None,
    ):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if body is not None:
            self.body = body


@pytest.mark.parametrize(
    "message, expected_type",
    [
        ("Your credit balance is too low to access the Anthropic API", "balance"),
        ("You exceeded your current quota, please check your plan", "balance"),
        ("insufficient_quota", "balance"),
    ],
)
def test_balance_patterns(message: str, expected_type: str) -> None:
    result = classify_provider_exception(_FakeExc(message, status_code=400))
    assert result.error_type == expected_type
    assert "credits" in result.message or "top up" in result.message


def test_auth_401() -> None:
    result = classify_provider_exception(
        _FakeExc("Invalid API key provided", status_code=401)
    )
    assert result.error_type == "auth"
    assert result.status_code == 401


def test_auth_by_message() -> None:
    # Some SDKs raise without a clean status code but mention the key.
    result = classify_provider_exception(_FakeExc("The api key is missing"))
    assert result.error_type == "auth"


def test_rate_limit_by_status() -> None:
    result = classify_provider_exception(_FakeExc("slow down", status_code=429))
    assert result.error_type == "rate_limit"


def test_rate_limit_google_resource_exhausted() -> None:
    # google-genai raises ResourceExhausted; we match on class name.
    class ResourceExhausted(Exception):
        pass

    result = classify_provider_exception(ResourceExhausted("quota exceeded"))
    assert result.error_type == "rate_limit"


def test_bad_request_passthrough() -> None:
    result = classify_provider_exception(
        _FakeExc("model_not_found: claude-2", status_code=404)
    )
    assert result.error_type == "bad_request"
    assert result.status_code == 404


def test_other_falls_through() -> None:
    result = classify_provider_exception(Exception("network blip"))
    assert result.error_type == "other"
    assert result.status_code == 502


def test_balance_beats_auth_when_both_hint_present() -> None:
    # Anthropic's 400 for "credit balance is too low" can share vocabulary
    # with auth errors — balance should win because it has a concrete
    # remediation (top up or switch providers).
    result = classify_provider_exception(
        _FakeExc("Your credit balance is too low", status_code=400)
    )
    assert result.error_type == "balance"
