from __future__ import annotations

import json
from typing import Any

from mindcap.core.errors import NormalizationError
from mindcap.core.models import CaptureEnvelope


def _parse_json(unit_body: bytes) -> Any:
    return json.loads(unit_body.decode("utf-8"))


def normalize_distrokid(
    envelope: CaptureEnvelope,
    requested_identifier: str,
) -> dict[str, Any]:
    scope = "library" if requested_identifier == "account-library" else "release"
    source_id = f"distrokid-{requested_identifier}"

    parsed_units: list[dict[str, Any]] = []
    for unit in sorted(envelope.response_units, key=lambda item: item.sequence):
        payload: Any | None = None
        if "json" in unit.media_type:
            try:
                payload = _parse_json(unit.body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None

        parsed_units.append(
            {
                "unit_id": unit.unit_id,
                "sequence": unit.sequence,
                "media_type": unit.media_type,
                "source_url": unit.source_url,
                "endpoint_category": unit.endpoint_category,
                "retrieved_at": unit.retrieved_at.isoformat()
                if unit.retrieved_at
                else None,
                "safe_metadata": unit.safe_metadata,
                "top_level_keys": sorted(payload.keys())
                if isinstance(payload, dict)
                else [],
            }
        )

    if not parsed_units:
        raise NormalizationError("DistroKid capture produced no response units.")

    normalized: dict[str, Any] = {
        "schema": "mindcap.distrokid.normalized/v0.1",
        "provider": "distrokid",
        "source_type": scope,
        "source_id": source_id,
        "canonical_identifier": requested_identifier,
        "canonical_url": envelope.canonical_url,
        "captured_at": envelope.captured_at.isoformat(),
        "strategy": envelope.strategy,
        "warnings": list(envelope.warnings),
        "safe_metadata": dict(envelope.safe_metadata),
        "raw_response_units": parsed_units,
        "provider_metadata": {
            "response_unit_count": len(parsed_units),
            "unknown": dict(envelope.safe_metadata.get("provider_metadata") or {}),
        },
    }

    if scope == "library":
        normalized["library"] = {
            "canonical_identifier": "account-library",
            "releases": [],
            "completeness": {
                "expected_release_count": envelope.safe_metadata.get(
                    "expected_release_count"
                ),
                "unique_releases_discovered": envelope.safe_metadata.get(
                    "unique_releases_discovered"
                ),
                "batches_observed": envelope.safe_metadata.get("batches_observed"),
                "terminal_signal": envelope.safe_metadata.get("terminal_signal"),
                "duplicate_count": envelope.safe_metadata.get("duplicate_count", 0),
                "unresolved_count": envelope.safe_metadata.get("unresolved_count", 0),
                "capture_complete": bool(
                    envelope.safe_metadata.get("capture_complete", False)
                ),
            },
        }
    else:
        normalized["release"] = {
            "album_uuid": requested_identifier,
            "source_url": envelope.canonical_url,
            "tracks": [],
            "artwork": [],
            "store_destinations": [],
            "indexes": {
                "upc": [],
                "isrc": [],
            },
        }

    return normalized
