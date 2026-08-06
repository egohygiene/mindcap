from __future__ import annotations

import json
import shutil
from typing import Any

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import canonical_content_hash, sha256_bytes
from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle
from mindcap.plugins.distrokid.archive.layout import (
    bundle_path,
    safe_relative_path,
    source_root,
)
from mindcap.plugins.distrokid.archive.verifier import verify_distrokid_bundle


class DistroKidArchiveStorageStrategy:
    def persist(
        self,
        request: CaptureRequest,
        envelope: CaptureEnvelope,
        normalized: dict[str, Any],
        transcript: str,
    ) -> StoredBundle:
        source_id = str(normalized["source_id"])
        root = source_root(request.artifact_root, source_id)
        root.mkdir(parents=True, exist_ok=True)

        latest_path = root / "latest.json"
        history_path = root / "version-history.json"
        content_hash = canonical_content_hash(normalized)
        force = bool(request.options.get("force"))

        previous_version: int | None = None
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            if latest.get("canonical_content_hash") == content_hash and not force:
                existing = root / str(latest["bundle_path"])
                return StoredBundle(
                    source_id=source_id,
                    version=int(latest["version"]),
                    path=existing,
                    status="unchanged",
                    canonical_content_hash=content_hash,
                )
            previous_version = int(latest["version"])
            version = previous_version + 1
        else:
            version = 1

        staging = root / f".staging-v{version}"
        bundle = bundle_path(request.artifact_root, source_id, version)
        if bundle.exists():
            raise VerificationError(f'Bundle already exists: "{bundle}"')
        if staging.exists():
            shutil.rmtree(staging)

        checksums: list[dict[str, Any]] = []

        def write_bytes(relative_path: str, payload: bytes) -> None:
            safe_path = safe_relative_path(relative_path)
            path = staging / safe_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            checksums.append(
                {
                    "path": safe_path,
                    "sha256": sha256_bytes(payload),
                    "byte_size": len(payload),
                }
            )

        def write_json(relative_path: str, payload: dict[str, Any]) -> None:
            write_bytes(
                relative_path,
                json.dumps(
                    payload, indent=2, sort_keys=True, ensure_ascii=False
                ).encode("utf-8"),
            )

        try:
            raw_units: list[dict[str, Any]] = []
            for unit in sorted(envelope.response_units, key=lambda item: item.sequence):
                extension = ".json" if "json" in unit.media_type else ".bin"
                unit_path = f"raw/{unit.unit_id}{extension}"
                write_bytes(unit_path, unit.body)
                raw_units.append(
                    {
                        "unit_id": unit.unit_id,
                        "sequence": unit.sequence,
                        "path": unit_path,
                        "media_type": unit.media_type,
                        "source_url": unit.source_url,
                        "endpoint_category": unit.endpoint_category,
                        "retrieved_at": unit.retrieved_at.isoformat()
                        if unit.retrieved_at
                        else None,
                        "byte_size": len(unit.body),
                        "safe_metadata": unit.safe_metadata,
                    }
                )

            scope = str(normalized.get("source_type") or "release")
            metadata_path = (
                "library/metadata.json"
                if scope == "library"
                else "release/metadata.json"
            )
            raw_index_path = (
                "library/raw-index.json"
                if scope == "library"
                else "release/raw-index.json"
            )

            write_json(
                raw_index_path,
                {
                    "schema": "mindcap.distrokid-raw-index/v0.1",
                    "source_id": source_id,
                    "capture_version": version,
                    "units": raw_units,
                },
            )
            write_json(metadata_path, normalized)
            write_bytes("README.md", transcript.encode("utf-8"))

            release = normalized.get("release") or {}
            indexes = release.get("indexes") if isinstance(release, dict) else {}
            upc_values = indexes.get("upc") if isinstance(indexes, dict) else []
            isrc_values = indexes.get("isrc") if isinstance(indexes, dict) else []
            write_json("indexes/upc.json", {"values": upc_values or []})
            write_json("indexes/isrc.json", {"values": isrc_values or []})

            report = {
                "schema": "mindcap.distrokid-capture-report/v0.1",
                "source_id": source_id,
                "capture_version": version,
                "source_type": scope,
                "status": "complete_with_warnings"
                if normalized.get("warnings")
                else "complete",
                "warnings": normalized.get("warnings") or [],
                "safe_metadata": envelope.safe_metadata,
            }
            write_json("reports/capture-report.json", report)

            manifest = {
                "schema": "mindcap.distrokid-archive/v0.1",
                "provider": "distrokid",
                "source_type": scope,
                "source_id": source_id,
                "canonical_identifier": normalized.get("canonical_identifier"),
                "canonical_url": normalized.get("canonical_url"),
                "capture_version": version,
                "previous_version": previous_version,
                "captured_at": envelope.captured_at.isoformat(),
                "raw_unit_count": len(raw_units),
                "asset_count": len(envelope.assets),
                "warnings": normalized.get("warnings") or [],
                "readme_path": "README.md",
                "report_path": "reports/capture-report.json",
                "raw_index_path": raw_index_path,
                "metadata_path": metadata_path,
            }
            write_json("manifest.json", manifest)
            write_json("checksums.json", {"files": checksums})

            staging.replace(bundle)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        loaded_history = (
            json.loads(history_path.read_text(encoding="utf-8"))
            if history_path.exists()
            else None
        )
        history: dict[str, Any] = (
            loaded_history
            if isinstance(loaded_history, dict)
            else {"source_id": source_id, "versions": []}
        )
        versions = history.get("versions")
        if not isinstance(versions, list):
            versions = []
            history["versions"] = versions
        versions.append(
            {
                "version": version,
                "bundle_path": f"v{version}",
                "captured_at": envelope.captured_at.isoformat(),
                "canonical_content_hash": content_hash,
                "previous_version": previous_version,
            }
        )
        history_path.write_text(
            json.dumps(history, indent=2, sort_keys=True), encoding="utf-8"
        )
        latest_path.write_text(
            json.dumps(
                {
                    "source_id": source_id,
                    "version": version,
                    "bundle_path": f"v{version}",
                    "canonical_content_hash": content_hash,
                    "updated_at": envelope.captured_at.isoformat(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.verify(bundle)
        return StoredBundle(
            source_id=source_id,
            version=version,
            path=bundle,
            status="complete",
            canonical_content_hash=content_hash,
        )

    def verify(self, bundle_path: Any) -> None:
        verify_distrokid_bundle(bundle_path)
