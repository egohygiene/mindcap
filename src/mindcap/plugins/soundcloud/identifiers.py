"""SoundCloud source identifier canonicalization.

Supported input forms
---------------------
Account capture:
    ``"me"``
    ``"https://soundcloud.com/<permalink>"``

Individual track:
    ``"https://soundcloud.com/<account>/<track>"``

Playlist / album / set:
    ``"https://soundcloud.com/<account>/sets/<playlist>"``

Numeric or string provider track IDs are not supported directly as top-level
identifiers because they are ambiguous without knowing whether the ID refers to
a track, playlist, or user.  Pass a full URL instead.

Secret tokens (``?secret_token=...``) are preserved inside the returned
canonical identifier's ``safe_metadata`` but are never placed in filenames,
logs, or ordinary reports.
"""

from __future__ import annotations

from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit

from mindcap.core.errors import InvalidSourceError

_SOUNDCLOUD_HOSTS = frozenset(
    {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
)

# Path segments that are reserved system pages and are not user permalinks.
_RESERVED_PATHS = frozenset(
    {
        "you",
        "tags",
        "pages",
        "people",
        "search",
        "upload",
        "jobs",
        "login",
        "oauth",
        "legal",
        "privacy",
        "terms",
        "settings",
        "mobile",
        "download",
        "feed",
        "charts",
        "explore",
        "pro",
        "go",
        "developer",
    }
)

# Tracking/noise query parameters to strip when building canonical URLs.
_STRIP_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "si",
        "share_redirect",
    }
)

# The query parameter that carries private-share tokens.
_SECRET_TOKEN_PARAM = "secret_token"

# Track sub-path segments that indicate the URL is not a track permalink.
_TRACK_RESERVED = frozenset(
    {
        "sets",
        "reposts",
        "albums",
        "tracks",
        "likes",
        "following",
        "followers",
        "spotlight",
    }
)


def _validate_host(parsed: SplitResult) -> None:
    hostname = (parsed.hostname or "").lower()
    if hostname not in _SOUNDCLOUD_HOSTS:
        raise InvalidSourceError(
            f'Unsupported SoundCloud host: "{hostname or parsed.netloc}"'
        )


def _strip_query(parsed: SplitResult) -> tuple[str | None, dict[str, str]]:
    """Return (sanitized_query_string, secret_meta).

    The secret_token, if present, is removed from the public query string and
    returned separately so that it never enters filenames or logs.
    """
    query = parse_qs(parsed.query, keep_blank_values=False)
    secret_meta: dict[str, str] = {}

    secret_values = query.pop(_SECRET_TOKEN_PARAM, [])
    if secret_values:
        # Record presence without exposing the value.
        secret_meta[_SECRET_TOKEN_PARAM] = "<redacted>"

    # Remove known tracking noise.
    for param in _STRIP_PARAMS:
        query.pop(param, None)

    cleaned = urlencode({k: v[0] for k, v in query.items() if v}, safe="")
    return (cleaned or None), secret_meta


def _path_parts(parsed: SplitResult) -> list[str]:
    return [p for p in parsed.path.split("/") if p]


def canonicalize_soundcloud_identifier(
    value: str,
) -> tuple[str, str | None]:
    """Return ``(canonical_identifier, canonical_url)`` for *value*.

    Canonical identifier format:

    - Account: ``"soundcloud-account-<permalink>"``
    - Track: ``"soundcloud-track-<account>/<track>"``
    - Playlist: ``"soundcloud-playlist-<account>/sets/<playlist>"``

    The canonical URL is the normalized public SoundCloud URL with tracking
    parameters removed and fragments dropped.  Secret tokens are removed from
    the URL but noted separately; they never enter filenames or logs.

    Raises
    ------
    InvalidSourceError
        For empty, ambiguous, or unsupported inputs.
    """
    raw = value.strip()
    if not raw:
        raise InvalidSourceError("SoundCloud source cannot be empty.")

    # --- Special "me" shorthand (account capture of the authenticated user) ---
    if raw.lower() == "me":
        return "soundcloud-account-me", None

    # --- URL-based sources ---
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        # Not a URL — treat as unsupported bare identifier.
        raise InvalidSourceError(
            f'Unsupported SoundCloud source identifier: "{value}". '
            'Pass a full soundcloud.com URL or the special value "me".'
        )

    if parsed.scheme != "https":
        raise InvalidSourceError("SoundCloud sources must use HTTPS.") from None

    _validate_host(parsed)

    # Drop fragment; sanitize query.
    _, _secret_meta = _strip_query(parsed)

    parts = _path_parts(parsed)

    # soundcloud.com (no path) — not a useful capture target.
    if not parts:
        raise InvalidSourceError(
            "SoundCloud URL has no path segment. "
            "Provide an account, track, or playlist URL."
        )

    account_permalink = parts[0].lower()
    if account_permalink in _RESERVED_PATHS:
        raise InvalidSourceError(
            f'SoundCloud path "{account_permalink}" is a reserved system page, '
            "not a user account permalink."
        )

    # --- Account URL: soundcloud.com/<account> ---
    if len(parts) == 1:
        canonical_url = f"https://soundcloud.com/{account_permalink}"
        return f"soundcloud-account-{account_permalink}", canonical_url

    # --- Playlist URL: soundcloud.com/<account>/sets/<set> ---
    if len(parts) >= 3 and parts[1].lower() == "sets":
        set_permalink = parts[2].lower()
        canonical_url = (
            f"https://soundcloud.com/{account_permalink}/sets/{set_permalink}"
        )
        return (
            f"soundcloud-playlist-{account_permalink}/sets/{set_permalink}",
            canonical_url,
        )

    # --- Track URL: soundcloud.com/<account>/<track> ---
    if len(parts) == 2:
        track_permalink = parts[1].lower()
        # Guard against sub-pages that are not tracks.
        if track_permalink in _TRACK_RESERVED:
            raise InvalidSourceError(
                f'SoundCloud path "/{account_permalink}/{track_permalink}" '
                "is not a track URL."
            )
        canonical_url = f"https://soundcloud.com/{account_permalink}/{track_permalink}"
        return (
            f"soundcloud-track-{account_permalink}/{track_permalink}",
            canonical_url,
        )

    raise InvalidSourceError(
        f'Could not determine the SoundCloud source type from "{value}". '
        "Expected an account, track, or playlist URL."
    )


def source_scope(canonical_identifier: str) -> str:
    """Return ``"account"``, ``"track"``, or ``"playlist"`` for a canonical ID."""
    if canonical_identifier.startswith("soundcloud-account-"):
        return "account"
    if canonical_identifier.startswith("soundcloud-track-"):
        return "track"
    if canonical_identifier.startswith("soundcloud-playlist-"):
        return "playlist"
    raise InvalidSourceError(
        f'Cannot determine scope from SoundCloud identifier: "{canonical_identifier}"'
    )


def supports_soundcloud_source(value: str) -> bool:
    """Return ``True`` when *value* is a recognised SoundCloud source."""
    try:
        canonicalize_soundcloud_identifier(value)
    except InvalidSourceError:
        return False
    return True
