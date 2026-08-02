from __future__ import annotations

from typing import Any


def render_chrome_bookmarks_markdown(normalized: dict[str, Any]) -> str:
    summary = normalized.get("summary") or {}
    lines = [
        "# Chrome Bookmarks Capture",
        "",
        f"- Profiles captured: {summary.get('profile_count', 0)}",
        f"- Bookmarks captured: {summary.get('bookmark_count', 0)}",
        f"- Folders captured: {summary.get('folder_count', 0)}",
        "",
        "## Profiles",
    ]
    for profile in normalized.get("profiles") or []:
        name = profile.get("profile_name") or profile.get("profile_directory_name")
        lines.append(
            f"- {profile.get('channel')} / {name}: "
            f"{profile.get('bookmark_count', 0)} bookmarks, "
            f"{profile.get('folder_count', 0)} folders"
        )
    warnings = normalized.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)
