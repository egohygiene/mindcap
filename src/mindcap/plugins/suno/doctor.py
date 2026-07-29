from __future__ import annotations

from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from mindcap.config import default_artifact_root, suno_api_origin, suno_auth_file
from mindcap.plugins.suno.auth import (
    has_refreshable_clerk_state,
    jwt_state,
    load_suno_auth_state,
    private_permission_status,
    redact_secret,
)
from mindcap.plugins.suno.client import SunoClient


def _network_status(origin: str) -> str:
    try:
        response = httpx.get(origin, timeout=5.0, follow_redirects=True)
    except Exception:
        return "unreachable"
    return f"reachable ({response.status_code})"


def _authenticated_status() -> str:
    try:
        client = SunoClient()
        client.billing_info()
    except Exception:
        return "unverified"
    return "reachable"


def _artifact_writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".mindcap-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return "no"
    return "yes"


def doctor_suno(console: Console, *, verbose: bool = False) -> None:
    auth_path = suno_auth_file()
    state = load_suno_auth_state(required=False)
    jwt_label, jwt_detail = jwt_state(state)

    table = Table("Check", "Status")
    table.add_row("Credential file exists", "yes" if auth_path.is_file() else "no")
    table.add_row("Credential file permissions", private_permission_status(auth_path))
    table.add_row(
        "Refreshable Clerk state",
        "yes" if has_refreshable_clerk_state(state) else "no",
    )
    table.add_row("JWT state", jwt_label)
    table.add_row("Configured API origin", suno_api_origin())
    table.add_row("Network reachability", _network_status(suno_api_origin()))
    table.add_row("Authenticated API reachability", _authenticated_status())
    table.add_row(
        "Artifact destination writable",
        _artifact_writable(default_artifact_root()),
    )
    table.add_row(
        "Secrets redacted successfully",
        "yes"
        if redact_secret(getattr(state, "clerk_client_cookie", None)).startswith(
            "<redacted"
        )
        else "no",
    )
    console.print(table)

    if verbose:
        detail = Table("Verbose detail", "Value")
        detail.add_row("Credential file", str(auth_path))
        detail.add_row("JWT detail", jwt_detail)
        detail.add_row("Artifact root", str(default_artifact_root()))
        console.print(detail)
