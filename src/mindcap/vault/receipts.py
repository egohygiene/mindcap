from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mindcap.vault.layout import imports_dir, reports_dir, write_json_atomic



def new_run_id(prefix: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"



def write_import_receipt(vault_path: Path, payload: dict[str, Any], run_id: str) -> Path:
    path = imports_dir(vault_path) / f"import-{run_id}.json"
    write_json_atomic(path, payload)
    return path



def write_restore_receipt(vault_path: Path, payload: dict[str, Any], run_id: str) -> Path:
    path = reports_dir(vault_path) / f"restore-{run_id}.json"
    write_json_atomic(path, payload)
    return path
