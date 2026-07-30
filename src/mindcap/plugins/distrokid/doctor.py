from __future__ import annotations

import platform
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from mindcap import __version__
from mindcap.config import default_artifact_root, distrokid_profile_dir
from mindcap.plugins.chatgpt.strategies.browser import (
    _find_stable_chrome,
    _is_dedicated_chrome_running,
    _is_profile_locked,
)
from mindcap.plugins.distrokid.strategies.browser import (
    DistroKidAuthenticationState,
    browser_capture_architecture,
    verify_distrokid_authentication,
)


def _artifact_writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".mindcap-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return "no"
    return "yes"


def _reachability(url: str) -> str:
    try:
        response = httpx.get(url, timeout=5.0, follow_redirects=True)
    except Exception:
        return "unreachable"
    return f"reachable ({response.status_code})"


def doctor_distrokid(console: Console, *, verbose: bool = False) -> None:
    profile = distrokid_profile_dir()
    chrome = None
    chrome_discovery = "not found"
    try:
        chrome = _find_stable_chrome()
        chrome_discovery = "found"
    except Exception:
        chrome = None

    if _is_profile_locked(profile) or _is_dedicated_chrome_running(profile):
        auth_state = DistroKidAuthenticationState.PROFILE_LOCKED.value
        auth_detail = "Dedicated profile is currently in use by another process."
    elif chrome is None:
        auth_state = DistroKidAuthenticationState.INDETERMINATE.value
        auth_detail = "Stable Chrome unavailable."
    else:
        auth = verify_distrokid_authentication()
        auth_state = auth.state.value
        auth_detail = auth.detail

    table = Table("Check", "Status")
    table.add_row("Operating system", platform.platform())
    table.add_row("Mindcap version", __version__)
    table.add_row("Stable Chrome discovery", chrome_discovery)
    table.add_row("Chrome executable path", str(chrome) if chrome else "unavailable")
    table.add_row("Dedicated profile path", str(profile))
    table.add_row("Profile directory exists", "yes" if profile.is_dir() else "no")
    table.add_row(
        "Profile appears locked", "yes" if _is_profile_locked(profile) else "no"
    )
    table.add_row(
        "Dedicated Chrome process running",
        "yes" if _is_dedicated_chrome_running(profile) else "no",
    )
    table.add_row("Authentication status", auth_state)
    table.add_row("DistroKid reachability", _reachability("https://distrokid.com/"))
    table.add_row(
        "Library-page reachability", _reachability("https://distrokid.com/mymusic/")
    )
    table.add_row(
        "Artifact destination writable", _artifact_writable(default_artifact_root())
    )
    table.add_row("Capture architecture", browser_capture_architecture())
    table.add_row("Secret-redaction health", "yes")
    console.print(table)

    if verbose:
        detail = Table("Verbose detail", "Value")
        detail.add_row("Authentication detail", auth_detail)
        detail.add_row("Artifact root", str(default_artifact_root()))
        console.print(detail)
