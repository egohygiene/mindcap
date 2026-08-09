from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from mindcap.vault.backends.protocols import (
    ArtifactLocator,
    ArtifactQuery,
    DownloadResult,
    FilesystemVaultLocator,
    LocalArtifact,
    PublishedArtifact,
    StoredArtifact,
    StoreDescriptor,
    VerificationResult,
)
from mindcap.vault.errors import VaultError
from mindcap.vault.layout import read_json, write_json_atomic
from mindcap.vault.models import VaultMetadata
from mindcap.vault.packs import sha256_file


class FilesystemArtifactStore:
    def __init__(self, locator: FilesystemVaultLocator) -> None:
        self._locator = locator
        self._root = locator.path

    def describe(self) -> StoreDescriptor:
        return StoreDescriptor(
            backend="filesystem",
            locator=self._locator.canonical,
            details={"root": str(self._root)},
        )

    def initialize_vault(self, metadata: dict[str, str]) -> FilesystemVaultLocator:
        self._root.mkdir(parents=True, exist_ok=True)
        vault_metadata = VaultMetadata.from_dict(metadata)
        write_json_atomic(self._root / "vault.json", vault_metadata.to_dict())
        return self._locator

    def list_artifacts(self, query: ArtifactQuery) -> Iterable[StoredArtifact]:
        allowed = set(query.kinds)
        for seal_path in sorted((self._root / "packs").glob("*.seal.json")):
            payload = read_json(seal_path)
            kind = "pack-seal"
            if allowed and kind not in allowed:
                continue
            artifact_id = str(payload.get("pack_id"))
            yield StoredArtifact(
                locator=ArtifactLocator(
                    backend="filesystem",
                    stable_id=artifact_id,
                    handle=str(seal_path),
                ),
                kind=kind,
                artifact_id=artifact_id,
                sha256=str(payload.get("sha256", "")),
                byte_size=int(payload.get("byte_size", 0)),
                metadata=payload,
            )

    def stat_artifact(self, locator: ArtifactLocator) -> StoredArtifact:
        if locator.backend != "filesystem":
            raise VaultError("Artifact locator backend mismatch for filesystem store.")
        path = Path(locator.handle)
        payload = read_json(path)
        kind = str(payload.get("kind", "unknown"))
        return StoredArtifact(
            locator=locator,
            kind=kind,
            artifact_id=locator.stable_id,
            sha256=str(payload.get("sha256", "")),
            byte_size=int(payload.get("byte_size", 0)),
            metadata=payload,
        )

    def publish_immutable(self, artifact: LocalArtifact) -> PublishedArtifact:
        target = self._root / artifact.kind / artifact.path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_size = target.stat().st_size
            existing_sha = sha256_file(target)
            if existing_size != artifact.byte_size or existing_sha != artifact.sha256:
                raise VaultError(
                    f"Conflicting immutable artifact already exists: {target.name}"
                )
        else:
            target.write_bytes(artifact.path.read_bytes())
        return PublishedArtifact(
            locator=ArtifactLocator(
                backend="filesystem",
                stable_id=artifact.artifact_id,
                handle=str(target),
            ),
            kind=artifact.kind,
            artifact_id=artifact.artifact_id,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
        )

    def download(self, locator: ArtifactLocator, destination: Path) -> DownloadResult:
        source = Path(locator.handle)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return DownloadResult(
            locator=locator,
            destination=destination,
            sha256=sha256_file(destination),
            byte_size=destination.stat().st_size,
        )

    def verify_remote(self, artifact: PublishedArtifact) -> VerificationResult:
        path = Path(artifact.locator.handle)
        if not path.exists():
            return VerificationResult(verified=False, reason="artifact missing")
        size = path.stat().st_size
        digest = sha256_file(path)
        if size != artifact.byte_size:
            return VerificationResult(verified=False, reason="size mismatch")
        if digest != artifact.sha256:
            return VerificationResult(verified=False, reason="sha256 mismatch")
        return VerificationResult(verified=True)
