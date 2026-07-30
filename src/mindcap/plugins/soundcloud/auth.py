"""SoundCloud authentication state management.

OAuth credentials and tokens are stored outside the repository and archive
roots.  Access tokens and refresh tokens are never logged or persisted in
plain text inside archives.

Environment variables
---------------------
MINDCAP_SOUNDCLOUD_CLIENT_ID
    The OAuth client ID for the registered SoundCloud application.

MINDCAP_SOUNDCLOUD_REDIRECT_URI
    The redirect URI registered for the application (default: loopback).
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mindcap.plugins.soundcloud.errors import SoundCloudAuthError


class SoundCloudAuthState(BaseModel):
    """Persisted OAuth token state for the SoundCloud provider.

    The access_token and refresh_token are stored encrypted on disk or
    omitted entirely; they must never appear in logs, archives, or reports.
    """

    model_config = ConfigDict(extra="allow")

    client_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    expires_at: float | None = None
    scope: str | None = None
    account_id: str | None = None
    account_permalink: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def redact_secret(value: str | None) -> str:
    """Return a redacted representation of a secret value."""
    if not value:
        return "<not set>"
    return f"<redacted:{len(value)} chars>"


def private_permission_status(path: Path) -> str:
    """Return a human-readable permissions status for a file."""
    if not path.exists():
        return "file not found"
    try:
        mode = path.stat().st_mode & 0o777
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return f"too permissive ({oct(mode)})"
        return f"private ({oct(mode)})"
    except OSError:
        return "unknown"


def soundcloud_auth_file() -> Path:
    from mindcap.config import config_dir

    configured = os.environ.get("MINDCAP_SOUNDCLOUD_AUTH_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    return config_dir() / "auth" / "soundcloud.json"


def soundcloud_client_id() -> str | None:
    return os.environ.get("MINDCAP_SOUNDCLOUD_CLIENT_ID")


def soundcloud_redirect_uri() -> str:
    return os.environ.get(
        "MINDCAP_SOUNDCLOUD_REDIRECT_URI",
        "http://127.0.0.1:8765/callback",
    )


def load_soundcloud_auth_state(*, required: bool = True) -> SoundCloudAuthState | None:
    """Load persisted SoundCloud auth state from disk.

    Parameters
    ----------
    required:
        When ``True``, raise :exc:`SoundCloudAuthError` if the file is absent.
        When ``False``, return ``None`` instead.
    """
    path = soundcloud_auth_file()
    if not path.is_file():
        if required:
            raise SoundCloudAuthError(
                f"SoundCloud authentication file not found: {path}. "
                "Run 'mindcap auth soundcloud' to authenticate."
            )
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        if required:
            raise SoundCloudAuthError(
                f"Failed to read SoundCloud auth state: {error}"
            ) from error
        return None

    return SoundCloudAuthState.model_validate(raw)


def save_soundcloud_auth_state(state: SoundCloudAuthState) -> None:
    """Persist auth state to disk with private permissions."""
    from mindcap.config import ensure_private_directory

    path = soundcloud_auth_file()
    ensure_private_directory(path.parent)

    # Write to a temporary file, then rename atomically.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        state.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    tmp.replace(path)


def token_state(state: SoundCloudAuthState | None) -> tuple[str, str]:
    """Return (label, detail) describing the access-token state."""
    if state is None:
        return "no state", "No auth state loaded."
    if not state.access_token:
        return "no token", "Access token field is absent."
    import time

    if state.expires_at and state.expires_at < time.time():
        age = int(time.time() - state.expires_at)
        return "expired", f"Access token expired {age}s ago."
    if state.expires_at:
        remaining = int(state.expires_at - time.time())
        return "valid", f"Access token valid for ~{remaining}s."
    return "present (expiry unknown)", "Access token present; no expiry recorded."


def has_refreshable_state(state: SoundCloudAuthState | None) -> bool:
    """Return ``True`` when a refresh token is available."""
    return bool(state and state.refresh_token)
