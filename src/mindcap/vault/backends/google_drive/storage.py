from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mindcap.vault.backends.protocols import (
    ArtifactLocator,
    ArtifactQuery,
    DownloadResult,
    GoogleDriveVaultLocator,
    LocalArtifact,
    PublishedArtifact,
    StoredArtifact,
    StoreDescriptor,
    VerificationResult,
)
from mindcap.vault.errors import VaultError


class GoogleDriveArtifactStore:
    """Google Drive-backed artifact store.

    The storage contract exists now so the vault engine can select backends via
    a typed boundary. The transfer implementation is intentionally isolated in
    ``mindcap.integrations.google_drive`` and is wired in later steps.
    """

    def __init__(self, locator: GoogleDriveVaultLocator) -> None:
        self._locator = locator

    def describe(self) -> StoreDescriptor:
        return StoreDescriptor(
            backend="google-drive",
            locator=self._locator.canonical,
            details={"vault_folder_id": self._locator.folder_id},
        )

    def initialize_vault(self, metadata: dict[str, Any]) -> GoogleDriveVaultLocator:
        _ = metadata
        raise VaultError(
            "Google Drive initialization requires authenticated API access and "
            "must be performed via the Google Drive backend workflow."
        )

    def list_artifacts(self, query: ArtifactQuery) -> Iterable[StoredArtifact]:
        _ = query
        raise VaultError("Google Drive artifact listing is not yet wired.")

    def stat_artifact(self, locator: ArtifactLocator) -> StoredArtifact:
        _ = locator
        raise VaultError("Google Drive artifact stat is not yet wired.")

    def publish_immutable(self, artifact: LocalArtifact) -> PublishedArtifact:
        _ = artifact
        raise VaultError("Google Drive publish is not yet wired.")

    def download(self, locator: ArtifactLocator, destination: Path) -> DownloadResult:
        _ = (locator, destination)
        raise VaultError("Google Drive download is not yet wired.")

    def verify_remote(self, artifact: PublishedArtifact) -> VerificationResult:
        _ = artifact
        raise VaultError("Google Drive verification is not yet wired.")
