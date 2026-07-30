from __future__ import annotations

from mindcap.core.errors import MindcapError


class SoundCloudError(MindcapError):
    """Base error for expected SoundCloud failures."""


class SoundCloudApiError(SoundCloudError):
    """Raised when the SoundCloud API returns an unexpected response."""


class SoundCloudAuthError(SoundCloudError):
    """Raised when SoundCloud authentication is missing or invalid."""
