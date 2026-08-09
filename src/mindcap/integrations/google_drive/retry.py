from __future__ import annotations

from dataclasses import dataclass

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRYABLE_403_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    reason: str


def classify_google_error(status_code: int, reason: str | None = None) -> RetryDecision:
    if status_code in RETRYABLE_STATUS_CODES:
        return RetryDecision(retryable=True, reason=f"http_{status_code}")
    if status_code == 403 and reason in RETRYABLE_403_REASONS:
        return RetryDecision(retryable=True, reason=reason)
    return RetryDecision(retryable=False, reason=reason or f"http_{status_code}")
