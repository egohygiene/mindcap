from __future__ import annotations

import json
from pathlib import Path

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_file


def verify_distrokid_bundle(bundle_path: Path) -> None:
    manifest_path = bundle_path / "manifest.json"
    checksums_path = bundle_path / "checksums.json"
    if not manifest_path.is_file():
        raise VerificationError("Bundle manifest is missing.")
    if not checksums_path.is_file():
        raise VerificationError("Bundle checksums are missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in (
        "readme_path",
        "report_path",
        "raw_index_path",
        "metadata_path",
    ):
        required = bundle_path / manifest[key]
        if not required.is_file():
            raise VerificationError(f'Missing bundle artifact: "{required}"')

    for partial in bundle_path.rglob("*.part"):
        if partial.is_file():
            raise VerificationError(f'Partial file present: "{partial}"')

    root = bundle_path.resolve()
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for entry in checksums.get("files", []):
        path = bundle_path / entry["path"]
        if not path.is_file():
            raise VerificationError(f'Missing checksummed file: "{path}"')
        if path.resolve().is_relative_to(root) is False:
            raise VerificationError(f'Unsafe path escaped bundle root: "{path}"')
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise VerificationError(f'Checksum mismatch: "{path}"')
