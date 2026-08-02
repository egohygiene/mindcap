from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ChromeUserDataRoot:
    channel: str
    path: Path
    source: str = "automatic"


@dataclass(frozen=True)
class ChromeProfile:
    channel: str
    user_data_dir: Path
    profile_dir: Path
    profile_directory_name: str
    profile_id: str
    profile_name: str | None
    bookmarks_path: Path


@dataclass(frozen=True)
class FileSnapshotMetadata:
    size: int
    modified_ns: int


@dataclass(frozen=True)
class SnapshotResult:
    profile: ChromeProfile
    primary_bytes: bytes
    selected_bytes: bytes
    selected_source: str
    primary_before: FileSnapshotMetadata
    primary_after: FileSnapshotMetadata
    backup_bytes: bytes | None = None
    backup_before: FileSnapshotMetadata | None = None
    backup_after: FileSnapshotMetadata | None = None
    warnings: list[str] = field(default_factory=list)
    retries: int = 0
