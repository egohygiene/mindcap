from __future__ import annotations

import json
import shutil
from typing import Any

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import canonical_content_hash, sha256_bytes
from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle
from mindcap.plugins.chrome_bookmarks.archive.layout import (
    bundle_path,
    safe_relative_path,
    source_root,
)
from mindcap.plugins.chrome_bookmarks.archive.verifier import (
    verify_chrome_bookmarks_bundle,
)


class ChromeBookmarksStorageStrategy:
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
        finalized = bundle_path(
            request.artifact_root, request.provider, source_id, version
        )
        if finalized.exists():
            raise VerificationError(f'Bundle already exists: "{finalized}"')
        if staging.exists():
            shutil.rmtree(staging)

        checksums: list[dict[str, Any]] = []

        def write_bytes(relative_path: str, payload: bytes) -> None:
            safe_path = safe_relative_path(relative_path)
            destination = staging / safe_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            checksums.append(
                {
                    "path": safe_path,
                    "sha256": sha256_bytes(payload),
                    "byte_size": len(payload),
                }
            )

        def write_text(relative_path: str, payload: str) -> None:
            write_bytes(relative_path, payload.encode("utf-8"))

        try:
            raw_units: list[dict[str, Any]] = []
            for unit in sorted(envelope.response_units, key=lambda item: item.sequence):
                extension = ".json" if "json" in unit.media_type else ".bin"
                relative_path = f"raw/{unit.unit_id}{extension}"
                write_bytes(relative_path, unit.body)
                raw_units.append(
                    {
                        "unit_id": unit.unit_id,
                        "sequence": unit.sequence,
                        "path": relative_path,
                        "media_type": unit.media_type,
                        "byte_size": len(unit.body),
                        "safe_metadata": unit.safe_metadata,
                    }
                )

            write_text(
                "raw/index.json",
                json.dumps(
                    {
                        "schema": "mindcap.chrome-bookmarks.raw-index/v0.1",
                        "source_id": source_id,
                        "capture_version": version,
                        "units": raw_units,
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
            )
            write_text(
                "normalized/bookmarks.json",
                json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False),
            )
            write_text("README.md", transcript)
            write_text(
                "reports/capture-report.json",
                json.dumps(
                    {
                        "schema": "mindcap.chrome-bookmarks.capture-report/v0.1",
                        "source_id": source_id,
                        "capture_version": version,
                        "status": "complete_with_warnings"
                        if normalized.get("warnings")
                        else "complete",
                        "profile_count": normalized.get("summary", {}).get(
                            "profile_count", 0
                        ),
                        "bookmark_count": normalized.get("summary", {}).get(
                            "bookmark_count", 0
                        ),
                        "folder_count": normalized.get("summary", {}).get(
                            "folder_count", 0
                        ),
                        "warnings": normalized.get("warnings") or [],
                        "safe_metadata": envelope.safe_metadata,
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
            )
            write_text(
                "checksums.json",
                json.dumps({"files": checksums}, indent=2, sort_keys=True),
            )
            write_text(
                "manifest.json",
                json.dumps(
                    {
                        "schema": "mindcap.chrome-bookmarks.archive/v0.1",
                        "provider": request.provider,
                        "source_type": normalized.get("source_type"),
                        "source_id": source_id,
                        "canonical_identifier": normalized.get("canonical_identifier"),
                        "capture_version": version,
                        "previous_version": previous_version,
                        "captured_at": envelope.captured_at.isoformat(),
                        "raw_unit_count": len(raw_units),
                        "warnings": normalized.get("warnings") or [],
                        "readme_path": "README.md",
                        "report_json_path": "reports/capture-report.json",
                        "normalized_path": "normalized/bookmarks.json",
                        "raw_index_path": "raw/index.json",
                        "checksums_path": "checksums.json",
                    },
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
            )
            staging.replace(finalized)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        history = (
            json.loads(history_path.read_text(encoding="utf-8"))
            if history_path.exists()
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

    def verify(self, bundle_path: Any) -> None:
        verify_chrome_bookmarks_bundle(bundle_path)
