from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from mindcap.vault import catalog
from mindcap.vault.errors import VaultError
from mindcap.vault.layout import (
    catalog_path,
    catalog_seal_path,
    list_catalog_generations,
    list_incomplete_artifacts,
    load_vault_metadata,
    read_json,
)
from mindcap.vault.models import CatalogSeal, VerifySummary
from mindcap.vault.packs import load_pack_index, sha256_file

_CHUNK_SIZE = 1024 * 1024


def load_latest_valid_catalog(
    vault_path: Path,
) -> tuple[int | None, Path | None, CatalogSeal | None]:
    for generation in reversed(list_catalog_generations(vault_path)):
        db_path = catalog_path(vault_path, generation)
        seal_file = catalog_seal_path(vault_path, generation)
        if not seal_file.is_file() or not db_path.is_file():
            continue
        seal = CatalogSeal.from_dict(read_json(seal_file))
        if sha256_file(db_path) != seal.sha256:
            continue
        conn = catalog.connect_database(db_path)
        try:
            catalog.validate_database(conn)
        finally:
            conn.close()
        return (generation, db_path, seal)
    return (None, None, None)


def verify_vault(vault_path: Path, *, deep: bool = False) -> VerifySummary:
    generation, db_path, _seal = load_latest_valid_catalog(vault_path)
    load_vault_metadata(vault_path)
    incomplete = list_incomplete_artifacts(vault_path)
    if db_path is None:
        return VerifySummary(
            vault_path=vault_path,
            latest_generation=None,
            deep=deep,
            pack_count=0,
            object_count=0,
            archive_units=0,
            incomplete_artifacts=incomplete,
            orphaned_sealed_packs=(),
            valid=True,
        )
    conn = catalog.connect_database(db_path)
    try:
        catalog.validate_database(conn)
        rows = conn.execute(
            "SELECT pack_id, pack_path FROM packs ORDER BY pack_id"
        ).fetchall()
        catalog_pack_ids = {str(row["pack_id"]) for row in rows}
        for row in rows:
            pack_file = vault_path / str(row["pack_path"])
            if not pack_file.is_file():
                raise VaultError(f'Missing referenced pack: "{pack_file}"')
        pack_index = load_pack_index(vault_path)
        orphaned = tuple(sorted(set(pack_index.pack_seals) - catalog_pack_ids))
        if deep:
            object_rows = conn.execute(
                """
                SELECT o.sha256, o.byte_size, p.pack_path, ol.member_name
                FROM objects o
                JOIN object_locations ol ON ol.object_sha256 = o.sha256
                JOIN packs p ON p.pack_id = ol.pack_id
                ORDER BY o.sha256
                """
            ).fetchall()
            for row in object_rows:
                _verify_object(
                    pack_file=vault_path / str(row["pack_path"]),
                    member_name=str(row["member_name"]),
                    expected_sha256=str(row["sha256"]),
                    expected_size=int(row["byte_size"]),
                )
        counts = catalog.summarize(conn)
    finally:
        conn.close()
    return VerifySummary(
        vault_path=vault_path,
        latest_generation=generation,
        deep=deep,
        pack_count=int(counts["pack_count"]),
        object_count=int(counts["object_count"]),
        archive_units=int(counts["archive_units"]),
        incomplete_artifacts=incomplete,
        orphaned_sealed_packs=orphaned,
        valid=True,
    )


def _verify_object(
    *,
    pack_file: Path,
    member_name: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    digest = hashlib.sha256()
    total = 0
    with (
        zipfile.ZipFile(pack_file, "r") as archive,
        archive.open(member_name, "r") as handle,
    ):
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
            total += len(chunk)
    if total != expected_size or digest.hexdigest() != expected_sha256:
        raise VaultError(f"Deep verification failed for {pack_file.name}:{member_name}")
