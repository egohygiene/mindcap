from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mindcap.core.errors import CaptureFailedError
from mindcap.plugins.chrome_bookmarks.models import (
    ChromeProfile,
    FileSnapshotMetadata,
    SnapshotResult,
)


def _read_snapshot_metadata(path: Path) -> FileSnapshotMetadata:
    stat_result = path.stat()
    return FileSnapshotMetadata(
        size=stat_result.st_size, modified_ns=stat_result.st_mtime_ns
    )


def _stable_read(
    path: Path, *, label: str
) -> tuple[bytes, FileSnapshotMetadata, FileSnapshotMetadata]:
    before = _read_snapshot_metadata(path)
    payload = path.read_bytes()
    after = _read_snapshot_metadata(path)
    if before != after or len(payload) != before.size:
        raise CaptureFailedError(f"Chrome {label} changed during snapshot.")
    return payload, before, after


def snapshot_bookmarks(
    profile: ChromeProfile,
    *,
    retries: int = 3,
) -> SnapshotResult:
    backup_path = profile.bookmarks_path.with_name("Bookmarks.bak")
    fallback_warning = (
        "Primary Bookmarks JSON was invalid; "
        "normalized output used Bookmarks.bak instead."
    )
    last_error: Exception | None = None
    retry_count = 0

    with TemporaryDirectory(prefix="mindcap-chrome-bookmarks-") as staging_dir:
        staging_root = Path(staging_dir)
        backup_bytes: bytes | None = None
        backup_before: FileSnapshotMetadata | None = None
        backup_after: FileSnapshotMetadata | None = None
        if backup_path.is_file():
            try:
                backup_bytes, backup_before, backup_after = _stable_read(
                    backup_path, label="Bookmarks.bak"
                )
                (staging_root / "Bookmarks.bak").write_bytes(backup_bytes)
            except Exception:
                backup_bytes = None
                backup_before = None
                backup_after = None

        for attempt in range(1, retries + 1):
            retry_count = attempt - 1
            try:
                primary_bytes, primary_before, primary_after = _stable_read(
                    profile.bookmarks_path, label="Bookmarks"
                )
                (staging_root / "Bookmarks").write_bytes(primary_bytes)
                json.loads(primary_bytes.decode("utf-8"))
                return SnapshotResult(
                    profile=profile,
                    primary_bytes=primary_bytes,
                    selected_bytes=primary_bytes,
                    selected_source="primary",
                    primary_before=primary_before,
                    primary_after=primary_after,
                    backup_bytes=backup_bytes,
                    backup_before=backup_before,
                    backup_after=backup_after,
                    retries=retry_count,
                )
            except (
                CaptureFailedError,
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt == retries:
                    break

        if backup_bytes is not None:
            try:
                json.loads(backup_bytes.decode("utf-8"))
                primary_bytes, primary_before, primary_after = _stable_read(
                    profile.bookmarks_path, label="Bookmarks"
                )
                return SnapshotResult(
                    profile=profile,
                    primary_bytes=primary_bytes,
                    selected_bytes=backup_bytes,
                    selected_source="backup",
                    primary_before=primary_before,
                    primary_after=primary_after,
                    backup_bytes=backup_bytes,
                    backup_before=backup_before,
                    backup_after=backup_after,
                    warnings=[fallback_warning],
                    retries=retry_count,
                )
            except (
                CaptureFailedError,
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc

    detail = str(last_error) if last_error is not None else "unknown error"
    raise CaptureFailedError(
        "Could not capture a stable Chrome Bookmarks snapshot for "
        f'"{profile.profile_directory_name}": {detail}'
    )
