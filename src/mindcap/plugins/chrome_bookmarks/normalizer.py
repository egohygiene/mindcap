from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from mindcap.core.errors import NormalizationError
from mindcap.core.models import CaptureEnvelope
from mindcap.plugins.chrome_bookmarks.models import ChromeProfile
from mindcap.plugins.chrome_bookmarks.parser import parse_bookmark_bytes


def _profile_from_metadata(metadata: dict[str, Any]) -> ChromeProfile:
    try:
        return ChromeProfile(
            channel=str(metadata["channel"]),
            user_data_dir=Path(str(metadata["user_data_dir"])),
            profile_dir=Path(str(metadata["profile_dir"])),
            profile_directory_name=str(metadata["profile_directory_name"]),
            profile_id=str(metadata["profile_id"]),
            profile_name=(
                str(metadata["profile_name"])
                if metadata.get("profile_name") is not None
                else None
            ),
            bookmarks_path=Path(str(metadata["bookmarks_path"])),
        )
    except KeyError as exc:
        raise NormalizationError(
            f"Chrome Bookmarks capture metadata is missing required field: {exc}"
        ) from exc


def normalize_chrome_bookmarks(
    envelope: CaptureEnvelope,
    requested_identifier: str,
) -> dict[str, Any]:
    unit_by_id = {unit.unit_id: unit for unit in envelope.response_units}
    profiles_metadata = envelope.safe_metadata.get("profiles")
    if not isinstance(profiles_metadata, list) or not profiles_metadata:
        raise NormalizationError(
            "Chrome Bookmarks capture produced no profile metadata."
        )

    profiles: list[dict[str, Any]] = []
    bookmark_records: list[dict[str, Any]] = []
    folder_records: list[dict[str, Any]] = []
    warnings: list[str] = list(envelope.warnings)

    for entry in profiles_metadata:
        if not isinstance(entry, dict):
            continue
        profile = _profile_from_metadata(entry)
        selected_unit_id = entry.get("selected_unit_id")
        if not isinstance(selected_unit_id, str):
            raise NormalizationError(
                "Chrome Bookmarks profile "
                f"{profile.profile_id} has no selected response unit."
            )
        selected_unit = unit_by_id.get(selected_unit_id)
        if selected_unit is None:
            raise NormalizationError(
                f"Chrome Bookmarks response unit {selected_unit_id} was missing."
            )
        parsed = parse_bookmark_bytes(
            selected_unit.body,
            profile=profile,
            captured_at=envelope.captured_at,
        )
        bookmark_records.extend(parsed["bookmarks"])
        folder_records.extend(parsed["folders"])
        profile_warning_list = [str(item) for item in entry.get("warnings") or []]
        warnings.extend(profile_warning_list)
        warnings.extend(parsed["warnings"])
        profiles.append(
            {
                **asdict(profile),
                "user_data_dir": str(profile.user_data_dir),
                "profile_dir": str(profile.profile_dir),
                "bookmarks_path": str(profile.bookmarks_path),
                "selected_source": entry.get("selected_source"),
                "selected_unit_id": selected_unit_id,
                "warnings": profile_warning_list + parsed["warnings"],
                "bookmark_count": len(parsed["bookmarks"]),
                "folder_count": len(parsed["folders"]),
                "provider_version": parsed["provider_version"],
                "provider_checksum": parsed["checksum"],
            }
        )

    if not profiles:
        raise NormalizationError(
            "Chrome Bookmarks capture did not yield any readable profiles."
        )

    return {
        "schema": "mindcap.chrome-bookmarks.normalized/v0.1",
        "provider": "chrome-bookmarks",
        "source_type": "bookmark-collection",
        "source_id": f"chrome-bookmarks-{requested_identifier}",
        "canonical_identifier": requested_identifier,
        "canonical_url": None,
        "captured_at": envelope.captured_at.isoformat().replace("+00:00", "Z"),
        "strategy": envelope.strategy,
        "warnings": warnings,
        "profiles": profiles,
        "bookmark_records": bookmark_records,
        "folder_records": folder_records,
        "summary": {
            "profile_count": len(profiles),
            "bookmark_count": len(bookmark_records),
            "folder_count": len(folder_records),
        },
        "safe_metadata": dict(envelope.safe_metadata),
    }
