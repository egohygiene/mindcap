from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from mindcap.plugins.chrome_bookmarks.discovery import (
    automatic_user_data_roots,
    discover_profiles,
)


def collect_chrome_bookmarks_diagnostics() -> list[dict[str, Any]]:
    roots = automatic_user_data_roots(channel="stable")
    profiles = discover_profiles(channel="stable")
    checks: list[dict[str, Any]] = [
        {
            "name": "automatic_user_data_roots",
            "status": str(len(roots)),
            "detail": ", ".join(str(root.path) for root in roots),
        },
        {
            "name": "readable_profiles",
            "status": str(len(profiles)),
            "detail": ", ".join(profile.profile_directory_name for profile in profiles)
            or "none",
        },
    ]
    return checks


def doctor_chrome_bookmarks(console: Console, *, verbose: bool = False) -> None:
    checks = collect_chrome_bookmarks_diagnostics()
    table = Table("Check", "Status")
    for check in checks:
        table.add_row(str(check["name"]), str(check["status"]))
    console.print(table)
    if verbose:
        detail = Table("Check", "Detail")
        for check in checks:
            detail.add_row(str(check["name"]), str(check.get("detail") or "-"))
        console.print(detail)
