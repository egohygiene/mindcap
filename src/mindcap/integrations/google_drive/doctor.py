from __future__ import annotations

from pathlib import Path

from mindcap.integrations.google_drive.models import DRIVE_FILE_SCOPE


def doctor_google_drive(client_secrets_file: Path | None = None) -> dict[str, str]:
    diagnostics = {
        "scope": DRIVE_FILE_SCOPE,
        "client_secrets_file": (
            str(client_secrets_file) if client_secrets_file else "not provided"
        ),
        "status": "ok",
    }
    if client_secrets_file is not None and not client_secrets_file.is_file():
        diagnostics["status"] = "missing-client-secrets"
    return diagnostics
