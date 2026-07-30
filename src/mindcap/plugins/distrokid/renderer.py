from __future__ import annotations

from typing import Any


def render_distrokid_markdown(normalized: dict[str, Any]) -> str:
    source_type = str(normalized.get("source_type") or "unknown")
    canonical_identifier = str(normalized.get("canonical_identifier") or "unknown")
    warnings = list(normalized.get("warnings") or [])
    lines = [
        "# DistroKid Capture",
        "",
        f"- Source type: `{source_type}`",
        f"- Canonical identifier: `{canonical_identifier}`",
        f"- Canonical URL: {normalized.get('canonical_url') or 'unknown'}",
        f"- Response units: {len(normalized.get('raw_response_units') or [])}",
    ]
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- ⚠ {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)
