from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StoreDescriptor:
    backend: str
    locator: str
    details: dict[str, str]


@dataclass(frozen=True)
class FilesystemVaultLocator:
    path: Path

    @property
    def backend(self) -> str:
        return "filesystem"

    @property
    def canonical(self) -> str:
        return str(self.path)


@dataclass(frozen=True)
class GoogleDriveVaultLocator:
    folder_id: str

    @property
    def backend(self) -> str:
        return "google-drive"

    @property
    def canonical(self) -> str:
        return f"gdrive://{self.folder_id}"


VaultLocator = FilesystemVaultLocator | GoogleDriveVaultLocator


@dataclass(frozen=True)
class ArtifactLocator:
    backend: str
    stable_id: str
    handle: str


@dataclass(frozen=True)
class LocalArtifact:
    kind: str
    artifact_id: str
    path: Path
    sha256: str
    byte_size: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredArtifact:
    locator: ArtifactLocator
    kind: str
    artifact_id: str
    sha256: str
    byte_size: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PublishedArtifact:
    locator: ArtifactLocator
    kind: str
    artifact_id: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    reason: str | None = None


@dataclass(frozen=True)
class ArtifactQuery:
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadResult:
    locator: ArtifactLocator
    destination: Path
    sha256: str
    byte_size: int


class VaultArtifactStore(Protocol):
    def describe(self) -> StoreDescriptor: ...

    def initialize_vault(self, metadata: dict[str, Any]) -> VaultLocator: ...

    def list_artifacts(self, query: ArtifactQuery) -> Iterable[StoredArtifact]: ...

    def stat_artifact(self, locator: ArtifactLocator) -> StoredArtifact: ...

    def publish_immutable(self, artifact: LocalArtifact) -> PublishedArtifact: ...

    def download(
        self, locator: ArtifactLocator, destination: Path
    ) -> DownloadResult: ...

    def verify_remote(self, artifact: PublishedArtifact) -> VerificationResult: ...


def parse_vault_locator(value: str | Path | VaultLocator) -> VaultLocator:
    if isinstance(value, FilesystemVaultLocator | GoogleDriveVaultLocator):
        return value
    if isinstance(value, Path):
        return FilesystemVaultLocator(path=value.expanduser().resolve())
    text = value.strip()
    if text.startswith("gdrive://"):
        folder_id = text.removeprefix("gdrive://").strip("/")
        if not folder_id:
            raise ValueError('Google Drive locator must be "gdrive://<folder-id>".')
        return GoogleDriveVaultLocator(folder_id=folder_id)
    return FilesystemVaultLocator(path=Path(text).expanduser().resolve())
