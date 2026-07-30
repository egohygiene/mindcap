from __future__ import annotations

from typing import Any


def build_manifest(
    *,
    normalized: dict[str, Any],
    version: int,
    previous_version: int | None,
    raw_unit_count: int,
    captured_at: str,
) -> dict[str, Any]:
    """Build a JSON-serializable archive manifest."""
    source_type = normalized.get("source_type", "unknown")
    return {
        "schema": "mindcap.soundcloud-archive/v0.1",
        "provider": "soundcloud",
        "source_type": source_type,
        "source_id": normalized.get("source_id"),
        "canonical_url": normalized.get("canonical_url"),
        "capture_version": version,
        "previous_version": previous_version,
        "captured_at": captured_at,
        "raw_unit_count": raw_unit_count,
        "warnings": normalized.get("warnings") or [],
        "readme_path": "README.md",
        "checksums_path": "checksums.json",
        "report_json_path": "reports/capture-report.json",
        "source_metadata_path": "source/metadata.json",
    }
