from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from mindcap.core.errors import CaptureFailedError
from mindcap.plugins.chrome_bookmarks.models import ChromeProfile, ChromeUserDataRoot

_SUPPORTED_CHANNELS = frozenset({"stable", "beta", "dev", "canary"})


def _normalize_channel(channel: str | None) -> str:
    normalized = (channel or "stable").strip().lower()
    if normalized not in _SUPPORTED_CHANNELS:
        supported = ", ".join(sorted(_SUPPORTED_CHANNELS))
        raise CaptureFailedError(
            f'Unsupported Chrome channel "{channel}". Expected one of: {supported}.'
        )
    return normalized


def expand_user_data_dir(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().resolve(strict=False)


def automatic_user_data_roots(
    *,
    channel: str = "stable",
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> list[ChromeUserDataRoot]:
    env = environ or os.environ
    target_channel = _normalize_channel(channel)
    platform_value = platform_name or sys.platform
    roots: list[ChromeUserDataRoot] = []

    if platform_value == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Google"
        candidates = {
            "stable": base / "Chrome",
            "beta": base / "Chrome Beta",
            "dev": base / "Chrome Dev",
            "canary": base / "Chrome Canary",
        }
    elif platform_value.startswith("win"):
        local_app_data = env.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        google = base / "Google"
        candidates = {
            "stable": google / "Chrome" / "User Data",
            "beta": google / "Chrome Beta" / "User Data",
            "dev": google / "Chrome Dev" / "User Data",
            "canary": google / "Chrome SxS" / "User Data",
        }
    else:
        config_home = env.get("CHROME_CONFIG_HOME") or env.get("XDG_CONFIG_HOME")
        base = (
            Path(config_home).expanduser() if config_home else Path.home() / ".config"
        )
        candidates = {
            "stable": base / "google-chrome",
            "beta": base / "google-chrome-beta",
            "dev": base / "google-chrome-unstable",
            "canary": base / "google-chrome-canary",
        }

    path = candidates[target_channel]
    roots.append(ChromeUserDataRoot(channel=target_channel, path=path))
    return roots


def _load_profile_names(user_data_dir: Path) -> dict[str, str]:
    local_state_path = user_data_dir / "Local State"
    try:
        payload = json.loads(local_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    profile_data = payload.get("profile")
    info_cache = (
        profile_data.get("info_cache") if isinstance(profile_data, dict) else None
    )
    if not isinstance(info_cache, dict):
        return {}
    names: dict[str, str] = {}
    for key, value in info_cache.items():
        if isinstance(key, str) and isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name.strip():
                names[key] = name.strip()
    return names


def discover_profiles(
    *,
    channel: str = "stable",
    user_data_dirs: Sequence[str] | None = None,
    requested_profiles: Sequence[str] | None = None,
) -> list[ChromeProfile]:
    roots: list[ChromeUserDataRoot]
    if user_data_dirs:
        custom_channel = _normalize_channel(channel)
        roots = [
            ChromeUserDataRoot(
                channel=custom_channel,
                path=expand_user_data_dir(value),
                source="custom",
            )
            for value in user_data_dirs
        ]
    else:
        roots = automatic_user_data_roots(channel=channel)

    requested = {name for name in (requested_profiles or []) if name.strip()}
    discovered: list[ChromeProfile] = []
    for root in roots:
        if not root.path.exists():
            if root.source == "custom":
                raise CaptureFailedError(
                    f'Chrome user-data directory does not exist: "{root.path}"'
                )
            continue
        if not root.path.is_dir() or not os.access(root.path, os.R_OK):
            if root.source == "custom":
                raise CaptureFailedError(
                    f'Chrome user-data directory is not readable: "{root.path}"'
                )
            continue
        profile_names = _load_profile_names(root.path)
        for child in sorted(root.path.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            if requested and child.name not in requested:
                continue
            bookmarks_path = child / "Bookmarks"
            if not bookmarks_path.is_file() or not os.access(bookmarks_path, os.R_OK):
                continue
            root_token = hashlib.sha256(
                str(root.path.resolve(strict=False)).encode("utf-8")
            ).hexdigest()[:8]
            discovered.append(
                ChromeProfile(
                    channel=root.channel,
                    user_data_dir=root.path,
                    profile_dir=child,
                    profile_directory_name=child.name,
                    profile_id=f"{root.channel}:{root_token}:{child.name}",
                    profile_name=profile_names.get(child.name),
                    bookmarks_path=bookmarks_path,
                )
            )
    if requested:
        missing = sorted(
            requested - {profile.profile_directory_name for profile in discovered}
        )
        if missing:
            missing_names = ", ".join(missing)
            raise CaptureFailedError(
                f"Requested Chrome profiles were not found: {missing_names}"
            )
    return discovered
