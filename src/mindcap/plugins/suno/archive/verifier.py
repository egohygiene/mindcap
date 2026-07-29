from __future__ import annotations

import json
from pathlib import Path

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_bytes


def verify_workspace_bundle(bundle_path: Path) -> None:
    manifest_path = bundle_path / "manifest.json"
    checksums_path = bundle_path / "checksums.json"
    if not manifest_path.is_file():
        raise VerificationError("Bundle manifest is missing.")
    if not checksums_path.is_file():
        raise VerificationError("Bundle checksums are missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for required in (
        bundle_path / manifest["readme_path"],
        bundle_path / manifest["report_json_path"],
        bundle_path / manifest["report_markdown_path"],
        bundle_path / manifest["workspace_metadata_path"],
    ):
        if not required.is_file():
            raise VerificationError(f'Missing bundle artifact: "{required}"')
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for entry in checksums.get("files", []):
        path = bundle_path / entry["path"]
        if not path.is_file():
            raise VerificationError(f'Missing checksummed file: "{path}"')
        digest = sha256_bytes(path.read_bytes())
        if digest != entry["sha256"]:
            raise VerificationError(f'Checksum mismatch: "{path}"')
