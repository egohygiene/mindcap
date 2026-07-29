from __future__ import annotations

from mindcap.core.errors import MindcapError


class SunoError(MindcapError):
    """Base error for expected Suno failures."""


class SunoApiError(SunoError):
    """Raised when the Suno API returns an unexpected response."""
