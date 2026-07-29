from __future__ import annotations

import base64
import json
import os
import stat
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mindcap.config import ensure_private_directory, suno_auth_file
from mindcap.core.errors import AuthenticationRequiredError, InvalidSourceError


@dataclass(frozen=True)
class NormalizedCookieInput:
    clerk_client_cookie: str
    cookie_header: str
    device_id: str | None = None


@dataclass
class SunoAuthState:
    clerk_client_cookie: str | None = None
    cookie_header: str | None = None
    session_id: str | None = None
    jwt: str | None = None
    jwt_expires_at: str | None = None
    device_id: str | None = None
    updated_at: str | None = None


def _atomic_write(path: Path, content: str) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(content)
    with suppress(OSError):
        os.chmod(temporary, 0o600)
    temporary.replace(path)
    with suppress(OSError):
        os.chmod(path, 0o600)


def _strip_cookie_prefix(value: str) -> str:
    stripped = value.strip()
    if stripped[:7].lower() == "cookie:":
        return stripped[7:].strip()
    return stripped


def _extract_device_id(value: str) -> str | None:
    normalized = value.strip().replace("%22", '"').strip('"').strip("'").strip()
    if not normalized or ";" in normalized:
        return None
    return normalized


def _parse_cookie_header(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in _strip_cookie_prefix(value).split(";"):
        part = item.strip()
        if not part or "=" not in part:
            continue
        name, raw_value = part.split("=", 1)
        cookies[name.strip()] = raw_value.strip()
    return cookies


def normalize_cookie_input(value: str) -> NormalizedCookieInput:
    normalized = _strip_cookie_prefix(value)
    if not normalized:
        raise InvalidSourceError("Suno cookie input cannot be empty.")

    cookies = _parse_cookie_header(normalized)
    clerk_client_cookie = cookies.get("__client")
    if clerk_client_cookie:
        return NormalizedCookieInput(
            clerk_client_cookie=clerk_client_cookie,
            cookie_header=normalized,
            device_id=_extract_device_id(cookies.get("ajs_anonymous_id", "")),
        )

    if ";" in normalized or "=" in normalized:
        raise InvalidSourceError("Cookie input did not contain a __client field.")

    return NormalizedCookieInput(
        clerk_client_cookie=normalized,
        cookie_header=f"__client={normalized}",
        device_id=None,
    )


def load_suno_auth_state(*, required: bool = False) -> SunoAuthState | None:
    path = suno_auth_file()
    if not path.is_file():
        if required:
            raise AuthenticationRequiredError(
                "Run `mindcap auth suno --cookie-stdin` first."
            )
        return None
    state = SunoAuthState(**json.loads(path.read_text(encoding="utf-8")))
    if required and not state.clerk_client_cookie:
        raise AuthenticationRequiredError("Stored Suno auth state is incomplete.")
    return state


def save_suno_auth_state(state: SunoAuthState) -> Path:
    payload = asdict(state)
    _atomic_write(suno_auth_file(), json.dumps(payload, indent=2, sort_keys=True))
    return suno_auth_file()


def authenticate_suno_cookie_stdin(value: str) -> SunoAuthState:
    parsed = normalize_cookie_input(value)
    existing = load_suno_auth_state(required=False) or SunoAuthState()
    existing.clerk_client_cookie = parsed.clerk_client_cookie
    existing.cookie_header = parsed.cookie_header
    existing.device_id = parsed.device_id or existing.device_id or str(uuid.uuid4())
    existing.updated_at = datetime.now(UTC).isoformat()
    save_suno_auth_state(existing)
    return existing


def has_refreshable_clerk_state(state: SunoAuthState | None) -> bool:
    return bool(state and state.clerk_client_cookie)


def decode_jwt_payload(jwt: str) -> dict[str, object] | None:
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    padding = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[1] + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def jwt_expiration(state: SunoAuthState | None) -> datetime | None:
    if not state or not state.jwt:
        return None
    payload = decode_jwt_payload(state.jwt)
    if not payload:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


def jwt_state(
    state: SunoAuthState | None, *, now: datetime | None = None
) -> tuple[str, str]:
    expires_at = jwt_expiration(state)
    if expires_at is None:
        return "missing", "No JWT is currently stored."
    current = now or datetime.now(UTC)
    if expires_at <= current:
        return "expired", f"Expired at {expires_at.isoformat()}."
    if expires_at - current <= timedelta(minutes=30):
        return "stale", f"Expires soon at {expires_at.isoformat()}."
    return "active", f"Active until {expires_at.isoformat()}."


def private_permission_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    with suppress(OSError):
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            return f"too-open ({oct(mode)})"
        return f"private ({oct(mode)})"
    return "unavailable"


def redact_secret(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "<redacted>"
    return f"<redacted:{value[-4:]}>"


def redact_signed_url(url: str | None) -> str | None:
    if not url:
        return None
    prefix, _, _ = url.partition("?")
    return prefix
