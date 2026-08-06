"""Tests for SoundCloud source identifier canonicalization."""

from __future__ import annotations

import pytest

from mindcap.core.errors import InvalidSourceError
from mindcap.plugins.soundcloud.identifiers import (
    canonicalize_soundcloud_identifier,
    source_scope,
    supports_soundcloud_source,
)

# ---------------------------------------------------------------------------
# Account sources
# ---------------------------------------------------------------------------


def test_me_identifier_canonicalized() -> None:
    identifier, url = canonicalize_soundcloud_identifier("me")
    assert identifier == "soundcloud-account-me"
    assert url is None


def test_me_identifier_case_insensitive() -> None:
    identifier, _url = canonicalize_soundcloud_identifier("ME")
    assert identifier == "soundcloud-account-me"


def test_account_url_canonicalized() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name"
    )
    assert identifier == "soundcloud-account-artist-name"
    assert url == "https://soundcloud.com/artist-name"


def test_account_url_with_www_prefix() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://www.soundcloud.com/artist-name"
    )
    assert identifier == "soundcloud-account-artist-name"
    assert url == "https://soundcloud.com/artist-name"


def test_account_url_with_mobile_prefix() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://m.soundcloud.com/artist-name"
    )
    assert identifier == "soundcloud-account-artist-name"
    assert url == "https://soundcloud.com/artist-name"


def test_account_url_tracking_params_stripped() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name?utm_source=twitter&utm_medium=social"
    )
    assert identifier == "soundcloud-account-artist-name"
    assert url == "https://soundcloud.com/artist-name"
    assert url is not None
    assert "utm_source" not in url


def test_account_url_fragment_stripped() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name#section"
    )
    assert identifier == "soundcloud-account-artist-name"
    assert url == "https://soundcloud.com/artist-name"


# ---------------------------------------------------------------------------
# Track sources
# ---------------------------------------------------------------------------


def test_track_url_canonicalized() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name/my-track-title"
    )
    assert identifier == "soundcloud-track-artist-name/my-track-title"
    assert url == "https://soundcloud.com/artist-name/my-track-title"


def test_track_url_with_tracking_params_stripped() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name/my-track?si=abc123&ref=clipboard"
    )
    assert identifier == "soundcloud-track-artist-name/my-track"
    assert url == "https://soundcloud.com/artist-name/my-track"


def test_track_url_secret_token_not_in_canonical_url() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist/private-track?secret_token=s-ABC123"
    )
    assert identifier == "soundcloud-track-artist/private-track"
    assert url is not None
    assert "secret_token" not in url
    assert "s-ABC123" not in url


# ---------------------------------------------------------------------------
# Playlist sources
# ---------------------------------------------------------------------------


def test_playlist_url_canonicalized() -> None:
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name/sets/my-playlist"
    )
    assert identifier == "soundcloud-playlist-artist-name/sets/my-playlist"
    assert url == "https://soundcloud.com/artist-name/sets/my-playlist"


def test_album_url_canonicalized() -> None:
    # Albums are represented as sets with is_album=true in the API.
    identifier, url = canonicalize_soundcloud_identifier(
        "https://soundcloud.com/artist-name/sets/my-album-2024"
    )
    assert identifier == "soundcloud-playlist-artist-name/sets/my-album-2024"
    assert url == "https://soundcloud.com/artist-name/sets/my-album-2024"


# ---------------------------------------------------------------------------
# Scope detection
# ---------------------------------------------------------------------------


def test_scope_account() -> None:
    assert source_scope("soundcloud-account-artist") == "account"


def test_scope_track() -> None:
    assert source_scope("soundcloud-track-artist/track") == "track"


def test_scope_playlist() -> None:
    assert source_scope("soundcloud-playlist-artist/sets/pl") == "playlist"


def test_scope_unknown_raises() -> None:
    with pytest.raises(InvalidSourceError):
        source_scope("unknown-identifier")


# ---------------------------------------------------------------------------
# supports_soundcloud_source
# ---------------------------------------------------------------------------


def test_supports_me() -> None:
    assert supports_soundcloud_source("me") is True


def test_supports_valid_track_url() -> None:
    assert supports_soundcloud_source("https://soundcloud.com/artist/track") is True


def test_does_not_support_bare_uuid() -> None:
    # A bare UUID is not a recognized SoundCloud source.
    assert supports_soundcloud_source("8f8fd77f-c5bf-467a-8cb5-558fdbf86386") is False


def test_does_not_support_empty() -> None:
    assert supports_soundcloud_source("") is False


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_empty_value_raises() -> None:
    with pytest.raises(InvalidSourceError, match="cannot be empty"):
        canonicalize_soundcloud_identifier("")


def test_unsupported_host_raises() -> None:
    with pytest.raises(InvalidSourceError, match="Unsupported SoundCloud host"):
        canonicalize_soundcloud_identifier("https://example.com/artist/track")


def test_http_scheme_raises() -> None:
    with pytest.raises(InvalidSourceError, match="must use HTTPS"):
        canonicalize_soundcloud_identifier("http://soundcloud.com/artist/track")


def test_bare_non_url_raises() -> None:
    with pytest.raises(InvalidSourceError):
        canonicalize_soundcloud_identifier("not-a-url-and-not-me")


def test_reserved_path_raises() -> None:
    with pytest.raises(InvalidSourceError, match="reserved system page"):
        canonicalize_soundcloud_identifier("https://soundcloud.com/explore")


def test_no_path_segment_raises() -> None:
    with pytest.raises(InvalidSourceError, match="no path segment"):
        canonicalize_soundcloud_identifier("https://soundcloud.com/")


def test_track_reserved_sub_path_raises() -> None:
    with pytest.raises(InvalidSourceError, match="not a track URL"):
        canonicalize_soundcloud_identifier("https://soundcloud.com/artist/sets")
