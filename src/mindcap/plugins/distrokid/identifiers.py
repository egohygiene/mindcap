from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from mindcap.core.errors import InvalidSourceError

_DISTROKID_HOSTS = frozenset({"distrokid.com", "www.distrokid.com"})
_LIBRARY_PATH = "/mymusic/"
_RELEASE_PATH = "/dashboard/album/"


def _normalize_uuid(value: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise InvalidSourceError(f'Invalid DistroKid album UUID: "{value}"') from error


def canonicalize_distrokid_identifier(value: str) -> tuple[str, str | None]:
    raw = value.strip()
    if not raw:
        raise InvalidSourceError("DistroKid source cannot be empty.")

    try:
        album_uuid = _normalize_uuid(raw)
    except InvalidSourceError:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"}:
            raise InvalidSourceError(
                f'Unsupported DistroKid source identifier: "{value}"'
            ) from None
        if parsed.scheme != "https":
            raise InvalidSourceError("DistroKid sources must use HTTPS.") from None

        hostname = (parsed.hostname or "").lower()
        if hostname not in _DISTROKID_HOSTS:
            raise InvalidSourceError(
                f'Unsupported DistroKid host: "{hostname or parsed.netloc}"'
            ) from None

        normalized_path = parsed.path.rstrip("/") + "/"
        if normalized_path == _LIBRARY_PATH:
            return "account-library", "https://distrokid.com/mymusic/"

        if normalized_path != _RELEASE_PATH:
            raise InvalidSourceError(
                'Unsupported DistroKid path. Expected "/mymusic/" or '
                '"/dashboard/album/?albumuuid=<uuid>".'
            ) from None

        query = parse_qs(parsed.query)
        album_values: list[str] = []
        for key, values in query.items():
            if key.lower() != "albumuuid":
                continue
            album_values.extend(item for item in values if item.strip())
        if len(album_values) > 1:
            raise InvalidSourceError(
                'Ambiguous DistroKid release URL contains multiple "albumuuid" values.'
            ) from None
        if not album_values:
            raise InvalidSourceError(
                'DistroKid release URL is missing required "albumuuid" query parameter.'
            ) from None

        album_uuid = _normalize_uuid(album_values[0])
        return (
            album_uuid,
            f"https://distrokid.com/dashboard/album/?albumuuid={album_uuid}",
        )

    return (
        album_uuid,
        f"https://distrokid.com/dashboard/album/?albumuuid={album_uuid}",
    )


def supports_distrokid_source(value: str) -> bool:
    try:
        canonicalize_distrokid_identifier(value)
    except InvalidSourceError:
        return False
    return True
