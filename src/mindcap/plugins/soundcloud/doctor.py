"""SoundCloud doctor: privacy-safe diagnostics."""

from __future__ import annotations

import platform
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from mindcap import __version__
from mindcap.config import default_artifact_root
from mindcap.plugins.soundcloud.auth import (
    has_refreshable_state,
    load_soundcloud_auth_state,
    private_permission_status,
    redact_secret,
    soundcloud_auth_file,
    soundcloud_client_id,
    soundcloud_redirect_uri,
    token_state,
)


def _artifact_writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".mindcap-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return "yes"
    except OSError:
        return "no"


def _network_status(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=5.0, follow_redirects=True)
        return f"reachable ({resp.status_code})"
    except Exception:
        return "unreachable"


def _authenticated_reachability() -> str:
    try:
        from mindcap.plugins.soundcloud.client import SoundCloudClient

        client = SoundCloudClient()
        client.get_me()
        return "reachable"
    except Exception:
        return "unverified"


def doctor_soundcloud(console: Console, *, verbose: bool = False) -> None:
    """Print a privacy-safe SoundCloud diagnostics table.

    Authentication states:

    - ``verified`` — authenticated API call succeeded.
    - ``unverified`` — auth state present but API call not confirmed.
    - ``expired`` — access token has expired.
    - ``refreshable`` — expired but refresh token available.
    - ``indeterminate`` — state present but validity unknown.
    - ``unreachable`` — API is unreachable.
    - ``configuration-required`` — no credentials configured.
    """
    auth_path = soundcloud_auth_file()
    state = load_soundcloud_auth_state(required=False)
    token_label, _token_detail = token_state(state)
    client_id_configured = bool(soundcloud_client_id())

    table = Table("Check", "Status")
    table.add_row("Mindcap version", __version__)
    table.add_row("Operating system", platform.platform())
    table.add_row("Credential file exists", "yes" if auth_path.is_file() else "no")
    table.add_row("Credential file permissions", private_permission_status(auth_path))
    table.add_row(
        "Client ID configured",
        "yes" if client_id_configured else "no (MINDCAP_SOUNDCLOUD_CLIENT_ID not set)",
    )
    table.add_row("OAuth token state", token_label)
    table.add_row(
        "Refresh token available",
        "yes" if has_refreshable_state(state) else "no",
    )
    table.add_row("Configured redirect URI", soundcloud_redirect_uri())
    table.add_row(
        "Network reachability",
        _network_status("https://api.soundcloud.com"),
    )
    table.add_row(
        "Secrets redacted",
        "yes"
        if state is None
        or redact_secret(state.access_token).startswith("<redacted")
        or not state.access_token
        else "no",
    )
    table.add_row(
        "Artifact destination writable",
        _artifact_writable(default_artifact_root()),
    )

    if state and state.account_id:
        table.add_row(
            "Cached account",
            f"ID {state.account_id}"
            + (f" / {state.account_permalink}" if state.account_permalink else ""),
        )

    console.print(table)

    if verbose:
        detail = Table("Verbose detail", "Value")
        detail.add_row("Credential file path", str(auth_path))
        detail.add_row("Artifact root", str(default_artifact_root()))
        detail.add_row(
            "Token detail",
            _token_detail if state else "No auth state.",
        )
        console.print(detail)
