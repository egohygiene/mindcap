from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from mindcap.vault import catalog
from mindcap.vault.errors import UnsafeRestorePathError, VaultError
from mindcap.vault.layout import safe_relative_path
from mindcap.vault.models import RestoreSummary
from mindcap.vault.receipts import new_run_id, write_restore_receipt
from mindcap.vault.verifier import load_latest_valid_catalog

_CHUNK_SIZE = 1024 * 1024



def restore_archive_unit(
    *,
    vault_path: Path,
    provider: str,
    source_id: str,
    capture_version: str,
    destination: Path,
    overwrite: bool = False,
) -> RestoreSummary:
    generation, db_path, _seal = load_latest_valid_catalog(vault_path)
    if db_path is None or generation is None:
        raise VaultError("No sealed catalog generation is available for restore.")
    conn = catalog.connect_database(db_path)
    try:
        unit = conn.execute(
            """
            SELECT archive_unit_id, bundle_root
            FROM archive_units
            WHERE provider = ? AND source_id = ? AND capture_version = ?
            """,
            (provider, source_id, capture_version),
        ).fetchone()
        if unit is None:
            raise VaultError(
                f"Archive unit not found for {provider}:{source_id}:v{capture_version}"
            )
        bundle_root = safe_relative_path(str(unit["bundle_root"]))
        target_root = _secure_join(destination, bundle_root)
        target_root.mkdir(parents=True, exist_ok=True)
        rows = conn.execute(
            """
            SELECT af.relative_path, af.object_sha256, af.byte_size, p.pack_path, ol.member_name
            FROM archive_files af
            JOIN object_locations ol ON ol.object_sha256 = af.object_sha256
            JOIN packs p ON p.pack_id = ol.pack_id
            WHERE af.archive_unit_id = ?
            ORDER BY af.relative_path
            """,
            (int(unit["archive_unit_id"]),),
        ).fetchall()
    finally:
        conn.close()
    restored_files = 0
    for row in rows:
        relative_path = safe_relative_path(str(row["relative_path"]))
        target_path = _secure_join(target_root, relative_path)
        if target_path.exists() and not overwrite:
            raise VaultError(f'Restore target already exists: "{target_path}"')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_path.with_suffix(target_path.suffix + ".tmp")
        _extract_verified_object(
            pack_file=vault_path / str(row["pack_path"]),
            member_name=str(row["member_name"]),
            expected_sha256=str(row["object_sha256"]),
            expected_size=int(row["byte_size"]),
            output_path=temporary,
        )
        temporary.replace(target_path)
        restored_files += 1
    run_id = new_run_id("restore")
    receipt_path = write_restore_receipt(
        vault_path,
        {
            "run_id": run_id,
            "provider": provider,
            "source_id": source_id,
            "capture_version": capture_version,
            "destination": str(destination),
            "restored_bundle_path": str(target_root),
            "restored_files": restored_files,
        },
        run_id,
    )
    return RestoreSummary(
        vault_path=vault_path,
        destination=destination,
        restored_bundle_path=target_root,
        restored_files=restored_files,
        receipt_path=receipt_path,
    )



def _secure_join(root: Path, relative_path: str) -> Path:
    candidate = (root / Path(relative_path)).resolve()
    resolved_root = root.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise UnsafeRestorePathError(
            f'Restore path escaped destination root: "{relative_path}"'
        )
    return candidate



def _extract_verified_object(
    *,
    pack_file: Path,
    member_name: str,
    expected_sha256: str,
    expected_size: int,
    output_path: Path,
) -> None:
    digest = hashlib.sha256()
    total = 0
    with zipfile.ZipFile(pack_file, "r") as archive:
        with archive.open(member_name, "r") as source, output_path.open("wb") as target:
            while chunk := source.read(_CHUNK_SIZE):
                target.write(chunk)
                digest.update(chunk)
                total += len(chunk)
    if total != expected_size or digest.hexdigest() != expected_sha256:
        raise VaultError(f'Restored object hash mismatch for "{member_name}"')
