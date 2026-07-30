from __future__ import annotations

import json
from pathlib import Path

from mindcap.core.errors import VerificationError
from mindcap.core.hashing import sha256_file


def verify_soundcloud_bundle(bundle_path: Path) -> None:
    """Verify the integrity of a finalized SoundCloud archive bundle.

    Raises
    ------
    :exc:`~mindcap.core.errors.VerificationError`
        On any integrity failure.
    """
    manifest_path = bundle_path / "manifest.json"
    checksums_path = bundle_path / "checksums.json"

    if not manifest_path.is_file():
        raise VerificationError("Bundle manifest is missing.")
    if not checksums_path.is_file():
        raise VerificationError("Bundle checksums are missing.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_root = bundle_path.resolve()

    for required_key, label in (
        ("readme_path", "README"),
        ("report_json_path", "capture report"),
        ("source_metadata_path", "source metadata"),
    ):
        rel = manifest.get(required_key)
        if not rel:
            continue
        required = bundle_path / rel
        if not required.is_file():
            raise VerificationError(
                f'Missing required bundle artifact ({label}): "{required}"'
            )

    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))

    for partial in bundle_path.rglob("*.part"):
        if partial.is_file():
            raise VerificationError(f'Partial file present: "{partial}"')

    for entry in checksums.get("files", []):
        path = bundle_path / entry["path"]
        if not path.is_file():
            raise VerificationError(f'Missing checksummed file: "{path}"')
        if path.stat().st_size == 0:
            raise VerificationError(f'Zero-byte file in bundle: "{path}"')
        if not path.resolve().is_relative_to(bundle_root):
            raise VerificationError(f'Unsafe path escaped bundle root: "{path}"')
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise VerificationError(f'Checksum mismatch: "{path}"')
