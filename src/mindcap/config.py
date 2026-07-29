from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from platformdirs import user_data_path


def find_repository_root(start: Path | None = None) -> Path:
    """Find the nearest Git repository root without invoking Git."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def default_artifact_root() -> Path:
    configured = os.environ.get("MINDCAP_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return find_repository_root() / ".cache" / "mindcap"


def config_dir() -> Path:
    configured = os.environ.get("MINDCAP_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return user_data_path("mindcap", "Ego Hygiene")


def chatgpt_profile_dir() -> Path:
    configured = os.environ.get("MINDCAP_CHATGPT_PROFILE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return config_dir() / "browser" / "chatgpt"


def suno_auth_file() -> Path:
    configured = os.environ.get("MINDCAP_SUNO_AUTH_FILE")
    if configured:
        return Path(configured).expanduser().resolve()
    return config_dir() / "auth" / "suno.json"


def suno_api_origin() -> str:
    return os.environ.get(
        "MINDCAP_SUNO_API_ORIGIN", "https://studio-api-prod.suno.com"
    ).rstrip("/")


def ensure_private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    # Windows and some mounted filesystems do not expose POSIX permissions.
    with suppress(OSError):
        path.chmod(0o700)
    return path
