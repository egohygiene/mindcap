"""SoundCloud archive storage strategy."""

from __future__ import annotations

import json
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import canonical_content_hash, sha256_bytes
from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle
from mindcap.plugins.soundcloud.archive.layout import (
    bundle_path,
    safe_relative_path,
    source_root,
)
from mindcap.plugins.soundcloud.archive.manifest import build_manifest
from mindcap.plugins.soundcloud.archive.verifier import verify_soundcloud_bundle


class SoundCloudArchiveStorageStrategy:
    """Persist a SoundCloud capture envelope as an immutable versioned archive."""

    def persist(
        self,
        request: CaptureRequest,
        envelope: CaptureEnvelope,
        normalized: dict[str, Any],
        transcript: str,
    ) -> StoredBundle:
        source_id = str(normalized["source_id"])
        root = source_root(request.artifact_root, request.provider, source_id)
        root.mkdir(parents=True, exist_ok=True)
        latest_path = root / "latest.json"
        history_path = root / "version-history.json"
        content_hash = canonical_content_hash(normalized)
        force = bool(request.options.get("force"))

        previous_version: int | None = None
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            if latest.get("canonical_content_hash") == content_hash and not force:
                existing = root / latest["bundle_path"]
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
        finalized = bundle_path(
            request.artifact_root, request.provider, source_id, version
        )

        if finalized.exists():
            raise VerificationError(f'Bundle already exists: "{finalized}"')
        if staging.exists():
            shutil.rmtree(staging)

        checksums: list[dict[str, Any]] = []

        def write_bytes(relative_path: str, payload: bytes) -> None:
            relative_path = safe_relative_path(relative_path)
            path = staging / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            checksums.append(
                {
                    "path": relative_path,
                    "sha256": sha256_bytes(payload),
                    "byte_size": len(payload),
                }
            )

        def write_text(relative_path: str, payload: str) -> None:
            write_bytes(relative_path, payload.encode("utf-8"))

        try:
            # Raw response units.
            raw_units: list[dict[str, Any]] = []
            for unit in sorted(envelope.response_units, key=lambda u: u.sequence):
                extension = ".json" if "json" in unit.media_type else ".bin"
                rel = f"raw/{unit.unit_id}{extension}"
                write_bytes(rel, unit.body)
                raw_units.append(
                    {
                        "unit_id": unit.unit_id,
                        "sequence": unit.sequence,
                        "path": rel,
                        "media_type": unit.media_type,
                        "endpoint_category": unit.endpoint_category,
                        "retrieved_at": unit.retrieved_at.isoformat()
                        if unit.retrieved_at
                        else None,
                        "byte_size": len(unit.body),
                        "safe_metadata": unit.safe_metadata,
                    }
                )

            write_text(
                "source/raw-index.json",
                json.dumps(
                    {
                        "schema": "mindcap.soundcloud-raw-index/v0.1",
                        "source_id": source_id,
                        "capture_version": version,
                        "units": raw_units,
                    },
                    indent=2,
                    sort_keys=True,
                ),
            )
            write_text(
                "source/metadata.json",
                json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False),
            )
            write_text("README.md", transcript)

            report: dict[str, Any] = {
                "schema": "mindcap.soundcloud-capture-report/v0.1",
                "source_id": source_id,
                "source_type": normalized.get("source_type"),
                "capture_version": version,
                "status": "complete_with_warnings"
                if normalized.get("warnings")
                else "complete",
                "raw_unit_count": len(envelope.response_units),
                "warnings": normalized.get("warnings") or [],
                "safe_metadata": envelope.safe_metadata,
            }
            write_text(
                "reports/capture-report.json",
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
            )

            manifest = build_manifest(
                normalized=normalized,
                version=version,
                previous_version=previous_version,
                raw_unit_count=len(envelope.response_units),
                captured_at=envelope.captured_at.isoformat(),
            )
            write_text(
                "checksums.json",
                json.dumps({"files": checksums}, indent=2, sort_keys=True),
            )
            write_text(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True),
            )
            staging.replace(finalized)

        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            for asset in envelope.assets:
                with suppress(OSError):
                    asset.temporary_path.unlink()
                with suppress(OSError):
                    asset.temporary_path.parent.rmdir()

        # Update version history and latest pointer.
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
        self.verify(finalized)
        return StoredBundle(
            source_id=source_id,
            version=version,
            path=finalized,
            status="complete",
            canonical_content_hash=content_hash,
        )

    def verify(self, bundle_path: Path) -> None:
        verify_soundcloud_bundle(bundle_path)
