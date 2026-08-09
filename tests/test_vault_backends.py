from __future__ import annotations

from pathlib import Path

import pytest

from mindcap.integrations.google_drive.transfer_state import (
    ResumableTransferState,
    read_transfer_state,
    write_transfer_state,
)
from mindcap.integrations.google_drive.uploads import validate_chunk_size
from mindcap.vault.backends import (
    FilesystemVaultLocator,
    GoogleDriveVaultLocator,
    parse_vault_locator,
)


def test_parse_vault_locator_supports_filesystem_and_gdrive(tmp_path: Path) -> None:
    local = parse_vault_locator(tmp_path)
    remote = parse_vault_locator("gdrive://folder-123")

    assert isinstance(local, FilesystemVaultLocator)
    assert local.path == tmp_path.resolve()
    assert isinstance(remote, GoogleDriveVaultLocator)
    assert remote.folder_id == "folder-123"
    assert remote.canonical == "gdrive://folder-123"


def test_parse_vault_locator_rejects_empty_gdrive_id() -> None:
    with pytest.raises(ValueError, match="gdrive://<folder-id>"):
        parse_vault_locator("gdrive://")


def test_upload_chunk_size_must_be_256_kib_aligned() -> None:
    valid = validate_chunk_size(8 * 1024 * 1024)
    assert valid.size_bytes == 8 * 1024 * 1024

    with pytest.raises(ValueError, match="multiple of 256 KiB"):
        validate_chunk_size(1_000_000)


def test_transfer_state_written_owner_only_and_redacted(tmp_path: Path) -> None:
    state = ResumableTransferState(
        vault_id="vault-1",
        artifact_kind="pack",
        artifact_id="pack-1",
        staging_path="/tmp/stage/pack.zip",
        expected_size=123,
        expected_sha256="abc",
        session_uri="https://upload.example/session",
        confirmed_offset=0,
        created_at="2026-01-01T00:00:00+00:00",
        parent_folder_id="folder-1",
    )
    path = write_transfer_state(tmp_path, state)

    assert path.stat().st_mode & 0o777 == 0o600
    loaded = read_transfer_state(tmp_path, "pack-1")
    assert loaded == state
    assert loaded.redacted()["session_uri"] == "<redacted>"
