from __future__ import annotations

from pathlib import Path

from mindcap.integrations.google_drive.models import (
    DRIVE_FILE_SCOPE,
    GoogleDriveAuthProfile,
)


def authenticate_google_drive(client_secrets_file: Path) -> GoogleDriveAuthProfile:
    if not client_secrets_file.is_file():
        raise FileNotFoundError(f"Client secrets file not found: {client_secrets_file}")
    # The real OAuth loopback flow is implemented in a follow-up change using an
    # injected browser + token storage adapter. This function deliberately avoids
    # reading or storing tokens in the repository.
    return GoogleDriveAuthProfile(account="configured", scope=DRIVE_FILE_SCOPE)
