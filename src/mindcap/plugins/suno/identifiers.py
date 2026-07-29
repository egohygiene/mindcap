from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from mindcap.core.errors import InvalidSourceError

_SUNO_HOSTS = frozenset({"suno.com", "www.suno.com"})
_CLIP_PATH_PREFIXES = ("/song/", "/clip/", "/songs/")
_WORKSPACE_KEYS = ("wid", "workspace_id", "project_id")


def _parse_uuid(value: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise InvalidSourceError(f'Invalid Suno workspace identifier: "{value}"') from error


def canonicalize_suno_identifier(value: str) -> tuple[str, str | None]:
    raw = value.strip()
    if not raw:
        raise InvalidSourceError("Suno source cannot be empty.")

    try:
        workspace_id = _parse_uuid(raw)
    except InvalidSourceError:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"}:
            raise
        hostname = (parsed.hostname or "").lower()
        if hostname not in _SUNO_HOSTS:
            raise InvalidSourceError(f'Unsupported Suno host: "{hostname or parsed.netloc}"')
        if any(parsed.path.startswith(prefix) for prefix in _CLIP_PATH_PREFIXES):
            raise InvalidSourceError("Clip URLs are not valid Suno workspace sources.")

        query = parse_qs(parsed.query)
        for key in _WORKSPACE_KEYS:
            values = [item for item in query.get(key, []) if item]
            if len(values) > 1:
                raise InvalidSourceError(
                    f'Ambiguous Suno workspace URL contains multiple "{key}" values.'
                )
            if values:
                workspace_id = _parse_uuid(values[0])
                return workspace_id, f"https://suno.com/create?wid={workspace_id}"

        parts = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/create":
            raise InvalidSourceError("Suno workspace URL is missing the wid query parameter.")
        for part in reversed(parts):
            try:
                workspace_id = _parse_uuid(part)
            except InvalidSourceError:
                continue
            if any(marker in parts for marker in ("workspace", "workspaces", "project", "projects")):
                return workspace_id, f"https://suno.com/create?wid={workspace_id}"
        raise InvalidSourceError(f'Could not derive a Suno workspace ID from "{value}".')

    return workspace_id, f"https://suno.com/create?wid={workspace_id}"


def supports_suno_source(value: str) -> bool:
    try:
        canonicalize_suno_identifier(value)
    except InvalidSourceError:
        return False
    return True
