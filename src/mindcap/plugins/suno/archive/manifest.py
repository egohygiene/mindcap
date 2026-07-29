from __future__ import annotations

from typing import Any


def build_manifest(
    *,
    normalized: dict[str, Any],
    version: int,
    previous_version: int | None,
    raw_unit_count: int,
    asset_count: int,
    captured_at: str,
) -> dict[str, Any]:
    return {
        "schema": "mindcap.suno-archive/v0.1",
        "provider": "suno",
        "source_type": "workspace",
        "workspace_id": normalized["workspace_id"],
        "source_id": normalized["source_id"],
        "canonical_url": normalized.get("canonical_url"),
        "title": normalized.get("title"),
        "capture_version": version,
        "previous_version": previous_version,
        "captured_at": captured_at,
        "raw_unit_count": raw_unit_count,
        "asset_count": asset_count,
        "clip_count": len(normalized.get("clips") or []),
        "warnings": normalized.get("warnings") or [],
        "readme_path": "README.md",
        "checksums_path": "checksums.json",
        "report_json_path": "reports/capture-report.json",
        "report_markdown_path": "reports/capture-report.md",
        "workspace_metadata_path": "workspace/metadata.json",
    }
