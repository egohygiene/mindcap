from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from mindcap.core.errors import NormalizationError
from mindcap.plugins.chrome_bookmarks.models import ChromeProfile

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def normalize_chrome_timestamp(raw: str | None) -> dict[str, str | None]:
    if raw is None:
        return {"raw": None, "value": None, "status": "missing", "warning": None}
    if raw == "0":
        return {"raw": raw, "value": None, "status": "zero", "warning": None}
    try:
        microseconds = int(raw)
    except ValueError:
        return {
            "raw": raw,
            "value": None,
            "status": "invalid",
            "warning": "Chrome timestamp was not numeric.",
        }
    try:
        converted = _CHROME_EPOCH + timedelta(microseconds=microseconds)
    except OverflowError:
        return {
            "raw": raw,
            "value": None,
            "status": "invalid",
            "warning": "Chrome timestamp was out of range.",
        }
    return {
        "raw": raw,
        "value": converted.isoformat().replace("+00:00", "Z"),
        "status": "parsed",
        "warning": None,
    }


def _string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def parse_bookmark_payload(
    payload: object,
    *,
    profile: ChromeProfile,
    captured_at: datetime,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise NormalizationError("Chrome Bookmarks payload must be a JSON object.")
    roots = payload.get("roots")
    if not isinstance(roots, dict):
        raise NormalizationError(
            'Chrome Bookmarks payload is missing the "roots" object.'
        )

    bookmarks: list[dict[str, Any]] = []
    folders: list[dict[str, Any]] = []
    warnings: list[str] = []

    def visit_children(
        nodes: object,
        *,
        root_kind: str,
        folder_path: list[str],
    ) -> None:
        if not isinstance(nodes, list):
            warnings.append(f"Root {root_kind} had non-list children and was skipped.")
            return
        for position, node in enumerate(nodes):
            if not isinstance(node, dict):
                warnings.append(
                    f"Skipped malformed node at root {root_kind} position {position}."
                )
                continue
            node_type = _string(node.get("type"))
            title = _string(node.get("name")) or ""
            timestamp_added = normalize_chrome_timestamp(
                _string(node.get("date_added"))
            )
            timestamp_modified = normalize_chrome_timestamp(
                _string(node.get("date_modified"))
            )
            timestamp_last_used = normalize_chrome_timestamp(
                _string(node.get("date_last_used"))
            )
            common = {
                "schema_version": "mindcap.normalized-bookmark/v0.1",
                "source_provider": "chrome-bookmarks",
                "browser": "google-chrome",
                "browser_channel": profile.channel,
                "profile_id": profile.profile_id,
                "profile_name": profile.profile_name,
                "profile_directory": profile.profile_directory_name,
                "source_root": root_kind,
                "root_kind": root_kind,
                "source_node_id": _string(node.get("id")),
                "source_guid": _string(node.get("guid")),
                "folder_path": list(folder_path),
                "title": title,
                "position": position,
                "date_added_raw": timestamp_added["raw"],
                "date_added": timestamp_added["value"],
                "date_added_status": timestamp_added["status"],
                "date_added_warning": timestamp_added["warning"],
                "date_last_used_raw": timestamp_last_used["raw"],
                "date_last_used": timestamp_last_used["value"],
                "date_last_used_status": timestamp_last_used["status"],
                "date_last_used_warning": timestamp_last_used["warning"],
                "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
            }
            if node_type == "folder":
                folders.append(
                    {
                        **common,
                        "record_type": "folder",
                        "date_modified_raw": timestamp_modified["raw"],
                        "date_modified": timestamp_modified["value"],
                        "date_modified_status": timestamp_modified["status"],
                        "date_modified_warning": timestamp_modified["warning"],
                    }
                )
                visit_children(
                    node.get("children"),
                    root_kind=root_kind,
                    folder_path=[*folder_path, title],
                )
                continue
            if node_type == "url":
                url = _string(node.get("url"))
                if not url:
                    warnings.append(
                        "Skipped bookmark without URL at "
                        f"root {root_kind} position {position}."
                    )
                    continue
                bookmarks.append(
                    {
                        **common,
                        "record_type": "bookmark",
                        "url": url,
                        "url_scheme": urlsplit(url).scheme or None,
                    }
                )
                continue
            warnings.append(
                "Skipped unsupported Chrome bookmark node type "
                f"{node_type!r} at root {root_kind} position {position}."
            )

    for root_kind in ("bookmark_bar", "other", "synced"):
        root_node = roots.get(root_kind)
        if not isinstance(root_node, dict):
            continue
        visit_children(root_node.get("children"), root_kind=root_kind, folder_path=[])

    return {
        "bookmarks": bookmarks,
        "folders": folders,
        "warnings": warnings,
        "provider_version": _string(payload.get("version")),
        "checksum": _string(payload.get("checksum")),
    }


def parse_bookmark_bytes(
    payload: bytes,
    *,
    profile: ChromeProfile,
    captured_at: datetime,
) -> dict[str, Any]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NormalizationError(f"Chrome Bookmarks JSON was invalid: {exc}") from exc
    return parse_bookmark_payload(parsed, profile=profile, captured_at=captured_at)
