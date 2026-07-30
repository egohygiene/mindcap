from __future__ import annotations

import pytest

from mindcap.core.errors import InvalidSourceError
from mindcap.plugins.distrokid.identifiers import canonicalize_distrokid_identifier

ALBUM_UUID = "642baa93-568f-47a7-9955-8e4426a9d1d0"


def test_library_url_is_canonicalized() -> None:
    identifier, url = canonicalize_distrokid_identifier(
        "https://www.distrokid.com/mymusic?utm_source=ignored"
    )
    assert identifier == "account-library"
    assert url == "https://distrokid.com/mymusic/"


def test_album_url_is_canonicalized_and_uuid_normalized() -> None:
    identifier, url = canonicalize_distrokid_identifier(
        "https://distrokid.com/dashboard/album/?ALBUMUUID=642BAA93-568F-47A7-99558E4426A9D1D0&foo=bar"
    )
    assert identifier == ALBUM_UUID
    assert url == f"https://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}"


def test_bare_album_uuid_is_canonicalized() -> None:
    identifier, url = canonicalize_distrokid_identifier(ALBUM_UUID.upper())
    assert identifier == ALBUM_UUID
    assert url == f"https://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}"


def test_duplicate_albumuuid_query_values_are_rejected() -> None:
    with pytest.raises(InvalidSourceError, match='multiple "albumuuid"'):
        canonicalize_distrokid_identifier(
            "https://distrokid.com/dashboard/album/?albumuuid="
            f"{ALBUM_UUID}&albumuuid={ALBUM_UUID}"
        )


def test_missing_albumuuid_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="missing required"):
        canonicalize_distrokid_identifier("https://distrokid.com/dashboard/album/")


def test_unsupported_host_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="Unsupported DistroKid host"):
        canonicalize_distrokid_identifier("https://example.com/mymusic/")


def test_unsupported_path_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="Unsupported DistroKid path"):
        canonicalize_distrokid_identifier("https://distrokid.com/dashboard/")


def test_unsafe_scheme_is_rejected() -> None:
    with pytest.raises(InvalidSourceError, match="must use HTTPS"):
        canonicalize_distrokid_identifier(
            f"http://distrokid.com/dashboard/album/?albumuuid={ALBUM_UUID}"
        )
