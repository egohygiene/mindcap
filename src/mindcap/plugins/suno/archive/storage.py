from __future__ import annotations

import json
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import canonical_content_hash, sha256_bytes
from mindcap.core.models import CaptureEnvelope, CaptureRequest, StoredBundle
from mindcap.plugins.suno.archive.layout import (
    bundle_path,
    safe_relative_path,
    source_root,
)
from mindcap.plugins.suno.archive.manifest import build_manifest
from mindcap.plugins.suno.archive.verifier import verify_workspace_bundle


class SunoWorkspaceStorageStrategy:
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
        bundle = bundle_path(
            request.artifact_root, request.provider, source_id, version
        )
        if bundle.exists():
            raise VerificationError(f'Bundle already exists: "{bundle}"')
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
                        "source_url": unit.source_url,
                        "endpoint_category": unit.endpoint_category,
                        "retrieved_at": unit.retrieved_at.isoformat()
                        if unit.retrieved_at
                        else None,
                        "byte_size": len(unit.body),
                        "safe_metadata": unit.safe_metadata,
                    }
                )
            write_text(
                "workspace/raw-index.json",
                json.dumps(
                    {
                        "schema": "mindcap.suno-raw-index/v0.1",
                        "source_id": source_id,
                        "capture_version": version,
                        "units": raw_units,
                    },
                    indent=2,
                    sort_keys=True,
                ),
            )
            write_text(
                "workspace/metadata.json",
                json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False),
            )
            write_text("README.md", transcript)
            report_json = {
                "schema": "mindcap.suno-capture-report/v0.1",
                "source_id": source_id,
                "capture_version": version,
                "status": "complete_with_warnings"
                if normalized.get("warnings")
                else "complete",
                "workspace_id": normalized["workspace_id"],
                "clip_count": len(normalized.get("clips") or []),
                "asset_count": len(envelope.assets),
                "warnings": normalized.get("warnings") or [],
                "safe_metadata": envelope.safe_metadata,
            }
            report_markdown = [
                "# Suno Capture Report",
                "",
                f"- Source ID: `{source_id}`",
                f"- Capture version: {version}",
                f"- Workspace ID: `{normalized['workspace_id']}`",
                f"- Status: {report_json['status']}",
                f"- Clips archived: {report_json['clip_count']}",
                f"- Assets archived: {report_json['asset_count']}",
                "",
            ]
            warnings = report_json.get("warnings") or []
            if warnings:
                report_markdown.extend(["## Warnings", ""])
                for warning in warnings:
                    report_markdown.append(f"- ⚠ {warning}")
                report_markdown.append("")
            write_text(
                "reports/capture-report.json",
                json.dumps(report_json, indent=2, sort_keys=True, ensure_ascii=False),
            )
            write_text(
                "reports/capture-report.md",
                "\n".join(report_markdown).rstrip() + "\n",
            )

            clip_map = {
                str(clip["clip_id"]): clip
                for clip in normalized.get("clips") or []
                if clip.get("clip_id")
            }
            for clip_id, clip in clip_map.items():
                write_text(
                    f"clips/{clip_id}/metadata.json",
                    json.dumps(clip, indent=2, sort_keys=True, ensure_ascii=False),
                )
                raw_refs = [
                    ref
                    for ref in normalized.get("raw_response_units") or []
                    if ref.get("clip_id") in {None, clip_id}
                ]
                write_text(
                    f"clips/{clip_id}/raw-index.json",
                    json.dumps(
                        {"units": raw_refs},
                        indent=2,
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                )
                prompts = clip.get("prompts") or {}
                if prompts.get("prompt"):
                    write_text(
                        f"clips/{clip_id}/prompts/prompt.txt", f"{prompts['prompt']}\n"
                    )
                if prompts.get("lyrics_prompt"):
                    write_text(
                        f"clips/{clip_id}/prompts/lyrics-prompt.txt",
                        f"{prompts['lyrics_prompt']}\n",
                    )
                if prompts.get("style_prompt"):
                    write_text(
                        f"clips/{clip_id}/prompts/style-prompt.txt",
                        f"{prompts['style_prompt']}\n",
                    )
                excluded_styles = prompts.get("excluded_styles") or []
                if excluded_styles:
                    write_text(
                        f"clips/{clip_id}/prompts/excluded-styles.txt",
                        "\n".join(str(item) for item in excluded_styles) + "\n",
                    )
                lyrics = clip.get("lyrics") or {}
                if lyrics.get("plain"):
                    write_text(
                        f"clips/{clip_id}/lyrics/lyrics.txt", f"{lyrics['plain']}\n"
                    )
                    write_text(
                        f"clips/{clip_id}/lyrics/lyrics.md", f"{lyrics['plain']}\n"
                    )
                if lyrics.get("aligned_words"):
                    write_text(
                        f"clips/{clip_id}/lyrics/aligned.json",
                        json.dumps(
                            lyrics["aligned_words"],
                            indent=2,
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                    )

            for asset in envelope.assets:
                relative_path = safe_relative_path(asset.relative_path)
                payload = asset.temporary_path.read_bytes()
                write_bytes(relative_path, payload)

            manifest = build_manifest(
                normalized=normalized,
                version=version,
                previous_version=previous_version,
                raw_unit_count=len(envelope.response_units),
                asset_count=len(envelope.assets),
                captured_at=envelope.captured_at.isoformat(),
            )
            write_text(
                "checksums.json",
                json.dumps({"files": checksums}, indent=2, sort_keys=True),
            )
            write_text("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            staging.replace(bundle)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            for asset in envelope.assets:
                with suppress(OSError):
                    asset.temporary_path.unlink()
                with suppress(OSError):
                    asset.temporary_path.parent.rmdir()

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

    def verify(self, bundle_path: Path) -> None:
        verify_workspace_bundle(bundle_path)
