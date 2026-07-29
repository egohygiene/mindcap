from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import canonical_content_hash, sha256_bytes
from mindcap.core.models import (
    CaptureEnvelope,
    CaptureRequest,
    StoredBundle,
)


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _yaml(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


class FilesystemStorageStrategy:
    def persist(
        self,
        request: CaptureRequest,
        envelope: CaptureEnvelope,
        normalized: dict[str, Any],
        transcript: str,
    ) -> StoredBundle:
        source_id = str(normalized["source_id"])
        source_root = (
            request.artifact_root / "conversations" / request.provider / source_id
        )
        source_root.mkdir(parents=True, exist_ok=True)
        latest_path = source_root / "latest.yaml"
        content_hash = canonical_content_hash(normalized)

        previous_version: int | None = None
        if latest_path.exists():
            latest = yaml.safe_load(latest_path.read_text(encoding="utf-8"))
            if latest.get("canonical_content_hash") == content_hash:
                existing = source_root / latest["bundle_path"]
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

        staging = source_root / f".staging-{uuid.uuid4().hex}"
        bundle = source_root / f"v{version}"
        if bundle.exists():
            raise VerificationError(f'Bundle already exists: "{bundle}"')

        try:
            raw_dir = staging / "raw"
            normalized_dir = staging / "normalized"
            reports_dir = staging / "reports"
            raw_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            reports_dir.mkdir(parents=True)

            raw_units: list[dict[str, Any]] = []
            unit_hashes: list[str] = []
            for unit in sorted(envelope.response_units, key=lambda item: item.sequence):
                extension = ".json" if "json" in unit.media_type else ".bin"
                filename = f"{unit.unit_id}{extension}"
                (raw_dir / filename).write_bytes(unit.body)
                digest = sha256_bytes(unit.body)
                unit_hashes.append(digest)
                raw_units.append(
                    {
                        "unit_id": unit.unit_id,
                        "sequence": unit.sequence,
                        "path": filename,
                        "media_type": unit.media_type,
                        "byte_size": len(unit.body),
                        "sha256": digest,
                        "source_url": unit.source_url,
                    }
                )
            raw_combined_hash = sha256_bytes("".join(unit_hashes).encode())
            (raw_dir / "index.yaml").write_text(
                _yaml(
                    {
                        "schema": "mindcap.raw-index/v0.1",
                        "source_id": source_id,
                        "capture_version": version,
                        "units": raw_units,
                        "combined_hash": raw_combined_hash,
                        "security_transformations": 0,
                    }
                ),
                encoding="utf-8",
            )

            normalized = dict(normalized)
            normalized["capture_version"] = version
            normalized_bytes = json.dumps(
                normalized, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8")
            (normalized_dir / "conversation.json").write_bytes(normalized_bytes)
            (normalized_dir / "conversation.md").write_text(
                transcript, encoding="utf-8"
            )

            manifest = {
                "schema": "mindcap.source-artifact/v0.1",
                "source_id": source_id,
                "source_type": "conversation",
                "provider": request.provider,
                "provider_id": request.canonical_identifier,
                "canonical_url": request.canonical_url,
                "capture_version": version,
                "previous_version": previous_version,
                "capture_status": (
                    "complete_with_warnings"
                    if normalized.get("attachment_warnings")
                    else "complete"
                ),
                "captured_at": envelope.captured_at.isoformat(),
                "sensitivity": request.sensitivity,
                "strategy": envelope.strategy,
                "raw_index_path": "raw/index.yaml",
                "normalized_path": "normalized/conversation.json",
                "transcript_path": "normalized/conversation.md",
                "capture_report_path": "reports/capture-report.yaml",
                "redaction_ledger_path": None,
                "raw_combined_hash": raw_combined_hash,
                "normalized_content_hash": sha256_bytes(normalized_bytes),
                "canonical_content_hash": content_hash,
                "versions": {
                    "schema": "mindcap.source-artifact/v0.1",
                    "normalized_schema": "mindcap.normalized-conversation/v0.1",
                    "raw_index_schema": "mindcap.raw-index/v0.1",
                    "normalizer": "mindcap.chatgpt.normalizer/v0.1",
                    "renderer": "mindcap.chatgpt.renderer/v0.1",
                    "canonicalizer": "mindcap.hashing/v0.1",
                },
                "conversation": {
                    "title": normalized.get("title"),
                    "provider_node_count": normalized.get("provider_node_count"),
                    "provider_message_count": normalized.get("provider_message_count"),
                    "visible_message_count": normalized.get("visible_message_count"),
                    "hidden_message_count": normalized.get("hidden_message_count"),
                    "structural_node_count": normalized.get("structural_node_count"),
                    "knowledge_eligible_message_count": normalized.get(
                        "knowledge_eligible_message_count"
                    ),
                    # Legacy field kept for backward compatibility; mirrors
                    # visible_message_count for human-facing display.
                    "message_count": normalized.get("visible_message_count")
                    or len(normalized.get("messages", {})),
                    "participant_count": len(normalized.get("participants", [])),
                },
            }
            (staging / "manifest.yaml").write_text(_yaml(manifest), encoding="utf-8")
            (reports_dir / "capture-report.yaml").write_text(
                _yaml(
                    {
                        "schema": "mindcap.capture-report/v0.1",
                        "status": (
                            "complete_with_warnings"
                            if normalized.get("attachment_warnings")
                            else "complete"
                        ),
                        "source_id": source_id,
                        "capture_version": version,
                        "strategy": envelope.strategy,
                        "warnings": (envelope.warnings or [])
                        + (normalized.get("attachment_warnings") or []),
                        "safe_metadata": envelope.safe_metadata,
                        # Counts
                        "provider_node_count": normalized.get("provider_node_count"),
                        "provider_message_count": normalized.get(
                            "provider_message_count"
                        ),
                        "visible_message_count": normalized.get(
                            "visible_message_count"
                        ),
                        "hidden_message_count": normalized.get(
                            "hidden_message_count"
                        ),
                        "structural_node_count": normalized.get(
                            "structural_node_count"
                        ),
                        "knowledge_eligible_message_count": normalized.get(
                            "knowledge_eligible_message_count"
                        ),
                        # Legacy
                        "message_count": normalized.get("visible_message_count")
                        or len(normalized.get("messages", {})),
                        # Attachments
                        "attachments_discovered": len(
                            normalized.get("attachments") or []
                        ),
                        "attachments_downloaded": sum(
                            1
                            for a in (normalized.get("attachments") or [])
                            if a.get("capture_status")
                            in {"downloaded", "verified"}
                        ),
                        "attachments_unavailable": sum(
                            1
                            for a in (normalized.get("attachments") or [])
                            if a.get("capture_status")
                            not in {"downloaded", "verified"}
                        ),
                    }
                ),
                encoding="utf-8",
            )
            staging.replace(bundle)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        history_path = source_root / "version-history.yaml"
        loaded_history = (
            yaml.safe_load(history_path.read_text(encoding="utf-8"))
            if history_path.exists()
            else None
        )
        history: dict[str, Any] = (
            loaded_history
            if isinstance(loaded_history, dict)
            else {"source_id": source_id, "versions": []}
        )
        versions = history.setdefault("versions", [])
        if not isinstance(versions, list):
            raise VerificationError("Version history has an invalid versions field.")
        versions.append(
            {
                "version": version,
                "captured_at": envelope.captured_at.isoformat(),
                "canonical_content_hash": content_hash,
                "raw_combined_hash": raw_combined_hash,
                "bundle_path": f"v{version}",
                "strategy": envelope.strategy,
                "previous_version": previous_version,
            }
        )
        _atomic_text(history_path, _yaml(history))
        _atomic_text(
            latest_path,
            _yaml(
                {
                    "source_id": source_id,
                    "version": version,
                    "bundle_path": f"v{version}",
                    "canonical_content_hash": content_hash,
                    "updated_at": envelope.captured_at.isoformat(),
                }
            ),
        )
        self.verify(bundle)
        return StoredBundle(
            source_id=source_id,
            version=version,
            path=bundle,
            status="complete",
            canonical_content_hash=content_hash,
        )

    def verify(self, bundle_path: Path) -> None:
        manifest_path = bundle_path / "manifest.yaml"
        if not manifest_path.is_file():
            raise VerificationError("Bundle manifest is missing.")
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        raw_index_path = bundle_path / manifest["raw_index_path"]
        normalized_path = bundle_path / manifest["normalized_path"]
        transcript_path = bundle_path / manifest["transcript_path"]
        for required in (raw_index_path, normalized_path, transcript_path):
            if not required.is_file():
                raise VerificationError(f'Missing bundle artifact: "{required}"')

        raw_index = yaml.safe_load(raw_index_path.read_text(encoding="utf-8"))
        unit_hashes: list[str] = []
        for unit in raw_index["units"]:
            raw_path = raw_index_path.parent / unit["path"]
            actual = sha256_bytes(raw_path.read_bytes())
            if actual != unit["sha256"]:
                raise VerificationError(f'Raw hash mismatch: "{raw_path}"')
            unit_hashes.append(actual)
        combined = sha256_bytes("".join(unit_hashes).encode())
        if combined != manifest["raw_combined_hash"]:
            raise VerificationError("Combined raw hash mismatch.")
        if (
            sha256_bytes(normalized_path.read_bytes())
            != manifest["normalized_content_hash"]
        ):
            raise VerificationError("Normalized content hash mismatch.")
