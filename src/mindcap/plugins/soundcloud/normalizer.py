"""Normalize raw SoundCloud provider responses into stable Mindcap schemas.

Normalization rules
-------------------
- Raw provider objects are preserved intact in a ``provider_metadata`` field.
- Unknown fields survive: models use ``extra="allow"``.
- Null fields are not converted to empty values unless the schema explicitly
  documents the transformation.
- Tag strings and tag lists are kept separate; tokenization is deferred.
- Secret tokens are never written to normalized output.
"""

from __future__ import annotations

import re
from typing import Any

from mindcap.core.models import CaptureEnvelope

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_unit(envelope: CaptureEnvelope, category: str) -> dict[str, Any] | None:
    for unit in envelope.response_units:
        if unit.endpoint_category == category:
            try:
                import json

                payload = json.loads(unit.body)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
    return None


def _load_units(envelope: CaptureEnvelope, category: str) -> list[dict[str, Any]]:
    import json

    results: list[dict[str, Any]] = []
    for unit in envelope.response_units:
        if unit.endpoint_category == category:
            try:
                payload = json.loads(unit.body)
                if isinstance(payload, dict):
                    results.append(payload)
            except Exception:
                continue
    return results


def _safe_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_tag_list(raw: str | None) -> list[str]:
    """Parse SoundCloud's quoted-word tag_list string into individual tags."""
    if not raw:
        return []
    # SoundCloud uses a mix of quoted phrases ("hip hop") and bare words.
    tokens: list[str] = []
    for match in re.finditer(r'"([^"]+)"|(\S+)', raw):
        tokens.append(match.group(1) or match.group(2))
    return [t for t in tokens if t]


def _redact_url(url: str | None) -> str | None:
    """Strip secret_token from a URL before storing."""
    if not url:
        return None
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=False)
    query.pop("secret_token", None)
    cleaned = urlencode({k: v[0] for k, v in query.items() if v})
    return urlunsplit(parsed._replace(query=cleaned, fragment=""))


# ---------------------------------------------------------------------------
# Track normalization
# ---------------------------------------------------------------------------


def _normalize_track(track: dict[str, Any]) -> dict[str, Any]:
    user = track.get("user") or {}
    publisher_meta = track.get("publisher_metadata") or {}
    media = track.get("media") or {}

    raw_tag_list = track.get("tag_list")
    tag_list = _parse_tag_list(raw_tag_list) if isinstance(raw_tag_list, str) else []

    # Redact secret_token from artwork/stream/download URLs.
    artwork_url = _redact_url(_safe_str(track.get("artwork_url")))
    waveform_url = _redact_url(_safe_str(track.get("waveform_url")))

    # Media transcodings — preserve structured form, redact URLs.
    transcodings: list[dict[str, Any]] = []
    for t in media.get("transcodings") or []:
        if not isinstance(t, dict):
            continue
        entry: dict[str, Any] = {}
        for key in ("preset", "format", "quality", "duration", "snipped"):
            if key in t:
                entry[key] = t[key]
        raw_fmt = t.get("format") or {}
        if isinstance(raw_fmt, dict):
            entry["protocol"] = raw_fmt.get("protocol")
            entry["mime_type"] = raw_fmt.get("mime_type")
        # URL is omitted — it is signed and not safe to store.
        transcodings.append(entry)

    return {
        "provider": "soundcloud",
        "source_type": "track",
        "track_id": _safe_str(track.get("id")),
        "user_id": _safe_str(user.get("id") or track.get("user_id")),
        "permalink": _safe_str(track.get("permalink")),
        "permalink_url": _redact_url(_safe_str(track.get("permalink_url"))),
        "urn": _safe_str(track.get("urn")),
        "track_type": _safe_str(track.get("track_type")),
        "has_secret_token": bool(track.get("secret_token")),
        # Naming and catalog identity
        "title": _safe_str(track.get("title")),
        "description": _safe_str(track.get("description")),
        "caption": _safe_str(track.get("caption")),
        "label_name": _safe_str(
            track.get("label_name") or publisher_meta.get("publisher")
        ),
        "isrc": _safe_str(publisher_meta.get("isrc") or track.get("isrc")),
        "release": _safe_str(track.get("release")),
        "release_date": _safe_str(track.get("release_date")),
        "original_release_date": _safe_str(track.get("original_release_date")),
        # Classification
        "genre": _safe_str(track.get("genre")),
        "tag_list_raw": raw_tag_list if isinstance(raw_tag_list, str) else None,
        "tags": tag_list,
        "license": _safe_str(track.get("license")),
        "bpm": track.get("bpm"),
        "key_signature": _safe_str(track.get("key_signature")),
        # Timing and technical
        "duration_ms": track.get("duration"),
        "full_duration_ms": track.get("full_duration"),
        "created_at": _safe_str(track.get("created_at")),
        "last_modified": _safe_str(track.get("last_modified")),
        "display_date": _safe_str(track.get("display_date")),
        "state": _safe_str(track.get("state")),
        # Visibility and permissions
        "sharing": _safe_str(track.get("sharing")),
        "access": _safe_str(track.get("access")),
        "streamable": track.get("streamable"),
        "downloadable": track.get("downloadable"),
        "has_downloads_left": track.get("has_downloads_left"),
        "embeddable_by": _safe_str(track.get("embeddable_by")),
        "commentable": track.get("commentable"),
        "policy": _safe_str(track.get("policy")),
        "monetization_model": _safe_str(track.get("monetization_model")),
        # Commerce
        "purchase_url": _redact_url(_safe_str(track.get("purchase_url"))),
        "purchase_title": _safe_str(track.get("purchase_title")),
        # Engagement
        "playback_count": track.get("playback_count"),
        "likes_count": track.get("likes_count"),
        "reposts_count": track.get("reposts_count"),
        "comment_count": track.get("comment_count"),
        "download_count": track.get("download_count"),
        # Assets (URLs sanitized)
        "artwork_url": artwork_url,
        "waveform_url": waveform_url,
        # Streaming representations (URLs omitted; metadata preserved)
        "stream_transcodings": transcodings,
        # Uploader
        "uploader": {
            "user_id": _safe_str(user.get("id")),
            "permalink": _safe_str(user.get("permalink")),
            "username": _safe_str(user.get("username")),
            "permalink_url": _redact_url(_safe_str(user.get("permalink_url"))),
            "avatar_url": _redact_url(_safe_str(user.get("avatar_url"))),
            "verified": user.get("verified"),
        },
        "publisher_metadata": {
            k: v for k, v in publisher_meta.items() if k not in {"secret_token"}
        },
        "provider_metadata": {
            k: v for k, v in track.items() if k not in {"secret_token", "secret_uri"}
        },
    }


# ---------------------------------------------------------------------------
# Account normalization
# ---------------------------------------------------------------------------


def _normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "soundcloud",
        "source_type": "account",
        "user_id": _safe_str(user.get("id")),
        "permalink": _safe_str(user.get("permalink")),
        "permalink_url": _redact_url(_safe_str(user.get("permalink_url"))),
        "urn": _safe_str(user.get("urn")),
        "username": _safe_str(user.get("username")),
        "display_name": _safe_str(
            user.get("full_name") or user.get("display_name") or user.get("username")
        ),
        "first_name": _safe_str(user.get("first_name")),
        "last_name": _safe_str(user.get("last_name")),
        "description": _safe_str(user.get("description")),
        "city": _safe_str(user.get("city")),
        "country_code": _safe_str(user.get("country_code")),
        "country": _safe_str(user.get("country")),
        "avatar_url": _redact_url(_safe_str(user.get("avatar_url"))),
        "website": _safe_str(user.get("website")),
        "website_title": _safe_str(user.get("website_title")),
        "verified": user.get("verified"),
        "plan": _safe_str(user.get("plan")),
        "track_count": user.get("track_count"),
        "playlist_count": user.get("playlist_count"),
        "followers_count": user.get("followers_count"),
        "followings_count": user.get("followings_count"),
        "likes_count": user.get("likes_count"),
        "reposts_count": user.get("reposts_count"),
        "comments_count": user.get("comments_count"),
        "created_at": _safe_str(user.get("created_at")),
        "last_modified": _safe_str(user.get("last_modified")),
        "provider_metadata": {
            k: v for k, v in user.items() if k not in {"secret_token"}
        },
    }


# ---------------------------------------------------------------------------
# Playlist normalization
# ---------------------------------------------------------------------------


def _normalize_playlist(playlist: dict[str, Any]) -> dict[str, Any]:
    user = playlist.get("user") or {}
    raw_tag_list = playlist.get("tag_list")
    tags = _parse_tag_list(raw_tag_list) if isinstance(raw_tag_list, str) else []
    # Preserve ordered track IDs without duplicating full track archives.
    track_ids: list[str] = []
    for t in playlist.get("tracks") or []:
        if isinstance(t, dict):
            tid = t.get("id")
            if tid is not None:
                track_ids.append(str(tid))

    return {
        "provider": "soundcloud",
        "source_type": "playlist",
        "playlist_id": _safe_str(playlist.get("id")),
        "permalink": _safe_str(playlist.get("permalink")),
        "permalink_url": _redact_url(_safe_str(playlist.get("permalink_url"))),
        "urn": _safe_str(playlist.get("urn")),
        "title": _safe_str(playlist.get("title")),
        "description": _safe_str(playlist.get("description")),
        "label_name": _safe_str(playlist.get("label_name")),
        "genre": _safe_str(playlist.get("genre")),
        "tag_list_raw": raw_tag_list if isinstance(raw_tag_list, str) else None,
        "tags": tags,
        "license": _safe_str(playlist.get("license")),
        "release_date": _safe_str(playlist.get("release_date")),
        "created_at": _safe_str(playlist.get("created_at")),
        "last_modified": _safe_str(playlist.get("last_modified")),
        "sharing": _safe_str(playlist.get("sharing")),
        "access": _safe_str(playlist.get("access")),
        "track_count": playlist.get("track_count"),
        "likes_count": playlist.get("likes_count"),
        "reposts_count": playlist.get("reposts_count"),
        "is_album": playlist.get("is_album"),
        "set_type": _safe_str(playlist.get("set_type")),
        "artwork_url": _redact_url(_safe_str(playlist.get("artwork_url"))),
        "has_secret_token": bool(playlist.get("secret_token")),
        "track_ids": track_ids,
        "uploader": {
            "user_id": _safe_str(user.get("id")),
            "permalink": _safe_str(user.get("permalink")),
            "username": _safe_str(user.get("username")),
        },
        "provider_metadata": {
            k: v
            for k, v in playlist.items()
            if k not in {"secret_token", "secret_uri", "tracks"}
        },
    }


# ---------------------------------------------------------------------------
# Top-level normalizer
# ---------------------------------------------------------------------------


def normalize_soundcloud(
    envelope: CaptureEnvelope, requested_identifier: str
) -> dict[str, Any]:
    """Normalize a SoundCloud capture envelope into a stable Mindcap schema."""
    # Determine what we captured.
    account_payload = _load_unit(envelope, "account")
    track_payload = _load_unit(envelope, "track")
    playlist_payload = _load_unit(envelope, "playlist")

    # Collect all track pages for account-level discovery.
    track_pages = _load_units(envelope, "track-collection-page")
    playlist_pages = _load_units(envelope, "playlist-collection-page")

    normalized_tracks: list[dict[str, Any]] = []
    for page in track_pages:
        for item in page.get("collection") or []:
            if isinstance(item, dict):
                normalized_tracks.append(_normalize_track(item))

    normalized_playlists: list[dict[str, Any]] = []
    for page in playlist_pages:
        for item in page.get("collection") or []:
            if isinstance(item, dict):
                normalized_playlists.append(_normalize_playlist(item))

    base: dict[str, Any] = {
        "schema": "mindcap.soundcloud/v0.1",
        "provider": "soundcloud",
        "source_id": requested_identifier,
        "canonical_url": envelope.canonical_url,
        "captured_at": envelope.captured_at.isoformat(),
        "strategy": envelope.strategy,
        "warnings": list(envelope.warnings),
        "raw_response_unit_count": len(envelope.response_units),
    }

    if track_payload is not None:
        base["source_type"] = "track"
        base["track"] = _normalize_track(track_payload)
    elif playlist_payload is not None:
        base["source_type"] = "playlist"
        base["playlist"] = _normalize_playlist(playlist_payload)
    elif account_payload is not None:
        base["source_type"] = "account"
        base["account"] = _normalize_user(account_payload)
        base["tracks"] = normalized_tracks
        base["playlists"] = normalized_playlists
    else:
        base["source_type"] = "unknown"

    return base
