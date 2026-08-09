from __future__ import annotations

from dataclasses import dataclass

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


@dataclass(frozen=True)
class GoogleDriveAuthProfile:
    account: str
    scope: str = DRIVE_FILE_SCOPE


@dataclass(frozen=True)
class GoogleDriveFileMetadata:
    id: str
    name: str
    parent_id: str
    sha256: str | None
    size: int | None
    trashed: bool = False
