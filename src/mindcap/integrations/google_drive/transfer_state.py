from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResumableTransferState:
    vault_id: str
    artifact_kind: str
    artifact_id: str
    staging_path: str
    expected_size: int
    expected_sha256: str
    session_uri: str
    confirmed_offset: int
    created_at: str
    parent_folder_id: str
    remote_file_id: str | None = None

    def redacted(self) -> dict[str, object]:
        payload = asdict(self)
        payload["session_uri"] = "<redacted>"
        return payload


def _state_path(root: Path, artifact_id: str) -> Path:
    return root / f"{artifact_id}.transfer.json"


def write_transfer_state(root: Path, state: ResumableTransferState) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = _state_path(root, state.artifact_id)
    path.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def read_transfer_state(root: Path, artifact_id: str) -> ResumableTransferState:
    payload = json.loads(_state_path(root, artifact_id).read_text(encoding="utf-8"))
    return ResumableTransferState(**payload)
