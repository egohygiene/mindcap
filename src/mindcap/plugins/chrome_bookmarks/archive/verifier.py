from __future__ import annotations

import json
from pathlib import Path

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_file


def verify_chrome_bookmarks_bundle(bundle_path: Path) -> None:
    manifest_path = bundle_path / "manifest.json"
    checksums_path = bundle_path / "checksums.json"
    if not manifest_path.is_file():
        raise VerificationError("Bundle manifest is missing.")
    if not checksums_path.is_file():
        raise VerificationError("Bundle checksums are missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for required_key in (
        "readme_path",
        "report_json_path",
        "normalized_path",
        "raw_index_path",
    ):
        relative = manifest.get(required_key)
        if not isinstance(relative, str) or not relative:
            raise VerificationError(
                f"Bundle manifest is missing required field: {required_key}"
            )
        if not (bundle_path / relative).is_file():
            raise VerificationError(
                f'Missing required bundle artifact: "{bundle_path / relative}"'
            )
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for entry in checksums.get("files", []):
        path = bundle_path / str(entry["path"])
        if not path.is_file():
            raise VerificationError(f'Missing checksummed file: "{path}"')
        if sha256_file(path) != entry["sha256"]:
            raise VerificationError(f'Checksum mismatch: "{path}"')
