from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from mindcap.core.errors import InvalidSourceError

CHATGPT_IDENTIFIER = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# File-path extensions treated as local JSON conversation sources.
_JSON_SUFFIXES = frozenset({".json"})
# Extensions treated as export ZIP archives.
_ZIP_SUFFIXES = frozenset({".zip"})


def canonicalize_chatgpt_identifier(value: str) -> tuple[str, str | None]:
    candidate = value.strip()
    if CHATGPT_IDENTIFIER.fullmatch(candidate):
        identifier = candidate.lower()
        return identifier, f"https://chatgpt.com/c/{identifier}"

    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.hostname in {
        "chatgpt.com",
        "www.chatgpt.com",
    }:
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) != 2 or segments[0] != "c":
            raise InvalidSourceError(
                "Expected a private ChatGPT URL shaped as "
                '"https://chatgpt.com/c/<conversation-id>".'
            )
        identifier = segments[1].lower()
        if not CHATGPT_IDENTIFIER.fullmatch(identifier):
            raise InvalidSourceError(
                f'Invalid ChatGPT conversation identifier: "{segments[1]}"'
            )
        return identifier, f"https://chatgpt.com/c/{identifier}"

    # --- File paths ---
    path = Path(candidate).expanduser()
    if path.suffix.lower() in _JSON_SUFFIXES and (path.exists() or path.is_absolute()):
        return _canonicalize_json_path(path)

    if path.suffix.lower() in _ZIP_SUFFIXES and (path.exists() or path.is_absolute()):
        return _canonicalize_export_path(path)

    if path.is_dir():
        return _canonicalize_export_path(path)

    raise InvalidSourceError(f'Not a supported ChatGPT source: "{value}"')


def _canonicalize_json_path(path: Path) -> tuple[str, None]:
    """Try to derive a conversation ID from a JSON file, falling back to a path hash."""
    try:
        payload = json.loads(path.read_bytes())
        conv_id = _extract_conversation_id(payload)
        if conv_id:
            return conv_id.lower(), None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return _path_import_id(path), None


def _canonicalize_export_path(path: Path) -> tuple[str, None]:
    """Return a stable synthetic import ID for a ZIP or directory export source."""
    return _path_import_id(path), None


def _path_import_id(path: Path) -> str:
    """Return a deterministic import ID derived from the absolute path string."""
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
    return f"import-{digest}"


def _extract_conversation_id(payload: object) -> str | None:
    """Return a ChatGPT conversation UUID from a parsed JSON payload, or None."""
    if isinstance(payload, dict):
        for key in ("id", "conversation_id"):
            value = payload.get(key)
            if isinstance(value, str) and CHATGPT_IDENTIFIER.fullmatch(value.strip()):
                return value.strip()
    if isinstance(payload, list) and len(payload) == 1:
        return _extract_conversation_id(payload[0])
    return None


def supports_chatgpt_source(value: str) -> bool:
    try:
        canonicalize_chatgpt_identifier(value)
    except InvalidSourceError:
        return False
    return True
