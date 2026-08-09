from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mindcap.core.errors import VerificationError
from mindcap.plugins.suno.archive.verifier import verify_workspace_bundle
from mindcap.vault.layout import safe_relative_path
from mindcap.vault.models import ArchiveDescriptor, ArchiveFile, CatalogRecord

_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


class SunoVaultArchiveAdapter:
    provider = "suno"

    def discover(self, source: Path):
        source = source.expanduser().resolve()
        if _looks_like_bundle(source):
            yield source
            return
        for root, dirnames, filenames in os.walk(source, topdown=True):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in _SKIP_DIRS and not Path(root, name).is_symlink()
            ]
            path = Path(root)
            if {"manifest.json", "checksums.json"}.issubset(filenames):
                if _looks_like_bundle(path):
                    yield path
                    dirnames[:] = []

    def validate(self, bundle: Path) -> None:
        verify_workspace_bundle(bundle)

    def describe(self, bundle: Path) -> ArchiveDescriptor:
        manifest = _read_json(bundle / "manifest.json")
        provider = str(manifest.get("provider") or "")
        if provider != self.provider:
            raise VerificationError(f'Unsupported provider in bundle manifest: "{provider}"')
        source_id = str(manifest.get("source_id") or "")
        capture_version = str(manifest.get("capture_version") or "")
        if not source_id or not capture_version:
            raise VerificationError("Suno bundle manifest is missing identity fields.")
        return ArchiveDescriptor(
            provider=self.provider,
            source_id=source_id,
            capture_version=capture_version,
            title=_optional_string(manifest.get("title")),
            captured_at=_optional_string(manifest.get("captured_at")),
            bundle_root=safe_relative_path(f"{source_id}/v{capture_version}"),
            manifest=manifest,
        )

    def iter_files(self, bundle: Path):
        bundle = bundle.resolve()
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            relative_path = safe_relative_path(path.relative_to(bundle).as_posix())
            yield ArchiveFile(
                absolute_path=path,
                relative_path=relative_path,
                byte_size=path.stat().st_size,
            )

    def iter_records(self, bundle: Path):
        descriptor = self.describe(bundle)
        workspace_metadata = _read_json(bundle / "workspace" / "metadata.json")
        yield CatalogRecord(
            provider=self.provider,
            record_type="workspace",
            external_id=descriptor.source_id,
            parent_external_id=None,
            title=_optional_string(workspace_metadata.get("title"))
            or descriptor.title,
            created_at=_optional_string(workspace_metadata.get("created_at")),
            updated_at=_optional_string(workspace_metadata.get("updated_at")),
            captured_at=descriptor.captured_at,
            payload=workspace_metadata,
        )
        yield CatalogRecord(
            provider=self.provider,
            record_type="capture-manifest",
            external_id=f"{descriptor.source_id}:v{descriptor.capture_version}",
            parent_external_id=descriptor.source_id,
            title=descriptor.title,
            created_at=None,
            updated_at=None,
            captured_at=descriptor.captured_at,
            payload=descriptor.manifest,
        )
        clips_root = bundle / "clips"
        if clips_root.is_dir():
            for metadata_file in sorted(clips_root.glob("*/metadata.json")):
                payload = _read_json(metadata_file)
                clip_id = str(payload.get("clip_id") or metadata_file.parent.name)
                yield CatalogRecord(
                    provider=self.provider,
                    record_type="clip",
                    external_id=clip_id,
                    parent_external_id=descriptor.source_id,
                    title=_optional_string(payload.get("title")),
                    created_at=_optional_string(payload.get("created_at")),
                    updated_at=_optional_string(payload.get("updated_at")),
                    captured_at=descriptor.captured_at,
                    payload=payload,
                )



def _looks_like_bundle(path: Path) -> bool:
    if not path.is_dir():
        return False
    manifest_path = path / "manifest.json"
    checksums_path = path / "checksums.json"
    return manifest_path.is_file() and checksums_path.is_file()



def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationError(f'Expected a JSON object: "{path}"')
    return value



def _optional_string(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None
