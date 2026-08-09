from __future__ import annotations

from mindcap.vault.backends.filesystem import FilesystemArtifactStore
from mindcap.vault.backends.protocols import (
    ArtifactLocator,
    ArtifactQuery,
    DownloadResult,
    FilesystemVaultLocator,
    GoogleDriveVaultLocator,
    LocalArtifact,
    PublishedArtifact,
    StoredArtifact,
    StoreDescriptor,
    VaultArtifactStore,
    VaultLocator,
    VerificationResult,
    parse_vault_locator,
)

__all__ = [
    "ArtifactLocator",
    "ArtifactQuery",
    "DownloadResult",
    "FilesystemArtifactStore",
    "FilesystemVaultLocator",
    "GoogleDriveVaultLocator",
    "LocalArtifact",
    "PublishedArtifact",
    "StoreDescriptor",
    "StoredArtifact",
    "VaultArtifactStore",
    "VaultLocator",
    "VerificationResult",
    "parse_vault_locator",
]
